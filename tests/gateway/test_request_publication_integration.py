from __future__ import annotations

import asyncio
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from examples.reference_consumer.application import ReferenceApplication
from examples.reference_consumer.interaction import ReferenceChannel
from imagent import Failed, Gateway, GatewayExtensions, GatewayLimits, ProjectionPolicy, Succeeded
from imagent.applications.contract import ProjectRef, ThreadRef
from imagent.applications.events import AgentEventType
from imagent.applications.requests import (
    ApprovalRequest,
    ApprovalResponse,
    InteractiveRequest,
    RequestResolution,
)
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.projection.request_correlation import InteractiveRequestProjection
from imagent.interaction.channels import DeliveryReceiptStatus
from imagent.interaction.controllers import MarkdownRequestPresenter
from imagent.interaction.operations import OperationErrorCode


def _gateway(*, application, channel, store):
    return Gateway(
        gateway_id="reference",
        applications=[application],
        channels=[channel],
        store=store,
        projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        limits=GatewayLimits(request_publication_wait_timeout_seconds=0.1),
        extensions=GatewayExtensions(request_presenter=MarkdownRequestPresenter()),
    )


class _ExpiringApplication(ReferenceApplication):
    """The native fixture owns expiry in both its event and pending snapshot."""

    def __init__(self, expiry_seconds):
        super().__init__()
        self.expiry_seconds = expiry_seconds

    def _publish(
        self,
        thread_ref: ThreadRef,
        event_type: AgentEventType,
        turn_id: str,
        data: dict[str, object],
        *,
        request: InteractiveRequest | None = None,
        request_resolution: RequestResolution | None = None,
    ) -> None:
        if request is not None:
            assert isinstance(request, ApprovalRequest)
            request = replace(
                request,
                expires_at=datetime.now(UTC) + timedelta(seconds=self.expiry_seconds),
            )
            self._pending_requests[request.request_ref] = request
        super()._publish(
            thread_ref,
            event_type,
            turn_id,
            data,
            request=request,
            request_resolution=request_resolution,
        )


class _PublicationStore(SQLiteGatewayStore):
    """Gate only real correlation persistence after real accepted delivery."""

    def __init__(self, path):
        super().__init__(path)
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()
        original = self._state.put_request_correlation

        async def put(correlation):
            self.arrived.set()
            await self.release.wait()
            return await original(correlation)

        self._state.put_request_correlation = put


class _ObservedChannel(ReferenceChannel):
    def __init__(self):
        super().__init__()
        self.delivered = asyncio.Event()

    async def send(self, message):
        result = await super().send(message)
        self.delivered.set()
        return result


class _HeldChannel(ReferenceChannel):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def send(self, message):
        self.entered.set()
        try:
            await self.release.wait()
            return await super().send(message)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class RequestPublicationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def create_thread(self, gateway, application, conversation, cwd):
        actions = gateway.actions(conversation.ref, actor=conversation.authenticated_actor)
        project = await actions.create_and_select_project(
            application.ref,
            cwd=cwd,
            action_id="project",
        )
        self.assertIsInstance(project, Succeeded)
        assert isinstance(project, Succeeded)
        assert isinstance(project.value.ref, ProjectRef)
        thread = await actions.create_and_bind_thread(project.value.ref, action_id="thread")
        self.assertIsInstance(thread, Succeeded)
        assert isinstance(thread, Succeeded)
        assert isinstance(thread.value.ref, ThreadRef)
        return actions, thread.value.ref

    async def test_public_sqlite_immediate_approval_joins_publication_and_executes_once(self):
        with TemporaryDirectory() as cwd:
            database = Path(cwd) / "bridge.sqlite3"
            store = _PublicationStore(database)
            application = ReferenceApplication()
            channel = ReferenceChannel()
            conversation = channel.conversation("one")
            consumer = _gateway(application=application, channel=channel, store=store)
            async with consumer:
                actions, thread = await self.create_thread(
                    consumer,
                    application,
                    conversation,
                    cwd,
                )
                request = await application.receive_native_approval_request(
                    thread, prompt="Approve synthetic action"
                )
                await asyncio.wait_for(store.arrived.wait(), 1)
                # Channel accepted and the completed outbound claim exists;
                # only the response-routing publication is held.
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                    self.assertEqual(
                        db.execute("SELECT count(*) FROM request_route_correlations").fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        db.execute(
                            "SELECT status FROM idempotency_records "
                            "WHERE record_key LIKE 'imagent:request-delivery:%'",
                        ).fetchone()[0],
                        "completed",
                    )
                self.assertEqual(len(channel.sent), 1)
                foreign = consumer.actions(channel.conversation("foreign").ref, actor="owner")
                rejected = await asyncio.wait_for(
                    foreign.respond_request(
                        request.request_ref,
                        ApprovalResponse("approve"),
                        action_id="foreign",
                    ),
                    1,
                )
                self.assertIsInstance(rejected, Failed)
                self.assertEqual(application.request_response_calls, 0)

                original = InteractiveRequestProjection.authorize_response

                async def reached(runtime, operation):
                    store.release.set()
                    return await original(runtime, operation)

                with patch.object(InteractiveRequestProjection, "authorize_response", reached):
                    result = await asyncio.wait_for(
                        actions.respond_request(
                            request.request_ref,
                            ApprovalResponse("approve"),
                            action_id="respond",
                        ),
                        1,
                    )
                self.assertIsInstance(result, Succeeded)
                replay = await actions.respond_request(
                    request.request_ref,
                    ApprovalResponse("approve"),
                    action_id="respond",
                )
                self.assertIsInstance(replay, Succeeded)
                duplicate = await actions.respond_request(
                    request.request_ref,
                    ApprovalResponse("approve"),
                    action_id="duplicate",
                )
                self.assertIsInstance(duplicate, Failed)
                self.assertEqual(application.request_response_calls, 1)

    async def test_stop_cancels_unpublished_authority_and_restart_recovers_native_pending(self):
        with TemporaryDirectory() as cwd:
            database = Path(cwd) / "bridge.sqlite3"
            store = _PublicationStore(database)
            application = ReferenceApplication()
            channel = ReferenceChannel()
            conversation = channel.conversation("one")
            consumer = _gateway(application=application, channel=channel, store=store)
            await consumer.start()
            response = None
            try:
                actions, thread = await self.create_thread(
                    consumer,
                    application,
                    conversation,
                    cwd,
                )
                request = await application.receive_native_approval_request(
                    thread, prompt="Approve synthetic action"
                )
                await asyncio.wait_for(store.arrived.wait(), 1)
                entered = asyncio.Event()
                original = InteractiveRequestProjection.authorize_response

                async def reached(runtime, operation):
                    entered.set()
                    return await original(runtime, operation)

                with patch.object(InteractiveRequestProjection, "authorize_response", reached):
                    response = asyncio.create_task(
                        actions.respond_request(
                            request.request_ref,
                            ApprovalResponse("approve"),
                            action_id="interrupted",
                        )
                    )
                    await asyncio.wait_for(entered.wait(), 1)
                    await asyncio.wait_for(consumer.stop(), 1)
                    outcome = await asyncio.wait_for(response, 1)
                self.assertIsInstance(outcome, Failed)
                self.assertEqual(application.request_response_calls, 0)
            finally:
                store.release.set()
                if response is not None:
                    await asyncio.gather(response, return_exceptions=True)
                await consumer.stop()

            # Fresh objects, same real database and native pending truth.
            new_channel = ReferenceChannel()
            new_store = _PublicationStore(database)
            restarted = _gateway(
                application=application,
                channel=new_channel,
                store=new_store,
            )
            async with restarted:
                await asyncio.wait_for(new_store.arrived.wait(), 1)
                new_store.release.set()
                result = await restarted.actions(conversation.ref, actor="owner").respond_request(
                    request.request_ref,
                    ApprovalResponse("approve"),
                    action_id="after-restart",
                )
                self.assertIsInstance(result, Succeeded)
                self.assertEqual(application.request_response_calls, 1)
                self.assertEqual(new_channel.native_send_calls, 0)

    async def test_stop_cancels_held_channel_send_and_releases_waiting_response(self):
        with TemporaryDirectory() as cwd:
            application = ReferenceApplication()
            channel = _HeldChannel()
            conversation = channel.conversation("one")
            consumer = _gateway(
                application=application,
                channel=channel,
                store=SQLiteGatewayStore(Path(cwd) / "bridge.sqlite3"),
            )
            await consumer.start()
            response = None
            try:
                actions, thread = await self.create_thread(consumer, application, conversation, cwd)
                request = await application.receive_native_approval_request(
                    thread, prompt="Approve synthetic action"
                )
                await asyncio.wait_for(channel.entered.wait(), 1)
                entered = asyncio.Event()
                original = InteractiveRequestProjection.authorize_response

                async def reached(runtime, operation):
                    entered.set()
                    return await original(runtime, operation)

                with patch.object(InteractiveRequestProjection, "authorize_response", reached):
                    response = asyncio.create_task(
                        actions.respond_request(
                            request.request_ref,
                            ApprovalResponse("approve"),
                            action_id="respond",
                        )
                    )
                    await asyncio.wait_for(entered.wait(), 1)
                    await asyncio.wait_for(consumer.stop(), 1)
                    result = await asyncio.wait_for(response, 1)
                self.assertIsInstance(result, Failed)
                self.assertTrue(channel.cancelled.is_set())
                self.assertEqual(application.request_response_calls, 0)
                self.assertEqual(channel.native_send_calls, 0)
            finally:
                channel.release.set()
                if response is not None:
                    await asyncio.gather(response, return_exceptions=True)
                await consumer.stop()

    async def test_independent_channel_child_inherits_context_but_waits_for_publication(self):
        with TemporaryDirectory() as cwd:
            store = _PublicationStore(Path(cwd) / "bridge.sqlite3")
            application = ReferenceApplication()
            channel = ReferenceChannel()
            conversation = channel.conversation("one")
            consumer = _gateway(application=application, channel=channel, store=store)
            child = None
            try:
                async with consumer:
                    actions, thread = await self.create_thread(
                        consumer, application, conversation, cwd
                    )
                    original_send = channel.send
                    child_created = asyncio.Event()
                    original_authorize = InteractiveRequestProjection.authorize_response

                    async def reached(runtime, operation):
                        await store.arrived.wait()
                        store.release.set()
                        return await original_authorize(runtime, operation)

                    async def send(message):
                        nonlocal child
                        receipt = await original_send(message)
                        child = asyncio.create_task(
                            actions.respond_request(
                                request.request_ref,
                                ApprovalResponse("approve"),
                                action_id="independent-child",
                            )
                        )
                        child_created.set()
                        # Deliberately return without awaiting the child. It is
                        # independent ingress despite inherited ContextVar state.
                        return receipt

                    with (
                        patch.object(channel, "send", send),
                        patch.object(InteractiveRequestProjection, "authorize_response", reached),
                    ):
                        request = await application.receive_native_approval_request(
                            thread, prompt="Approve synthetic action"
                        )
                        await asyncio.wait_for(child_created.wait(), 1)
                        assert child is not None
                        result = await asyncio.wait_for(child, 1)
                    self.assertIsInstance(result, Succeeded)
                    self.assertEqual(application.request_response_calls, 1)
            finally:
                store.release.set()
                if child is not None:
                    await asyncio.gather(child, return_exceptions=True)

    async def test_synchronous_channel_callback_cannot_deadlock_its_own_publication(self):
        await self._check_synchronous_callback(await_child=False)

    async def test_channel_awaiting_child_response_has_explicit_bounded_pending_outcome(self):
        await self._check_synchronous_callback(await_child=True)

    async def _check_synchronous_callback(self, *, await_child, expiry_seconds=None):
        with TemporaryDirectory() as cwd:
            application = (
                ReferenceApplication()
                if expiry_seconds is None
                else _ExpiringApplication(expiry_seconds)
            )
            channel = ReferenceChannel()
            conversation = channel.conversation("one")
            consumer = _gateway(
                application=application,
                channel=channel,
                store=SQLiteGatewayStore(Path(cwd) / "bridge.sqlite3"),
            )
            async with consumer:
                actions, thread = await self.create_thread(consumer, application, conversation, cwd)
                original_send = channel.send
                callback_done = asyncio.Event()
                callback_results = []

                async def send(message):
                    # Real receipt first, but Channel.send cannot return while
                    # its synchronous callback waits on this same publication.
                    receipt = await original_send(message)
                    response = actions.respond_request(
                        request.request_ref,
                        ApprovalResponse("approve"),
                        action_id="callback",
                    )
                    if await_child:
                        callback_results.append(await asyncio.create_task(response))
                    else:
                        callback_results.append(await response)
                    callback_done.set()
                    return receipt

                with patch.object(channel, "send", send):
                    request = await application.receive_native_approval_request(
                        thread, prompt="Approve synthetic action"
                    )
                    await asyncio.wait_for(callback_done.wait(), 1)
                    self.assertIsInstance(callback_results[0], Failed)
                    self.assertEqual(callback_results[0].error.code.value, "conflict")
                    self.assertIs(
                        callback_results[0].error.operation_error_code,
                        OperationErrorCode.REQUEST_DELIVERY_PENDING,
                    )
                    self.assertEqual(application.request_response_calls, 0)
                    # This terminal pre-dispatch outcome never runs later even
                    # once publication is ready; only a new reply may proceed.
                    replay = await actions.respond_request(
                        request.request_ref,
                        ApprovalResponse("approve"),
                        action_id="callback",
                    )
                    self.assertEqual(replay, callback_results[0])
                    self.assertEqual(application.request_response_calls, 0)
                    result = await asyncio.wait_for(
                        actions.respond_request(
                            request.request_ref,
                            ApprovalResponse("approve"),
                            action_id="independent",
                        ),
                        1,
                    )
                self.assertIsInstance(result, Succeeded)
                self.assertEqual(application.request_response_calls, 1)

    async def test_configured_wait_before_native_expiry_keeps_direct_pending_terminal(self):
        await self._check_synchronous_callback(await_child=False, expiry_seconds=0.3)

    async def test_configured_wait_before_native_expiry_keeps_child_pending_terminal(self):
        await self._check_synchronous_callback(await_child=True, expiry_seconds=0.3)

    async def test_native_expiry_cancelled_direct_response_retries_reserved_in_place(self):
        await self._check_native_expiry_cancellation(await_child=False, restart=False)

    async def test_native_expiry_cancelled_child_response_retries_reserved_in_place(self):
        await self._check_native_expiry_cancellation(await_child=True, restart=False)

    async def test_native_expiry_cancelled_direct_response_recovers_reserved_after_restart(self):
        await self._check_native_expiry_cancellation(await_child=False, restart=True)

    async def test_native_expiry_cancelled_child_response_recovers_reserved_after_restart(self):
        await self._check_native_expiry_cancellation(await_child=True, restart=True)

    async def _check_native_expiry_cancellation(self, *, await_child, restart):
        with TemporaryDirectory() as cwd:
            database = Path(cwd) / "bridge.sqlite3"
            application = _ExpiringApplication(0.04)
            channel = ReferenceChannel()
            conversation = channel.conversation("one")
            consumer = _gateway(
                application=application,
                channel=channel,
                store=SQLiteGatewayStore(database),
            )
            publication_done = asyncio.Event()
            callback_cancelled = asyncio.Event()
            original_publish = InteractiveRequestProjection.deliver_request_once

            async def observed_publish(runtime, *args):
                try:
                    return await original_publish(runtime, *args)
                finally:
                    publication_done.set()

            def receipts():
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                    return db.execute(
                        "SELECT phase, outcome_json FROM gateway_effect_receipts "
                        "WHERE category='request_response'"
                    ).fetchall()

            # start composes/caches this owner callback. The observer only marks
            # real publication teardown, so retries never depend on a sleep.
            with patch.object(
                InteractiveRequestProjection, "deliver_request_once", observed_publish
            ):
                await consumer.start()
            try:
                actions, thread = await self.create_thread(consumer, application, conversation, cwd)
                original_send = channel.send

                async def send(message):
                    receipt = await original_send(message)
                    try:
                        response = actions.respond_request(
                            request.request_ref,
                            ApprovalResponse("approve"),
                            action_id="expiry-cancelled",
                        )
                        if await_child:
                            await asyncio.create_task(response)
                        else:
                            await response
                        self.fail("the native send-expiry fence must cancel this held preflight")
                    except asyncio.CancelledError:
                        callback_cancelled.set()
                        raise
                    return receipt

                # Hold an ordinary preflight suspension until the existing send
                # expiry cancels it. This makes RESERVED cancellation causal,
                # rather than racing two timers or patching cancellation itself.
                gate = asyncio.Event()
                original_authorize = InteractiveRequestProjection.authorize_response

                async def held_preflight(runtime, operation):
                    await gate.wait()
                    return await original_authorize(runtime, operation)

                with (
                    patch.object(channel, "send", send),
                    patch.object(
                        InteractiveRequestProjection, "authorize_response", held_preflight
                    ),
                ):
                    request = await application.receive_native_approval_request(
                        thread, prompt="Synthetic expiring request"
                    )
                    await asyncio.wait_for(callback_cancelled.wait(), 1)
                    await asyncio.wait_for(publication_done.wait(), 1)
                self.assertEqual(receipts(), [("reserved", None)])
                self.assertEqual(application.request_response_calls, 0)
                self.assertEqual(len(channel.sent), 1)
                pending = await application.list_pending_requests()
                self.assertEqual(len(pending), 1)
                assert pending[0].expires_at is not None
                self.assertLessEqual(pending[0].expires_at, datetime.now(UTC))

                new_channel = None
                if restart:
                    await consumer.stop()
                    new_channel = ReferenceChannel()
                    consumer = _gateway(
                        application=application,
                        channel=new_channel,
                        store=SQLiteGatewayStore(database),
                    )
                    await consumer.start()
                    actions = consumer.actions(
                        conversation.ref, actor=conversation.authenticated_actor
                    )
                    # Startup must not execute RESERVED or resend expired truth.
                    self.assertEqual(receipts(), [("reserved", None)])
                    self.assertEqual(new_channel.native_send_calls, 0)
                    self.assertEqual(application.request_response_calls, 0)

                retry = await actions.respond_request(
                    request.request_ref,
                    ApprovalResponse("approve"),
                    action_id="expiry-cancelled",
                )
                self.assertIsInstance(retry, Failed)
                assert isinstance(retry, Failed)
                self.assertIs(retry.error.operation_error_code, OperationErrorCode.REQUEST_STALE)
                replay = await actions.respond_request(
                    request.request_ref,
                    ApprovalResponse("approve"),
                    action_id="expiry-cancelled",
                )
                self.assertEqual(replay, retry)
                fresh = await actions.respond_request(
                    request.request_ref,
                    ApprovalResponse("approve"),
                    action_id="fresh-but-expired",
                )
                self.assertIsInstance(fresh, Failed)
                assert isinstance(fresh, Failed)
                self.assertIs(fresh.error.operation_error_code, OperationErrorCode.REQUEST_STALE)
                self.assertEqual(application.request_response_calls, 0)
                self.assertEqual(len(receipts()), 2)
                self.assertTrue(all(row[0] == "terminal" for row in receipts()))
                if new_channel is not None:
                    self.assertEqual(new_channel.native_send_calls, 0)
            finally:
                await consumer.stop()

    async def test_rejected_and_unknown_channel_receipts_never_authorize_response(self):
        for status in (DeliveryReceiptStatus.REJECTED_BY_PLATFORM, DeliveryReceiptStatus.UNKNOWN):
            with self.subTest(status=status), TemporaryDirectory() as cwd:
                application = ReferenceApplication()
                channel = _ObservedChannel()
                conversation = channel.conversation("one")
                consumer = _gateway(
                    application=application,
                    channel=channel,
                    store=SQLiteGatewayStore(Path(cwd) / "bridge.sqlite3"),
                )
                async with consumer:
                    actions, thread = await self.create_thread(
                        consumer,
                        application,
                        conversation,
                        cwd,
                    )
                    channel.set_next_delivery_status(conversation.ref, status)
                    request = await application.receive_native_approval_request(
                        thread, prompt="Approve synthetic action"
                    )
                    await asyncio.wait_for(channel.delivered.wait(), 1)
                    result = await asyncio.wait_for(
                        actions.respond_request(
                            request.request_ref,
                            ApprovalResponse("approve"),
                            action_id="respond",
                        ),
                        1,
                    )
                    self.assertIsInstance(result, Failed)
                    assert isinstance(result, Failed)
                    self.assertIs(
                        result.error.operation_error_code, OperationErrorCode.REQUEST_STALE
                    )
                    self.assertEqual(application.request_response_calls, 0)
                    self.assertEqual(channel.native_send_calls, 1)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import patch

from imagent import GatewayLimits
from imagent.applications.capabilities import ProjectMode
from imagent.applications.requests import ApprovalResponse, RequestStaleError
from imagent.gateway.persistence.memory import (
    InMemoryProjectionRouteRepository,
    InMemoryRequestCorrelationRepository,
)
from imagent.gateway.persistence.repository_contracts import IdempotencyClaimStatus
from imagent.gateway.persistence.state_contracts import RequestRouteState, ThreadProjectionRoute
from imagent.gateway.projection.request_correlation import (
    InteractiveRequestProjection,
    RequestResponseRouted,
    RespondToRequest,
    _RequestPublicationPending,
)
from imagent.interaction.controllers import MarkdownRequestPresenter
from imagent.interaction.messages import ConversationRef
from imagent.testing import FakeAgentApplicationAdapter


class PublicationGate(InMemoryRequestCorrelationRepository):
    def __init__(self):
        super().__init__()
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()
        self.held_conversation = ConversationRef("channel", "one")

    async def put_request_correlation(self, correlation):
        if correlation.conversation_ref == self.held_conversation:
            self.arrived.set()
            await self.release.wait()
        return await super().put_request_correlation(correlation)


class RequestPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.application = FakeAgentApplicationAdapter(
            application_instance_id="app",
            project_mode=ProjectMode.FLAT,
        )
        self.thread = (
            await self.application.create_thread(self.application.default_project_ref)
        ).ref
        self.conversation = ConversationRef("channel", "one")
        self.route = ThreadProjectionRoute(
            route_id="route-one",
            thread_ref=self.thread,
            conversation_ref=self.conversation,
            updated_at=datetime.now(UTC),
        )
        self.routes = InMemoryProjectionRouteRepository()
        await self.routes.put_projection_route(self.route)
        self.correlations = PublicationGate()
        self.delivered = []

        async def deliver(message):
            self.delivered.append(message)
            return IdempotencyClaimStatus.ACQUIRED

        self.runtime = InteractiveRequestProjection(
            applications={"app": self.application},
            projections=self.routes,
            correlations=self.correlations,
            request_presenter=MarkdownRequestPresenter(),
            execute_application=self.application.execute,
            active_routes=self.routes.list_projection_routes,
            deliver_request_outbound=deliver,
            publication_wait_timeout_seconds=0.02,
        )
        self.request = await self.application.open_approval_request(self.thread, turn_id="turn")
        self.response = RespondToRequest(
            operation_id="respond",
            conversation_ref=self.conversation,
            actor="owner",
            request_ref=self.request.request_ref,
            response=ApprovalResponse("accept"),
            created_at=datetime.now(UTC),
        )
        self.tasks = []

    def task(self, awaitable):
        task = asyncio.create_task(awaitable)
        self.tasks.append(task)
        return task

    async def asyncTearDown(self):
        self.correlations.release.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.assertEqual(self.runtime._publication_locks.active_key_count, 0)
        self.assertEqual(self.runtime._request_locks.active_key_count, 0)
        self.assertFalse(self.runtime._publications)

    async def begin_publication(self, route=None):
        task = self.task(self.runtime.deliver_request_once(route or self.route, self.request))
        await asyncio.wait_for(self.correlations.arrived.wait(), 1)
        self.assertEqual(len(self.delivered), 1)
        self.assertFalse(await self.correlations.list_request_correlations())
        return task

    async def test_authorization_waits_for_accepted_delivery_correlation(self):
        publication = await self.begin_publication()
        # Event.set does not yield. The old authorization reads the empty row
        # before the released publisher runs; the fixed path joins publication.
        self.correlations.release.set()
        await asyncio.wait_for(self.runtime.authorize_response(self.response), 1)
        await publication
        result = await self.runtime.route_response(self.response, completed_at=datetime.now(UTC))
        self.assertIsInstance(result, RequestResponseRouted)
        self.assertEqual(len(self.application.request_responses), 1)

    async def test_direct_routed_response_obeys_the_same_publication_barrier(self):
        publication = await self.begin_publication()
        self.correlations.release.set()
        result = await asyncio.wait_for(
            self.runtime.route_response(self.response, completed_at=datetime.now(UTC)),
            1,
        )
        await publication
        self.assertIsInstance(result, RequestResponseRouted)
        self.assertEqual(len(self.application.request_responses), 1)

    async def test_foreign_conversation_does_not_wait_or_gain_authority(self):
        await self.begin_publication()
        foreign = replace(self.response, conversation_ref=ConversationRef("channel", "foreign"))
        with self.assertRaises(RequestStaleError):
            await asyncio.wait_for(self.runtime.authorize_response(foreign), 1)
        self.assertFalse(self.application.request_responses)
        self.assertFalse(self.correlations.release.is_set())

    async def test_slow_other_destination_does_not_block_valid_response(self):
        self.correlations.release.set()
        await self.runtime.deliver_request_once(self.route, self.request)
        self.correlations.held_conversation = ConversationRef("channel", "two")
        self.correlations.release.clear()
        self.correlations.arrived.clear()
        second = replace(
            self.route, route_id="route-two", conversation_ref=self.correlations.held_conversation
        )
        await self.routes.put_projection_route(second)
        publication = self.task(self.runtime.deliver_request_once(second, self.request))
        await asyncio.wait_for(self.correlations.arrived.wait(), 1)
        result = await asyncio.wait_for(
            self.runtime.route_response(self.response, completed_at=datetime.now(UTC)),
            1,
        )
        self.assertIsInstance(result, RequestResponseRouted)
        self.assertFalse(publication.done())
        self.correlations.release.set()
        await publication
        self.assertTrue(
            all(
                row.state is RequestRouteState.RESPONDED
                for row in await self.correlations.list_request_correlations()
            )
        )

    async def test_cancelled_publication_releases_response_without_authority(self):
        publication = await self.begin_publication()
        entered = asyncio.Event()

        async def respond():
            entered.set()
            await self.runtime.authorize_response(self.response)

        response = self.task(respond())
        await entered.wait()
        publication.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await publication
        with self.assertRaises(RequestStaleError):
            await asyncio.wait_for(response, 1)
        self.assertFalse(self.application.request_responses)
        self.assertFalse(await self.correlations.list_request_correlations())

    async def test_cancelled_response_does_not_cancel_publication(self):
        publication = await self.begin_publication()
        entered = asyncio.Event()

        async def respond():
            entered.set()
            await self.runtime.authorize_response(self.response)

        response = self.task(respond())
        await entered.wait()
        response.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await response
        self.assertFalse(publication.done())
        self.correlations.release.set()
        await publication
        await self.runtime.authorize_response(self.response)

    def test_publication_wait_limit_is_explicit_finite_positive_and_default_compatible(self):
        self.assertEqual(GatewayLimits().request_publication_wait_timeout_seconds, 5.0)
        for invalid in (0, -1, True, float("nan"), float("inf"), "5"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                GatewayLimits(request_publication_wait_timeout_seconds=cast(float, invalid))

    async def test_pending_wait_deadline_does_not_exceed_native_request_expiry(self):
        now = datetime.now(UTC)
        self.request = replace(self.request, expires_at=now + timedelta(seconds=10))
        publication = await self.begin_publication()
        self.runtime._publication_wait_timeout_seconds = 5.0
        with patch("imagent.gateway.projection.request_correlation.datetime") as clock:
            clock.now.return_value = now + timedelta(seconds=9.99)
            with self.assertRaises(_RequestPublicationPending):
                await asyncio.wait_for(self.runtime.authorize_response(self.response), 0.5)
        self.assertFalse(publication.done())
        self.assertFalse(self.application.request_responses)
        self.assertFalse(await self.correlations.list_request_correlations())

    async def test_unknown_delivery_does_not_publish_authority(self):
        async def unknown(_message):
            return IdempotencyClaimStatus.IN_FLIGHT

        self.runtime._deliver_request_outbound = unknown
        with self.assertRaises(RuntimeError):
            await self.runtime.deliver_request_once(self.route, self.request)
        with self.assertRaises(RequestStaleError):
            await self.runtime.authorize_response(self.response)
        self.assertFalse(await self.correlations.list_request_correlations())

    async def test_delivery_exception_never_publishes_authority(self):
        async def failed(_message):
            raise RuntimeError("synthetic send failure")

        self.runtime._deliver_request_outbound = failed
        with self.assertRaisesRegex(RuntimeError, "synthetic send failure"):
            await self.runtime.deliver_request_once(self.route, self.request)
        with self.assertRaises(RequestStaleError):
            await self.runtime.authorize_response(self.response)
        self.assertFalse(await self.correlations.list_request_correlations())

    async def test_inherited_callback_context_is_inactive_after_publication(self):
        later = asyncio.Event()
        response = None

        async def delayed_response():
            await later.wait()
            await self.runtime.authorize_response(self.response)

        async def callback(message):
            nonlocal response
            self.delivered.append(message)
            response = self.task(delayed_response())
            return IdempotencyClaimStatus.ACQUIRED

        self.runtime._deliver_request_outbound = callback
        self.correlations.release.set()
        await self.runtime.deliver_request_once(self.route, self.request)
        later.set()
        assert response is not None
        await asyncio.wait_for(response, 1)

    async def test_publication_callback_preserves_preexisting_durable_response_authority(self):
        self.correlations.release.set()
        await self.runtime.deliver_request_once(self.route, self.request)

        async def callback(_message):
            await self.runtime.authorize_response(self.response)
            return IdempotencyClaimStatus.ALREADY_COMPLETED

        self.runtime._deliver_request_outbound = callback
        await asyncio.wait_for(self.runtime.deliver_request_once(self.route, self.request), 1)

    async def test_publication_callback_does_not_wait_for_other_queued_publication(self):
        other = await self.application.open_approval_request(self.thread, turn_id="other-turn")
        other_response = replace(self.response, request_ref=other.request_ref)
        publication = self.task(self.runtime.deliver_request_once(self.route, other))
        await asyncio.wait_for(self.correlations.arrived.wait(), 1)

        async def callback(_message):
            with self.assertRaises(_RequestPublicationPending):
                await self.runtime.authorize_response(other_response)
            self.correlations.release.set()
            return IdempotencyClaimStatus.ACQUIRED

        self.runtime._deliver_request_outbound = callback
        await asyncio.wait_for(self.runtime.deliver_request_once(self.route, self.request), 1)
        await publication

    async def test_reentrant_delivery_response_fails_without_deadlock(self):
        async def callback(message):
            self.delivered.append(message)
            with self.assertRaises(_RequestPublicationPending):
                await self.runtime.authorize_response(self.response)
            return IdempotencyClaimStatus.ACQUIRED

        self.runtime._deliver_request_outbound = callback
        self.correlations.release.set()
        await asyncio.wait_for(self.runtime.deliver_request_once(self.route, self.request), 1)
        await self.runtime.authorize_response(self.response)


if __name__ == "__main__":
    unittest.main()

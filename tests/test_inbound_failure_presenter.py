from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from imagent.adapters import IdempotencyClaimStatus
from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import (
    AcceptedTurn,
    ApplicationInputOutcomeUnknown,
    AttachmentContent,
    Content,
    ConversationRef,
    InboundMessage,
    LocalPath,
    OutboundMessage,
    TextContent,
    ThreadRef,
)
from imagent.diagnostics import InboundFailurePresentationFailureCode
from imagent.gateway import (
    GatewayExtensions,
    GatewayLimits,
    GatewayRepositories,
    ImAgentGateway,
    InboundFailurePhase,
)
from imagent.inbound_admission import ClaimedInbound
from imagent.inbound_failures import (
    InboundFailurePresentation,
    InboundFailurePresentationCapacityError,
    InboundFailurePresentationError,
    InboundFailurePresentationRuntime,
    InboundFailurePresentationTimeout,
    handle_claimed_inbound,
)
from imagent.projection_runtime import InputPostAcceptanceError
from imagent.storage import InMemoryIdempotencyRepository, SQLiteGatewayState
from imagent.testing import FakeChannelAdapter


class _RecordingPresenter:
    def __init__(self, repository: InMemoryIdempotencyRepository | None = None) -> None:
        self.repository = repository
        self.presented: list[InboundFailurePresentation] = []

    async def present_failure(
        self,
        phase,
        *,
        conversation_ref,
        delivery_id,
        reply_to_message_id,
    ):
        facts = InboundFailurePresentation(
            phase=phase,
            conversation_ref=conversation_ref,
            delivery_id=delivery_id,
            reply_to_message_id=reply_to_message_id,
        )
        self.presented.append(facts)
        return OutboundMessage(
            delivery_id=delivery_id,
            conversation_ref=conversation_ref,
            content=(TextContent(f"bounded {phase.value}"),),
            created_at=datetime.now(UTC),
            reply_to=reply_to_message_id,
        )


class _FunctionPresenter:
    def __init__(self, function) -> None:
        self._function = function

    async def present_failure(self, phase, **identity):
        return await self._function(phase, **identity)


class _FailingTransformer:
    async def transform_content(self, message: InboundMessage) -> tuple[Content, ...]:
        del message
        raise RuntimeError("secret transformer failure")


class InboundFailurePresenterTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_presenter_preserves_release_and_raise(self) -> None:
        repository, claimed = await self._claim("absent-pre")

        async def process(_before_application_send) -> None:
            raise RuntimeError("original pre-acceptance failure")

        with self.assertRaisesRegex(RuntimeError, "original pre-acceptance"):
            await handle_claimed_inbound(
                claimed,
                process=process,
                idempotency=repository,
                presentation=None,
                deliver=self._deliveries([]),
            )

        self.assertEqual(await self._reclaim(repository, claimed), IdempotencyClaimStatus.ACQUIRED)

    async def test_configured_pre_acceptance_completes_before_presentation(self) -> None:
        repository, claimed = await self._claim("presented-pre")
        observed_claims: list[IdempotencyClaimStatus] = []

        async def present(phase, **identity):
            observed_claims.append(await self._reclaim(repository, claimed))
            return self._output(phase, **identity)

        runtime = self._runtime(_FunctionPresenter(present))
        deliveries: list[OutboundMessage] = []

        async def process(_before_application_send) -> None:
            raise RuntimeError("must not escape to presenter")

        await handle_claimed_inbound(
            claimed,
            process=process,
            idempotency=repository,
            presentation=runtime,
            deliver=self._deliveries(deliveries),
        )

        self.assertEqual(observed_claims, [IdempotencyClaimStatus.ALREADY_COMPLETED])
        self.assertEqual(
            await self._reclaim(repository, claimed), IdempotencyClaimStatus.ALREADY_COMPLETED
        )
        self._assert_stable_delivery(claimed.message, deliveries[0])

    async def test_unknown_stays_side_effect_started_before_and_after_presentation(self) -> None:
        repository, claimed = await self._claim("presented-unknown")
        observed_claims: list[IdempotencyClaimStatus] = []

        async def present(phase, **identity):
            observed_claims.append(await self._reclaim(repository, claimed))
            return self._output(phase, **identity)

        async def process(before_application_send) -> None:
            await before_application_send()
            cause = RuntimeError("secret unknown native outcome")
            raise ApplicationInputOutcomeUnknown("unknown", cause)

        deliveries: list[OutboundMessage] = []
        await handle_claimed_inbound(
            claimed,
            process=process,
            idempotency=repository,
            presentation=self._runtime(_FunctionPresenter(present)),
            deliver=self._deliveries(deliveries),
        )

        self.assertEqual(observed_claims, [IdempotencyClaimStatus.IN_FLIGHT])
        self.assertEqual(await self._reclaim(repository, claimed), IdempotencyClaimStatus.IN_FLIGHT)
        self.assertEqual(deliveries[0].content, (TextContent("bounded outcome_unknown"),))
        self._assert_stable_delivery(claimed.message, deliveries[0])

    async def test_untyped_failure_after_dispatch_fence_is_outcome_unknown(self) -> None:
        repository, claimed = await self._claim("untyped-after-dispatch")
        presenter = _RecordingPresenter()

        async def process(before_application_send) -> None:
            await before_application_send()
            raise RuntimeError("adapter failed after dispatch fence")

        await handle_claimed_inbound(
            claimed,
            process=process,
            idempotency=repository,
            presentation=self._runtime(presenter),
            deliver=self._deliveries([]),
        )

        self.assertEqual(
            [facts.phase for facts in presenter.presented],
            [InboundFailurePhase.OUTCOME_UNKNOWN],
        )
        self.assertEqual(await self._reclaim(repository, claimed), IdempotencyClaimStatus.IN_FLIGHT)

    async def test_post_acceptance_completes_before_presentation(self) -> None:
        repository, claimed = await self._claim("presented-post")
        observed_claims: list[IdempotencyClaimStatus] = []

        async def present(phase, **identity):
            observed_claims.append(await self._reclaim(repository, claimed))
            return self._output(phase, **identity)

        async def process(_before_application_send) -> None:
            raise InputPostAcceptanceError(
                AcceptedTurn(
                    thread_ref=ThreadRef("app", "thread"),
                    turn_id="turn",
                    client_message_id="client",
                ),
                RuntimeError("secret correlation failure"),
            )

        deliveries: list[OutboundMessage] = []
        await handle_claimed_inbound(
            claimed,
            process=process,
            idempotency=repository,
            presentation=self._runtime(_FunctionPresenter(present)),
            deliver=self._deliveries(deliveries),
        )

        self.assertEqual(observed_claims, [IdempotencyClaimStatus.ALREADY_COMPLETED])
        self.assertEqual(
            await self._reclaim(repository, claimed), IdempotencyClaimStatus.ALREADY_COMPLETED
        )
        self._assert_stable_delivery(claimed.message, deliveries[0])

    async def test_presenter_and_channel_failure_never_reopen_claim(self) -> None:
        for failure_kind in ("presenter", "channel"):
            for phase in InboundFailurePhase:
                with self.subTest(failure_kind=failure_kind, phase=phase):
                    repository, claimed = await self._claim(f"{failure_kind}-{phase.value}")

                    async def present(current_phase, **identity):
                        if failure_kind == "presenter":
                            raise RuntimeError("secret presenter failure")
                        return self._output(current_phase, **identity)

                    async def deliver(_message: OutboundMessage) -> object:
                        raise RuntimeError("secret channel failure")

                    with self.assertRaisesRegex(RuntimeError, f"secret {failure_kind}"):
                        await handle_claimed_inbound(
                            claimed,
                            process=self._phase_process(repository, claimed, phase),
                            idempotency=repository,
                            presentation=self._runtime(_FunctionPresenter(present)),
                            deliver=(
                                deliver if failure_kind == "channel" else self._deliveries([])
                            ),
                        )
                    expected = (
                        IdempotencyClaimStatus.IN_FLIGHT
                        if phase is InboundFailurePhase.OUTCOME_UNKNOWN
                        else IdempotencyClaimStatus.ALREADY_COMPLETED
                    )
                    self.assertEqual(await self._reclaim(repository, claimed), expected)

    async def test_original_cancellation_releases_without_presentation(self) -> None:
        repository, claimed = await self._claim("original-cancel")
        presenter = _RecordingPresenter()

        async def process(_before_application_send) -> None:
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await handle_claimed_inbound(
                claimed,
                process=process,
                idempotency=repository,
                presentation=self._runtime(presenter),
                deliver=self._deliveries([]),
            )

        self.assertEqual(presenter.presented, [])
        self.assertEqual(await self._reclaim(repository, claimed), IdempotencyClaimStatus.ACQUIRED)

    async def test_cancellation_after_dispatch_fence_keeps_claim_sticky(self) -> None:
        repository, claimed = await self._claim("cancel-after-dispatch")
        presenter = _RecordingPresenter()

        async def process(before_application_send) -> None:
            await before_application_send()
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await handle_claimed_inbound(
                claimed,
                process=process,
                idempotency=repository,
                presentation=self._runtime(presenter),
                deliver=self._deliveries([]),
            )

        self.assertEqual(presenter.presented, [])
        self.assertEqual(await self._reclaim(repository, claimed), IdempotencyClaimStatus.IN_FLIGHT)

    async def test_presenter_cancellation_keeps_pre_acceptance_terminal(self) -> None:
        repository, claimed = await self._claim("presenter-cancel")
        entered = asyncio.Event()

        async def present(_phase, **_identity):
            entered.set()
            await asyncio.Event().wait()

        async def process(_before_application_send) -> None:
            raise RuntimeError("pre")

        task = asyncio.create_task(
            handle_claimed_inbound(
                claimed,
                process=process,
                idempotency=repository,
                presentation=self._runtime(_FunctionPresenter(present)),
                deliver=self._deliveries([]),
            )
        )
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(
            await self._reclaim(repository, claimed), IdempotencyClaimStatus.ALREADY_COMPLETED
        )

    async def test_terminal_pre_acceptance_claim_survives_sqlite_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.sqlite3"
            first = SQLiteGatewayState(path)
            message = self._message("sqlite-terminal")
            scope = "inbound:fake-channel"
            key = "conversation-1:sqlite-terminal"
            owner = "owner-sqlite-terminal"
            self.assertEqual(
                await first.claim(scope, key, owner_token=owner),
                IdempotencyClaimStatus.ACQUIRED,
            )
            claimed = ClaimedInbound(message, scope, key, owner)

            await handle_claimed_inbound(
                claimed,
                process=self._raising(RuntimeError("pre")),
                idempotency=first,
                presentation=self._runtime(_RecordingPresenter()),
                deliver=self._deliveries([]),
            )
            await first.close()

            reopened = SQLiteGatewayState(path)
            try:
                self.assertEqual(
                    await reopened.claim(scope, key, owner_token="replacement"),
                    IdempotencyClaimStatus.ALREADY_COMPLETED,
                )
            finally:
                await reopened.close()

    async def test_unknown_and_post_acceptance_claims_survive_sqlite_restart(self) -> None:
        for phase, expected in (
            (InboundFailurePhase.OUTCOME_UNKNOWN, IdempotencyClaimStatus.IN_FLIGHT),
            (InboundFailurePhase.POST_ACCEPTANCE, IdempotencyClaimStatus.ALREADY_COMPLETED),
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "gateway.sqlite3"
                first = SQLiteGatewayState(path)
                message = self._message(f"sqlite-{phase.value}")
                scope = "inbound:fake-channel"
                key = f"conversation-1:{message.message_id}"
                owner = f"owner-{phase.value}"
                self.assertEqual(
                    await first.claim(scope, key, owner_token=owner),
                    IdempotencyClaimStatus.ACQUIRED,
                )
                claimed = ClaimedInbound(message, scope, key, owner)

                async def process(before_application_send) -> None:
                    await before_application_send()
                    if phase is InboundFailurePhase.OUTCOME_UNKNOWN:
                        raise ApplicationInputOutcomeUnknown(
                            "unknown",
                            RuntimeError("unknown"),
                        )
                    raise InputPostAcceptanceError(
                        AcceptedTurn(ThreadRef("app", "thread"), "turn", "client"),
                        RuntimeError("post"),
                    )

                await handle_claimed_inbound(
                    claimed,
                    process=process,
                    idempotency=first,
                    presentation=self._runtime(_RecordingPresenter()),
                    deliver=self._deliveries([]),
                )
                await first.close()

                reopened = SQLiteGatewayState(path)
                try:
                    self.assertEqual(
                        await reopened.claim(scope, key, owner_token="replacement"),
                        expected,
                    )
                finally:
                    await reopened.close()

    async def test_invalid_presenter_identity_is_terminal_before_delivery(self) -> None:
        repository, claimed = await self._claim("invalid-identity")

        async def present(phase, **identity):
            output = self._output(phase, **identity)
            return OutboundMessage(
                delivery_id="changed",
                conversation_ref=output.conversation_ref,
                content=output.content,
                created_at=output.created_at,
                reply_to=output.reply_to,
            )

        deliveries: list[OutboundMessage] = []
        with self.assertRaisesRegex(InboundFailurePresentationError, "delivery identity"):
            await handle_claimed_inbound(
                claimed,
                process=self._raising(RuntimeError("pre")),
                idempotency=repository,
                presentation=self._runtime(_FunctionPresenter(present)),
                deliver=self._deliveries(deliveries),
            )

        self.assertEqual(deliveries, [])
        self.assertEqual(
            await self._reclaim(repository, claimed), IdempotencyClaimStatus.ALREADY_COMPLETED
        )

    async def test_presenter_output_rejects_authority_metadata_and_unbounded_text(self) -> None:
        cases = (
            (
                (AttachmentContent("artifact", "text/plain", LocalPath("/tmp/secret")),),
                {},
                GatewayLimits(),
                "text-only",
            ),
            (
                (TextContent("12345"),),
                {},
                GatewayLimits(inbound_failure_present_max_text_characters=4),
                "text limit",
            ),
            (
                (TextContent("one"), TextContent("two")),
                {},
                GatewayLimits(inbound_failure_present_max_items=1),
                "unbounded content",
            ),
            ((), {}, GatewayLimits(), "unbounded content"),
            (
                (TextContent("ok"),),
                {"consumer": "arbitrary"},
                GatewayLimits(),
                "metadata",
            ),
        )

        for index, (content, metadata, limits, expected_error) in enumerate(cases):
            with self.subTest(expected_error=expected_error):
                repository, claimed = await self._claim(f"invalid-output-{index}")

                async def present(phase, **identity):
                    output = self._output(phase, **identity)
                    return OutboundMessage(
                        delivery_id=output.delivery_id,
                        conversation_ref=output.conversation_ref,
                        content=content,
                        created_at=output.created_at,
                        reply_to=output.reply_to,
                        metadata=metadata,
                    )

                deliveries: list[OutboundMessage] = []
                with self.assertRaisesRegex(InboundFailurePresentationError, expected_error):
                    await handle_claimed_inbound(
                        claimed,
                        process=self._raising(RuntimeError("pre")),
                        idempotency=repository,
                        presentation=self._runtime(
                            _FunctionPresenter(present),
                            limits=limits,
                        ),
                        deliver=self._deliveries(deliveries),
                    )
                self.assertEqual(deliveries, [])
                self.assertEqual(
                    await self._reclaim(repository, claimed),
                    IdempotencyClaimStatus.ALREADY_COMPLETED,
                )

    async def test_i1_failure_is_presented_as_pre_acceptance_without_error_leak(self) -> None:
        channel = FakeChannelAdapter("fake-channel")
        presenter = _RecordingPresenter()
        idempotency = InMemoryIdempotencyRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                idempotency=idempotency,
            ),
            extensions=GatewayExtensions(
                inbound_content_transformer=_FailingTransformer(),
                inbound_failure_presenter=presenter,
            ),
        )
        message = self._message("i1-failure")

        await gateway.start()
        try:
            await channel.emit_message(message)
            await channel.emit_message(message)
            snapshot = gateway.diagnostics_snapshot()
        finally:
            await gateway.stop()

        self.assertEqual(
            [facts.phase for facts in presenter.presented], [InboundFailurePhase.PRE_ACCEPTANCE]
        )
        self.assertEqual(len(channel.sent), 1)
        self.assertNotIn("secret transformer failure", json.dumps(asdict(snapshot), default=str))
        self.assertEqual(snapshot.schema_version, 6)
        self.assertIsNotNone(snapshot.gateway.inbound_failure_presenter)
        assert snapshot.gateway.inbound_failure_presenter is not None
        self.assertEqual(snapshot.gateway.inbound_failure_presenter.invocation_count, 1)
        self.assertEqual(snapshot.gateway.inbound_failure_presenter.success_count, 1)
        self.assertEqual(snapshot.gateway.inbound_failure_presenter.failure_count, 0)
        self.assertEqual(
            await self._reclaim_for(message, idempotency), IdempotencyClaimStatus.ALREADY_COMPLETED
        )

    async def test_timeout_capacity_and_shutdown_are_bounded_and_redacted(self) -> None:
        cancellation_count = 0

        async def present(_phase, **_identity):
            nonlocal cancellation_count
            while True:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancellation_count += 1
                    if cancellation_count >= 3:
                        raise

        runtime = self._runtime(
            _FunctionPresenter(present),
            limits=GatewayLimits(
                inbound_failure_present_timeout_seconds=0.01,
                inbound_failure_present_max_concurrency=1,
            ),
        )
        facts = self._presentation("bounded-runtime")

        with self.assertRaises(InboundFailurePresentationTimeout):
            await runtime.present(facts)
        with self.assertRaises(InboundFailurePresentationCapacityError):
            await runtime.present(facts)
        before_close = runtime.diagnostic_facts()
        await runtime.close()
        await asyncio.sleep(0)

        self.assertEqual(cancellation_count, 3)
        self.assertEqual(before_close.invocation_count, 2)
        self.assertEqual(before_close.success_count, 0)
        self.assertEqual(before_close.failure_count, 2)
        self.assertEqual(before_close.timeout_count, 1)
        self.assertEqual(before_close.cancellation_overrun_count, 1)
        self.assertEqual(before_close.capacity_rejection_count, 1)
        self.assertEqual(
            before_close.last_failure_code,
            InboundFailurePresentationFailureCode.CAPACITY_EXHAUSTED,
        )

    async def test_external_cancellation_overrun_is_bounded_and_diagnosed(self) -> None:
        entered = asyncio.Event()
        cancellation_count = 0

        async def present(_phase, **_identity):
            nonlocal cancellation_count
            entered.set()
            while True:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancellation_count += 1
                    if cancellation_count >= 2:
                        raise

        runtime = self._runtime(
            _FunctionPresenter(present),
            limits=GatewayLimits(
                inbound_failure_present_timeout_seconds=0.01,
                inbound_failure_present_max_concurrency=1,
            ),
        )
        task = asyncio.create_task(runtime.present(self._presentation("external-cancel")))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        facts = runtime.diagnostic_facts()
        self.assertEqual(facts.cancellation_count, 1)
        self.assertEqual(facts.cancellation_overrun_count, 1)
        self.assertEqual(
            facts.last_failure_code,
            InboundFailurePresentationFailureCode.CANCELLED,
        )
        await runtime.close()
        await asyncio.sleep(0)
        self.assertEqual(cancellation_count, 2)

    @staticmethod
    def _runtime(presenter, *, limits: GatewayLimits = GatewayLimits()):
        return InboundFailurePresentationRuntime(
            presenter,
            timeout_seconds=limits.inbound_failure_present_timeout_seconds,
            max_items=limits.inbound_failure_present_max_items,
            max_text_characters=limits.inbound_failure_present_max_text_characters,
            max_concurrency=limits.inbound_failure_present_max_concurrency,
        )

    async def _claim(self, message_id: str):
        repository = InMemoryIdempotencyRepository()
        message = self._message(message_id)
        scope = "inbound:fake-channel"
        key = f"conversation-1:{message_id}"
        owner = f"owner-{message_id}"
        self.assertEqual(
            await repository.claim(scope, key, owner_token=owner),
            IdempotencyClaimStatus.ACQUIRED,
        )
        return repository, ClaimedInbound(message, scope, key, owner)

    @staticmethod
    async def _reclaim(repository, claimed):
        return await repository.claim(
            claimed.scope,
            claimed.key,
            owner_token="replacement",
        )

    @staticmethod
    async def _reclaim_for(message, repository):
        return await repository.claim(
            "inbound:fake-channel",
            f"{message.conversation_ref.native_conversation_id}:{message.message_id}",
            owner_token="replacement",
        )

    @staticmethod
    def _deliveries(deliveries: list[OutboundMessage]):
        async def deliver(message: OutboundMessage) -> object:
            deliveries.append(message)
            return object()

        return deliver

    @staticmethod
    def _output(phase, *, conversation_ref, delivery_id, reply_to_message_id):
        return OutboundMessage(
            delivery_id=delivery_id,
            conversation_ref=conversation_ref,
            content=(TextContent(f"bounded {phase.value}"),),
            created_at=datetime.now(UTC),
            reply_to=reply_to_message_id,
        )

    @staticmethod
    def _raising(error: BaseException):
        async def process(_before_application_send) -> None:
            raise error

        return process

    def _phase_process(self, repository, claimed, phase):
        if phase is InboundFailurePhase.PRE_ACCEPTANCE:
            return self._raising(RuntimeError("pre"))
        if phase is InboundFailurePhase.OUTCOME_UNKNOWN:

            async def unknown(before_application_send) -> None:
                await before_application_send()
                raise ApplicationInputOutcomeUnknown("unknown", RuntimeError("unknown"))

            return unknown
        return self._raising(
            InputPostAcceptanceError(
                AcceptedTurn(ThreadRef("app", "thread"), "turn", "client"),
                RuntimeError("post"),
            )
        )

    @staticmethod
    def _assert_stable_delivery(message: InboundMessage, output: OutboundMessage) -> None:
        assert output.conversation_ref == message.conversation_ref
        assert output.reply_to == message.message_id
        assert output.delivery_id == (
            f"imagent:gateway:fake-channel:conversation-1:{message.message_id}:inbound-failure"
        )

    @staticmethod
    def _presentation(message_id: str) -> InboundFailurePresentation:
        return InboundFailurePresentation(
            phase=InboundFailurePhase.PRE_ACCEPTANCE,
            conversation_ref=ConversationRef("fake-channel", "conversation-1"),
            delivery_id=f"delivery-{message_id}",
            reply_to_message_id=message_id,
        )

    @staticmethod
    def _message(message_id: str) -> InboundMessage:
        return InboundMessage(
            message_id=message_id,
            conversation_ref=ConversationRef("fake-channel", "conversation-1"),
            sender="user-1",
            content=(TextContent("original"),),
            created_at=datetime.now(UTC),
        )

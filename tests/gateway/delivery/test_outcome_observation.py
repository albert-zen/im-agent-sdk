from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from datetime import UTC, datetime

from imagent.applications.contract import AgentMessage, ThreadRef
from imagent.contracts import DeliveryPrincipal
from imagent.gateway import GatewayExtensions, GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.delivery import (
    DeliveryCoordinator,
    DeliveryCoordinatorConfig,
    DeliveryOutcome,
    DeliveryOutcomeContext,
    DeliveryOutcomeErrorCode,
    DeliveryOutcomeObserver,
    ScopedDeliveryAuthorizer,
)
from imagent.gateway.delivery import outcome_observation as outcome_observation_owner
from imagent.gateway.delivery.outcome_observation import (
    DeliveryOutcomeObserverRuntime,
)
from imagent.gateway.delivery.proactive import ConversationDeliveryTarget, DeliveryIntent
from imagent.gateway.diagnostics import DeliveryOutcomeObserverFailureCode
from imagent.gateway.persistence import InMemoryIdempotencyRepository, ThreadProjectionRoute
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryDeliverySubmissionRepository,
    InMemoryProjectionRouteRepository,
)
from imagent.gateway.projection import derive_projection_delivery_id
from imagent.gateway.projection.checkpoints import _ProjectionCheckpointAuthority
from imagent.gateway.routing.projection_routes import derive_projection_route_id
from imagent.interaction.channels import (
    ChannelCapabilities,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySupportLevel,
)
from imagent.interaction.media import AttachmentContent, LocalPath
from imagent.interaction.messages import (
    ConversationRef,
    MessageRole,
    OutboundMessage,
    TextContent,
)
from imagent.projections import (
    ProjectedAgentMessage,
    deliver_projected_message,
)
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class DeliveryOutcomeObservationFacadeTests(unittest.TestCase):
    def test_gateway_facade_uses_exact_owner_objects_and_old_module_is_absent(self) -> None:
        self.assertIs(DeliveryOutcome, outcome_observation_owner.DeliveryOutcome)
        self.assertIs(DeliveryOutcomeContext, outcome_observation_owner.DeliveryOutcomeContext)
        self.assertIs(DeliveryOutcomeErrorCode, outcome_observation_owner.DeliveryOutcomeErrorCode)
        self.assertIs(DeliveryOutcomeObserver, outcome_observation_owner.DeliveryOutcomeObserver)
        self.assertNotIn("imagent.delivery_outcomes", sys.modules)
        self.assertIsNone(importlib.util.find_spec("imagent.delivery_outcomes"))


class _Observer:
    def __init__(self, *, fail: bool = False, block: bool = False) -> None:
        self.fail = fail
        self.block = block
        self.calls: list[tuple[DeliveryOutcomeContext, DeliveryOutcome]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def observe_delivery_outcome(
        self,
        context: DeliveryOutcomeContext,
        outcome: DeliveryOutcome,
    ) -> None:
        self.calls.append((context, outcome))
        self.entered.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("observer secret")


class _CancellationOverrunObserver:
    def __init__(self) -> None:
        self.calls: list[tuple[DeliveryOutcomeContext, DeliveryOutcome]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def observe_delivery_outcome(
        self,
        context: DeliveryOutcomeContext,
        outcome: DeliveryOutcome,
    ) -> None:
        self.calls.append((context, outcome))
        self.entered.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                continue


class _ReceiptChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[DeliveryReceiptStatus] = []
        self.delay = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, message):
        self.sent.append(message)
        self.entered.set()
        if self.delay:
            await self.release.wait()
        status = (
            self.statuses.pop(0) if self.statuses else DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM
        )
        return DeliveryReceipt(
            status=status,
            native_message_id=(
                "native-secret" if status is DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM else None
            ),
            detail="native diagnostic secret",
            retry_after_seconds=(0 if status is DeliveryReceiptStatus.RETRYABLE_FAILURE else None),
        )


class _ExecutionFailureCoordinator(DeliveryCoordinator):
    async def deliver(self, channel, message):
        del channel, message
        raise RuntimeError("coordinator secret")


class DeliveryOutcomeObserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.conversation = ConversationRef("fake-channel", "conversation")
        self.authorizer = ScopedDeliveryAuthorizer()
        self.token = await self.authorizer.issue(
            DeliveryPrincipal(
                principal_id="caller",
                allowed_conversations=(self.conversation,),
            )
        )

    def _gateway(
        self,
        observer: DeliveryOutcomeObserver | None,
        *,
        channel: FakeChannelAdapter | None = None,
        coordinator: DeliveryCoordinator | None = None,
        limits: GatewayLimits | None = None,
        submissions: InMemoryDeliverySubmissionRepository | None = None,
    ) -> ImAgentGateway:
        return ImAgentGateway(
            channels=[channel or _ReceiptChannel()],
            applications=[FakeAgentApplicationAdapter()],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                delivery_submissions=submissions,
            ),
            limits=limits or GatewayLimits(),
            extensions=GatewayExtensions(delivery_outcome_observer=observer),
            delivery_authorizer=self.authorizer,
            delivery_coordinator=coordinator,
        )

    def _intent(self, delivery_id: str, text: str = "hello") -> DeliveryIntent:
        return DeliveryIntent(
            delivery_id=delivery_id,
            target=ConversationDeliveryTarget(self.conversation),
            content=(TextContent(text),),
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
            metadata={"phase": "final"},
        )

    async def test_receipt_is_observed_once_after_coordinator_cleanup(self) -> None:
        observer = _Observer()
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(max_pending=1, max_pending_per_destination=1)
        )
        channel = _ReceiptChannel()
        gateway = self._gateway(observer, channel=channel, coordinator=coordinator)
        await gateway.start()
        try:
            result = await gateway.deliver_proactively(
                self._intent("accepted"),
                credential=self.token,
            )
            await observer.entered.wait()
        finally:
            await gateway.stop()

        self.assertEqual(result.state.value, "accepted")
        self.assertEqual(len(observer.calls), 1)
        context, outcome = observer.calls[0]
        self.assertTrue(context.message.delivery_id.startswith("imagent:delivery-submission:"))
        self.assertNotEqual(context.message.delivery_id, channel.sent[0].delivery_id)
        self.assertIsNone(getattr(context.message.metadata, "__setitem__", None))
        assert outcome.receipt is not None
        self.assertEqual(outcome.receipt.status, DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)
        self.assertEqual(outcome.receipt.native_message_id, "native-secret")
        self.assertIsNone(outcome.receipt.detail)
        self.assertEqual(coordinator._pending, 0)

    async def test_internal_segment_retry_and_multi_segment_are_one_notification(self) -> None:
        observer = _Observer()
        channel = _ReceiptChannel()
        channel.statuses = [
            DeliveryReceiptStatus.RETRYABLE_FAILURE,
            DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
        ]
        channel._capabilities = ChannelCapabilities(
            plain_text=DeliverySupportLevel.NATIVE,
            max_text_length=3,
        )
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(
                max_attempts=2,
                retry_initial_seconds=0,
                retry_max_seconds=0,
            )
        )
        gateway = self._gateway(observer, channel=channel, coordinator=coordinator)
        await gateway.start()
        try:
            await gateway.deliver_proactively(
                self._intent("retry-segments", "abcdef"),
                credential=self.token,
            )
            await _wait_until(lambda: len(observer.calls) == 1)
        finally:
            await gateway.stop()

        self.assertEqual(len(channel.sent), 3)
        self.assertEqual(len(observer.calls), 1)
        receipt = observer.calls[0][1].receipt
        assert receipt is not None
        self.assertEqual(len(receipt.segments), 2)

    async def test_later_resumed_retryable_destination_is_a_new_attempt(self) -> None:
        observer = _Observer()
        channel = _ReceiptChannel()
        channel.statuses = [
            DeliveryReceiptStatus.RETRYABLE_FAILURE,
            DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
        ]
        gateway = self._gateway(observer, channel=channel)
        intent = self._intent("resumed-retry")
        await gateway.start()
        try:
            first = await gateway.deliver_proactively(intent, credential=self.token)
            second = await gateway.deliver_proactively(intent, credential=self.token)
            await _wait_until(lambda: len(observer.calls) == 2)
        finally:
            await gateway.stop()

        self.assertEqual(first.state.value, "retryable")
        self.assertEqual(second.state.value, "accepted")
        self.assertEqual(len(channel.sent), 2)
        self.assertEqual(len(observer.calls), 2)

    async def test_rejected_retryable_and_unknown_receipts_are_typed(self) -> None:
        observer = _Observer()
        channel = _ReceiptChannel()
        gateway = self._gateway(observer, channel=channel)
        await gateway.start()
        try:
            for index, status in enumerate(
                (
                    DeliveryReceiptStatus.REJECTED_BY_PLATFORM,
                    DeliveryReceiptStatus.RETRYABLE_FAILURE,
                    DeliveryReceiptStatus.UNKNOWN,
                )
            ):
                channel.statuses.append(status)
                await gateway.deliver_proactively(
                    self._intent(f"status-{index}"),
                    credential=self.token,
                )
            await _wait_until(lambda: len(observer.calls) == 3)
        finally:
            await gateway.stop()

        self.assertEqual(
            tuple(call[1].receipt.status for call in observer.calls if call[1].receipt),
            (
                DeliveryReceiptStatus.REJECTED_BY_PLATFORM,
                DeliveryReceiptStatus.RETRYABLE_FAILURE,
                DeliveryReceiptStatus.UNKNOWN,
            ),
        )

    async def test_partial_multi_segment_attempt_is_observed_once(self) -> None:
        observer = _Observer()
        channel = _ReceiptChannel()
        channel._capabilities = ChannelCapabilities(
            plain_text=DeliverySupportLevel.NATIVE,
            max_text_length=3,
        )
        channel.statuses = [
            DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            DeliveryReceiptStatus.REJECTED_BY_PLATFORM,
        ]
        gateway = self._gateway(observer, channel=channel)
        await gateway.start()
        try:
            result = await gateway.deliver_proactively(
                self._intent("partial", "abcdef"),
                credential=self.token,
            )
            await observer.entered.wait()
        finally:
            await gateway.stop()

        self.assertEqual(result.state.value, "partial")
        self.assertEqual(len(observer.calls), 1)
        receipt = observer.calls[0][1].receipt
        assert receipt is not None
        self.assertEqual(len(receipt.segments), 2)

    async def test_execution_exception_and_cancellation_use_bounded_errors(self) -> None:
        execution_observer = _Observer()
        execution_gateway = self._gateway(
            execution_observer,
            coordinator=_ExecutionFailureCoordinator(),
        )
        await execution_gateway.start()
        try:
            result = await execution_gateway.deliver_proactively(
                self._intent("execution-error"),
                credential=self.token,
            )
            await execution_observer.entered.wait()
        finally:
            await execution_gateway.stop()
        self.assertEqual(result.state.value, "unknown")
        self.assertEqual(
            execution_observer.calls[0][1].error,
            DeliveryOutcomeErrorCode.EXECUTION_FAILED,
        )

        cancellation_observer = _Observer()
        channel = _ReceiptChannel()
        channel.delay = True
        cancellation_gateway = self._gateway(cancellation_observer, channel=channel)
        await cancellation_gateway.start()
        delivery = asyncio.create_task(
            cancellation_gateway.deliver_proactively(
                self._intent("cancelled"),
                credential=self.token,
            )
        )
        await channel.entered.wait()
        delivery.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await delivery
            await cancellation_observer.entered.wait()
        finally:
            channel.release.set()
            await cancellation_gateway.stop()
        self.assertEqual(
            cancellation_observer.calls[0][1].error,
            DeliveryOutcomeErrorCode.CANCELLED,
        )

    async def test_observer_failure_and_capacity_never_change_delivery(self) -> None:
        observer = _Observer(fail=True, block=True)
        gateway = self._gateway(
            observer,
            limits=GatewayLimits(delivery_outcome_observer_max_concurrency=1),
        )
        await gateway.start()
        try:
            first = await gateway.deliver_proactively(
                self._intent("observer-one"),
                credential=self.token,
            )
            await observer.entered.wait()
            second = await gateway.deliver_proactively(
                self._intent("observer-two"),
                credential=self.token,
            )
            observer.release.set()

            def two_failures() -> bool:
                current = gateway.diagnostics_snapshot().gateway.delivery_outcome_observer
                return current is not None and current.failure_count == 2

            await _wait_until(two_failures)
        finally:
            observer.release.set()
            await gateway.stop()

        self.assertEqual(first.state.value, "accepted")
        self.assertEqual(second.state.value, "accepted")
        facts = gateway.diagnostics_snapshot().gateway.delivery_outcome_observer
        assert facts is not None
        self.assertEqual(facts.notification_count, 2)
        self.assertEqual(facts.capacity_rejection_count, 1)
        self.assertEqual(
            facts.last_failure_code, DeliveryOutcomeObserverFailureCode.OBSERVER_FAILED
        )
        self.assertNotIn("observer secret", repr(facts))

    async def test_observer_timeout_is_detached_bounded_and_redacted(self) -> None:
        observer = _Observer(block=True)
        gateway = self._gateway(
            observer,
            limits=GatewayLimits(delivery_outcome_observer_timeout_seconds=0.001),
        )
        await gateway.start()
        try:
            result = await gateway.deliver_proactively(
                self._intent("observer-timeout"),
                credential=self.token,
            )

            def timed_out() -> bool:
                current = gateway.diagnostics_snapshot().gateway.delivery_outcome_observer
                return current is not None and current.timeout_count == 1

            await _wait_until(timed_out)
        finally:
            observer.release.set()
            await gateway.stop()

        self.assertEqual(result.state.value, "accepted")
        facts = gateway.diagnostics_snapshot().gateway.delivery_outcome_observer
        assert facts is not None
        self.assertEqual(facts.last_failure_code, DeliveryOutcomeObserverFailureCode.TIMED_OUT)
        self.assertNotIn("observer-timeout", repr(facts))

    async def test_cancellation_overrun_is_bounded_retains_capacity_and_not_result(self) -> None:
        observer = _CancellationOverrunObserver()
        gateway = self._gateway(
            observer,
            limits=GatewayLimits(
                delivery_outcome_observer_timeout_seconds=0.005,
                delivery_outcome_observer_max_concurrency=1,
            ),
        )
        await gateway.start()
        try:
            first = await gateway.deliver_proactively(
                self._intent("observer-overrun-one"),
                credential=self.token,
            )
            await observer.entered.wait()

            def overran() -> bool:
                current = gateway.diagnostics_snapshot().gateway.delivery_outcome_observer
                return current is not None and current.cancellation_overrun_count == 1

            await _wait_until(overran)
            second = await gateway.deliver_proactively(
                self._intent("observer-overrun-two"),
                credential=self.token,
            )
            await asyncio.wait_for(gateway.stop(), timeout=0.2)
        finally:
            observer.release.set()
            await asyncio.sleep(0)

        self.assertEqual(first.state.value, "accepted")
        self.assertEqual(second.state.value, "accepted")
        facts = gateway.diagnostics_snapshot().gateway.delivery_outcome_observer
        assert facts is not None
        self.assertEqual(facts.notification_count, 2)
        self.assertEqual(facts.timeout_count, 1)
        self.assertEqual(facts.cancellation_overrun_count, 1)
        self.assertEqual(facts.capacity_rejection_count, 1)
        self.assertEqual(
            facts.last_failure_code,
            DeliveryOutcomeObserverFailureCode.CAPACITY_EXHAUSTED,
        )
        self.assertNotIn("observer-overrun", repr(facts))

    async def test_attachment_and_receipt_string_facts_share_a_finite_budget(self) -> None:
        observer = _Observer()
        runtime = DeliveryOutcomeObserverRuntime(
            observer,
            timeout_seconds=1,
            max_items=4,
            max_text_characters=32,
            max_concurrency=1,
        )
        runtime.start()
        try:
            runtime.notify(
                OutboundMessage(
                    delivery_id="d",
                    conversation_ref=ConversationRef("c", "v"),
                    content=(
                        AttachmentContent(
                            attachment_id="a",
                            media_type="m",
                            source=LocalPath("/" + "x" * 64),
                        ),
                    ),
                    created_at=datetime(2026, 8, 3, tzinfo=UTC),
                ),
                receipt=DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM),
            )
            runtime.notify(
                OutboundMessage(
                    delivery_id="d",
                    conversation_ref=ConversationRef("c", "v"),
                    content=(TextContent("x"),),
                    created_at=datetime(2026, 8, 3, tzinfo=UTC),
                ),
                receipt=DeliveryReceipt(
                    status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
                    native_message_id="n" * 64,
                ),
            )
            runtime.notify(
                OutboundMessage(
                    delivery_id="d",
                    conversation_ref=ConversationRef("c", "v"),
                    content=(TextContent("x" * 20),),
                    created_at=datetime(2026, 8, 3, tzinfo=UTC),
                ),
                receipt=DeliveryReceipt(
                    status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
                    native_message_id="n" * 20,
                ),
            )
        finally:
            await runtime.close()

        self.assertEqual(observer.calls, [])
        facts = runtime.diagnostic_facts()
        self.assertEqual(facts.notification_count, 3)
        self.assertEqual(facts.failure_count, 3)
        self.assertEqual(
            facts.last_failure_code,
            DeliveryOutcomeObserverFailureCode.INVALID_FACTS,
        )

    async def test_message_and_attachment_metadata_must_be_bounded_scalars(self) -> None:
        observer = _Observer()
        runtime = DeliveryOutcomeObserverRuntime(
            observer,
            timeout_seconds=1,
            max_items=4,
            max_text_characters=1_024,
            max_concurrency=1,
        )
        runtime.start()
        try:
            runtime.notify(
                OutboundMessage(
                    delivery_id="metadata-count",
                    conversation_ref=ConversationRef("c", "v"),
                    content=(TextContent("x"),),
                    created_at=datetime(2026, 8, 3, tzinfo=UTC),
                    metadata={f"key-{index}": index for index in range(17)},
                ),
                receipt=DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM),
            )
            runtime.notify(
                OutboundMessage(
                    delivery_id="metadata-type",
                    conversation_ref=ConversationRef("c", "v"),
                    content=(
                        AttachmentContent(
                            attachment_id="a",
                            media_type="text/plain",
                            source=LocalPath("/tmp/a"),
                            metadata={"nested": ["not", "scalar"]},
                        ),
                    ),
                    created_at=datetime(2026, 8, 3, tzinfo=UTC),
                ),
                receipt=DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM),
            )
        finally:
            await runtime.close()

        self.assertEqual(observer.calls, [])
        facts = runtime.diagnostic_facts()
        self.assertEqual(facts.notification_count, 2)
        self.assertEqual(facts.failure_count, 2)
        self.assertEqual(
            facts.last_failure_code,
            DeliveryOutcomeObserverFailureCode.INVALID_FACTS,
        )

    async def test_durable_replay_and_preflight_rejection_do_not_fabricate_attempts(self) -> None:
        observer = _Observer()
        submissions = InMemoryDeliverySubmissionRepository()
        channel = _ReceiptChannel()
        gateway = self._gateway(observer, channel=channel, submissions=submissions)
        await gateway.start()
        try:
            intent = self._intent("replayed")
            await gateway.deliver_proactively(intent, credential=self.token)
            await _wait_until(lambda: len(observer.calls) == 1)
            await gateway.deliver_proactively(intent, credential=self.token)

            oversized = self._intent("preflight", "too much")
            channel._capabilities = ChannelCapabilities(
                plain_text=DeliverySupportLevel.UNSUPPORTED,
                markdown=DeliverySupportLevel.UNSUPPORTED,
            )
            await gateway.deliver_proactively(oversized, credential=self.token)
            await asyncio.sleep(0)
        finally:
            await gateway.stop()

        self.assertEqual(len(observer.calls), 1)

    async def test_completed_projection_recovery_converges_without_reobserving(self) -> None:
        observer = _Observer()
        projections = InMemoryProjectionRouteRepository()
        idempotency = InMemoryIdempotencyRepository()
        gateway = ImAgentGateway(
            channels=[_ReceiptChannel()],
            applications=[FakeAgentApplicationAdapter()],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
                idempotency=idempotency,
            ),
            extensions=GatewayExtensions(delivery_outcome_observer=observer),
        )
        thread = ThreadRef("fake-agent", "thread-recovery")
        route = ThreadProjectionRoute(
            route_id=derive_projection_route_id(thread, self.conversation),
            thread_ref=thread,
            conversation_ref=self.conversation,
        )
        route = await projections.put_projection_route(route)
        projected = ProjectedAgentMessage(
            message=AgentMessage(
                agent_item_id="agent-item-recovery",
                thread_ref=thread,
                role=MessageRole.ASSISTANT,
                content=(TextContent("recover"),),
                created_at=datetime(2026, 8, 3, tzinfo=UTC),
            ),
            turn_id=None,
            checkpoint=True,
        )
        outbound = OutboundMessage(
            delivery_id=derive_projection_delivery_id(
                route.conversation_ref,
                route.thread_ref,
                projected.message.agent_item_id,
            ),
            conversation_ref=route.conversation_ref,
            content=(TextContent("recover"),),
            created_at=projected.message.created_at,
        )

        await gateway.start()
        try:
            await gateway._deliver_projected_outbound(outbound, True)
            await _wait_until(lambda: len(observer.calls) == 1)
            recovered = await deliver_projected_message(
                projections,
                route,
                projected,
                deliver_outbound=gateway._deliver_projected_outbound,
                checkpoint_authority=_ProjectionCheckpointAuthority(
                    projections=projections,
                ),
                authoritative=True,
            )
            await asyncio.sleep(0)
        finally:
            await gateway.stop()

        self.assertEqual(recovered.checkpoint_agent_item_id, "agent-item-recovery")
        self.assertEqual(len(observer.calls), 1)

    async def test_absent_observer_preserves_delivery_and_diagnostics(self) -> None:
        gateway = self._gateway(None)
        self.assertIsNone(gateway.diagnostics_snapshot().gateway.delivery_outcome_observer)
        await gateway.start()
        try:
            result = await gateway.deliver_proactively(
                self._intent("absent"),
                credential=self.token,
            )
        finally:
            await gateway.stop()
        self.assertEqual(result.state.value, "accepted")

    async def test_internal_attempt_is_observed_but_o1_suppression_is_not(self) -> None:
        observer = _Observer()

        class Suppress:
            async def present(self, message, context):
                del message, context
                return None

        gateway = ImAgentGateway(
            channels=[_ReceiptChannel()],
            applications=[FakeAgentApplicationAdapter()],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            extensions=GatewayExtensions(
                outbound_presentation=Suppress(),
                delivery_outcome_observer=observer,
            ),
        )
        message = OutboundMessage(
            delivery_id="internal-attempt",
            conversation_ref=self.conversation,
            content=(TextContent("hello"),),
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
        )
        await gateway.start()
        try:
            await gateway._deliver_outbound(message)
            await _wait_until(lambda: len(observer.calls) == 1)
            await gateway._deliver_projected_outbound(
                OutboundMessage(
                    delivery_id="suppressed",
                    conversation_ref=self.conversation,
                    content=(TextContent("hidden"),),
                    created_at=message.created_at,
                ),
                True,
            )
            await asyncio.sleep(0)
        finally:
            await gateway.stop()

        self.assertEqual(len(observer.calls), 1)


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)

from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from imagent.adapters import IdempotencyClaimStatus
from imagent.applications.capabilities import ProjectMode, SupportLevel
from imagent.applications.contract import (
    AcceptedTurn,
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    ApplicationInputDispatchHandler,
    ApplicationInputOutcomeUnknown,
    InputContinuationPreference,
    InputDisposition,
    ThreadRef,
    TurnReplyCorrelationPolicy,
)
from imagent.applications.events import AgentEvent, AgentEventType
from imagent.applications.operations import (
    ApplicationOperation,
    GetThreadHistory,
    ThreadHistoryRead,
)
from imagent.contracts import (
    BindConversationToThread,
    ConversationBound,
    GatewayOperationFailed,
    ObserveThread,
)
from imagent.gateway import GatewayExtensions, GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.delivery import DeliveryCoordinator, DeliveryCoordinatorConfig
from imagent.gateway.persistence import (
    ConversationBinding,
    InMemoryIdempotencyRepository,
    ProjectionPolicy,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
)
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryProjectionRouteRepository,
)
from imagent.gateway.persistence.sqlite import SQLiteGatewayState
from imagent.gateway.presentation import OutboundPresentationContext
from imagent.gateway.projection import derive_projection_delivery_id
from imagent.interaction.channels import DeliveryReceipt
from imagent.interaction.controllers import ControllerActions
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    MessageRole,
    OutboundMessage,
    TextContent,
)
from imagent.projection_runtime import TurnAcceptanceBufferOverflow
from imagent.projections import (
    ProjectionWorkerState,
    derive_live_projection_delivery_id,
    derive_projection_route_id,
    derive_turn_reply_correlation_id,
    immutable_projection_metadata,
)
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class CapturingOutboundGateway(ImAgentGateway):
    logical_outbound: list[OutboundMessage]

    async def _deliver_outbound(
        self,
        message: OutboundMessage,
        presentation_context: OutboundPresentationContext | None = None,
        *,
        cancellable: bool = False,
    ) -> IdempotencyClaimStatus:
        self.logical_outbound.append(message)
        return await super()._deliver_outbound(
            message,
            presentation_context,
            cancellable=cancellable,
        )


class OversizedMetadataProbe(Mapping[str, object]):
    def __init__(self) -> None:
        self.iterations = 0

    def __getitem__(self, key: str) -> object:
        raise AssertionError(f"oversized metadata value was read: {key}")

    def __iter__(self) -> Iterator[str]:
        for index in range(1_000):
            self.iterations += 1
            if self.iterations > 17:
                raise AssertionError("oversized metadata was iterated past the boundary")
            yield f"key-{index}"

    def __len__(self) -> int:
        return 1_000


class ProjectionHardeningTests(unittest.IsolatedAsyncioTestCase):
    def test_projected_metadata_rejects_unbounded_or_mutable_values(self) -> None:
        invalid = (
            {f"key-{index}": index for index in range(17)},
            {"key": "x" * 257},
            {"key": {"nested": "value"}},
            {"key": ["mutable"]},
            {"key": float("inf")},
            {"key": 2**63},
        )
        for metadata in invalid:
            with self.subTest(metadata=metadata):
                with self.assertRaises(ValueError):
                    immutable_projection_metadata(metadata)

        oversized = OversizedMetadataProbe()
        with self.assertRaises(ValueError):
            immutable_projection_metadata(oversized)
        self.assertEqual(oversized.iterations, 17)

        projected = immutable_projection_metadata({"phase": "commentary", "streaming": False})
        self.assertEqual(
            dict(projected),
            {"phase": "commentary", "streaming": False},
        )
        self.assertIsNone(getattr(projected, "__setitem__", None))

    async def test_live_completed_message_preserves_immutable_metadata(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "metadata-live")
        channel = FakeChannelAdapter()
        projections = InMemoryProjectionRouteRepository()
        gateway = CapturingOutboundGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
            ),
        )
        gateway.logical_outbound = []
        await gateway.start()
        try:
            await gateway.execute_gateway(
                _observe("observe-metadata-live", conversation, thread.ref)
            )
            source_metadata: dict[str, object] = {
                "phase": "commentary",
                "native_application": "fake",
            }
            message = AgentMessage(
                agent_item_id="metadata-live-item",
                thread_ref=thread.ref,
                role=MessageRole.ASSISTANT,
                content=(TextContent("Working"),),
                created_at=datetime.now(UTC),
                metadata=source_metadata,
            )
            application._publish(
                thread.ref,
                AgentEventType.MESSAGE_COMPLETED,
                "turn-metadata-live",
                {"message": message},
            )
            await _wait_until(lambda: len(channel.sent) == 1)

            outbound = gateway.logical_outbound[0]
            self.assertEqual(dict(outbound.metadata), source_metadata)
            self.assertIsNot(outbound.metadata, source_metadata)
            self.assertEqual(outbound.conversation_ref, conversation)
            self.assertEqual(outbound.reply_to, None)
            self.assertEqual(
                outbound.delivery_id,
                derive_projection_delivery_id(
                    conversation,
                    thread.ref,
                    message.agent_item_id,
                ),
            )
            source_metadata["phase"] = "mutated-after-projection"
            self.assertEqual(outbound.metadata["phase"], "commentary")
            self.assertIsNone(getattr(outbound.metadata, "__setitem__", None))
            self.assertEqual(channel.sent[0].metadata["phase"], "commentary")
            self.assertEqual(
                channel.sent[0].metadata["native_application"],
                "fake",
            )

            routes = await projections.list_projection_routes(thread.ref)
            for _ in range(100):
                routes = await projections.list_projection_routes(thread.ref)
                if routes[0].checkpoint_agent_item_id is not None:
                    break
                await asyncio.sleep(0)
            self.assertEqual(routes[0].checkpoint_agent_item_id, message.agent_item_id)
        finally:
            await gateway.stop()

    async def test_live_only_created_message_deduplicates_without_checkpoint(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "live-only")
        channel = FakeChannelAdapter()
        projections = InMemoryProjectionRouteRepository()
        gateway = CapturingOutboundGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
            ),
        )
        gateway.logical_outbound = []
        await gateway.start()
        try:
            await gateway.execute_gateway(_observe("observe-live-only", conversation, thread.ref))
            message = AgentMessage(
                agent_item_id="live-event-1",
                thread_ref=thread.ref,
                role=MessageRole.SYSTEM,
                content=(TextContent("Working"),),
                created_at=datetime.now(UTC),
                metadata={"live_only": True},
            )
            for _ in range(2):
                application._publish(
                    thread.ref,
                    AgentEventType.MESSAGE_CREATED,
                    "turn-live-only",
                    {"message": message},
                )
            await _wait_until(lambda: len(gateway.logical_outbound) == 2)

            self.assertEqual(len(channel.sent), 1)
            self.assertEqual(
                gateway.logical_outbound[0].delivery_id,
                derive_live_projection_delivery_id(
                    conversation,
                    thread.ref,
                    f"fake-agent:thread:{thread.ref.native_thread_id}:message:live-event-1",
                ),
            )
            route = (await projections.list_projection_routes(thread.ref))[0]
            self.assertIsNone(route.checkpoint_agent_item_id)
            self.assertIsNone(route.checkpointed_at)
        finally:
            await gateway.stop()

    async def test_authoritative_history_preserves_agent_metadata(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        await application.send_input(
            thread.ref,
            AgentInput(
                client_message_id="metadata-history-input",
                content=(TextContent("Run from history"),),
            ),
        )
        conversation = ConversationRef("fake-channel", "metadata-history")
        channel = FakeChannelAdapter()
        projections = InMemoryProjectionRouteRepository()
        gateway = CapturingOutboundGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
            ),
            limits=GatewayLimits(baseline_history_limit=1),
        )
        gateway.logical_outbound = []
        await gateway.start()
        try:
            await gateway.execute_gateway(
                _observe("observe-metadata-history", conversation, thread.ref)
            )
            await _wait_until(lambda: len(channel.sent) == 2)

            self.assertEqual(
                [dict(message.metadata) for message in gateway.logical_outbound],
                [{"phase": "commentary"}, {"phase": "final_answer"}],
            )
            self.assertEqual(
                [message.delivery_id for message in gateway.logical_outbound],
                [
                    derive_projection_delivery_id(
                        conversation,
                        thread.ref,
                        "turn-1:message:1",
                    ),
                    derive_projection_delivery_id(
                        conversation,
                        thread.ref,
                        "turn-1:message:2",
                    ),
                ],
            )
            self.assertEqual(
                [message.metadata["phase"] for message in channel.sent],
                ["commentary", "final_answer"],
            )
            routes = await projections.list_projection_routes(thread.ref)
            for _ in range(100):
                routes = await projections.list_projection_routes(thread.ref)
                if routes[0].checkpoint_agent_item_id == "turn-1:message:2":
                    break
                await asyncio.sleep(0)
            self.assertEqual(routes[0].checkpoint_agent_item_id, "turn-1:message:2")
        finally:
            await gateway.stop()

    async def test_transient_coordinator_backpressure_recovers_without_sticky_route(
        self,
    ) -> None:
        application = CountingSubscriptionApplication()
        thread = await application.create_thread()
        fast_conversation = ConversationRef("fast-channel", "fast")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=fast_conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        await projections.put_projection_route(_route(thread.ref, fast_conversation))
        slow = BlockingChannelAdapter("slow-channel")
        fast = FakeChannelAdapter("fast-channel")
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(
                max_pending=1,
                backpressure_retry_after_seconds=0.01,
            )
        )
        gateway = ImAgentGateway(
            channels=[slow, fast],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            delivery_coordinator=coordinator,
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0.01,
                subscription_retry_max_seconds=0.01,
            ),
        )
        await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            blocker = coordinator.submit(
                slow,
                OutboundMessage(
                    delivery_id="capacity-blocker",
                    conversation_ref=ConversationRef("slow-channel", "slow"),
                    content=(TextContent("block"),),
                    created_at=datetime.now(UTC),
                ),
            )
            await slow.delivery_started.wait()
            await application.send_input(
                thread.ref,
                AgentInput(client_message_id="burst", content=(TextContent("run"),)),
            )
            await _wait_until(lambda: application.subscription_calls >= 2)

            slow.release.set()
            await blocker.result()
            await _wait_until(lambda: len(fast.sent) == 1)
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )

            stored = await projections.list_projection_routes(thread.ref)
            self.assertEqual(len(stored), 1)
            self.assertTrue(all(route.checkpoint_agent_item_id is not None for route in stored))
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(health.delivery_failure_count, 0)
            self.assertGreaterEqual(health.restart_count, 1)
        finally:
            slow.release.set()
            await gateway.stop()

    async def test_concurrent_first_observers_share_one_owned_subscription(
        self,
    ) -> None:
        application = CountingSubscriptionApplication()
        thread = await application.create_thread()
        first = ConversationRef("fake-channel", "first")
        second = ConversationRef("fake-channel", "second")
        gateway = ImAgentGateway(
            channels=[],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await asyncio.gather(
                gateway.execute_gateway(_observe("observe-first", first, thread.ref)),
                gateway.execute_gateway(_observe("observe-second", second, thread.ref)),
            )
            self.assertEqual(application.subscription_calls, 1)
            self.assertEqual(application.active_subscriptions, 1)
            self.assertEqual(application.max_active_subscriptions, 1)
        finally:
            await gateway.stop()
        self.assertEqual(application.active_subscriptions, 0)

    async def test_first_observe_uses_bounded_recent_baseline(self) -> None:
        application = RecordingHistoryApplication()
        thread = await application.create_thread()
        for index in range(20):
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id=f"archived-{index}",
                    content=(TextContent(str(index)),),
                ),
            )
        conversation = ConversationRef("fake-channel", "conversation")
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            limits=GatewayLimits(
                baseline_history_limit=3,
            ),
        )
        await gateway.start()
        try:
            await gateway.execute_gateway(_observe("observe-bounded", conversation, thread.ref))
            self.assertEqual(len(channel.sent), 6)
            self.assertEqual(application.history_calls, [(3, 1)])
        finally:
            await gateway.stop()

    async def test_first_observe_caps_items_from_one_large_turn(self) -> None:
        application = LargeTurnHistoryApplication()
        thread = await application.create_thread()
        await application.seed_large_turn(
            thread.ref,
            message_count=50,
        )
        conversation = ConversationRef("fake-channel", "conversation")
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            limits=GatewayLimits(
                baseline_history_limit=1,
                projection_item_limit=5,
            ),
        )
        await gateway.start()
        try:
            await gateway.execute_gateway(_observe("observe-large-turn", conversation, thread.ref))
            self.assertEqual(len(channel.sent), 5)
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(
                health.last_gap,
                (
                    f"{derive_projection_route_id(thread.ref, conversation)}:"
                    "projection_window_truncated"
                ),
            )
        finally:
            await gateway.stop()

    async def test_live_completion_during_baseline_is_ordered_and_deduplicated(
        self,
    ) -> None:
        application = BlockingHistoryApplication()
        thread = await application.create_thread()
        await application.send_input(
            thread.ref,
            AgentInput(
                client_message_id="before-baseline",
                content=(TextContent("baseline"),),
            ),
        )
        conversation = ConversationRef("fake-channel", "conversation")
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )
        await gateway.start()
        try:
            observing = asyncio.create_task(
                gateway.execute_gateway(_observe("observe-race", conversation, thread.ref))
            )
            await application.history_started.wait()
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id="during-baseline",
                    content=(TextContent("live"),),
                ),
            )
            application.release_history.set()
            await observing
            await _wait_until(lambda: len(channel.sent) == 4)
            self.assertEqual(
                [_message_text(item) for item in channel.sent],
                [
                    "fake commentary",
                    "fake final_answer",
                    "fake commentary",
                    "fake final_answer",
                ],
            )
        finally:
            application.release_history.set()
            await gateway.stop()

    async def test_route_barrier_precedes_durable_visibility(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        first = ConversationRef("fake-channel", "first")
        second = ConversationRef("fake-channel", "second")
        channel = FakeChannelAdapter()
        projections = YieldingProjectionRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await gateway.execute_gateway(_observe("first-route", first, thread.ref))
            projections.block_next_put = True
            observing = asyncio.create_task(
                gateway.execute_gateway(_observe("second-route", second, thread.ref))
            )
            await projections.route_visible.wait()
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id="live-before-put-return",
                    content=(TextContent("live"),),
                ),
            )
            await asyncio.sleep(0)
            self.assertFalse(any(item.conversation_ref == second for item in channel.sent))
            projections.release_put.set()
            await observing
            await _wait_until(
                lambda: sum(item.conversation_ref == second for item in channel.sent) == 2
            )
        finally:
            projections.release_put.set()
            await gateway.stop()

    async def test_failed_route_refresh_releases_existing_worker_barrier(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        channel = FakeChannelAdapter()
        projections = FailingRefreshRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await gateway.execute_gateway(_observe("initial-observe", conversation, thread.ref))
            projections.fail_next_put = True
            failed = await gateway.execute_gateway(
                _observe("failed-refresh", conversation, thread.ref)
            )
            self.assertIsInstance(failed, GatewayOperationFailed)
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id="after-failed-refresh",
                    content=(TextContent("live"),),
                ),
            )
            await _wait_until(lambda: len(channel.sent) == 2)
        finally:
            await gateway.stop()

    async def test_post_acceptance_correlation_failure_keeps_inbound_terminal(
        self,
    ) -> None:
        application = PassiveAcceptanceApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        projections = FailingTurnCorrelationRepository()
        idempotency = InMemoryIdempotencyRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                idempotency=idempotency,
                projections=projections,
            ),
        )
        await gateway.start()
        inbound = _inbound(conversation, "post-accept-correlation")
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated correlation failure"):
                await channel.on_message(inbound)
            await channel.on_message(inbound)

            self.assertEqual(application.send_input_calls, 1)
            self.assertEqual(
                await idempotency.claim(
                    "inbound:fake-channel",
                    "conversation:post-accept-correlation",
                ),
                IdempotencyClaimStatus.ALREADY_COMPLETED,
            )
        finally:
            await gateway.stop()

    async def test_post_acceptance_drain_failure_keeps_inbound_terminal(self) -> None:
        application = PassiveAcceptanceApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        idempotency = InMemoryIdempotencyRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                idempotency=idempotency,
            ),
        )

        async def fail_drain(thread_ref: ThreadRef) -> None:
            del thread_ref
            raise RuntimeError("simulated buffered-event drain failure")

        gateway._projection_runtime._drain_buffered_events = fail_drain
        await gateway.start()
        inbound = _inbound(conversation, "post-accept-drain")
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated buffered-event drain failure"):
                await channel.on_message(inbound)
            await channel.on_message(inbound)

            self.assertEqual(application.send_input_calls, 1)
            self.assertEqual(
                await idempotency.claim(
                    "inbound:fake-channel",
                    "conversation:post-accept-drain",
                ),
                IdempotencyClaimStatus.ALREADY_COMPLETED,
            )
        finally:
            await gateway.stop()

    async def test_acceptance_buffer_overflow_keeps_input_terminal_and_recovers(
        self,
    ) -> None:
        application = OverflowingAcceptanceApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        idempotency = InMemoryIdempotencyRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                idempotency=idempotency,
            ),
            limits=GatewayLimits(
                turn_acceptance_event_max_pending=1,
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        inbound = _inbound(conversation, "acceptance-overflow")
        handling = asyncio.create_task(channel.on_message(inbound))
        try:
            await application.turn_persisted.wait()
            await _wait_until(
                lambda: (
                    (health := gateway.get_projection_health(thread.ref)) is not None
                    and health.event_overflow_count == 1
                )
            )
            application.release_acceptance.set()
            with self.assertRaises(TurnAcceptanceBufferOverflow):
                await handling

            await channel.on_message(inbound)
            await _wait_until(lambda: len(channel.sent) == 2)
            self.assertEqual(len(application._inputs), 1)
            self.assertEqual(
                await idempotency.claim(
                    "inbound:fake-channel",
                    "conversation:acceptance-overflow",
                ),
                IdempotencyClaimStatus.ALREADY_COMPLETED,
            )
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(
                health.last_event_overflow,
                "turn_acceptance_buffer_overflow",
            )
        finally:
            application.release_acceptance.set()
            if not handling.done():
                handling.cancel()
                await asyncio.gather(handling, return_exceptions=True)
            await gateway.stop()

    async def test_pre_acceptance_failure_releases_inbound_for_retry(self) -> None:
        application = FailOnceBeforeAcceptanceApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        idempotency = InMemoryIdempotencyRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                idempotency=idempotency,
            ),
        )
        await gateway.start()
        inbound = _inbound(conversation, "pre-accept-failure")
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated pre-acceptance failure"):
                await channel.on_message(inbound)
            await channel.on_message(inbound)

            self.assertEqual(application.send_input_calls, 2)
            self.assertEqual(application.accepted_input_calls, 1)
            self.assertEqual(
                await idempotency.claim(
                    "inbound:fake-channel",
                    "conversation:pre-accept-failure",
                ),
                IdempotencyClaimStatus.ALREADY_COMPLETED,
            )
        finally:
            await gateway.stop()

    async def test_dispatched_unknown_input_keeps_inbound_in_flight(self) -> None:
        application = UnknownOutcomeApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        idempotency = InMemoryIdempotencyRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                idempotency=idempotency,
            ),
        )
        await gateway.start()
        inbound = _inbound(conversation, "unknown-native-outcome")
        try:
            with self.assertRaises(asyncio.CancelledError):
                await channel.on_message(inbound)
            await channel.on_message(inbound)

            self.assertEqual(application.send_input_calls, 1)
            self.assertEqual(
                await idempotency.claim(
                    "inbound:fake-channel",
                    "conversation:unknown-native-outcome",
                ),
                IdempotencyClaimStatus.IN_FLIGHT,
            )
        finally:
            await gateway.stop()

    async def test_primary_post_acceptance_error_survives_drain_failure(self) -> None:
        application = PassiveAcceptanceApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=FailingTurnCorrelationRepository(),
            ),
        )

        async def fail_drain(thread_ref: ThreadRef) -> None:
            del thread_ref
            raise RuntimeError("secondary drain failure")

        gateway._projection_runtime._drain_buffered_events = fail_drain
        await gateway.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated correlation failure") as raised:
                await channel.on_message(_inbound(conversation, "combined-failure"))
            self.assertTrue(
                any("secondary drain failure" in note for note in raised.exception.__notes__)
            )
            self.assertIsNone(raised.exception.__cause__)
        finally:
            await gateway.stop()

    async def test_failed_terminal_write_remains_sticky_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.sqlite3"
            state = FailingAcceptedInputSQLiteState(path)
            application = PassiveAcceptanceApplication()
            thread = await application.create_thread()
            conversation = ConversationRef("fake-channel", "conversation")
            await state.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=application.summary.ref,
                    thread_ref=thread.ref,
                )
            )
            channel = FakeChannelAdapter()
            gateway = ImAgentGateway(
                channels=[channel],
                applications=[application],
                repositories=GatewayRepositories(
                    bindings=state,
                    idempotency=state,
                    projections=state,
                ),
            )
            await gateway.start()
            try:
                with self.assertRaisesRegex(RuntimeError, "simulated terminal write failure"):
                    await channel.on_message(_inbound(conversation, "terminal-write-failure"))
            finally:
                await gateway.stop()
                await state.close()

            reopened = SQLiteGatewayState(path)
            try:
                self.assertEqual(
                    await reopened.claim(
                        "inbound:fake-channel",
                        "conversation:terminal-write-failure",
                    ),
                    IdempotencyClaimStatus.IN_FLIGHT,
                )
                self.assertEqual(application.send_input_calls, 1)
            finally:
                await reopened.close()

    async def test_turn_reply_correlation_is_destination_safe(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        first = ConversationRef("fake-channel", "first")
        second = ConversationRef("fake-channel", "second")
        bindings = InMemoryBindingRepository()
        for conversation in (first, second):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=application.summary.ref,
                    thread_ref=thread.ref,
                )
            )
        channel = FakeChannelAdapter()
        projections = InMemoryProjectionRouteRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await gateway.execute_gateway(_observe("observe-first", first, thread.ref))
            await gateway.execute_gateway(_observe("observe-second", second, thread.ref))
            await channel.on_message(_inbound(first, "message-first"))
            await channel.on_message(_inbound(second, "message-second"))
            await _wait_until(lambda: len(channel.sent) == 8)
            await _wait_for_no_correlations(projections)
            self.assertEqual(
                await projections.list_turn_reply_correlations(),
                (),
            )
        finally:
            await gateway.stop()

        first_deliveries = [item for item in channel.sent if item.conversation_ref == first]
        second_deliveries = [item for item in channel.sent if item.conversation_ref == second]
        self.assertEqual(
            [item.reply_to for item in first_deliveries].count("message-first"),
            2,
        )
        self.assertEqual(
            [item.reply_to for item in first_deliveries].count(None),
            2,
        )
        self.assertEqual(
            [item.reply_to for item in second_deliveries].count("message-second"),
            2,
        )
        self.assertEqual(
            [item.reply_to for item in second_deliveries].count(None),
            2,
        )

    async def test_two_conversations_steer_one_turn_without_retargeting(self) -> None:
        application = ContinuationApplication()
        thread = await application.create_thread()
        first = ConversationRef("fake-channel", "first")
        second = ConversationRef("fake-channel", "second")
        bindings = InMemoryBindingRepository()
        for conversation in (first, second):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=application.summary.ref,
                    thread_ref=thread.ref,
                )
            )
        projections = InMemoryProjectionRouteRepository()
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(first, "message-first"))
            await channel.on_message(_inbound(second, "message-second"))
            correlation = await projections.get_turn_reply_correlation(
                thread.ref,
                "turn-active",
            )
            assert correlation is not None
            self.assertEqual(correlation.conversation_ref, first)
            self.assertEqual(correlation.reply_to_message_id, "message-first")
            self.assertEqual(
                application.native_dispatches,
                [InputDisposition.STARTED, InputDisposition.STEERED],
            )
            self.assertEqual(
                application.received_continuations,
                [
                    InputContinuationPreference.PREFER_ACTIVE_TURN,
                    InputContinuationPreference.PREFER_ACTIVE_TURN,
                ],
            )
        finally:
            await gateway.stop()

    async def test_steer_preserves_original_destination_after_sqlite_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "steer-restart.sqlite3"
            application = ContinuationApplication()
            thread = await application.create_thread()
            first = ConversationRef("fake-channel", "first")
            second = ConversationRef("fake-channel", "second")
            initial = SQLiteGatewayState(path)
            for conversation in (first, second):
                await initial.put(
                    ConversationBinding(
                        conversation_ref=conversation,
                        application_ref=application.summary.ref,
                        thread_ref=thread.ref,
                    )
                )
            first_channel = FakeChannelAdapter()
            first_gateway = ImAgentGateway(
                channels=[first_channel],
                applications=[application],
                repositories=GatewayRepositories(
                    bindings=initial,
                    idempotency=initial,
                    projections=initial,
                ),
                projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            )
            await first_gateway.start()
            try:
                await first_channel.on_message(_inbound(first, "message-first"))
            finally:
                await first_gateway.stop()
                await initial.close()

            recovered = SQLiteGatewayState(path)
            second_channel = FakeChannelAdapter()
            second_gateway = ImAgentGateway(
                channels=[second_channel],
                applications=[application],
                repositories=GatewayRepositories(
                    bindings=recovered,
                    idempotency=recovered,
                    projections=recovered,
                ),
                projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            )
            await second_gateway.start()
            try:
                await second_channel.on_message(_inbound(second, "message-second"))
                correlation = await recovered.get_turn_reply_correlation(
                    thread.ref,
                    "turn-active",
                )
                assert correlation is not None
                self.assertEqual(correlation.conversation_ref, first)
                self.assertEqual(correlation.reply_to_message_id, "message-first")
            finally:
                await second_gateway.stop()
                await recovered.close()

    async def test_uncorrelated_steer_fails_before_native_dispatch(self) -> None:
        application = ContinuationApplication(active_turn_id="turn-external")
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=InMemoryProjectionRouteRepository(),
            ),
        )
        await gateway.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "without an existing reply correlation"):
                await channel.on_message(_inbound(conversation, "message-steer"))
            self.assertEqual(application.native_dispatches, [])
        finally:
            await gateway.stop()

    async def test_steer_racing_started_correlation_fails_before_dispatch_then_retries(
        self,
    ) -> None:
        application = ContinuationApplication(block_first_acceptance=True)
        thread = await application.create_thread()
        first = ConversationRef("fake-channel", "first")
        second = ConversationRef("fake-channel", "second")
        bindings = InMemoryBindingRepository()
        for conversation in (first, second):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=application.summary.ref,
                    thread_ref=thread.ref,
                )
            )
        projections = InMemoryProjectionRouteRepository()
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        await gateway.execute_gateway(_observe("observe-first", first, thread.ref))
        await gateway.execute_gateway(_observe("observe-second", second, thread.ref))
        first_input = asyncio.create_task(channel.on_message(_inbound(first, "message-first")))
        try:
            await application.first_native_dispatch.wait()
            self.assertIsNone(
                await projections.get_turn_reply_correlation(
                    thread.ref,
                    "turn-active",
                )
            )
            with self.assertRaisesRegex(RuntimeError, "without an existing reply correlation"):
                await channel.on_message(_inbound(second, "message-second"))
            self.assertEqual(application.native_dispatches, [InputDisposition.STARTED])

            application.release_first_acceptance.set()
            await first_input
            await channel.on_message(_inbound(second, "message-second"))
            correlation = await projections.get_turn_reply_correlation(
                thread.ref,
                "turn-active",
            )
            assert correlation is not None
            self.assertEqual(correlation.conversation_ref, first)
            self.assertEqual(
                application.native_dispatches,
                [InputDisposition.STARTED, InputDisposition.STEERED],
            )
        finally:
            application.release_first_acceptance.set()
            if not first_input.done():
                await first_input
            await gateway.stop()

    async def test_replaced_steer_turn_is_post_acceptance_and_never_retargets(
        self,
    ) -> None:
        application = ContinuationApplication(replacement_turn_id="turn-replacement")
        thread = await application.create_thread()
        first = ConversationRef("fake-channel", "first")
        second = ConversationRef("fake-channel", "second")
        bindings = InMemoryBindingRepository()
        for conversation in (first, second):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=application.summary.ref,
                    thread_ref=thread.ref,
                )
            )
        projections = InMemoryProjectionRouteRepository()
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(first, "message-first"))
            with self.assertRaisesRegex(RuntimeError, "different Turn"):
                await channel.on_message(_inbound(second, "message-second"))
            original = await projections.get_turn_reply_correlation(
                thread.ref,
                "turn-active",
            )
            assert original is not None
            self.assertEqual(original.conversation_ref, first)
            self.assertIsNone(
                await projections.get_turn_reply_correlation(
                    thread.ref,
                    "turn-replacement",
                )
            )
        finally:
            await gateway.stop()

    async def test_foreground_restart_restores_only_bound_thread_and_reclaims_switch(
        self,
    ) -> None:
        application = CountingSubscriptionApplication()
        thread_a = await application.create_thread()
        thread_b = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        binding = await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread_b.ref,
            )
        )
        for thread in (thread_a, thread_b):
            await projections.put_projection_route(_route(thread.ref, conversation))

        first_gateway = ImAgentGateway(
            channels=[FakeChannelAdapter()],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )
        await first_gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    first_gateway,
                    thread_b.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            self.assertEqual(application.subscription_threads, [thread_b.ref])
            self.assertIsNone(first_gateway.get_projection_health(thread_a.ref))
        finally:
            await first_gateway.stop()

        second_gateway = ImAgentGateway(
            channels=[FakeChannelAdapter()],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )
        await second_gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    second_gateway,
                    thread_b.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            self.assertEqual(
                application.subscription_threads,
                [thread_b.ref, thread_b.ref],
            )
            switched = await second_gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="bind-a",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread_a.ref,
                    expected_revision=binding.revision,
                    created_at=datetime.now(UTC),
                )
            )
            assert isinstance(switched, ConversationBound)
            self.assertEqual(switched.binding.thread_ref, thread_a.ref)
            await _wait_until(
                lambda: _health_state_is(
                    second_gateway,
                    thread_a.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            self.assertIsNone(second_gateway.get_projection_health(thread_b.ref))
            self.assertEqual(application.subscription_threads[-1], thread_a.ref)
        finally:
            await second_gateway.stop()

    async def test_external_turn_does_not_inherit_latest_inbound_reply(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(conversation, "origin-message"))
            await _wait_until(lambda: len(channel.sent) == 2)
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id="external-client",
                    content=(TextContent("external"),),
                ),
            )
            await _wait_until(lambda: len(channel.sent) == 4)
        finally:
            await gateway.stop()
        self.assertEqual(
            [item.reply_to for item in channel.sent],
            ["origin-message", "origin-message", None, None],
        )

    async def test_foreground_activation_reconciles_only_new_destination(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        other_thread = await application.create_thread()
        first = ConversationRef("fake-channel", "first")
        second = ConversationRef("fake-channel", "second")
        bindings = InMemoryBindingRepository()
        first_binding = await bindings.put(
            ConversationBinding(
                conversation_ref=first,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        second_binding = await bindings.put(
            ConversationBinding(
                conversation_ref=second,
                application_ref=application.summary.ref,
                thread_ref=other_thread.ref,
            )
        )
        self.assertEqual(first_binding.thread_ref, thread.ref)
        projections = InMemoryProjectionRouteRepository()
        for conversation in (first, second):
            await projections.put_projection_route(_route(thread.ref, conversation))
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )
        await gateway.start()
        try:
            switched = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="activate-second-route",
                    conversation_ref=second,
                    actor="user",
                    thread_ref=thread.ref,
                    expected_revision=second_binding.revision,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(switched, ConversationBound)
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id="foreground-live",
                    content=(TextContent("live"),),
                ),
            )
            await _wait_until(lambda: len(channel.sent) == 4)
            self.assertEqual(
                {
                    conversation: sum(
                        item.conversation_ref == conversation for item in channel.sent
                    )
                    for conversation in (first, second)
                },
                {first: 2, second: 2},
            )
        finally:
            await gateway.stop()

    async def test_restart_scan_stops_at_checkpoint_with_strict_page_bound(
        self,
    ) -> None:
        application = RecordingHistoryApplication()
        thread = await application.create_thread()
        for index in range(10):
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id=f"turn-{index}",
                    content=(TextContent(str(index)),),
                ),
            )
        history = await application.execute(
            GetThreadHistory(
                operation_id="read-checkpoint",
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
                limit=10,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        assert isinstance(history, ThreadHistoryRead)
        checkpoint = history.history.turns[7].agent_messages[-1].agent_item_id
        application.history_calls.clear()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        route = await projections.put_projection_route(_route(thread.ref, conversation))
        await projections.advance_projection_checkpoint(
            route.route_id,
            expected_agent_item_id=None,
            agent_item_id=checkpoint,
            checkpointed_at=datetime.now(UTC),
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            limits=GatewayLimits(
                recovery_history_page_size=2,
                recovery_max_pages=2,
            ),
        )
        await gateway.start()
        try:
            await _wait_until(lambda: len(channel.sent) == 4)
            self.assertEqual(application.history_calls, [(2, 1), (2, 2)])
        finally:
            await gateway.stop()

    async def test_missing_checkpoint_is_bounded_and_visible_as_gap(self) -> None:
        application = RecordingHistoryApplication()
        thread = await application.create_thread()
        for index in range(10):
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id=f"turn-{index}",
                    content=(TextContent(str(index)),),
                ),
            )
        latest_history = await application.execute(
            GetThreadHistory(
                operation_id="latest-after-gap",
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
                limit=1,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        assert isinstance(latest_history, ThreadHistoryRead)
        latest_agent_item_id = latest_history.history.turns[0].agent_messages[-1].agent_item_id
        application.history_calls.clear()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        route = await projections.put_projection_route(_route(thread.ref, conversation))
        await projections.advance_projection_checkpoint(
            route.route_id,
            expected_agent_item_id=None,
            agent_item_id="expired-agent-item",
            checkpointed_at=datetime.now(UTC),
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            limits=GatewayLimits(
                recovery_history_page_size=2,
                recovery_max_pages=2,
                projection_item_limit=3,
            ),
        )
        await gateway.start()
        try:
            async with asyncio.timeout(1):
                while True:
                    stored = (await projections.list_projection_routes(thread.ref))[0]
                    if stored.checkpoint_agent_item_id == latest_agent_item_id:
                        break
                    await asyncio.sleep(0)
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertIn("checkpoint_out_of_window", health.last_gap or "")
            self.assertEqual(application.history_calls, [(2, 1), (2, 2)])
            self.assertEqual(
                stored.checkpoint_agent_item_id,
                latest_agent_item_id,
            )
        finally:
            await gateway.stop()

    async def test_completed_delivery_converges_lagging_checkpoint(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        await application.send_input(
            thread.ref,
            AgentInput(client_message_id="completed", content=(TextContent("done"),)),
        )
        history = await application.execute(
            GetThreadHistory(
                operation_id="read-history",
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
                limit=1,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        assert isinstance(history, ThreadHistoryRead)
        messages = history.history.turns[0].agent_messages
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        idempotency = InMemoryIdempotencyRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        await projections.put_projection_route(_route(thread.ref, conversation))
        for message in messages:
            delivery_id = derive_projection_delivery_id(
                conversation,
                thread.ref,
                message.agent_item_id,
            )
            self.assertEqual(
                await idempotency.claim("outbound:fake-channel", delivery_id),
                IdempotencyClaimStatus.ACQUIRED,
            )
            await idempotency.complete("outbound:fake-channel", delivery_id)
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                idempotency=idempotency,
                projections=projections,
            ),
        )
        await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            stored = (await projections.list_projection_routes(thread.ref))[0]
            self.assertEqual(
                stored.checkpoint_agent_item_id,
                messages[-1].agent_item_id,
            )
            self.assertEqual(channel.sent, [])
        finally:
            await gateway.stop()

    async def test_stale_outbound_claim_before_reservation_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.sqlite3"
            conversation = ConversationRef("fake-channel", "conversation")
            message = OutboundMessage(
                delivery_id="pre-reservation-crash",
                conversation_ref=conversation,
                content=(TextContent("recover"),),
                created_at=datetime.now(UTC),
            )
            first = SQLiteGatewayState(path)
            self.assertEqual(
                await first.claim("outbound:fake-channel", message.delivery_id),
                IdempotencyClaimStatus.ACQUIRED,
            )
            await first.close()

            recovered = SQLiteGatewayState(path, stale_claim_after_seconds=0)
            channel = FakeChannelAdapter()
            gateway = ImAgentGateway(
                channels=[channel],
                applications=[],
                repositories=GatewayRepositories(
                    bindings=recovered,
                    idempotency=recovered,
                    projections=recovered,
                    delivery_submissions=recovered,
                ),
            )
            try:
                self.assertEqual(
                    await gateway._deliver_outbound(message),
                    IdempotencyClaimStatus.ACQUIRED,
                )
                self.assertEqual(len(channel.sent), 1)
                self.assertEqual(channel.sent[0].content, message.content)
            finally:
                await recovered.close()

    async def test_accepted_submission_converges_after_outer_complete_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.sqlite3"
            conversation = ConversationRef("fake-channel", "conversation")
            message = OutboundMessage(
                delivery_id="accepted-before-outer-complete",
                conversation_ref=conversation,
                content=(TextContent("recover"),),
                created_at=datetime.now(UTC),
            )
            first = FailingOutboundCompleteOnceSQLiteState(path)
            channel = FakeChannelAdapter()
            first_gateway = ImAgentGateway(
                channels=[channel],
                applications=[],
                repositories=GatewayRepositories(
                    bindings=first,
                    idempotency=first,
                    projections=first,
                    delivery_submissions=first,
                ),
            )
            try:
                with self.assertRaisesRegex(RuntimeError, "simulated outer complete crash"):
                    await first_gateway._deliver_outbound(message)
                self.assertEqual(len(channel.sent), 1)
                self.assertEqual(channel.sent[0].content, message.content)
            finally:
                await first.close()

            recovered = SQLiteGatewayState(path, stale_claim_after_seconds=0)
            recovered_gateway = ImAgentGateway(
                channels=[channel],
                applications=[],
                repositories=GatewayRepositories(
                    bindings=recovered,
                    idempotency=recovered,
                    projections=recovered,
                    delivery_submissions=recovered,
                ),
            )
            try:
                self.assertEqual(
                    await recovered_gateway._deliver_outbound(message),
                    IdempotencyClaimStatus.ALREADY_COMPLETED,
                )
                self.assertEqual(len(channel.sent), 1)
                self.assertEqual(channel.sent[0].content, message.content)
            finally:
                await recovered.close()

    async def test_in_flight_delivery_does_not_advance_checkpoint(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        await application.send_input(
            thread.ref,
            AgentInput(client_message_id="in-flight", content=(TextContent("run"),)),
        )
        history = await application.execute(
            GetThreadHistory(
                operation_id="read-history",
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
                limit=1,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        assert isinstance(history, ThreadHistoryRead)
        first_message = history.history.turns[0].agent_messages[0]
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        idempotency = InMemoryIdempotencyRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        route = await projections.put_projection_route(_route(thread.ref, conversation))
        delivery_id = derive_projection_delivery_id(
            conversation,
            thread.ref,
            first_message.agent_item_id,
        )
        await idempotency.claim("outbound:fake-channel", delivery_id)
        gateway = ImAgentGateway(
            channels=[FakeChannelAdapter()],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                idempotency=idempotency,
                projections=projections,
            ),
        )
        await gateway.start()
        try:
            await _wait_until(lambda: _delivery_failure_count(gateway, thread.ref) == 1)
            stored = (await projections.list_projection_routes(thread.ref))[0]
            self.assertIsNone(stored.checkpoint_agent_item_id)
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(health.last_delivery_route_id, route.route_id)
        finally:
            await gateway.stop()

    async def test_stale_correlations_are_cleaned_without_agent_state(self) -> None:
        projections = InMemoryProjectionRouteRepository()
        thread = ThreadRef("fake-agent", "thread-1")
        conversation = ConversationRef("fake-channel", "conversation")
        now = datetime.now(UTC)
        for turn_id, created_at in (
            ("old-turn", now - timedelta(minutes=5)),
            ("current-turn", now),
        ):
            await projections.put_turn_reply_correlation(
                TurnReplyCorrelation(
                    correlation_id=derive_turn_reply_correlation_id(
                        thread,
                        turn_id,
                    ),
                    thread_ref=thread,
                    turn_id=turn_id,
                    client_message_id=f"client-{turn_id}",
                    conversation_ref=conversation,
                    reply_to_message_id=f"message-{turn_id}",
                    created_at=created_at,
                )
            )
        gateway = ImAgentGateway(
            channels=[],
            applications=[],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
            ),
            limits=GatewayLimits(
                turn_correlation_retention_seconds=60,
            ),
        )
        await gateway.start()
        try:
            remaining = await projections.list_turn_reply_correlations()
            self.assertEqual([item.turn_id for item in remaining], ["current-turn"])
        finally:
            await gateway.stop()

    async def test_active_input_enforces_correlation_retention_after_start(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            limits=GatewayLimits(
                turn_correlation_retention_seconds=60,
            ),
        )
        await gateway.start()
        try:
            await projections.put_turn_reply_correlation(
                TurnReplyCorrelation(
                    correlation_id=derive_turn_reply_correlation_id(
                        thread.ref,
                        "orphaned-turn",
                    ),
                    thread_ref=thread.ref,
                    turn_id="orphaned-turn",
                    client_message_id="orphaned-client",
                    conversation_ref=conversation,
                    reply_to_message_id="orphaned-message",
                    created_at=datetime.now(UTC) - timedelta(minutes=5),
                )
            )
            await channel.on_message(_inbound(conversation, "current-message"))
            await _wait_until(lambda: len(channel.sent) == 2)
            self.assertEqual(
                await projections.get_turn_reply_correlation(
                    thread.ref,
                    "orphaned-turn",
                ),
                None,
            )
        finally:
            await gateway.stop()

    async def test_thread_deletion_cleans_routes_and_reply_correlations(self) -> None:
        application = CountingSubscriptionApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        await projections.put_projection_route(_route(thread.ref, conversation))
        await projections.put_turn_reply_correlation(
            TurnReplyCorrelation(
                correlation_id=derive_turn_reply_correlation_id(
                    thread.ref,
                    "turn-orphaned",
                ),
                thread_ref=thread.ref,
                turn_id="turn-orphaned",
                client_message_id="client-orphaned",
                conversation_ref=conversation,
                reply_to_message_id="message-orphaned",
                created_at=datetime.now(UTC),
            )
        )
        gateway = ImAgentGateway(
            channels=[FakeChannelAdapter()],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
        )
        await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            application.publish_thread_deleted(thread.ref)
            await _wait_for_no_routes(projections, thread.ref)
            await _wait_for_no_correlations(projections)
        finally:
            await gateway.stop()

    async def test_subscription_failure_recovers_without_new_input(self) -> None:
        application = FlakySubscriptionApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        await projections.put_projection_route(_route(thread.ref, conversation))
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(health.restart_count, 1)
            self.assertEqual(application.subscription_calls, 2)
            self.assertEqual(application.pending_snapshot_calls, 2)
            self.assertFalse(health.interactive_request_recovery_degraded)
            await application.send_input(
                thread.ref,
                AgentInput(client_message_id="external", content=(TextContent("go"),)),
            )
            await _wait_until(lambda: len(channel.sent) == 2)
        finally:
            await gateway.stop()

    async def test_subscription_failure_marks_request_recovery_degraded_without_snapshot(
        self,
    ) -> None:
        application = FlakySubscriptionApplication()
        application._summary = replace(
            application.summary,
            capabilities=replace(
                application.summary.capabilities,
                runtime=replace(
                    application.summary.capabilities.runtime,
                    pending_request_snapshot=SupportLevel.UNSUPPORTED,
                ),
            ),
        )
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "degraded-subscription")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        await projections.put_projection_route(_route(thread.ref, conversation))
        gateway = ImAgentGateway(
            channels=[FakeChannelAdapter()],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(health.restart_count, 1)
            self.assertTrue(health.interactive_request_recovery_degraded)
            self.assertEqual(application.pending_snapshot_calls, 0)
        finally:
            await gateway.stop()

    async def test_route_repository_failure_does_not_strand_worker_start(
        self,
    ) -> None:
        application = CountingSubscriptionApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = FailOnceListingProjectionRepository(fail_on_call=2)
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        await projections.put_projection_route(_route(thread.ref, conversation))
        gateway = ImAgentGateway(
            channels=[FakeChannelAdapter()],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        async with asyncio.timeout(1):
            await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(health.restart_count, 1)
            self.assertGreaterEqual(projections.list_calls, 3)
        finally:
            await gateway.stop()

    async def test_recovery_failure_retries_without_new_input(self) -> None:
        application = FlakyRecoveryApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        await projections.put_projection_route(_route(thread.ref, conversation))
        gateway = ImAgentGateway(
            channels=[FakeChannelAdapter()],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(application.history_calls, 2)
            self.assertEqual(health.restart_count, 1)
            self.assertIsNone(health.last_recovery_error)
        finally:
            await gateway.stop()

    async def test_recovery_waits_for_accepted_turn_reply_correlation(
        self,
    ) -> None:
        application = AcceptanceRecoveryRaceApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        await projections.put_projection_route(_route(thread.ref, conversation))
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
        )
        await gateway.start()
        try:
            await application.history_started.wait()
            inbound = asyncio.create_task(
                channel.on_message(_inbound(conversation, "recovery-origin"))
            )
            await application.turn_persisted.wait()
            application.release_history.set()
            await asyncio.sleep(0)
            self.assertEqual(channel.sent, [])
            application.release_acceptance.set()
            await inbound
            await _wait_until(lambda: len(channel.sent) == 2)
            self.assertEqual(
                [item.reply_to for item in channel.sent],
                ["recovery-origin", "recovery-origin"],
            )
        finally:
            application.release_history.set()
            application.release_acceptance.set()
            await gateway.stop()

    async def test_restart_without_checkpoint_is_bounded_and_degraded(self) -> None:
        application = RecordingHistoryApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        route = await projections.put_projection_route(_route(thread.ref, conversation))
        gateway = ImAgentGateway(
            channels=[FakeChannelAdapter()],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
        )
        await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(
                health.last_gap,
                f"{route.route_id}:checkpoint_missing",
            )
            self.assertEqual(application.history_calls, [(3, 1)])
        finally:
            await gateway.stop()

    async def test_stale_route_does_not_block_gateway_start(self) -> None:
        thread = ThreadRef("missing-application", "thread")
        conversation = ConversationRef("fake-channel", "conversation")
        projections = InMemoryProjectionRouteRepository()
        await projections.put_projection_route(_route(thread, conversation))
        gateway = ImAgentGateway(
            channels=[],
            applications=[],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0.01,
                subscription_retry_max_seconds=0.01,
            ),
        )
        async with asyncio.timeout(1):
            await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread,
                    ProjectionWorkerState.RETRYING,
                )
            )
            health = gateway.get_projection_health(thread)
            assert health is not None
            self.assertIn(
                "Agent application is not registered",
                health.last_subscription_error or "",
            )
        finally:
            await gateway.stop()

    async def test_eager_channel_input_and_restore_do_not_strand_barrier(
        self,
    ) -> None:
        conversation = ConversationRef("eager-channel", "conversation")
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        channel = EagerInboundChannel(_inbound(conversation, "startup-message"))
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )
        await gateway.start()
        try:
            await _wait_until(lambda: len(channel.sent) == 2)
            binding = await gateway.get_binding(conversation)
            assert binding is not None
            assert binding.thread_ref is not None
            await application.send_input(
                binding.thread_ref,
                AgentInput(
                    client_message_id="after-restore",
                    content=(TextContent("external"),),
                ),
            )
            await _wait_until(lambda: len(channel.sent) == 4)
        finally:
            await gateway.stop()

    async def test_startup_failure_releases_buffered_inbound_admission(self) -> None:
        conversation = ConversationRef("eager-channel", "conversation")
        message = _inbound(conversation, "startup-message")
        channel = EagerInboundChannel(message)
        idempotency = InMemoryIdempotencyRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                idempotency=idempotency,
            ),
        )

        async def fail_reconciliation(restart_open_refs) -> None:
            del restart_open_refs
            raise RuntimeError("reconciliation failed")

        gateway._projection_runtime.reconcile_pending_requests = fail_reconciliation
        with self.assertRaisesRegex(RuntimeError, "reconciliation failed"):
            await gateway.start()
        if channel.delivery_task is not None:
            await channel.delivery_task

        self.assertEqual(
            await idempotency.claim(
                "inbound:eager-channel",
                "conversation:startup-message",
                owner_token="retry-owner",
            ),
            IdempotencyClaimStatus.ACQUIRED,
        )

    async def test_startup_rollback_rejects_new_inbound_during_claim_release(self) -> None:
        class BlockingReleaseRepository(InMemoryIdempotencyRepository):
            def __init__(self) -> None:
                super().__init__()
                self.release_started = asyncio.Event()
                self.finish_release = asyncio.Event()

            async def release(self, scope, key, *, owner_token=None) -> None:
                if key == "conversation:startup-message":
                    self.release_started.set()
                    await self.finish_release.wait()
                await super().release(scope, key, owner_token=owner_token)

        class RecordingController:
            def __init__(self) -> None:
                self.messages: list[InboundMessage] = []

            async def handle(
                self,
                message: InboundMessage,
                actions: ControllerActions,
            ) -> tuple[OutboundMessage, ...] | None:
                del actions
                self.messages.append(message)
                return ()

        conversation = ConversationRef("eager-channel", "conversation")
        channel = EagerInboundChannel(_inbound(conversation, "startup-message"))
        idempotency = BlockingReleaseRepository()
        controller = RecordingController()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                idempotency=idempotency,
            ),
            extensions=GatewayExtensions(
                controller=controller,
            ),
        )

        async def fail_reconciliation(restart_open_refs) -> None:
            del restart_open_refs
            raise RuntimeError("reconciliation failed")

        gateway._projection_runtime.reconcile_pending_requests = fail_reconciliation
        start_task = asyncio.create_task(gateway.start())
        await idempotency.release_started.wait()
        await channel.emit_message(_inbound(conversation, "rollback-message"))
        idempotency.finish_release.set()

        with self.assertRaisesRegex(RuntimeError, "reconciliation failed"):
            await start_task
        self.assertEqual(controller.messages, [])

    async def test_startup_drain_failure_keeps_racing_inbound_buffered(self) -> None:
        class BlockingReleaseRepository(InMemoryIdempotencyRepository):
            def __init__(self) -> None:
                super().__init__()
                self.release_started = asyncio.Event()
                self.finish_release = asyncio.Event()

            async def release(self, scope, key, *, owner_token=None) -> None:
                if key == "conversation:startup-message":
                    self.release_started.set()
                    await self.finish_release.wait()
                await super().release(scope, key, owner_token=owner_token)

        class FailingController:
            def __init__(self) -> None:
                self.messages: list[InboundMessage] = []

            async def handle(
                self,
                message: InboundMessage,
                actions: ControllerActions,
            ) -> tuple[OutboundMessage, ...] | None:
                del actions
                self.messages.append(message)
                if message.message_id == "startup-message":
                    raise RuntimeError("startup message failed")
                return ()

        conversation = ConversationRef("eager-channel", "conversation")
        channel = EagerInboundChannel(_inbound(conversation, "startup-message"))
        idempotency = BlockingReleaseRepository()
        controller = FailingController()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                idempotency=idempotency,
            ),
            extensions=GatewayExtensions(
                controller=controller,
            ),
        )

        start_task = asyncio.create_task(gateway.start())
        await idempotency.release_started.wait()
        await channel.emit_message(_inbound(conversation, "racing-message"))
        idempotency.finish_release.set()

        with self.assertRaisesRegex(RuntimeError, "startup message failed"):
            await start_task
        self.assertEqual(
            [message.message_id for message in controller.messages],
            ["startup-message"],
        )
        self.assertEqual(
            await idempotency.claim(
                "inbound:eager-channel",
                "conversation:racing-message",
                owner_token="retry-owner",
            ),
            IdempotencyClaimStatus.ACQUIRED,
        )

    async def test_buffered_inbound_is_refenced_after_startup_wait(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationRef("eager-channel", "conversation")
            channel = EagerInboundChannel(_inbound(conversation, "startup-message"))
            idempotency = SQLiteGatewayState(
                Path(directory) / "gateway.sqlite3",
                stale_claim_after_seconds=0,
            )
            gateway = ImAgentGateway(
                channels=[channel],
                applications=[],
                repositories=GatewayRepositories(
                    bindings=InMemoryBindingRepository(),
                    idempotency=idempotency,
                ),
            )
            reconciliation_started = asyncio.Event()
            finish_reconciliation = asyncio.Event()

            async def pause_reconciliation(restart_open_refs) -> None:
                del restart_open_refs
                reconciliation_started.set()
                await finish_reconciliation.wait()

            gateway._projection_runtime.reconcile_pending_requests = pause_reconciliation
            start_task = asyncio.create_task(gateway.start())
            await reconciliation_started.wait()
            if channel.delivery_task is not None:
                await channel.delivery_task
            self.assertEqual(
                await idempotency.claim(
                    "inbound:eager-channel",
                    "conversation:startup-message",
                    owner_token="replacement-owner",
                ),
                IdempotencyClaimStatus.ACQUIRED,
            )
            finish_reconciliation.set()
            try:
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    await start_task
                self.assertEqual(channel.sent, [])
            finally:
                if not start_task.done():
                    start_task.cancel()
                await idempotency.close()

    async def test_one_route_delivery_failure_is_isolated_and_visible(self) -> None:
        application = CountingSubscriptionApplication()
        thread = await application.create_thread()
        good_conversation = ConversationRef("good-channel", "good")
        bad_conversation = ConversationRef("bad-channel", "bad")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        for conversation in (good_conversation, bad_conversation):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=application.summary.ref,
                    thread_ref=thread.ref,
                )
            )
            await projections.put_projection_route(_route(thread.ref, conversation))
        good = FakeChannelAdapter("good-channel")
        bad = FailingChannelAdapter("bad-channel")
        gateway = ImAgentGateway(
            channels=[good, bad],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await _wait_until(
                lambda: _health_state_is(
                    gateway,
                    thread.ref,
                    ProjectionWorkerState.RUNNING,
                )
            )
            await good.on_message(_inbound(good_conversation, "origin"))
            await _wait_until(lambda: len(good.sent) == 2)
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertEqual(health.delivery_failure_count, 1)
            self.assertEqual(
                health.last_delivery_route_id,
                derive_projection_route_id(thread.ref, bad_conversation),
            )
            self.assertEqual(application.subscription_calls, 1)
            self.assertEqual(bad.attempts, 1)

            await application.send_input(
                thread.ref,
                AgentInput(client_message_id="external", content=(TextContent("more"),)),
            )
            await _wait_until(lambda: len(good.sent) == 4)
            self.assertEqual(application.subscription_calls, 1)
            self.assertEqual(bad.attempts, 1)
        finally:
            await gateway.stop()


class RecordingHistoryApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.history_calls: list[tuple[int, int]] = []

    async def execute(self, operation: ApplicationOperation):
        if isinstance(operation, GetThreadHistory):
            self.history_calls.append((operation.limit, operation.page))
        return await super().execute(operation)


class LargeTurnHistoryApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)

    async def seed_large_turn(
        self,
        thread_ref: ThreadRef,
        *,
        message_count: int,
    ) -> None:
        await self.send_input(
            thread_ref,
            AgentInput(
                client_message_id="large-turn",
                content=(TextContent("large"),),
            ),
        )
        turn = self._turn_history[thread_ref][0]
        template = turn.agent_messages[0]
        self._turn_history[thread_ref] = [
            replace(
                turn,
                agent_messages=tuple(
                    replace(
                        template,
                        agent_item_id=f"large-agent-{index}",
                        content=(TextContent(f"item-{index}"),),
                    )
                    for index in range(message_count)
                ),
            )
        ]


class BlockingHistoryApplication(RecordingHistoryApplication):
    def __init__(self) -> None:
        super().__init__()
        self.history_started = asyncio.Event()
        self.release_history = asyncio.Event()
        self._blocked_once = False

    async def execute(self, operation: ApplicationOperation):
        if isinstance(operation, GetThreadHistory) and not self._blocked_once:
            result = await super().execute(operation)
            self._blocked_once = True
            self.history_started.set()
            await self.release_history.wait()
            return result
        return await super().execute(operation)


class CountingSubscriptionApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.subscription_calls = 0
        self.active_subscriptions = 0
        self.max_active_subscriptions = 0
        self.subscription_threads: list[ThreadRef] = []

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        events = super().subscribe_thread(thread_ref, after_cursor)
        self.subscription_calls += 1
        self.subscription_threads.append(thread_ref)
        self.active_subscriptions += 1
        self.max_active_subscriptions = max(
            self.max_active_subscriptions,
            self.active_subscriptions,
        )

        async def counted() -> AsyncIterator[AgentEvent]:
            try:
                async for event in events:
                    yield event
            finally:
                self.active_subscriptions -= 1
                close = getattr(events, "aclose", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result

        return counted()

    def publish_thread_deleted(self, thread_ref: ThreadRef) -> None:
        self._publish(
            thread_ref,
            AgentEventType.THREAD_DELETED,
            "deleted-thread",
            {},
        )


class FlakySubscriptionApplication(CountingSubscriptionApplication):
    def __init__(self) -> None:
        super().__init__()
        self.pending_snapshot_calls = 0

    async def list_pending_requests(self):
        self.pending_snapshot_calls += 1
        return await super().list_pending_requests()

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if self.subscription_calls == 0:
            self.subscription_calls += 1
            raise RuntimeError("temporary subscription failure")
        return super().subscribe_thread(thread_ref, after_cursor)


class FlakyRecoveryApplication(CountingSubscriptionApplication):
    def __init__(self) -> None:
        super().__init__()
        self.history_calls = 0

    async def execute(self, operation: ApplicationOperation):
        if isinstance(operation, GetThreadHistory):
            self.history_calls += 1
            if self.history_calls == 1:
                raise RuntimeError("temporary history failure")
        return await super().execute(operation)


class AcceptanceRecoveryRaceApplication(CountingSubscriptionApplication):
    def __init__(self) -> None:
        super().__init__()
        self.history_started = asyncio.Event()
        self.release_history = asyncio.Event()
        self.turn_persisted = asyncio.Event()
        self.release_acceptance = asyncio.Event()
        self._blocked_history = False

    async def execute(self, operation: ApplicationOperation):
        if isinstance(operation, GetThreadHistory) and not self._blocked_history:
            self._blocked_history = True
            self.history_started.set()
            await self.release_history.wait()
        return await super().execute(operation)

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn:
        accepted = await super().send_input(
            thread_ref,
            message,
            continuation=continuation,
            before_dispatch=before_dispatch,
        )
        self.turn_persisted.set()
        await self.release_acceptance.wait()
        return accepted


class OverflowingAcceptanceApplication(CountingSubscriptionApplication):
    def __init__(self) -> None:
        super().__init__()
        self.turn_persisted = asyncio.Event()
        self.release_acceptance = asyncio.Event()

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn:
        accepted = await super().send_input(
            thread_ref,
            message,
            continuation=continuation,
            before_dispatch=before_dispatch,
        )
        self.turn_persisted.set()
        await self.release_acceptance.wait()
        return accepted


class ContinuationApplication(FakeAgentApplicationAdapter):
    def __init__(
        self,
        *,
        active_turn_id: str | None = None,
        replacement_turn_id: str | None = None,
        block_first_acceptance: bool = False,
    ) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.active_turn_id = active_turn_id
        self.replacement_turn_id = replacement_turn_id
        self.block_first_acceptance = block_first_acceptance
        self.native_dispatches: list[InputDisposition] = []
        self.received_continuations: list[InputContinuationPreference] = []
        self.first_native_dispatch = asyncio.Event()
        self.release_first_acceptance = asyncio.Event()

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = InputContinuationPreference.START_NEW_TURN,
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn:
        self.received_continuations.append(continuation)
        if (
            continuation is InputContinuationPreference.PREFER_ACTIVE_TURN
            and self.active_turn_id is not None
        ):
            disposition = InputDisposition.STEERED
            policy = TurnReplyCorrelationPolicy.PRESERVE_EXISTING
            expected_turn_id = self.active_turn_id
        else:
            disposition = InputDisposition.STARTED
            policy = TurnReplyCorrelationPolicy.CREATE_NEW
            expected_turn_id = None
        if before_dispatch is not None:
            await before_dispatch(
                ApplicationInputDispatch(
                    thread_ref=thread_ref,
                    client_message_id=message.client_message_id,
                    disposition=disposition,
                    correlation_policy=policy,
                    expected_turn_id=expected_turn_id,
                )
            )
        self.native_dispatches.append(disposition)
        if disposition is InputDisposition.STARTED:
            accepted_turn_id = "turn-active"
            self.active_turn_id = accepted_turn_id
            self.first_native_dispatch.set()
            if self.block_first_acceptance:
                await self.release_first_acceptance.wait()
        else:
            accepted_turn_id = self.replacement_turn_id or expected_turn_id
            assert accepted_turn_id is not None
        return AcceptedTurn(
            thread_ref=thread_ref,
            turn_id=accepted_turn_id,
            client_message_id=message.client_message_id,
            disposition=disposition,
            correlation_policy=policy,
        )


class PassiveAcceptanceApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.send_input_calls = 0
        self.accepted_input_calls = 0

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn:
        del continuation
        self.send_input_calls += 1
        if before_dispatch is not None:
            await before_dispatch(
                ApplicationInputDispatch(
                    thread_ref=thread_ref,
                    client_message_id=message.client_message_id,
                    disposition=InputDisposition.STARTED,
                    correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
                )
            )
        self.accepted_input_calls += 1
        return AcceptedTurn(
            thread_ref=thread_ref,
            turn_id=f"turn-{self.accepted_input_calls}",
            client_message_id=message.client_message_id,
        )


class FailOnceBeforeAcceptanceApplication(PassiveAcceptanceApplication):
    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn:
        if self.send_input_calls == 0:
            self.send_input_calls += 1
            raise RuntimeError("simulated pre-acceptance failure")
        return await super().send_input(
            thread_ref,
            message,
            continuation=continuation,
            before_dispatch=before_dispatch,
        )


class UnknownOutcomeApplication(PassiveAcceptanceApplication):
    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn:
        del continuation
        self.send_input_calls += 1
        if before_dispatch is not None:
            await before_dispatch(
                ApplicationInputDispatch(
                    thread_ref=thread_ref,
                    client_message_id=message.client_message_id,
                    disposition=InputDisposition.STARTED,
                    correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
                )
            )
        cancellation = asyncio.CancelledError()
        raise ApplicationInputOutcomeUnknown(
            "native input was dispatched but its outcome is unknown",
            cancellation,
        ) from cancellation


class FailingTurnCorrelationRepository(InMemoryProjectionRouteRepository):
    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation:
        raise RuntimeError("simulated correlation failure")


class FailingAcceptedInputSQLiteState(SQLiteGatewayState):
    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation:
        del correlation
        raise RuntimeError("simulated correlation failure")

    async def complete(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        del scope, key, owner_token
        raise RuntimeError("simulated terminal write failure")


class FailingOutboundCompleteOnceSQLiteState(SQLiteGatewayState):
    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        self._failed_outbound_complete = False

    async def complete(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        if scope.startswith("outbound:") and not self._failed_outbound_complete:
            self._failed_outbound_complete = True
            raise RuntimeError("simulated outer complete crash")
        await super().complete(scope, key, owner_token=owner_token)


class FailingRefreshRepository(InMemoryProjectionRouteRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_put = False

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        if self.fail_next_put:
            self.fail_next_put = False
            raise RuntimeError("simulated route refresh failure")
        return await super().put_projection_route(route)


class FailOnceListingProjectionRepository(InMemoryProjectionRouteRepository):
    def __init__(self, *, fail_on_call: int) -> None:
        super().__init__()
        self.fail_on_call = fail_on_call
        self.list_calls = 0

    async def list_projection_routes(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[ThreadProjectionRoute, ...]:
        self.list_calls += 1
        if self.list_calls == self.fail_on_call:
            raise RuntimeError("temporary projection repository failure")
        return await super().list_projection_routes(thread_ref)


class YieldingProjectionRepository(InMemoryProjectionRouteRepository):
    def __init__(self) -> None:
        super().__init__()
        self.block_next_put = False
        self.route_visible = asyncio.Event()
        self.release_put = asyncio.Event()

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        stored = await super().put_projection_route(route)
        if self.block_next_put:
            self.block_next_put = False
            self.route_visible.set()
            await self.release_put.wait()
        return stored


class EagerInboundChannel(FakeChannelAdapter):
    def __init__(self, inbound: InboundMessage) -> None:
        super().__init__(inbound.conversation_ref.channel_instance_id)
        self._inbound = inbound
        self.delivery_task: asyncio.Task[None] | None = None

    async def start(self, on_message, on_admission=None) -> None:
        await super().start(on_message, on_admission)
        self.delivery_task = asyncio.create_task(self.emit_message(self._inbound))
        await asyncio.sleep(0)


class FailingChannelAdapter(FakeChannelAdapter):
    def __init__(self, channel_instance_id: str) -> None:
        super().__init__(channel_instance_id)
        self.attempts = 0

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        self.attempts += 1
        raise RuntimeError("simulated destination failure")


class BlockingChannelAdapter(FakeChannelAdapter):
    def __init__(self, channel_instance_id: str) -> None:
        super().__init__(channel_instance_id)
        self.delivery_started = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        self.delivery_started.set()
        await self.release.wait()
        return await super().send(message)


def _observe(
    operation_id: str,
    conversation_ref: ConversationRef,
    thread_ref: ThreadRef,
) -> ObserveThread:
    return ObserveThread(
        operation_id=operation_id,
        conversation_ref=conversation_ref,
        actor="user",
        thread_ref=thread_ref,
        created_at=datetime.now(UTC),
    )


def _route(
    thread_ref: ThreadRef,
    conversation_ref: ConversationRef,
) -> ThreadProjectionRoute:
    return ThreadProjectionRoute(
        route_id=derive_projection_route_id(thread_ref, conversation_ref),
        thread_ref=thread_ref,
        conversation_ref=conversation_ref,
    )


def _inbound(
    conversation_ref: ConversationRef,
    message_id: str,
) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation_ref,
        sender="user",
        content=(TextContent("run"),),
        created_at=datetime.now(UTC),
    )


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


def _message_text(message: OutboundMessage) -> str:
    content = message.content[0]
    assert isinstance(content, TextContent)
    return content.text


def _health_state_is(
    gateway: ImAgentGateway,
    thread_ref: ThreadRef,
    state: ProjectionWorkerState,
) -> bool:
    health = gateway.get_projection_health(thread_ref)
    return health is not None and health.state is state


def _delivery_failure_count(
    gateway: ImAgentGateway,
    thread_ref: ThreadRef,
) -> int:
    health = gateway.get_projection_health(thread_ref)
    return health.delivery_failure_count if health is not None else 0


async def _wait_for_no_correlations(
    repository: InMemoryProjectionRouteRepository,
) -> None:
    async with asyncio.timeout(1):
        while await repository.list_turn_reply_correlations():
            await asyncio.sleep(0)


async def _wait_for_no_routes(
    repository: InMemoryProjectionRouteRepository,
    thread_ref: ThreadRef,
) -> None:
    async with asyncio.timeout(1):
        while await repository.list_projection_routes(thread_ref):
            await asyncio.sleep(0)

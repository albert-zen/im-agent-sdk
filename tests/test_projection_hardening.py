from __future__ import annotations

import asyncio
import inspect
import unittest
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from imagent.adapters import IdempotencyClaimStatus
from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import (
    AcceptedTurn,
    AgentEvent,
    AgentEventType,
    AgentInput,
    ApplicationOperation,
    BindConversationToThread,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    DeliveryReceipt,
    GatewayOperationFailed,
    GetThreadHistory,
    InboundMessage,
    ObserveThread,
    OutboundMessage,
    ProjectionPolicy,
    ProjectMode,
    TextContent,
    ThreadHistoryRead,
    ThreadProjectionRoute,
    ThreadRef,
    TurnReplyCorrelation,
)
from imagent.gateway import ImAgentGateway
from imagent.projections import (
    InMemoryProjectionRouteRepository,
    ProjectionWorkerState,
    derive_projection_delivery_id,
    derive_projection_route_id,
    derive_turn_reply_correlation_id,
)
from imagent.storage import InMemoryIdempotencyRepository
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class ProjectionHardeningTests(unittest.IsolatedAsyncioTestCase):
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
            bindings=InMemoryBindingRepository(),
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
            bindings=InMemoryBindingRepository(),
            baseline_history_limit=3,
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
            bindings=InMemoryBindingRepository(),
            baseline_history_limit=1,
            projection_item_limit=5,
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
            bindings=InMemoryBindingRepository(),
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
            bindings=InMemoryBindingRepository(),
            projections=projections,
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
            bindings=InMemoryBindingRepository(),
            projections=projections,
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
            bindings=bindings,
            projections=projections,
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
            bindings=bindings,
            projections=projections,
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
            bindings=bindings,
            projections=projections,
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
            health_b = second_gateway.get_projection_health(thread_b.ref)
            assert health_b is not None
            self.assertIs(health_b.state, ProjectionWorkerState.STOPPED)
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
            bindings=bindings,
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
            bindings=bindings,
            projections=projections,
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
            bindings=bindings,
            projections=projections,
            recovery_history_page_size=2,
            recovery_max_pages=2,
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
            bindings=bindings,
            projections=projections,
            recovery_history_page_size=2,
            recovery_max_pages=2,
            projection_item_limit=3,
        )
        await gateway.start()
        try:
            await _wait_until(lambda: len(channel.sent) == 3)
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertIn("checkpoint_out_of_window", health.last_gap or "")
            self.assertEqual(application.history_calls, [(2, 1), (2, 2)])
            stored = (await projections.list_projection_routes(thread.ref))[0]
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
            bindings=bindings,
            projections=projections,
            idempotency=idempotency,
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
            bindings=bindings,
            projections=projections,
            idempotency=idempotency,
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
            bindings=InMemoryBindingRepository(),
            projections=projections,
            turn_correlation_retention_seconds=60,
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
            bindings=bindings,
            projections=projections,
            turn_correlation_retention_seconds=60,
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
            bindings=bindings,
            projections=projections,
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
            bindings=bindings,
            projections=projections,
            subscription_retry_initial_seconds=0,
            subscription_retry_max_seconds=0,
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
            await application.send_input(
                thread.ref,
                AgentInput(client_message_id="external", content=(TextContent("go"),)),
            )
            await _wait_until(lambda: len(channel.sent) == 2)
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
            bindings=bindings,
            projections=projections,
            subscription_retry_initial_seconds=0,
            subscription_retry_max_seconds=0,
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
            bindings=bindings,
            projections=projections,
            subscription_retry_initial_seconds=0,
            subscription_retry_max_seconds=0,
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
            bindings=bindings,
            projections=projections,
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
            bindings=bindings,
            projections=projections,
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
            bindings=InMemoryBindingRepository(),
            projections=projections,
            subscription_retry_initial_seconds=0.01,
            subscription_retry_max_seconds=0.01,
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
            bindings=InMemoryBindingRepository(),
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
            bindings=bindings,
            projections=projections,
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
    ) -> AcceptedTurn:
        accepted = await super().send_input(thread_ref, message)
        self.turn_persisted.set()
        await self.release_acceptance.wait()
        return accepted


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

    async def start(self, on_message, on_operation) -> None:
        await super().start(on_message, on_operation)
        self.delivery_task = asyncio.create_task(on_message(self._inbound))
        await asyncio.sleep(0)


class FailingChannelAdapter(FakeChannelAdapter):
    def __init__(self, channel_instance_id: str) -> None:
        super().__init__(channel_instance_id)
        self.attempts = 0

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        self.attempts += 1
        raise RuntimeError("simulated destination failure")


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

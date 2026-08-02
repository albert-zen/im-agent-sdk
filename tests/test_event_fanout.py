from __future__ import annotations

import asyncio
import inspect
import unittest
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime

from test_gateway_vertical_slice import NativeZenClient

from imagent.applications import CodexApplicationAdapter
from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import (
    AgentEventType,
    AgentMessage,
    ApplicationRef,
    ConversationBinding,
    ConversationRef,
    InboundMessage,
    ProjectionPolicy,
    ProjectMode,
    SelectApplication,
    SupportLevel,
    TextContent,
    ThreadRef,
)
from imagent.events import EventBroadcaster, EventStreamOverflow, EventStreamReset
from imagent.gateway import GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway_startup import GatewayNotRunning, GatewayStartupOverflow
from imagent.request_correlations import InMemoryRequestCorrelationRepository
from imagent.request_projection_runtime import InteractiveRequestProjection
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class EventBroadcasterTests(unittest.IsolatedAsyncioTestCase):
    def test_capacity_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_pending must be positive"):
            EventBroadcaster[str, str](max_pending=0)

    async def test_slow_and_cancelled_subscribers_do_not_block_or_steal(self) -> None:
        broadcaster = EventBroadcaster[str, str]()
        fast = broadcaster.subscribe("thread")
        slow = broadcaster.subscribe("thread")
        cancelled = broadcaster.subscribe("thread")
        pending = asyncio.create_task(anext(cancelled))
        await asyncio.sleep(0)
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending

        broadcaster.publish("thread", "first")
        broadcaster.publish("thread", "second")

        self.assertEqual(await anext(fast), "first")
        self.assertEqual(await anext(fast), "second")
        self.assertEqual(await anext(slow), "first")
        self.assertEqual(await anext(slow), "second")
        self.assertEqual(broadcaster.subscriber_count("thread"), 2)
        await fast.aclose()
        await slow.aclose()
        self.assertEqual(broadcaster.subscriber_count("thread"), 0)

    async def test_slow_subscriber_overflow_is_explicit_and_isolated(self) -> None:
        broadcaster = EventBroadcaster[str, str](max_pending=2)
        fast = broadcaster.subscribe("thread")
        slow = broadcaster.subscribe("thread")
        unrelated = broadcaster.subscribe("other-thread")

        broadcaster.publish("thread", "first")
        self.assertEqual(await anext(fast), "first")
        broadcaster.publish("thread", "second")
        self.assertEqual(await anext(fast), "second")
        broadcaster.publish("thread", "third")
        broadcaster.publish("other-thread", "unrelated")

        self.assertEqual(slow.pending_count, 0)
        self.assertEqual(broadcaster.subscriber_count("thread"), 1)
        with self.assertRaises(EventStreamOverflow) as raised:
            await anext(slow)
        self.assertEqual(raised.exception.max_pending, 2)
        self.assertEqual(
            raised.exception.gap_code,
            "application_event_fanout_overflow",
        )
        self.assertEqual(await anext(fast), "third")
        self.assertEqual(await anext(unrelated), "unrelated")
        await fast.aclose()
        await unrelated.aclose()

    async def test_explicit_stream_reset_terminates_current_subscribers(self) -> None:
        broadcaster = EventBroadcaster[str, str](max_pending=2)
        first = broadcaster.subscribe("first")
        second = broadcaster.subscribe("second")
        broadcaster.publish("first", "discarded")

        broadcaster.fail_all(EventStreamReset)

        self.assertEqual(broadcaster.subscriber_count("first"), 0)
        self.assertEqual(broadcaster.subscriber_count("second"), 0)
        with self.assertRaises(EventStreamReset):
            await anext(first)
        with self.assertRaises(EventStreamReset):
            await anext(second)

    async def test_appserver_fans_out_multiple_messages_before_terminal_event(self) -> None:
        native = NativeZenClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            cwd="/repo",
        )
        thread_ref = ThreadRef("codex-main", "thread-1")
        first = adapter.subscribe_thread(thread_ref)
        second = adapter.subscribe_thread(thread_ref)

        for item_id, phase in (("progress-1", "commentary"), ("answer-1", "final_answer")):
            await asyncio.wait_for(
                native._notify(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {
                                "id": item_id,
                                "type": "agentMessage",
                                "phase": phase,
                                "text": phase,
                            },
                        },
                    }
                ),
                timeout=0.1,
            )
        await asyncio.wait_for(
            native._notify(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    },
                }
            ),
            timeout=0.1,
        )

        first_events = [await anext(first) for _ in range(3)]
        second_events = [await anext(second) for _ in range(3)]
        self.assertEqual(
            [event.event_id for event in first_events],
            [event.event_id for event in second_events],
        )
        self.assertEqual(
            [event.type for event in first_events],
            [
                AgentEventType.MESSAGE_COMPLETED,
                AgentEventType.MESSAGE_COMPLETED,
                AgentEventType.TURN_COMPLETED,
            ],
        )
        self.assertTrue(all(event.sequence is None for event in first_events))
        self.assertTrue(all(event.sequence_epoch is None for event in first_events))
        self.assertTrue(all(event.cursor is None for event in first_events))
        messages = [event.data["message"] for event in first_events[:2]]
        self.assertTrue(all(isinstance(message, AgentMessage) for message in messages))
        phases = [
            message.metadata["phase"] for message in messages if isinstance(message, AgentMessage)
        ]
        self.assertEqual(phases, ["commentary", "final_answer"])
        await _close(first)
        await _close(second)

    async def test_appserver_overflow_is_subscription_scoped(self) -> None:
        native = NativeZenClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            cwd="/repo",
            event_buffer_max_pending=2,
        )
        thread_ref = ThreadRef("codex-main", "thread-1")
        fast = adapter.subscribe_thread(thread_ref)
        slow = adapter.subscribe_thread(thread_ref)

        for index in range(3):
            await native._notify(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "id": f"answer-{index}",
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": str(index),
                        },
                    },
                }
            )
            message = (await anext(fast)).data["message"]
            self.assertIsInstance(message, AgentMessage)
            assert isinstance(message, AgentMessage)
            self.assertEqual(message.agent_item_id, f"answer-{index}")

        with self.assertRaises(EventStreamOverflow):
            await anext(slow)
        self.assertEqual(adapter._events.subscriber_count("thread-1"), 1)
        await _close(fast)

    async def test_appserver_connection_reset_becomes_application_event_gap(self) -> None:
        native = NativeZenClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            cwd="/repo",
        )
        events = adapter.subscribe_thread(ThreadRef("codex-main", "thread-1"))
        await native._notify(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "lost-on-reset",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "recover from native history",
                    },
                },
            }
        )

        await native.reset_connection()

        completed = await anext(events)
        self.assertEqual(completed.type, AgentEventType.MESSAGE_COMPLETED)
        with self.assertRaises(EventStreamReset) as raised:
            await anext(events)
        self.assertEqual(raised.exception.gap_code, "application_event_connection_reset")


class GatewayConcurrentTurnProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_turns_on_one_thread_do_not_steal_events(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        bindings = InMemoryBindingRepository()
        first_conversation = ConversationRef("fake-channel", "first")
        second_conversation = ConversationRef("fake-channel", "second")
        for conversation in (first_conversation, second_conversation):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("fake-agent"),
                    thread_ref=thread.ref,
                )
            )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await asyncio.gather(
                channel.on_message(_inbound(first_conversation, "first-message")),
                channel.on_message(_inbound(second_conversation, "second-message")),
            )
            async with asyncio.timeout(1):
                while len(channel.sent) < 8:
                    await asyncio.sleep(0)
        finally:
            await gateway.stop()

        replies = [message.reply_to for message in channel.sent]
        self.assertEqual(replies.count("first-message"), 2)
        self.assertEqual(replies.count("second-message"), 2)
        self.assertEqual(replies.count(None), 4)
        self.assertEqual(len({message.delivery_id for message in channel.sent}), 8)

    async def test_event_overflow_recovers_threads_independently(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(
            project_mode=ProjectMode.FLAT,
            event_buffer_max_pending=2,
        )
        overflow_thread = await application.create_thread()
        healthy_thread = await application.create_thread()
        bindings = InMemoryBindingRepository()
        overflow_conversation = ConversationRef("fake-channel", "overflow")
        healthy_conversation = ConversationRef("fake-channel", "healthy")
        for conversation, thread in (
            (overflow_conversation, overflow_thread),
            (healthy_conversation, healthy_thread),
        ):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("fake-agent"),
                    thread_ref=thread.ref,
                )
            )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(overflow_conversation, "overflow-input"))
            await channel.on_message(_inbound(healthy_conversation, "healthy-input"))
            async with asyncio.timeout(1):
                while len(channel.sent) < 4:
                    await asyncio.sleep(0)
            overflow_health = gateway.get_projection_health(overflow_thread.ref)
            healthy_health = gateway.get_projection_health(healthy_thread.ref)
            assert overflow_health is not None
            assert healthy_health is not None
            self.assertEqual(overflow_health.event_overflow_count, 1)
            self.assertEqual(
                overflow_health.last_event_overflow,
                "application_event_fanout_overflow",
            )
            self.assertFalse(overflow_health.interactive_request_recovery_degraded)
            self.assertEqual(healthy_health.event_overflow_count, 1)
        finally:
            await gateway.stop()

    async def test_appserver_transport_reset_enters_authoritative_projection_recovery(
        self,
    ) -> None:
        channel = FakeChannelAdapter()
        native = NativeZenClient()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            cwd="/repo",
        )
        thread_ref = ThreadRef("codex-main", "thread-1")
        native.threads[thread_ref.native_thread_id] = {
            "id": thread_ref.native_thread_id,
            "cwd": "/repo",
            "preview": "Recover reset output",
            "status": {"type": "idle"},
        }
        conversation = ConversationRef("fake-channel", "appserver-reset")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread_ref,
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(conversation, "before-reset"))
            async with asyncio.timeout(1):
                while gateway.get_projection_health(thread_ref) is None:
                    await asyncio.sleep(0)
            await native.reset_connection()
            async with asyncio.timeout(1):
                while True:
                    health = gateway.get_projection_health(thread_ref)
                    if (
                        health is not None
                        and health.state.value == "running"
                        and health.restart_count == 1
                    ):
                        break
                    await asyncio.sleep(0)
            self.assertEqual(
                health.last_event_gap,
                "application_event_connection_reset",
            )
            self.assertIsNone(health.last_event_overflow)
        finally:
            await gateway.stop()

    async def test_event_overflow_marks_request_recovery_degraded_without_snapshot(
        self,
    ) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(
            project_mode=ProjectMode.FLAT,
            event_buffer_max_pending=2,
        )
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
        conversation = ConversationRef("fake-channel", "degraded")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(conversation, "degraded-input"))
            async with asyncio.timeout(1):
                while True:
                    health = gateway.get_projection_health(thread.ref)
                    if health is not None and health.event_overflow_count == 1:
                        break
                    await asyncio.sleep(0)
            self.assertTrue(health.interactive_request_recovery_degraded)
        finally:
            await gateway.stop()


class GatewayStartupAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_entries_drain_in_cross_kind_fifo_order(self) -> None:
        conversation = ConversationRef("startup-channel", "conversation")
        operation = SelectApplication(
            operation_id="startup-operation",
            conversation_ref=conversation,
            actor="user-1",
            application_ref=ApplicationRef("fake-agent"),
            created_at=datetime.now(UTC),
        )
        channel = _StartupEntriesChannel(
            (
                _inbound(conversation, "first"),
                operation,
                _inbound(conversation, "second"),
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            limits=GatewayLimits(
                startup_buffer_max_pending=3,
            ),
        )
        drained: list[str] = []

        async def record_message(
            message: InboundMessage,
            *,
            idempotency_owner_token: str,
        ) -> None:
            del idempotency_owner_token
            drained.append(f"message:{message.message_id}")

        async def record_operation(operation) -> None:
            drained.append(f"operation:{operation.operation_id}")

        gateway._process_message = record_message
        gateway._handle_operation = record_operation
        await gateway.start()
        try:
            self.assertEqual(
                drained,
                ["message:first", "operation:startup-operation", "message:second"],
            )
        finally:
            await gateway.stop()

    async def test_startup_overflow_fails_and_stops_started_channel(self) -> None:
        conversation = ConversationRef("startup-channel", "conversation")
        channel = _StartupEntriesChannel(
            tuple(_inbound(conversation, f"message-{index}") for index in range(3))
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            limits=GatewayLimits(
                startup_buffer_max_pending=2,
            ),
        )

        with self.assertRaises(GatewayStartupOverflow) as raised:
            await gateway.start()
        self.assertEqual(raised.exception.max_pending, 2)
        self.assertFalse(channel.started)
        startup = gateway.diagnostics_snapshot().gateway.startup_queue
        self.assertEqual(startup.capacity, 2)
        self.assertEqual(startup.depth, 0)
        self.assertEqual(startup.overflow_count, 1)

    async def test_startup_failure_rejects_callbacks_during_teardown(self) -> None:
        active_channel = _BlockingStopStartupChannel()
        failing_channel = _FailingStartupChannel()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        gateway = ImAgentGateway(
            channels=[active_channel, failing_channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )
        starting = asyncio.create_task(gateway.start())
        await active_channel.stopping.wait()
        try:
            with self.assertRaises(GatewayNotRunning):
                await active_channel.on_message(
                    _inbound(
                        ConversationRef("active-startup-channel", "conversation"),
                        "during-failed-start",
                    )
                )
            self.assertEqual(application._inputs, [])
        finally:
            active_channel.release_stop.set()
        with self.assertRaisesRegex(RuntimeError, "simulated channel startup failure"):
            await starting


class RequestGapRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_snapshot_recovery_is_scoped_to_overflowed_thread(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        first_thread = await application.create_thread()
        second_thread = await application.create_thread()
        first_request = await application.open_approval_request(
            first_thread.ref,
            turn_id="turn-first",
        )
        await application.open_approval_request(
            second_thread.ref,
            turn_id="turn-second",
        )
        delivered = []

        async def active_routes(thread_ref):
            del thread_ref
            return ()

        async def deliver_request(routes, request) -> None:
            del routes
            delivered.append(request)

        async def cancel_request(request_ref) -> None:
            del request_ref

        projection = InteractiveRequestProjection(
            applications={"fake-agent": application},
            correlations=InMemoryRequestCorrelationRepository(),
            active_routes=active_routes,
            deliver_request=deliver_request,
            cancel_request=cancel_request,
        )

        degraded = await projection.reconcile_application_after_event_gap(
            application,
            first_thread.ref,
        )

        self.assertFalse(degraded)
        self.assertEqual(
            [request.request_ref for request in delivered],
            [first_request.request_ref],
        )


class _StartupEntriesChannel(FakeChannelAdapter):
    def __init__(self, entries) -> None:
        super().__init__("startup-channel")
        self._entries = entries

    async def start(self, on_message, on_operation, on_admission=None) -> None:
        await super().start(on_message, on_operation, on_admission)
        for entry in self._entries:
            if isinstance(entry, InboundMessage):
                await on_message(entry)
            else:
                await on_operation(entry)


class _BlockingStopStartupChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__("active-startup-channel")
        self.stopping = asyncio.Event()
        self.release_stop = asyncio.Event()

    async def stop(self) -> None:
        self.stopping.set()
        await self.release_stop.wait()
        await super().stop()


class _FailingStartupChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__("failing-startup-channel")

    async def start(self, on_message, on_operation, on_admission=None) -> None:
        await super().start(on_message, on_operation, on_admission)
        raise RuntimeError("simulated channel startup failure")


def _inbound(conversation: ConversationRef, message_id: str) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation,
        sender="user-1",
        content=(TextContent("run"),),
        created_at=datetime.now(UTC),
    )


async def _close(events) -> None:
    close = getattr(events, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result

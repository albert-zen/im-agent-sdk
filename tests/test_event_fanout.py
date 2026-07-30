from __future__ import annotations

import asyncio
import inspect
import unittest
from contextlib import suppress
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
    TextContent,
    ThreadRef,
)
from imagent.events import EventBroadcaster
from imagent.gateway import ImAgentGateway
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class EventBroadcasterTests(unittest.IsolatedAsyncioTestCase):
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
            bindings=bindings,
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

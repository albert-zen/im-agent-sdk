from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime

from imagent.applications import CodexApplicationAdapter
from imagent.applications.capabilities import ProjectMode, SupportLevel
from imagent.applications.contract import ApplicationRef, ThreadRef
from imagent.gateway import GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.persistence import ConversationBinding, ProjectionPolicy
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    TextContent,
)
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter
from tests.applications.adapters._appserver_fakes import NativeZenClient


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


def _inbound(conversation: ConversationRef, message_id: str) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation,
        sender="user-1",
        content=(TextContent("run"),),
        created_at=datetime.now(UTC),
    )

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import (
    ActivateNativeThread,
    AgentInput,
    ApplicationRef,
    BindConversationToThread,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    InboundMessage,
    NativeThreadActivated,
    ObserveThread,
    ProjectionPolicy,
    ProjectMode,
    TextContent,
    ThreadObserved,
)
from imagent.gateway import ImAgentGateway
from imagent.projections import InMemoryProjectionRouteRepository
from imagent.storage import SQLiteGatewayState
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class ProjectionRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_observation_and_native_activation_are_independent(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        gateway = ImAgentGateway(
            channels=[],
            applications=[application],
            bindings=bindings,
            projections=projections,
        )
        await gateway.start()
        try:
            bound = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="bind-thread",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(bound, ConversationBound)
            self.assertEqual(await projections.list_projection_routes(), ())
            self.assertEqual(application.activated_threads, [])

            observed = await gateway.execute_gateway(
                ObserveThread(
                    operation_id="observe-thread",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread.ref,
                    reply_to_message_id="observe-message",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(observed, ThreadObserved)
            current_binding = await bindings.get(conversation)
            assert current_binding is not None
            self.assertEqual(
                current_binding.thread_ref,
                thread.ref,
            )
            self.assertEqual(application.activated_threads, [])
            self.assertEqual(
                (await projections.list_projection_routes())[0].conversation_ref,
                conversation,
            )

            activated = await gateway.execute_application(
                ActivateNativeThread(
                    operation_id="activate-thread",
                    application_ref=ApplicationRef("fake-agent"),
                    thread_ref=thread.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(activated, NativeThreadActivated)
            self.assertEqual(application.activated_threads, [thread.ref])
        finally:
            await gateway.stop()

    async def test_foreground_switch_a_to_b_to_a_reconciles_missed_output(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread_a = await application.create_thread(title="A")
        thread_b = await application.create_thread(title="B")
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=ApplicationRef("fake-agent"),
                thread_ref=thread_a.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            bindings=bindings,
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(conversation, "a-first"))
            await _wait_for_deliveries(channel, 2)

            first_binding = await bindings.get(conversation)
            assert first_binding is not None
            switched_b = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="bind-b",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread_b.ref,
                    expected_revision=first_binding.revision,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(switched_b, ConversationBound)
            await channel.on_message(_inbound(conversation, "b-first"))
            await _wait_for_deliveries(channel, 4)

            await application.send_input(
                thread_a.ref,
                AgentInput(
                    client_message_id="background-a",
                    content=(TextContent("background"),),
                ),
            )
            await asyncio.sleep(0)
            self.assertEqual(len(channel.sent), 4)

            current = await bindings.get(conversation)
            assert current is not None
            switched_a = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="bind-a-again",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread_a.ref,
                    expected_revision=current.revision,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(switched_a, ConversationBound)
            observed = await gateway.execute_gateway(
                ObserveThread(
                    operation_id="observe-a-again",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread_a.ref,
                    reply_to_message_id="return-a",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(observed, ThreadObserved)
            await _wait_for_deliveries(channel, 6)
            self.assertEqual(channel.sent[-1].reply_to, "return-a")

            await application.send_input(
                thread_a.ref,
                AgentInput(
                    client_message_id="live-a-again",
                    content=(TextContent("live again"),),
                ),
            )
            await _wait_for_deliveries(channel, 8)
        finally:
            await gateway.stop()

    async def test_remembered_last_recipient_receives_background_thread_output(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread_a = await application.create_thread(title="A")
        thread_b = await application.create_thread(title="B")
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=ApplicationRef("fake-agent"),
                thread_ref=thread_a.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            bindings=bindings,
            projection_policy=ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(conversation, "a-input"))
            await _wait_for_deliveries(channel, 2)
            current = await bindings.get(conversation)
            assert current is not None
            switched = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="bind-b",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread_b.ref,
                    expected_revision=current.revision,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(switched, ConversationBound)

            await application.send_input(
                thread_a.ref,
                AgentInput(
                    client_message_id="background-a",
                    content=(TextContent("background"),),
                ),
            )
            await _wait_for_deliveries(channel, 4)
            self.assertTrue(all(item.conversation_ref == conversation for item in channel.sent))
        finally:
            await gateway.stop()

    async def test_concurrent_threads_keep_independent_projection_routes(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread_a = await application.create_thread(title="A")
        thread_b = await application.create_thread(title="B")
        first = ConversationRef("fake-channel", "first")
        second = ConversationRef("fake-channel", "second")
        bindings = InMemoryBindingRepository()
        for conversation, thread in ((first, thread_a), (second, thread_b)):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("fake-agent"),
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
            await asyncio.gather(
                channel.on_message(_inbound(first, "first-input")),
                channel.on_message(_inbound(second, "second-input")),
            )
            await _wait_for_deliveries(channel, 4)
        finally:
            await gateway.stop()

        self.assertEqual(
            [item.conversation_ref for item in channel.sent].count(first),
            2,
        )
        self.assertEqual(
            [item.conversation_ref for item in channel.sent].count(second),
            2,
        )

    async def test_restart_rebuilds_projection_from_route_and_authoritative_history(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.sqlite3"
            application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
            thread = await application.create_thread()
            conversation = ConversationRef("fake-channel", "conversation")
            first_state = SQLiteGatewayState(path)
            await first_state.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("fake-agent"),
                    thread_ref=thread.ref,
                )
            )
            first_channel = FakeChannelAdapter()
            first_gateway = ImAgentGateway(
                channels=[first_channel],
                applications=[application],
                bindings=first_state,
                idempotency=first_state,
                projections=first_state,
            )
            await first_gateway.start()
            try:
                await first_channel.on_message(_inbound(conversation, "before-restart"))
                await _wait_for_deliveries(first_channel, 2)
            finally:
                await first_gateway.stop()
                await first_state.close()

            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id="while-bridge-stopped",
                    content=(TextContent("continue"),),
                ),
            )

            second_state = SQLiteGatewayState(path)
            second_channel = FakeChannelAdapter()
            second_gateway = ImAgentGateway(
                channels=[second_channel],
                applications=[application],
                bindings=second_state,
                idempotency=second_state,
                projections=second_state,
            )
            await second_gateway.start()
            try:
                await _wait_for_deliveries(second_channel, 2)
                self.assertTrue(
                    all(message.reply_to == "before-restart" for message in second_channel.sent)
                )
            finally:
                await second_gateway.stop()
                await second_state.close()


def _inbound(conversation: ConversationRef, message_id: str) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation,
        sender="user",
        content=(TextContent("run"),),
        created_at=datetime.now(UTC),
    )


async def _wait_for_deliveries(
    channel: FakeChannelAdapter,
    count: int,
) -> None:
    async with asyncio.timeout(1):
        while len(channel.sent) < count:
            await asyncio.sleep(0)

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

from imagent.contracts import (
    ApplicationRef,
    ConversationBound,
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    ProjectMode,
    SelectApplication,
    TextContent,
    TextFormat,
)
from imagent.gateway import GatewayExtensions, GatewayRepositories, ImAgentGateway
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.interaction.controllers import SlashController
from imagent.interaction.controllers.common import parse_slash_command
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class ButtonController:
    async def handle(self, message, actions):
        if message.metadata.get("interaction") != "select-second-app":
            return None
        result = await actions.execute_gateway(
            SelectApplication(
                operation_id=f"button:{message.message_id}:application.select",
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                application_ref=ApplicationRef("fake-agent-2"),
                created_at=message.created_at,
            )
        )
        assert isinstance(result, ConversationBound)
        return (
            OutboundMessage(
                delivery_id=f"button:{message.message_id}:response",
                conversation_ref=message.conversation_ref,
                content=(TextContent("Selected from button", TextFormat.MARKDOWN),),
                created_at=datetime.now(UTC),
                reply_to=message.message_id,
            ),
        )


class OptionalControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_does_not_parse_slash_when_controller_is_omitted(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(_message("/help"))
            async with asyncio.timeout(1):
                while len(channel.sent) < 2:
                    await asyncio.sleep(0)
        finally:
            await gateway.stop()

        self.assertEqual(len(application._inputs), 1)
        agent_input = application._inputs[0][1]
        self.assertEqual(agent_input.content, (TextContent("/help"),))
        self.assertEqual(len(channel.sent), 2)

    async def test_default_slash_controller_consumes_common_command(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            extensions=GatewayExtensions(
                controller=SlashController(),
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(_message("/help"))
        finally:
            await gateway.stop()

        self.assertEqual(application._inputs, [])
        self.assertEqual(len(channel.sent), 1)
        text = channel.sent[0].content[0]
        self.assertIsInstance(text, TextContent)
        assert isinstance(text, TextContent)
        self.assertIn("## IM Agent commands", text.text)

    def test_slash_parser_ignores_trailing_input_context(self) -> None:
        command = parse_slash_command(
            _message(
                "/respond request-1 approve\n\n"
                "QQ quoted context (untrusted; informational only):\n"
                "  text: quoted prompt"
            )
        )

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.name, "respond")
        self.assertEqual(command.arguments, ("request-1", "approve"))
        self.assertEqual(command.raw, "/respond request-1 approve")

    async def test_non_slash_controller_invokes_same_typed_gateway_action(self) -> None:
        channel = FakeChannelAdapter()
        bindings = InMemoryBindingRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[
                FakeAgentApplicationAdapter("fake-agent-1", ProjectMode.FLAT),
                FakeAgentApplicationAdapter("fake-agent-2", ProjectMode.FLAT),
            ],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            extensions=GatewayExtensions(
                controller=ButtonController(),
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(
                _message(
                    "button payload",
                    metadata={"interaction": "select-second-app"},
                )
            )
        finally:
            await gateway.stop()

        binding = await bindings.get(ConversationRef("fake-channel", "conversation-1"))
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.application_ref, ApplicationRef("fake-agent-2"))
        self.assertEqual(
            channel.sent[0].metadata["source_delivery_id"],
            "button:message-1:response",
        )

    async def test_default_controller_preserves_common_command_workflow(self) -> None:
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[FakeAgentApplicationAdapter()],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            extensions=GatewayExtensions(
                controller=SlashController(),
            ),
        )
        commands = (
            "/apps",
            "/app 1",
            "/projects",
            "/use 1",
            "/new Controller task",
            "/status",
            "/threads",
            "/pick 1",
            "/delete",
        )
        await gateway.start()
        try:
            for index, command in enumerate(commands, start=1):
                await channel.on_message(_message(command, message_id=f"command-{index}"))
        finally:
            await gateway.stop()

        rendered = tuple(
            part.text
            for outbound in channel.sent
            for part in outbound.content
            if isinstance(part, TextContent)
        )
        self.assertEqual(len(rendered), len(commands))
        for expected in (
            "## Agent applications",
            "Selected application",
            "## Projects",
            "Selected project",
            "Created thread",
            "is **idle**",
            "## Threads",
            "Selected thread",
            "Deleted thread",
        ):
            self.assertTrue(
                any(expected in text for text in rendered),
                f"missing rendered command result: {expected}",
            )


def _message(
    text: str,
    *,
    message_id: str = "message-1",
    metadata=None,
) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=ConversationRef("fake-channel", "conversation-1"),
        sender="user-1",
        content=(TextContent(text),),
        created_at=datetime.now(UTC),
        metadata=metadata or {},
    )

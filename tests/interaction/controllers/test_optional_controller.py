from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

from imagent.applications.capabilities import ProjectMode
from imagent.gateway import GatewayExtensions
from imagent.gateway.composition import _GatewayRuntimeDependencies
from imagent.gateway.orchestration import _GatewayRuntime
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.gateway.routing.bindings import ConversationBinding
from imagent.interaction.controllers.common import parse_slash_command
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    TextContent,
)
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter
from tests._command_support import common_command_registry


class OptionalControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_does_not_parse_slash_when_controller_is_omitted(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        bindings = InMemoryBindingRepository()
        thread = await application.create_thread(application.default_project_ref)
        await bindings.put(
            ConversationBinding(
                conversation_ref=ConversationRef("fake-channel", "conversation-1"),
                application_ref=application.summary.ref,
                project_ref=application.default_project_ref,
                thread_ref=thread.ref,
            )
        )
        gateway = _GatewayRuntime(
            channels=[channel],
            applications=[application],
            repositories=_GatewayRuntimeDependencies(
                bindings=bindings,
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

    async def test_legacy_gateway_rejects_controller_without_substituting_actions(
        self,
    ) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        gateway = _GatewayRuntime(
            channels=[channel],
            applications=[application],
            repositories=_GatewayRuntimeDependencies(
                bindings=InMemoryBindingRepository(),
            ),
            extensions=GatewayExtensions(
                controller=common_command_registry(),
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "coherent GatewayStore session"):
            await gateway.start()
        self.assertFalse(channel.started)

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

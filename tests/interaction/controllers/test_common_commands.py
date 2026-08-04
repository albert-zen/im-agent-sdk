from __future__ import annotations

import unittest
from datetime import UTC, datetime

from imagent.applications.capabilities import ProjectMode
from imagent.applications.operations import (
    ApplicationOperation,
    ApplicationOperationResult,
    ListProjects,
)
from imagent.contracts import (
    GatewayOperation,
    GatewayOperationResult,
)
from imagent.gateway import GatewayExtensions, GatewayRepositories, ImAgentGateway
from imagent.gateway.persistence import ConversationBinding
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.interaction.controllers import (
    CommandExecutionSafety,
    CommandHandlerActions,
    CommandInvocation,
    CommandInvocationFacts,
    CommandRegistry,
    CommandRegistryLimits,
    CommandResult,
    ControllerActions,
    register_common_commands,
)
from imagent.interaction.messages import ConversationRef, InboundMessage, TextContent
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _ProductActions(ControllerActions):
    def __init__(self) -> None:
        self.fences: list[CommandInvocationFacts] = []

    async def execute_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        raise AssertionError(operation)

    async def execute_gateway(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        raise AssertionError(operation)

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None:
        del conversation_ref
        return None

    async def enter_effectful_command(self, invocation: CommandInvocationFacts) -> None:
        self.fences.append(invocation)


class _RecordingApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.MANAGED)
        self.operations: list[ApplicationOperation] = []

    async def execute(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        self.operations.append(operation)
        return await super().execute(operation)


class CommonCommandCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_common_and_product_commands_share_one_registry(self) -> None:
        registry = CommandRegistry()
        register_common_commands(registry, include=("help",))
        service_calls: list[str] = []

        @registry.command("credits", safety=CommandExecutionSafety.EFFECTFUL)
        async def credits(
            invocation: CommandInvocation,
            actions: CommandHandlerActions,
        ) -> CommandResult:
            self.assertFalse(hasattr(actions, "enter_effectful_command"))
            service_calls.append(invocation.actor)
            return CommandResult.text("42 credits")

        registry.freeze()
        actions = _ProductActions()
        help_outputs = await registry.handle(_message("/help"), actions)
        product_outputs = await registry.handle(_message("/credits"), actions)
        assert help_outputs is not None and product_outputs is not None
        self.assertIn("IM Agent commands", _text(help_outputs))
        self.assertEqual(_text(product_outputs), "42 credits")
        self.assertEqual(service_calls, ["user-a"])
        self.assertEqual(len(actions.fences), 1)

    async def test_consumer_can_omit_common_new_without_shadowing(self) -> None:
        registry = CommandRegistry()
        register_common_commands(registry, include=("help",))

        @registry.command("new")
        async def product_new(
            invocation: CommandInvocation,
            actions: CommandHandlerActions,
        ) -> CommandResult:
            del invocation, actions
            return CommandResult.text("product new")

        registry.freeze()
        outputs = await registry.handle(_message("/new"), _ProductActions())
        assert outputs is not None
        self.assertEqual(_text(outputs), "product new")

    async def test_read_only_listing_does_not_create_application_binding(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
        bindings = InMemoryBindingRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=bindings),
            extensions=GatewayExtensions(controller=_default_registry()),
        )
        await gateway.start()
        try:
            await channel.emit_message(_message("/projects", channel_id="fake-channel"))
            self.assertIsNone(await bindings.get(_conversation("fake-channel")))
        finally:
            await gateway.stop()

    async def test_scoped_operation_ids_do_not_collide_across_conversations(self) -> None:
        first = FakeChannelAdapter("channel-a")
        second = FakeChannelAdapter("channel-b")
        application = _RecordingApplication()
        gateway = ImAgentGateway(
            channels=[first, second],
            applications=[application],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            extensions=GatewayExtensions(controller=_default_registry()),
        )
        await gateway.start()
        try:
            await first.emit_message(_message("/projects", channel_id="channel-a"))
            await second.emit_message(_message("/projects", channel_id="channel-b"))
        finally:
            await gateway.stop()
        operations = [item for item in application.operations if isinstance(item, ListProjects)]
        self.assertEqual(len(operations), 2)
        self.assertNotEqual(operations[0].operation_id, operations[1].operation_id)
        self.assertTrue(all(len(item.operation_id) <= 512 for item in operations))

    async def test_selection_views_evict_by_capacity_and_expire_by_lifetime(self) -> None:
        now = [0.0]
        first = FakeChannelAdapter("channel-a")
        second = FakeChannelAdapter("channel-b")
        application = _RecordingApplication()
        registry = CommandRegistry(
            limits=CommandRegistryLimits(view_cache_capacity=1, view_cache_ttl_seconds=1.0)
        )
        register_common_commands(registry, clock=lambda: now[0])
        registry.freeze()
        gateway = ImAgentGateway(
            channels=[first, second],
            applications=[application],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            extensions=GatewayExtensions(controller=registry),
        )
        await gateway.start()
        try:
            await first.emit_message(
                _message("/projects", channel_id="channel-a", message_id="a-list-1")
            )
            await second.emit_message(
                _message("/projects", channel_id="channel-b", message_id="b-list-1")
            )
            await first.emit_message(
                _message("/use 1", channel_id="channel-a", message_id="a-use-1")
            )
            self.assertEqual(_project_list_count(application), 3)

            await first.emit_message(
                _message("/projects", channel_id="channel-a", message_id="a-list-2")
            )
            now[0] = 2.0
            await first.emit_message(
                _message("/use 1", channel_id="channel-a", message_id="a-use-2")
            )
            self.assertEqual(_project_list_count(application), 5)
        finally:
            await gateway.stop()


def _default_registry() -> CommandRegistry:
    registry = CommandRegistry()
    register_common_commands(registry)
    registry.freeze()
    return registry


def _conversation(channel_id: str = "channel-a") -> ConversationRef:
    return ConversationRef(channel_id, "conversation-a")


def _message(
    text: str,
    *,
    channel_id: str = "channel-a",
    message_id: str = "same-native-message",
) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=_conversation(channel_id),
        sender="user-a",
        content=(TextContent(text),),
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
    )


def _text(outputs) -> str:
    return "\n".join(
        item.text for output in outputs for item in output.content if isinstance(item, TextContent)
    )


def _project_list_count(application: _RecordingApplication) -> int:
    return sum(isinstance(item, ListProjects) for item in application.operations)


if __name__ == "__main__":
    unittest.main()

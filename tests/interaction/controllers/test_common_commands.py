from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import cast

from imagent.applications.capabilities import ProjectMode, ThreadDeletionCapability
from imagent.applications.contract import ProjectRef, ThreadRef
from imagent.applications.operations import ThreadDeletionMode
from imagent.applications.requests import ApprovalResponse, RequestRef, RequestResponse
from imagent.gateway.actions import ActionValue, ConversationActions
from imagent.gateway.outcomes import Succeeded
from imagent.gateway.persistence.state_contracts import ConversationBinding
from imagent.interaction.controllers import (
    CommandExecutionSafety,
    CommandInvocation,
    CommandInvocationFacts,
    CommandLimits,
    CommandRegistry,
    CommandResult,
    include_common_commands,
)
from imagent.interaction.controllers.common import _BoundedViewCache
from imagent.interaction.messages import ConversationRef, InboundMessage, TextContent
from imagent.testing import FakeAgentApplicationAdapter


class _ProductActions:
    def __init__(self) -> None:
        self.fences: list[CommandInvocationFacts] = []

    async def _enter_effectful_command(self, invocation: CommandInvocationFacts) -> None:
        self.fences.append(invocation)


class _CommonActions(_ProductActions):
    def __init__(
        self,
        *,
        bound_thread: bool = False,
        conversation: ConversationRef | None = None,
    ) -> None:
        super().__init__()
        adapter = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
        self.application = adapter.summary
        self.project = ProjectRef(self.application.ref.application_instance_id, "project-a")
        self.thread = ThreadRef(self.project, "thread-a")
        self.binding = ConversationBinding(
            conversation_ref=conversation or _conversation(),
            application_ref=self.application.ref,
            project_ref=self.project,
            thread_ref=self.thread if bound_thread else None,
            generation=4,
        )
        self.calls: list[tuple[str, object]] = []

    async def list_applications(self):
        self.calls.append(("list_applications", None))
        return Succeeded((self.application,))

    async def get_binding(self):
        self.calls.append(("get_binding", None))
        return self.binding

    async def create_and_bind_thread(
        self,
        project_ref: ProjectRef,
        *,
        action_id: str,
        title: str | None = None,
        initial_context=(),
    ):
        self.calls.append(("create_and_bind_thread", (project_ref, action_id, title)))
        return Succeeded(ActionValue(ref=self.thread, binding_generation=5))

    async def observe_thread(
        self,
        thread_ref: ThreadRef,
        *,
        action_id: str,
        reply_to_message_id: str | None = None,
    ):
        self.calls.append(("observe_thread", (thread_ref, action_id, reply_to_message_id)))
        return Succeeded(ActionValue(ref=thread_ref, route_id="route-a"))

    async def delete_thread(
        self,
        thread_ref: ThreadRef,
        *,
        mode: ThreadDeletionMode,
        action_id: str,
    ):
        self.calls.append(("delete_thread", (thread_ref, mode, action_id)))
        return Succeeded(ActionValue(ref=thread_ref))

    async def respond_request(
        self,
        request_ref: RequestRef,
        response: RequestResponse,
        *,
        action_id: str,
    ):
        self.calls.append(("respond_request", (request_ref, response, action_id)))
        return Succeeded(ActionValue(ref=request_ref))


class CommonCommandCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_common_and_product_commands_share_one_local_registry_and_surface(self) -> None:
        registry = CommandRegistry()
        include_common_commands(registry, names=("help",))
        received: list[object] = []

        @registry.command("credits", safety=CommandExecutionSafety.EFFECTFUL)
        async def credits(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            del invocation
            received.append(actions)
            return CommandResult.text("42 credits")

        registry.freeze()
        actions = _ProductActions()
        help_outputs = await registry.handle(_message("/help"), _surface(actions))
        product_outputs = await registry.handle(_message("/credits"), _surface(actions))
        assert help_outputs is not None and product_outputs is not None
        self.assertIn("IM Agent commands", _text(help_outputs))
        self.assertEqual(_text(product_outputs), "42 credits")
        self.assertEqual(received, [actions])
        self.assertEqual(len(actions.fences), 1)

    async def test_consumer_can_omit_common_new_without_shadowing(self) -> None:
        registry = CommandRegistry()
        include_common_commands(registry, names=("help",))

        @registry.command("new")
        async def product_new(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            del invocation, actions
            return CommandResult.text("product new")

        registry.freeze()
        outputs = await registry.handle(_message("/new"), _surface(_ProductActions()))
        assert outputs is not None
        self.assertEqual(_text(outputs), "product new")

    async def test_unbound_read_does_not_implicitly_select_single_application(self) -> None:
        actions = _CommonActions()
        actions.binding = ConversationBinding(_conversation(), generation=0)
        outputs = await _default_registry().handle(_message("/projects"), _surface(actions))
        assert outputs is not None
        self.assertIn("Choose an Agent application", _text(outputs))
        self.assertNotIn("select_application", {name for name, _ in actions.calls})

    async def test_new_uses_owned_workflow_then_observation(self) -> None:
        actions = _CommonActions()
        outputs = await _default_registry().handle(
            _message("/new Scoped task"),
            _surface(actions),
        )
        assert outputs is not None
        self.assertIn("Created thread", _text(outputs))
        names = [name for name, _ in actions.calls]
        self.assertEqual(names.count("create_and_bind_thread"), 1)
        self.assertEqual(names.count("observe_thread"), 1)
        self.assertNotIn("create_thread", names)
        self.assertNotIn("bind_thread", names)

    async def test_delete_never_silently_clears_binding(self) -> None:
        actions = _CommonActions(bound_thread=True)
        self.assertIs(
            actions.application.capabilities.threads.deletion,
            ThreadDeletionCapability.PERMANENT,
        )
        outputs = await _default_registry().handle(_message("/delete"), _surface(actions))
        assert outputs is not None
        self.assertIn("Deleted thread", _text(outputs))
        names = [name for name, _ in actions.calls]
        self.assertEqual(names.count("delete_thread"), 1)
        self.assertNotIn("clear_thread", names)

    async def test_effect_ids_include_conversation_and_request_response_is_scoped(self) -> None:
        first_conversation = ConversationRef("channel-a", "conversation-a")
        second_conversation = ConversationRef("channel-a", "conversation-b")
        first_actions = _CommonActions(conversation=first_conversation)
        second_actions = _CommonActions(conversation=second_conversation)
        registry = _default_registry()

        await registry.handle(
            _message("/new Task", conversation=first_conversation),
            _surface(first_actions),
        )
        await registry.handle(
            _message("/new Task", conversation=second_conversation),
            _surface(second_actions),
        )
        first_id = _call_action_id(first_actions, "create_and_bind_thread")
        second_id = _call_action_id(second_actions, "create_and_bind_thread")
        self.assertNotEqual(first_id, second_id)

        outputs = await registry.handle(
            _message(
                "/respond app-a request-a approve",
                message_id="request-message",
                conversation=first_conversation,
            ),
            _surface(first_actions),
        )
        assert outputs is not None
        self.assertIn("Response submitted", _text(outputs))
        request_call = next(
            value for name, value in first_actions.calls if name == "respond_request"
        )
        assert isinstance(request_call, tuple)
        request_ref, response, action_id = request_call
        assert isinstance(request_ref, RequestRef)
        self.assertEqual(request_ref.native_request_id, "request-a")
        self.assertEqual(response, ApprovalResponse("approve"))
        self.assertIsInstance(action_id, str)

    def test_selection_view_cache_expires_and_evicts_oldest_conversation(self) -> None:
        now = [10.0]
        limits = CommandLimits(view_cache_capacity=2, view_cache_ttl_seconds=5.0)
        cache = _BoundedViewCache[str](
            capacity=limits.view_cache_capacity,
            ttl_seconds=limits.view_cache_ttl_seconds,
            clock=lambda: now[0],
        )
        first = ConversationRef("channel-a", "first")
        second = ConversationRef("channel-a", "second")
        third = ConversationRef("channel-a", "third")
        cache.put(first, "first")
        cache.put(second, "second")
        cache.put(third, "third")
        self.assertIsNone(cache.get(first))
        self.assertEqual(cache.get(second), "second")
        now[0] = 16.0
        self.assertIsNone(cache.get(second))
        self.assertIsNone(cache.get(third))


def _default_registry() -> CommandRegistry:
    registry = CommandRegistry()
    include_common_commands(registry)
    registry.freeze()
    return registry


def _surface(value: object) -> ConversationActions:
    """Keep focused command fakes outside the public nominal action contract."""

    return cast(ConversationActions, value)


def _call_action_id(actions: _CommonActions, name: str) -> str:
    value = next(value for call_name, value in actions.calls if call_name == name)
    assert isinstance(value, tuple)
    action_id = value[1]
    assert isinstance(action_id, str)
    return action_id


def _conversation() -> ConversationRef:
    return ConversationRef("channel-a", "conversation-a")


def _message(
    text: str,
    *,
    message_id: str = "message-a",
    conversation: ConversationRef | None = None,
) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation or _conversation(),
        sender="user-a",
        content=(TextContent(text),),
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
    )


def _text(outputs) -> str:
    return "\n".join(
        item.text for output in outputs for item in output.content if isinstance(item, TextContent)
    )


if __name__ == "__main__":
    unittest.main()

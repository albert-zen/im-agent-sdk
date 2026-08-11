from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

from imagent.applications.capabilities import ProjectMode
from imagent.gateway import GatewayExtensions
from imagent.gateway.actions import ConversationActions
from imagent.gateway.composition import _GatewayRuntimeDependencies
from imagent.gateway.orchestration import _GatewayRuntime
from imagent.gateway.persistence import InMemoryIdempotencyRepository
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.interaction.controllers import (
    CommandArgumentContract,
    CommandDefinition,
    CommandExecutionSafety,
    CommandHandler,
    CommandInvocation,
    CommandInvocationFacts,
    CommandLimits,
    CommandRegistry,
    CommandRegistryError,
    CommandRegistryFailureCode,
    CommandRegistryNotFrozenError,
    CommandResult,
    CommandResultError,
)
from imagent.interaction.media import AttachmentContent, LocalPath
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    TextContent,
)
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _Actions:
    def __init__(self, *, fence_error: Exception | None = None) -> None:
        self.events: list[tuple[str, object]] = []
        self.fence_error = fence_error

    async def execute_application(self, operation):
        self.events.append(("application", operation))
        raise AssertionError("application action was not expected")

    async def execute_gateway(self, operation):
        self.events.append(("gateway", operation))
        raise AssertionError("gateway action was not expected")

    async def get_binding(self, conversation_ref):
        self.events.append(("binding", conversation_ref))
        return None

    async def _enter_effectful_command(self, invocation: CommandInvocationFacts) -> None:
        self.events.append(("fence", invocation))
        if self.fence_error is not None:
            raise self.fence_error


class _BlockingFenceActions(_Actions):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def _enter_effectful_command(self, invocation: CommandInvocationFacts) -> None:
        self.events.append(("fence", invocation))
        self.entered.set()
        await self.release.wait()


def _surface(value: object) -> ConversationActions:
    """Keep focused registry fakes outside the public nominal action contract."""

    return cast(ConversationActions, value)


class CommandRegistryDefinitionTests(unittest.TestCase):
    def test_limits_reject_non_positive_and_non_finite_values(self) -> None:
        defaults = CommandLimits()
        invalid = {
            "max_commands": 0,
            "max_aliases": False,
            "max_aliases_per_command": -1,
            "max_command_name_length": 0,
            "max_help_text_length": 0,
            "max_input_line_length": 0,
            "max_arguments": 0,
            "max_argument_length": 0,
            "max_result_items": 0,
            "max_result_text_characters": 0,
            "max_concurrency": 0,
            "handler_timeout_seconds": float("inf"),
            "cancellation_join_timeout_seconds": float("nan"),
            "view_cache_capacity": 0,
            "view_cache_ttl_seconds": 0.0,
        }
        for name, value in invalid.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                replace(defaults, **{name: value})

    def test_instances_decorator_collisions_and_freeze_are_local(self) -> None:
        first = CommandRegistry()
        second = CommandRegistry()

        @first.command("Echo", aliases=("say",))
        async def echo(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            del actions
            return CommandResult.text(" ".join(invocation.arguments))

        second.register(CommandDefinition("echo", echo))
        with self.assertRaisesRegex(CommandRegistryError, "collision"):
            first.register(CommandDefinition("other", echo, aliases=("SAY",)))
        first.freeze()
        first.freeze()
        with self.assertRaises(CommandRegistryError):
            first.register(CommandDefinition("late", echo))
        second.register(CommandDefinition("other", echo))
        second.freeze()
        self.assertTrue(first.frozen)
        self.assertTrue(second.frozen)

    def test_unfrozen_and_empty_registry_fail_before_runtime(self) -> None:
        registry = CommandRegistry()
        with self.assertRaises(CommandRegistryNotFrozenError):
            registry.validate_startup()
        with self.assertRaisesRegex(CommandRegistryError, "at least one"):
            registry.freeze()

    def test_definition_and_table_bounds_fail_during_registration(self) -> None:
        async def handler(invocation, actions):
            del invocation, actions
            return CommandResult()

        limits = CommandLimits(
            max_commands=1,
            max_aliases=1,
            max_aliases_per_command=1,
            max_command_name_length=4,
            max_help_text_length=5,
            max_arguments=1,
        )
        registry = CommandRegistry(limits)
        registry.register(
            CommandDefinition(
                "one",
                handler,
                aliases=("uno",),
                arguments=CommandArgumentContract(0, 1),
                summary="short",
            )
        )
        with self.assertRaisesRegex(CommandRegistryError, "entry capacity"):
            registry.register(CommandDefinition("two", handler))

        cases = (
            (CommandDefinition("toolong", handler), "command names"),
            (CommandDefinition("two", handler, aliases=("a", "b")), "too many aliases"),
            (CommandDefinition("two", handler, summary="longer"), "summary"),
            (
                CommandDefinition(
                    "two",
                    handler,
                    arguments=CommandArgumentContract(0, 2),
                ),
                "argument contract",
            ),
        )
        for definition, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(CommandRegistryError, error):
                CommandRegistry(limits).register(definition)

        alias_registry = CommandRegistry(replace(limits, max_commands=2, max_aliases_per_command=2))
        alias_registry.register(CommandDefinition("one", handler, aliases=("a",)))
        with self.assertRaisesRegex(CommandRegistryError, "alias capacity"):
            alias_registry.register(CommandDefinition("two", handler, aliases=("b",)))


class CommandRegistryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_non_empty_line_alias_and_unknown_are_bounded(self) -> None:
        seen: list[CommandInvocation] = []

        async def handler(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            del actions
            seen.append(invocation)
            return CommandResult.text("ok")

        registry = CommandRegistry()
        registry.register(
            CommandDefinition(
                "echo",
                handler,
                aliases=("say",),
                arguments=CommandArgumentContract(1, 1),
            )
        )
        registry.freeze()
        actions = _Actions()
        outputs = await registry.handle(
            _message("\n  /SAY one\nignored two"),
            _surface(actions),
        )
        assert outputs is not None
        self.assertEqual(_text(outputs), "ok")
        self.assertEqual(seen[0].command_name, "echo")
        self.assertEqual(seen[0].arguments, ("one",))
        self.assertIsNone(await registry.handle(_message("ordinary"), _surface(actions)))
        unknown = await registry.handle(_message("/missing"), _surface(actions))
        assert unknown is not None
        self.assertIn("Unknown command", _text(unknown))
        self.assertEqual(
            registry.diagnostic_facts().last_failure_code,
            CommandRegistryFailureCode.UNKNOWN_COMMAND,
        )

    async def test_effectful_fence_precedes_handler_and_is_hidden(self) -> None:
        actions = _Actions()

        async def handler(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            self.assertIs(actions, actions_events)
            actions_events.events.append(("handler", invocation))
            return CommandResult.text("done")

        actions_events = actions
        registry = CommandRegistry()
        registry.register(
            CommandDefinition(
                "mutate",
                handler,
                safety=CommandExecutionSafety.EFFECTFUL,
            )
        )
        registry.freeze()
        outputs = await registry.handle(_message("/mutate"), _surface(actions))
        assert outputs is not None
        self.assertEqual(_text(outputs), "done")
        self.assertEqual([event[0] for event in actions.events], ["fence", "handler"])
        invocation = actions.events[0][1]
        assert isinstance(invocation, CommandInvocation)
        self.assertEqual(invocation.conversation_ref, _conversation())
        self.assertEqual(invocation.message_id, "message-1")

    async def test_fence_failure_prevents_handler_invocation(self) -> None:
        called = False

        async def handler(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            del invocation, actions
            nonlocal called
            called = True
            return CommandResult()

        registry = CommandRegistry()
        registry.register(
            CommandDefinition(
                "mutate",
                handler,
                safety=CommandExecutionSafety.EFFECTFUL,
            )
        )
        registry.freeze()
        with self.assertRaisesRegex(RuntimeError, "fence failed"):
            await registry.handle(
                _message("/mutate"),
                _surface(_Actions(fence_error=RuntimeError("fence failed"))),
            )
        self.assertFalse(called)

    async def test_invalid_result_is_explicit(self) -> None:
        async def handler(invocation, actions):
            del invocation, actions
            return "not a result"

        registry = CommandRegistry()
        registry.register(CommandDefinition("bad", cast(CommandHandler, handler)))
        registry.freeze()
        with self.assertRaises(CommandResultError):
            await registry.handle(_message("/bad"), _surface(_Actions()))

    async def test_parser_and_result_bounds_fail_at_their_boundary(self) -> None:
        async def handler(invocation, actions):
            del actions
            if invocation.command_name == "items":
                return CommandResult(content=(TextContent("a"), TextContent("b")))
            return CommandResult.text("x" * 65)

        limits = CommandLimits(
            max_input_line_length=12,
            max_arguments=1,
            max_argument_length=3,
            max_result_items=1,
            max_result_text_characters=64,
        )
        registry = CommandRegistry(limits)
        registry.register(
            CommandDefinition("text", handler, arguments=CommandArgumentContract(0, 1))
        )
        registry.register(CommandDefinition("items", handler))
        registry.freeze()
        actions = _Actions()

        for message, expected in (
            ("/text abcd", "argument exceeds"),
            ("/text a b", "too many arguments"),
            ("/text 1234567", "line limit"),
        ):
            with self.subTest(message=message):
                outputs = await registry.handle(_message(message), _surface(actions))
                assert outputs is not None
                self.assertIn(expected, _text(outputs))
        with self.assertRaisesRegex(CommandResultError, "text limit"):
            await registry.handle(_message("/text"), _surface(actions))
        with self.assertRaisesRegex(CommandResultError, "item limit"):
            await registry.handle(_message("/items"), _surface(actions))

    async def test_non_text_result_content_is_rejected(self) -> None:
        async def handler(invocation, actions):
            del invocation, actions
            attachment = AttachmentContent(
                attachment_id="attachment-a",
                media_type="application/octet-stream",
                source=LocalPath("/untrusted"),
                metadata={"unbounded": "x" * 100_000},
            )
            return CommandResult(content=cast(tuple[TextContent, ...], (attachment,)))

        registry = CommandRegistry()
        registry.register(CommandDefinition("attachment", handler))
        registry.freeze()
        with self.assertRaisesRegex(CommandResultError, "invalid content"):
            await registry.handle(_message("/attachment"), _surface(_Actions()))

    async def test_effectful_fence_reserves_concurrency_before_waiting(self) -> None:
        async def handler(invocation, actions):
            del invocation, actions
            return CommandResult.text("done")

        registry = CommandRegistry(CommandLimits(max_concurrency=1))
        registry.register(
            CommandDefinition(
                "mutate",
                handler,
                safety=CommandExecutionSafety.EFFECTFUL,
            )
        )
        registry.freeze()
        first_actions = _BlockingFenceActions()
        first = asyncio.create_task(registry.handle(_message("/mutate"), _surface(first_actions)))
        await first_actions.entered.wait()
        self.assertEqual(registry.diagnostic_facts().active_handler_count, 1)
        rejected = await registry.handle(
            _message("/mutate", message_id="message-2"),
            _surface(_Actions()),
        )
        assert rejected is not None
        self.assertIn("capacity", _text(rejected).lower())
        first_actions.release.set()
        outputs = await first
        assert outputs is not None
        self.assertEqual(_text(outputs), "done")

    async def test_delivery_identity_is_scoped_without_delimiter_collisions(self) -> None:
        async def handler(invocation, actions):
            del invocation, actions
            return CommandResult.text("done")

        registry = CommandRegistry()
        registry.register(CommandDefinition("read", handler))
        registry.freeze()
        first = await registry.handle(
            _message_for(ConversationRef("channel", "a:b"), "c", "/read"),
            _surface(_Actions()),
        )
        second = await registry.handle(
            _message_for(ConversationRef("channel", "a"), "b:c", "/read"),
            _surface(_Actions()),
        )
        assert first is not None and second is not None
        self.assertNotEqual(first[0].delivery_id, second[0].delivery_id)
        self.assertLessEqual(len(first[0].delivery_id), 512)

    async def test_timeout_overrun_retains_capacity_until_join(self) -> None:
        release = asyncio.Event()

        async def overrun(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            del invocation, actions
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release.wait()
                return CommandResult.text("late")
            raise AssertionError("unreachable")

        registry = CommandRegistry(
            CommandLimits(
                max_concurrency=1,
                handler_timeout_seconds=0.01,
                cancellation_join_timeout_seconds=0.01,
            )
        )
        registry.register(CommandDefinition("slow", overrun))
        registry.freeze()
        with self.assertRaises(TimeoutError):
            await registry.handle(_message("/slow"), _surface(_Actions()))
        self.assertEqual(registry.diagnostic_facts().active_handler_count, 1)
        rejected = await registry.handle(
            _message("/slow", message_id="message-2"),
            _surface(_Actions()),
        )
        assert rejected is not None
        self.assertIn("capacity", _text(rejected).lower())
        release.set()
        async with asyncio.timeout(1):
            while registry.diagnostic_facts().active_handler_count:
                await asyncio.sleep(0)

    async def test_close_cancels_and_joins_admitted_handler(self) -> None:
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def handler(invocation, actions) -> CommandResult:
            del invocation, actions
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
            raise AssertionError("unreachable")

        registry = CommandRegistry()
        registry.register(CommandDefinition("wait", handler))
        registry.freeze()
        invocation = asyncio.create_task(registry.handle(_message("/wait"), _surface(_Actions())))
        await entered.wait()
        await registry.close()
        with self.assertRaises(asyncio.CancelledError):
            await invocation
        self.assertTrue(cancelled.is_set())
        self.assertEqual(registry.diagnostic_facts().active_handler_count, 0)


class CommandRegistryGatewayFenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unfrozen_registry_fails_before_channel_startup(self) -> None:
        registry = CommandRegistry()

        async def handler(invocation, actions):
            del invocation, actions
            return CommandResult()

        registry.register(CommandDefinition("read", handler))
        channel = FakeChannelAdapter("channel-a")
        gateway = _gateway(channel, registry, InMemoryIdempotencyRepository())
        with self.assertRaises(CommandRegistryNotFrozenError):
            await gateway.start()
        self.assertFalse(channel.started)

    async def test_frozen_registry_is_rejected_without_coherent_store_session(self) -> None:
        registry = CommandRegistry()

        async def handler(invocation, actions):
            del invocation, actions
            return CommandResult()

        registry.register(CommandDefinition("read", handler))
        registry.freeze()
        channel = FakeChannelAdapter("channel-a")
        gateway = _gateway(
            channel,
            registry,
            InMemoryIdempotencyRepository(),
        )
        with self.assertRaisesRegex(RuntimeError, "coherent GatewayStore session"):
            await gateway.start()
        self.assertFalse(channel.started)


def _conversation() -> ConversationRef:
    return ConversationRef("channel-a", "conversation-a")


def _message(text: str, *, message_id: str = "message-1") -> InboundMessage:
    return _message_for(_conversation(), message_id, text)


def _message_for(
    conversation_ref: ConversationRef,
    message_id: str,
    text: str,
) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation_ref,
        sender="user-a",
        content=(TextContent(text),),
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
    )


def _gateway(
    channel: FakeChannelAdapter,
    controller: CommandRegistry,
    idempotency: InMemoryIdempotencyRepository,
) -> _GatewayRuntime:
    return _GatewayRuntime(
        channels=[channel],
        applications=[FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)],
        repositories=_GatewayRuntimeDependencies(
            bindings=InMemoryBindingRepository(),
            idempotency=idempotency,
        ),
        extensions=GatewayExtensions(controller=controller),
    )


def _text(outputs) -> str:
    return "\n".join(
        item.text for output in outputs for item in output.content if isinstance(item, TextContent)
    )


if __name__ == "__main__":
    unittest.main()

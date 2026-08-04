from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import cast

from imagent.adapters import IdempotencyClaimStatus
from imagent.contracts import (
    AttachmentContent,
    ConversationRef,
    LocalPath,
    ProjectMode,
    TextContent,
)
from imagent.gateway import GatewayExtensions, GatewayRepositories, ImAgentGateway
from imagent.gateway.admission import inbound_idempotency_identity
from imagent.gateway.persistence import InMemoryIdempotencyRepository
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.interaction.controllers import (
    CommandArgumentContract,
    CommandDefinition,
    CommandExecutionSafety,
    CommandHandler,
    CommandHandlerActions,
    CommandInvocation,
    CommandInvocationFacts,
    CommandRegistry,
    CommandRegistryError,
    CommandRegistryFailureCode,
    CommandRegistryLimits,
    CommandRegistryNotFrozenError,
    CommandResult,
    CommandResultError,
    ControllerActions,
)
from imagent.interaction.messages import InboundMessage
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _Actions(ControllerActions):
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

    async def enter_effectful_command(self, invocation: CommandInvocationFacts) -> None:
        self.events.append(("fence", invocation))
        if self.fence_error is not None:
            raise self.fence_error


class _FailingFenceRepository(InMemoryIdempotencyRepository):
    async def mark_side_effect_started(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        del scope, key, owner_token
        raise RuntimeError("fence persistence failed")


class _BlockingFenceActions(_Actions):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def enter_effectful_command(self, invocation: CommandInvocationFacts) -> None:
        self.events.append(("fence", invocation))
        self.entered.set()
        await self.release.wait()


@dataclass(frozen=True, slots=True)
class _ForgedInvocation:
    invocation_id: str
    conversation_ref: ConversationRef
    message_id: str
    actor: str
    command_name: str
    arguments: tuple[str, ...]
    created_at: datetime


class _ForgingController:
    async def handle(self, message: InboundMessage, actions: ControllerActions):
        await actions.enter_effectful_command(
            _ForgedInvocation(
                invocation_id="forged",
                conversation_ref=message.conversation_ref,
                message_id=message.message_id,
                actor=message.sender,
                command_name="mutate",
                arguments=(),
                created_at=message.created_at,
            )
        )
        return ()


class CommandRegistryDefinitionTests(unittest.TestCase):
    def test_limits_reject_non_positive_and_non_finite_values(self) -> None:
        defaults = CommandRegistryLimits()
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
            actions: CommandHandlerActions,
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

        limits = CommandRegistryLimits(
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
            actions: CommandHandlerActions,
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
        outputs = await registry.handle(_message("\n  /SAY one\nignored two"), actions)
        assert outputs is not None
        self.assertEqual(_text(outputs), "ok")
        self.assertEqual(seen[0].command_name, "echo")
        self.assertEqual(seen[0].arguments, ("one",))
        self.assertIsNone(await registry.handle(_message("ordinary"), actions))
        unknown = await registry.handle(_message("/missing"), actions)
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
            actions: CommandHandlerActions,
        ) -> CommandResult:
            self.assertFalse(hasattr(actions, "enter_effectful_command"))
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
        outputs = await registry.handle(_message("/mutate"), actions)
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
            actions: CommandHandlerActions,
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
                _Actions(fence_error=RuntimeError("fence failed")),
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
            await registry.handle(_message("/bad"), _Actions())

    async def test_parser_and_result_bounds_fail_at_their_boundary(self) -> None:
        async def handler(invocation, actions):
            del actions
            if invocation.command_name == "items":
                return CommandResult(content=(TextContent("a"), TextContent("b")))
            return CommandResult.text("x" * 65)

        limits = CommandRegistryLimits(
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
                outputs = await registry.handle(_message(message), actions)
                assert outputs is not None
                self.assertIn(expected, _text(outputs))
        with self.assertRaisesRegex(CommandResultError, "text limit"):
            await registry.handle(_message("/text"), actions)
        with self.assertRaisesRegex(CommandResultError, "item limit"):
            await registry.handle(_message("/items"), actions)

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
            await registry.handle(_message("/attachment"), _Actions())

    async def test_effectful_fence_reserves_concurrency_before_waiting(self) -> None:
        async def handler(invocation, actions):
            del invocation, actions
            return CommandResult.text("done")

        registry = CommandRegistry(CommandRegistryLimits(max_concurrency=1))
        registry.register(
            CommandDefinition(
                "mutate",
                handler,
                safety=CommandExecutionSafety.EFFECTFUL,
            )
        )
        registry.freeze()
        first_actions = _BlockingFenceActions()
        first = asyncio.create_task(registry.handle(_message("/mutate"), first_actions))
        await first_actions.entered.wait()
        self.assertEqual(registry.diagnostic_facts().active_handler_count, 1)
        rejected = await registry.handle(
            _message("/mutate", message_id="message-2"),
            _Actions(),
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
            _Actions(),
        )
        second = await registry.handle(
            _message_for(ConversationRef("channel", "a"), "b:c", "/read"),
            _Actions(),
        )
        assert first is not None and second is not None
        self.assertNotEqual(first[0].delivery_id, second[0].delivery_id)
        self.assertLessEqual(len(first[0].delivery_id), 512)

    async def test_timeout_overrun_retains_capacity_until_join(self) -> None:
        release = asyncio.Event()

        async def overrun(
            invocation: CommandInvocation,
            actions: CommandHandlerActions,
        ) -> CommandResult:
            del invocation, actions
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release.wait()
                return CommandResult.text("late")
            raise AssertionError("unreachable")

        registry = CommandRegistry(
            CommandRegistryLimits(
                max_concurrency=1,
                handler_timeout_seconds=0.01,
                cancellation_join_timeout_seconds=0.01,
            )
        )
        registry.register(CommandDefinition("slow", overrun))
        registry.freeze()
        with self.assertRaises(TimeoutError):
            await registry.handle(_message("/slow"), _Actions())
        self.assertEqual(registry.diagnostic_facts().active_handler_count, 1)
        rejected = await registry.handle(_message("/slow", message_id="message-2"), _Actions())
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
        invocation = asyncio.create_task(registry.handle(_message("/wait"), _Actions()))
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

    async def test_effectful_exception_keeps_claim_terminal_unknown(self) -> None:
        registry = CommandRegistry()

        @registry.command("boom", safety=CommandExecutionSafety.EFFECTFUL)
        async def boom(invocation, actions):
            del invocation, actions
            raise RuntimeError("unknown product outcome")

        registry.freeze()
        idempotency = InMemoryIdempotencyRepository()
        channel = FakeChannelAdapter("channel-a")
        gateway = _gateway(channel, registry, idempotency)
        await gateway.start()
        message = _message("/boom")
        try:
            with self.assertRaisesRegex(RuntimeError, "unknown product outcome"):
                await channel.emit_message(message)
        finally:
            await gateway.stop()
        scope, key = inbound_idempotency_identity(message.conversation_ref, message.message_id)
        self.assertEqual(
            await idempotency.claim(scope, key, owner_token="replacement"),
            IdempotencyClaimStatus.IN_FLIGHT,
        )

    async def test_fence_repository_failure_releases_without_handler(self) -> None:
        called = False
        registry = CommandRegistry()

        @registry.command("mutate", safety=CommandExecutionSafety.EFFECTFUL)
        async def mutate(invocation, actions):
            del invocation, actions
            nonlocal called
            called = True
            return CommandResult()

        registry.freeze()
        idempotency = _FailingFenceRepository()
        channel = FakeChannelAdapter("channel-a")
        gateway = _gateway(channel, registry, idempotency)
        await gateway.start()
        message = _message("/mutate")
        try:
            with self.assertRaisesRegex(RuntimeError, "fence persistence failed"):
                await channel.emit_message(message)
        finally:
            await gateway.stop()
        self.assertFalse(called)
        scope, key = inbound_idempotency_identity(message.conversation_ref, message.message_id)
        self.assertEqual(
            await idempotency.claim(scope, key, owner_token="replacement"),
            IdempotencyClaimStatus.ACQUIRED,
        )

    async def test_cancellation_after_effect_fence_keeps_claim_terminal_unknown(self) -> None:
        entered = asyncio.Event()
        registry = CommandRegistry()

        @registry.command("wait", safety=CommandExecutionSafety.EFFECTFUL)
        async def wait(invocation, actions):
            del invocation, actions
            entered.set()
            await asyncio.Future()
            return CommandResult()

        registry.freeze()
        idempotency = InMemoryIdempotencyRepository()
        channel = FakeChannelAdapter("channel-a")
        gateway = _gateway(channel, registry, idempotency)
        await gateway.start()
        message = _message("/wait")
        task = asyncio.create_task(channel.emit_message(message))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await gateway.stop()
        scope, key = inbound_idempotency_identity(message.conversation_ref, message.message_id)
        self.assertEqual(
            await idempotency.claim(scope, key, owner_token="replacement"),
            IdempotencyClaimStatus.IN_FLIGHT,
        )

    async def test_read_only_cancellation_releases_before_effect_fence(self) -> None:
        entered = asyncio.Event()
        registry = CommandRegistry()

        @registry.command("wait")
        async def wait(invocation, actions):
            del invocation, actions
            entered.set()
            await asyncio.Future()
            return CommandResult()

        registry.freeze()
        idempotency = InMemoryIdempotencyRepository()
        channel = FakeChannelAdapter("channel-a")
        gateway = _gateway(channel, registry, idempotency)
        await gateway.start()
        message = _message("/wait")
        task = asyncio.create_task(channel.emit_message(message))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await gateway.stop()
        scope, key = inbound_idempotency_identity(message.conversation_ref, message.message_id)
        self.assertEqual(
            await idempotency.claim(scope, key, owner_token="replacement"),
            IdempotencyClaimStatus.ACQUIRED,
        )

    async def test_gateway_rejects_forged_command_identity_before_fence(self) -> None:
        idempotency = InMemoryIdempotencyRepository()
        channel = FakeChannelAdapter("channel-a")
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                idempotency=idempotency,
            ),
            extensions=GatewayExtensions(controller=_ForgingController()),
        )
        await gateway.start()
        message = _message("/mutate")
        try:
            with self.assertRaisesRegex(ValueError, "does not match"):
                await channel.emit_message(message)
        finally:
            await gateway.stop()
        scope, key = inbound_idempotency_identity(message.conversation_ref, message.message_id)
        self.assertEqual(
            await idempotency.claim(scope, key, owner_token="replacement"),
            IdempotencyClaimStatus.ACQUIRED,
        )


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
) -> ImAgentGateway:
    return ImAgentGateway(
        channels=[channel],
        applications=[FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)],
        repositories=GatewayRepositories(
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

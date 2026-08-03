from __future__ import annotations

import asyncio
import hashlib
import math
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from ..messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
    TextFormat,
)
from .contract import (
    CommandHandlerActions,
    ControllerActions,
    _derive_command_invocation_id,
    _narrow_handler_actions,
)

_COMMAND_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class CommandExecutionSafety(StrEnum):
    READ_ONLY = "read_only"
    EFFECTFUL = "effectful"


class CommandResultStatus(StrEnum):
    COMPLETED = "completed"
    KNOWN_FAILURE = "known_failure"


class CommandRegistryFailureCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    UNKNOWN_COMMAND = "unknown_command"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    HANDLER_TIMED_OUT = "handler_timed_out"
    HANDLER_FAILED = "handler_failed"
    INVALID_RESULT = "invalid_result"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CommandRegistryLimits:
    max_commands: int = 64
    max_aliases: int = 256
    max_aliases_per_command: int = 8
    max_command_name_length: int = 64
    max_help_text_length: int = 512
    max_input_line_length: int = 4_096
    max_arguments: int = 32
    max_argument_length: int = 1_024
    max_result_items: int = 16
    max_result_text_characters: int = 16_384
    max_concurrency: int = 16
    handler_timeout_seconds: float = 30.0
    cancellation_join_timeout_seconds: float = 5.0
    view_cache_capacity: int = 128
    view_cache_ttl_seconds: float = 15 * 60.0

    def __post_init__(self) -> None:
        for name in (
            "max_commands",
            "max_aliases",
            "max_aliases_per_command",
            "max_command_name_length",
            "max_help_text_length",
            "max_input_line_length",
            "max_arguments",
            "max_argument_length",
            "max_result_items",
            "max_result_text_characters",
            "max_concurrency",
            "view_cache_capacity",
        ):
            _require_positive_int(getattr(self, name), name)
        for name in (
            "handler_timeout_seconds",
            "cancellation_join_timeout_seconds",
            "view_cache_ttl_seconds",
        ):
            _require_positive_finite(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class CommandArgumentContract:
    min_count: int = 0
    max_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.min_count, int) or isinstance(self.min_count, bool):
            raise ValueError("command minimum argument count must be an integer")
        if self.min_count < 0:
            raise ValueError("command minimum argument count must be non-negative")
        if not isinstance(self.max_count, int) or isinstance(self.max_count, bool):
            raise ValueError("command maximum argument count must be an integer")
        if self.max_count < self.min_count:
            raise ValueError("command maximum argument count must not be less than minimum")


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    invocation_id: str
    conversation_ref: ConversationRef
    message_id: str
    actor: str
    command_name: str
    arguments: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.invocation_id != derive_command_invocation_id(
            self.conversation_ref,
            self.message_id,
            self.command_name,
            self.arguments,
        ):
            raise ValueError("command invocation identity does not match its scoped facts")


@dataclass(frozen=True, slots=True)
class CommandResult:
    content: tuple[TextContent, ...] = ()
    status: CommandResultStatus = CommandResultStatus.COMPLETED

    @classmethod
    def text(
        cls,
        text: str,
        *,
        format: TextFormat = TextFormat.MARKDOWN,
    ) -> CommandResult:
        return cls(content=(TextContent(text, format),))

    @classmethod
    def failure(cls, message: str) -> CommandResult:
        return cls(
            content=(TextContent(f"**Error:** {message}", TextFormat.MARKDOWN),),
            status=CommandResultStatus.KNOWN_FAILURE,
        )


class CommandHandler(Protocol):
    async def __call__(
        self,
        invocation: CommandInvocation,
        actions: CommandHandlerActions,
    ) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    handler: CommandHandler
    aliases: tuple[str, ...] = ()
    arguments: CommandArgumentContract = CommandArgumentContract()
    summary: str = ""
    usage: str = ""
    safety: CommandExecutionSafety = CommandExecutionSafety.READ_ONLY
    handler_timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class CommandRegistryDiagnostics:
    invocation_count: int
    completed_count: int
    known_failure_count: int
    handler_failure_count: int
    timeout_count: int
    cancellation_count: int
    cancellation_overrun_count: int
    capacity_rejection_count: int
    active_handler_count: int
    last_failure_code: CommandRegistryFailureCode | None


class CommandRegistryError(ValueError):
    pass


class CommandRegistryFrozenError(CommandRegistryError):
    pass


class CommandRegistryNotFrozenError(CommandRegistryError):
    pass


class CommandHandlerTimeout(TimeoutError):
    def __init__(self, *, cancellation_overrun: bool) -> None:
        super().__init__("command handler exceeded its configured lifetime")
        self.cancellation_overrun = cancellation_overrun


class CommandResultError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _ParsedCommand:
    name: str
    arguments: tuple[str, ...]


class CommandRegistry:
    """One explicit, frozen, bounded command composition."""

    def __init__(self, limits: CommandRegistryLimits | None = None) -> None:
        self._limits = limits or CommandRegistryLimits()
        self._definitions: dict[str, CommandDefinition] = {}
        self._names: dict[str, str] = {}
        self._frozen = False
        self._active_tasks: set[asyncio.Task[CommandResult]] = set()
        self._admitted_count = 0
        self._invocation_count = 0
        self._completed_count = 0
        self._known_failure_count = 0
        self._handler_failure_count = 0
        self._timeout_count = 0
        self._cancellation_count = 0
        self._cancellation_overrun_count = 0
        self._capacity_rejection_count = 0
        self._last_failure_code: CommandRegistryFailureCode | None = None

    @property
    def limits(self) -> CommandRegistryLimits:
        return self._limits

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register(self, definition: CommandDefinition) -> None:
        if self._frozen:
            raise CommandRegistryFrozenError("command registry is frozen")
        canonical, aliases = self._validate_definition(definition)
        if len(self._definitions) >= self._limits.max_commands:
            raise CommandRegistryError("command registry entry capacity is exhausted")
        if sum(len(item.aliases) for item in self._definitions.values()) + len(aliases) > (
            self._limits.max_aliases
        ):
            raise CommandRegistryError("command registry alias capacity is exhausted")
        for name in (canonical, *aliases):
            if name in self._names:
                raise CommandRegistryError(f"command name or alias collision: {name}")
        self._definitions[canonical] = definition
        for name in (canonical, *aliases):
            self._names[name] = canonical

    def command(
        self,
        name: str,
        *,
        aliases: tuple[str, ...] = (),
        arguments: CommandArgumentContract = CommandArgumentContract(),
        summary: str = "",
        usage: str = "",
        safety: CommandExecutionSafety = CommandExecutionSafety.READ_ONLY,
        handler_timeout_seconds: float | None = None,
    ) -> Callable[[CommandHandler], CommandHandler]:
        def register_handler(handler: CommandHandler) -> CommandHandler:
            self.register(
                CommandDefinition(
                    name=name,
                    handler=handler,
                    aliases=aliases,
                    arguments=arguments,
                    summary=summary,
                    usage=usage,
                    safety=safety,
                    handler_timeout_seconds=handler_timeout_seconds,
                )
            )
            return handler

        return register_handler

    def freeze(self) -> None:
        if self._frozen:
            return
        if not self._definitions:
            raise CommandRegistryError("command registry must contain at least one command")
        self._frozen = True

    def validate_startup(self) -> None:
        if not self._frozen:
            raise CommandRegistryNotFrozenError(
                "command registry must be frozen before Gateway startup"
            )

    async def handle(
        self,
        message: InboundMessage,
        actions: ControllerActions,
    ) -> tuple[OutboundMessage, ...] | None:
        self.validate_startup()
        try:
            parsed = self._parse(message)
        except CommandRegistryError as error:
            self._record_failure(CommandRegistryFailureCode.INVALID_INPUT)
            return self._present(message, self._bounded_failure(str(error)))
        if parsed is None:
            return None
        self._invocation_count += 1
        canonical = self._names.get(parsed.name)
        if canonical is None:
            self._record_failure(CommandRegistryFailureCode.UNKNOWN_COMMAND)
            return self._present(
                message,
                self._bounded_failure(f"Unknown command `/{parsed.name}`. Use `/help`."),
            )
        definition = self._definitions[canonical]
        argument_error = _validate_argument_count(definition, parsed.arguments)
        if argument_error is not None:
            self._record_failure(CommandRegistryFailureCode.INVALID_INPUT)
            return self._present(message, self._bounded_failure(argument_error))
        invocation = CommandInvocation(
            invocation_id=derive_command_invocation_id(
                message.conversation_ref,
                message.message_id,
                canonical,
                parsed.arguments,
            ),
            conversation_ref=message.conversation_ref,
            message_id=message.message_id,
            actor=message.sender,
            command_name=canonical,
            arguments=parsed.arguments,
            created_at=message.created_at,
        )
        if self._admitted_count >= self._limits.max_concurrency:
            self._capacity_rejection_count += 1
            self._record_failure(CommandRegistryFailureCode.CAPACITY_EXHAUSTED)
            return self._present(
                message,
                self._bounded_failure("Command handler capacity is exhausted."),
            )
        self._admitted_count += 1
        if definition.safety is CommandExecutionSafety.EFFECTFUL:
            try:
                await actions.enter_effectful_command(invocation)
            except BaseException:
                self._admitted_count -= 1
                raise
        result = await self._invoke(
            definition,
            invocation,
            _narrow_handler_actions(actions),
        )
        result = self._validate_result(result)
        if result.status is CommandResultStatus.KNOWN_FAILURE:
            self._known_failure_count += 1
        else:
            self._completed_count += 1
        return self._present(message, result)

    async def close(self) -> None:
        tasks = tuple(self._active_tasks)
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        done, pending = await asyncio.wait(
            tasks,
            timeout=self._limits.cancellation_join_timeout_seconds,
        )
        for task in done:
            self._finish_task(task)
        if pending:
            self._cancellation_overrun_count += len(pending)
            for task in pending:
                task.cancel()

    def diagnostic_facts(self) -> CommandRegistryDiagnostics:
        return CommandRegistryDiagnostics(
            invocation_count=self._invocation_count,
            completed_count=self._completed_count,
            known_failure_count=self._known_failure_count,
            handler_failure_count=self._handler_failure_count,
            timeout_count=self._timeout_count,
            cancellation_count=self._cancellation_count,
            cancellation_overrun_count=self._cancellation_overrun_count,
            capacity_rejection_count=self._capacity_rejection_count,
            active_handler_count=self._admitted_count,
            last_failure_code=self._last_failure_code,
        )

    async def _invoke(
        self,
        definition: CommandDefinition,
        invocation: CommandInvocation,
        actions: CommandHandlerActions,
    ) -> CommandResult:
        timeout = definition.handler_timeout_seconds or self._limits.handler_timeout_seconds
        try:
            task = asyncio.create_task(
                definition.handler(invocation, actions),
                name=f"imagent-command-{invocation.command_name}",
            )
        except BaseException:
            self._admitted_count -= 1
            raise
        self._active_tasks.add(task)
        task.add_done_callback(self._finish_task)
        try:
            done, _ = await asyncio.wait((task,), timeout=timeout)
            if not done:
                joined = await self._cancel_and_join(task)
                self._timeout_count += 1
                if not joined:
                    self._cancellation_overrun_count += 1
                self._record_failure(CommandRegistryFailureCode.HANDLER_TIMED_OUT)
                raise CommandHandlerTimeout(cancellation_overrun=not joined)
            return task.result()
        except asyncio.CancelledError:
            joined = True
            if not task.done():
                joined = await self._cancel_and_join(task)
            self._cancellation_count += 1
            if not joined:
                self._cancellation_overrun_count += 1
            self._record_failure(CommandRegistryFailureCode.CANCELLED)
            raise
        except (CommandHandlerTimeout,):
            raise
        except BaseException:
            self._handler_failure_count += 1
            self._record_failure(CommandRegistryFailureCode.HANDLER_FAILED)
            raise

    async def _cancel_and_join(self, task: asyncio.Task[CommandResult]) -> bool:
        task.cancel()
        done, _ = await asyncio.wait(
            (task,),
            timeout=self._limits.cancellation_join_timeout_seconds,
        )
        if not done:
            task.cancel()
            return False
        self._finish_task(task)
        return True

    def _finish_task(self, task: asyncio.Task[CommandResult]) -> None:
        if task not in self._active_tasks:
            return
        self._active_tasks.remove(task)
        self._admitted_count -= 1
        _consume_task_result(task)

    def _parse(self, message: InboundMessage) -> _ParsedCommand | None:
        line = _first_non_empty_text_line(message)
        if line is None or not line.startswith("/"):
            return None
        if len(line) > self._limits.max_input_line_length:
            raise CommandRegistryError("Slash command exceeds the configured line limit.")
        try:
            tokens = shlex.split(line)
        except ValueError as error:
            raise CommandRegistryError(f"Invalid slash command: {error}") from error
        if not tokens or tokens[0] == "/":
            raise CommandRegistryError("Slash command name is required.")
        name = _normalize_name(tokens[0][1:], self._limits.max_command_name_length)
        arguments = tuple(tokens[1:])
        if len(arguments) > self._limits.max_arguments:
            raise CommandRegistryError("Slash command has too many arguments.")
        if any(len(argument) > self._limits.max_argument_length for argument in arguments):
            raise CommandRegistryError("Slash command argument exceeds the configured limit.")
        return _ParsedCommand(name=name, arguments=arguments)

    def _validate_definition(
        self,
        definition: CommandDefinition,
    ) -> tuple[str, tuple[str, ...]]:
        if not isinstance(definition, CommandDefinition):
            raise CommandRegistryError("registry entries must be CommandDefinition values")
        canonical = _normalize_name(definition.name, self._limits.max_command_name_length)
        if len(definition.aliases) > self._limits.max_aliases_per_command:
            raise CommandRegistryError("command has too many aliases")
        aliases = tuple(
            _normalize_name(alias, self._limits.max_command_name_length)
            for alias in definition.aliases
        )
        if len(set(aliases)) != len(aliases) or canonical in aliases:
            raise CommandRegistryError("command aliases must be unique")
        if definition.arguments.max_count > self._limits.max_arguments:
            raise CommandRegistryError("command argument contract exceeds registry limits")
        if not isinstance(definition.safety, CommandExecutionSafety):
            raise CommandRegistryError("command execution safety is invalid")
        for name, value in (("summary", definition.summary), ("usage", definition.usage)):
            if not isinstance(value, str) or len(value) > self._limits.max_help_text_length:
                raise CommandRegistryError(f"command {name} exceeds the configured limit")
        if definition.handler_timeout_seconds is not None:
            _require_positive_finite(
                definition.handler_timeout_seconds,
                "command handler timeout",
            )
        if not callable(definition.handler):
            raise CommandRegistryError("command handler must be callable")
        return canonical, aliases

    def _validate_result(self, result: CommandResult) -> CommandResult:
        if not isinstance(result, CommandResult):
            self._record_failure(CommandRegistryFailureCode.INVALID_RESULT)
            raise CommandResultError("command handler must return CommandResult")
        if not isinstance(result.status, CommandResultStatus):
            self._record_failure(CommandRegistryFailureCode.INVALID_RESULT)
            raise CommandResultError("command result status is invalid")
        if not isinstance(result.content, tuple) or len(result.content) > (
            self._limits.max_result_items
        ):
            self._record_failure(CommandRegistryFailureCode.INVALID_RESULT)
            raise CommandResultError("command result exceeds the configured item limit")
        text_characters = 0
        for item in result.content:
            if not isinstance(item, TextContent):
                self._record_failure(CommandRegistryFailureCode.INVALID_RESULT)
                raise CommandResultError("command result contains an invalid content item")
            text_characters += len(item.text)
        if text_characters > self._limits.max_result_text_characters:
            self._record_failure(CommandRegistryFailureCode.INVALID_RESULT)
            raise CommandResultError("command result exceeds the configured text limit")
        if result.status is CommandResultStatus.KNOWN_FAILURE and not result.content:
            self._record_failure(CommandRegistryFailureCode.INVALID_RESULT)
            raise CommandResultError("known command failure must contain presentation content")
        return result

    def _present(
        self,
        message: InboundMessage,
        result: CommandResult,
    ) -> tuple[OutboundMessage, ...]:
        if not result.content:
            return ()
        return (
            OutboundMessage(
                delivery_id=_derive_controller_delivery_id(message),
                conversation_ref=message.conversation_ref,
                content=result.content,
                created_at=datetime.now(UTC),
                reply_to=message.message_id,
            ),
        )

    def _bounded_failure(self, message: str) -> CommandResult:
        rendered = CommandResult.failure(message).content[0]
        return CommandResult(
            content=(
                TextContent(
                    rendered.text[: self._limits.max_result_text_characters],
                    rendered.format,
                ),
            ),
            status=CommandResultStatus.KNOWN_FAILURE,
        )

    def _record_failure(self, code: CommandRegistryFailureCode) -> None:
        self._last_failure_code = code


def derive_command_invocation_id(
    conversation_ref: ConversationRef,
    message_id: str,
    command_name: str,
    arguments: tuple[str, ...],
) -> str:
    return _derive_command_invocation_id(
        conversation_ref,
        message_id,
        command_name,
        arguments,
    )


def _derive_controller_delivery_id(message: InboundMessage) -> str:
    digest = hashlib.sha256()
    for value in (
        message.conversation_ref.channel_instance_id,
        message.conversation_ref.native_conversation_id,
        message.message_id,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return f"imagent:controller:sha256:{digest.hexdigest()}"


def _normalize_name(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise CommandRegistryError("command name must be a string")
    normalized = value.casefold()
    if (
        not normalized
        or len(normalized) > max_length
        or not _COMMAND_NAME_PATTERN.fullmatch(normalized)
    ):
        raise CommandRegistryError(
            "command names must use bounded lowercase letters, digits, _ or -"
        )
    return normalized


def _validate_argument_count(
    definition: CommandDefinition,
    arguments: tuple[str, ...],
) -> str | None:
    count = len(arguments)
    contract = definition.arguments
    if contract.min_count <= count <= contract.max_count:
        return None
    usage = definition.usage or f"/{definition.name}"
    return f"Use `{usage}`."


def _first_non_empty_text_line(message: InboundMessage) -> str | None:
    for item in message.content:
        if not isinstance(item, TextContent):
            continue
        for line in item.text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return None


def _require_positive_int(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _require_positive_finite(value: object, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be finite and positive")
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _consume_task_result(task: asyncio.Task[CommandResult]) -> None:
    try:
        task.result()
    except BaseException:
        pass

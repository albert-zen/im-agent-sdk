from __future__ import annotations

import hashlib
import shlex
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from ...applications.capabilities import ThreadDeletionCapability
from ...applications.contract import (
    ApplicationRef,
    ApplicationSummary,
    ProjectSummary,
    ThreadRef,
    ThreadSummary,
)
from ...applications.operations import ThreadDeletionMode
from ...applications.requests import ApprovalResponse, RequestRef, UserInputResponse
from ...gateway.outcomes import Failed, OutcomeUnknown, Partial, Succeeded
from ...gateway.persistence.state_contracts import ConversationBinding
from ..messages import ConversationRef, InboundMessage, TextContent
from .common_presentation import MarkdownSlashPresenter
from .registry import (
    CommandArgumentContract,
    CommandDefinition,
    CommandExecutionSafety,
    CommandInvocation,
    CommandLimits,
    CommandRegistry,
    CommandResult,
)

if TYPE_CHECKING:
    from ...gateway.actions import ActionResult, ActionValue, ConversationActions, ReadOutcome


class _ErrorCodeView(Protocol):
    @property
    def value(self) -> str: ...


class _ActionErrorView(Protocol):
    @property
    def code(self) -> _ErrorCodeView: ...

    @property
    def operation_error_code(self) -> _ErrorCodeView | None: ...


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    arguments: tuple[str, ...]
    raw: str


def parse_slash_command(message: InboundMessage) -> SlashCommand | None:
    command_line = next(
        (
            line.strip()
            for part in message.content
            if isinstance(part, TextContent)
            for line in part.text.splitlines()
            if line.strip()
        ),
        "",
    )
    if not command_line or not command_line.startswith("/"):
        return None
    limits = CommandLimits()
    if len(command_line) > limits.max_input_line_length:
        raise ValueError("Slash command exceeds the configured line limit.")
    try:
        tokens = shlex.split(command_line)
    except ValueError as error:
        raise ValueError(f"Invalid slash command: {error}") from error
    if not tokens:
        return None
    if len(tokens) - 1 > limits.max_arguments:
        raise ValueError("Slash command has too many arguments.")
    if any(len(argument) > limits.max_argument_length for argument in tokens[1:]):
        raise ValueError("Slash command argument exceeds the configured limit.")
    return SlashCommand(
        name=tokens[0][1:].casefold(),
        arguments=tuple(tokens[1:]),
        raw=command_line,
    )


@dataclass(frozen=True, slots=True)
class _ApplicationContext:
    binding: ConversationBinding | None
    application: ApplicationSummary


class _CommandError(RuntimeError):
    pass


class _CommonCommandRuntime:
    """Stateful common handlers registered into one explicit registry."""

    def __init__(
        self,
        presenter: MarkdownSlashPresenter,
        limits: CommandLimits,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._presenter = presenter or MarkdownSlashPresenter()
        self._project_views = _BoundedViewCache[tuple[ProjectSummary, ...]](
            capacity=limits.view_cache_capacity,
            ttl_seconds=limits.view_cache_ttl_seconds,
            clock=clock,
        )
        self._thread_views = _BoundedViewCache[tuple[ThreadSummary, ...]](
            capacity=limits.view_cache_capacity,
            ttl_seconds=limits.view_cache_ttl_seconds,
            clock=clock,
        )

    async def handle_command(
        self,
        invocation: CommandInvocation,
        actions: ConversationActions,
    ) -> CommandResult:
        message = InboundMessage(
            message_id=invocation.message_id,
            conversation_ref=invocation.conversation_ref,
            sender=invocation.actor,
            content=(),
            created_at=invocation.created_at,
        )
        command = SlashCommand(
            name=invocation.command_name,
            arguments=invocation.arguments,
            raw=f"/{invocation.command_name}",
        )
        try:
            text = await self._dispatch(message, command, actions)
            return CommandResult.text(text)
        except _CommandError as error:
            return CommandResult.failure(str(error))

    async def _dispatch(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ConversationActions,
    ) -> str:
        if command.name in {"help", "start"}:
            return self._presenter.help()
        if command.name == "apps":
            return self._presenter.applications(await self._applications(message, actions))
        if command.name == "app":
            return await self._select_application(message, command, actions)
        if command.name in {"respond", "answer"}:
            return await self._respond_to_request(message, command, actions)

        context = await self._ensure_application_binding(
            message,
            actions,
        )
        if command.name == "projects":
            projects = await self._list_projects(
                message,
                actions,
                context,
                query=" ".join(command.arguments) or None,
            )
            self._project_views.put(message.conversation_ref, projects)
            return self._presenter.projects(projects)
        if command.name in {"use", "project"}:
            return await self._select_project(message, command, actions, context)
        if command.name == "threads":
            threads = await self._list_threads(
                message,
                actions,
                context,
                query=" ".join(command.arguments) or None,
            )
            self._thread_views.put(message.conversation_ref, threads)
            return self._presenter.threads(threads)
        if command.name in {"pick", "thread"}:
            return await self._select_thread(message, command, actions, context)
        if command.name == "new":
            return await self._create_thread(message, command, actions, context)
        if command.name in {"delete", "archive"}:
            return await self._delete_thread(message, command, actions, context)
        if command.name == "status":
            return await self._thread_status(message, actions, context)
        if command.name == "catchup":
            return await self._turn_catchup(message, command, actions, context)
        if command.name == "history":
            return await self._thread_history(message, command, actions, context)
        raise _CommandError(f"Unknown command `/{command.name}`. Use `/help`.")

    async def _respond_to_request(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ConversationActions,
    ) -> str:
        if len(command.arguments) < 3:
            raise _CommandError(
                "Use `/respond <application> <request> <choice>` or "
                "`/answer <application> <request> <question>=<answer> ...`."
            )
        application_id, native_request_id, *response_arguments = command.arguments
        request_ref = RequestRef(
            application_ref=ApplicationRef(application_id),
            native_request_id=native_request_id,
        )
        if command.name == "respond":
            if len(response_arguments) != 1:
                raise _CommandError("Use `/respond <application> <request> <choice>`.")
            response = ApprovalResponse(response_arguments[0])
        else:
            answers: dict[str, list[str]] = {}
            for argument in response_arguments:
                question_id, separator, answer = argument.partition("=")
                if not separator or not question_id or not answer:
                    raise _CommandError("Each `/answer` value must be `<question>=<answer>`.")
                answers.setdefault(question_id, []).append(answer)
            response = UserInputResponse(
                {question_id: tuple(values) for question_id, values in answers.items()}
            )
        result = await actions.respond_request(
            request_ref,
            response,
            action_id=_operation_id(message, command, "conversation.respond_request"),
        )
        _require_action_success(result)
        return "Response submitted to the Agent application."

    async def _applications(
        self,
        message: InboundMessage,
        actions: ConversationActions,
    ) -> tuple[ApplicationSummary, ...]:
        result = await actions.list_applications()
        return _require_read_success(result)

    async def _select_application(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ConversationActions,
    ) -> str:
        applications = await self._applications(message, actions)
        if not command.arguments:
            return self._presenter.applications(applications)
        selected = _select(
            applications,
            " ".join(command.arguments),
            id_of=lambda item: item.ref.application_instance_id,
            label_of=lambda item: item.display_name,
        )
        if selected is None:
            raise _CommandError("Agent application not found.")
        current = await actions.get_binding()
        result = await actions.select_application(
            selected.ref,
            action_id=_operation_id(message, command, "application.select"),
            expected_generation=current.generation if current is not None else None,
        )
        _require_action_success(result)
        self._clear_views(message)
        return (
            f"Selected application **{selected.display_name}** "
            f"(`{selected.ref.application_instance_id}`)."
        )

    async def _ensure_application_binding(
        self,
        message: InboundMessage,
        actions: ConversationActions,
    ) -> _ApplicationContext:
        binding = await actions.get_binding()
        applications = await self._applications(message, actions)
        if binding is not None and binding.application_ref is not None:
            application = next(
                (item for item in applications if item.ref == binding.application_ref),
                None,
            )
            if application is None:
                raise _CommandError("Selected Agent application is unavailable.")
            return _ApplicationContext(binding=binding, application=application)
        raise _CommandError("Choose an Agent application with `/apps` and `/app <number>`.")

    async def _list_projects(
        self,
        message: InboundMessage,
        actions: ConversationActions,
        context: _ApplicationContext,
        *,
        query: str | None = None,
    ) -> tuple[ProjectSummary, ...]:
        result = await actions.list_projects(
            context.application.ref,
            query=query,
        )
        return _require_read_success(result).items

    async def _select_project(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ConversationActions,
        context: _ApplicationContext,
    ) -> str:
        if not command.arguments:
            raise _CommandError("Use `/use <number-or-project>` after `/projects`.")
        projects = self._project_views.get(message.conversation_ref)
        if projects is None:
            projects = await self._list_projects(message, actions, context)
            self._project_views.put(message.conversation_ref, projects)
        project = _select(
            projects,
            " ".join(command.arguments),
            id_of=lambda item: item.ref.project_id,
            label_of=lambda item: item.display_name,
        )
        if project is None:
            raise _CommandError("Project not found.")
        binding = _require_context_binding(context)
        result = await actions.select_project(
            project.ref,
            action_id=_operation_id(message, command, "conversation.bind_project"),
            expected_generation=binding.generation,
        )
        _require_action_success(result)
        self._thread_views.pop(message.conversation_ref, None)
        return f"Selected project **{project.display_name}** (`{project.ref.project_id}`)."

    async def _list_threads(
        self,
        message: InboundMessage,
        actions: ConversationActions,
        context: _ApplicationContext,
        *,
        query: str | None = None,
    ) -> tuple[ThreadSummary, ...]:
        binding = _require_context_binding(context)
        if binding.project_ref is None:
            raise _CommandError("Choose a project first with `/projects` and `/use <number>`.")
        result = await actions.list_threads(
            binding.project_ref,
            query=query,
        )
        return _require_read_success(result).items

    async def _select_thread(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ConversationActions,
        context: _ApplicationContext,
    ) -> str:
        if not command.arguments:
            raise _CommandError("Use `/pick <number-or-thread>` after `/threads`.")
        threads = self._thread_views.get(message.conversation_ref)
        if threads is None:
            threads = await self._list_threads(message, actions, context)
            self._thread_views.put(message.conversation_ref, threads)
        thread = _select(
            threads,
            " ".join(command.arguments),
            id_of=lambda item: item.ref.thread_id,
            label_of=lambda item: item.title or "",
        )
        if thread is None:
            raise _CommandError("Thread not found.")
        binding = _require_context_binding(context)
        result = await actions.bind_thread(
            thread.ref,
            action_id=_operation_id(message, command, "conversation.bind_thread"),
            expected_generation=binding.generation,
        )
        _require_action_success(result)
        _require_action_success(
            await actions.observe_thread(
                thread.ref,
                action_id=_operation_id(message, command, "thread.observe"),
                reply_to_message_id=message.message_id,
            )
        )
        return (
            f"Selected thread **{thread.title or thread.ref.thread_id}** "
            f"(`{thread.ref.thread_id}`)."
        )

    async def _create_thread(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ConversationActions,
        context: _ApplicationContext,
    ) -> str:
        binding = _require_context_binding(context)
        if binding.project_ref is None:
            raise _CommandError("Choose a project first with `/projects` and `/use <number>`.")
        result = await actions.create_and_bind_thread(
            binding.project_ref,
            title=" ".join(command.arguments) or "IM task",
            action_id=_operation_id(message, command, "create_and_bind_thread"),
        )
        value = _require_action_success(result)
        if not isinstance(value.ref, ThreadRef):
            raise _CommandError("Thread workflow returned an incompatible result.")
        thread_ref = value.ref
        _require_action_success(
            await actions.observe_thread(
                thread_ref,
                action_id=_operation_id(message, command, "thread.observe"),
                reply_to_message_id=message.message_id,
            )
        )
        self._thread_views.pop(message.conversation_ref, None)
        title = " ".join(command.arguments) or thread_ref.thread_id
        return f"Created thread **{title}** (`{thread_ref.thread_id}`)."

    async def _delete_thread(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ConversationActions,
        context: _ApplicationContext,
    ) -> str:
        binding = _require_context_binding(context)
        thread_ref = binding.thread_ref
        if thread_ref is None:
            raise _CommandError("No thread is selected.")
        capability = context.application.capabilities.threads.deletion
        if capability is ThreadDeletionCapability.UNSUPPORTED:
            raise _CommandError("Thread deletion is unsupported.")
        mode = ThreadDeletionMode(capability.value)
        result = await actions.delete_thread(
            thread_ref,
            mode=mode,
            action_id=_operation_id(message, command, "thread.delete"),
        )
        _require_action_success(result)
        self._thread_views.pop(message.conversation_ref, None)
        if mode is ThreadDeletionMode.ARCHIVE:
            return f"Archived thread `{thread_ref.thread_id}`."
        return f"Deleted thread `{thread_ref.thread_id}`."

    async def _thread_status(
        self,
        message: InboundMessage,
        actions: ConversationActions,
        context: _ApplicationContext,
    ) -> str:
        thread_ref = context.binding.thread_ref if context.binding is not None else None
        if thread_ref is None:
            raise _CommandError("No thread is selected.")
        status = _require_read_success(await actions.get_thread_status(thread_ref))
        return f"Thread `{thread_ref.thread_id}` is **{status.value}**."

    async def _turn_catchup(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ConversationActions,
        context: _ApplicationContext,
    ) -> str:
        thread_ref = context.binding.thread_ref if context.binding is not None else None
        if thread_ref is None:
            raise _CommandError("No thread is selected.")
        try:
            limit = _positive_limit(command.arguments, default=5)
        except ValueError as error:
            raise _CommandError(str(error)) from error
        catchup = _require_read_success(await actions.read_turn_catchup(thread_ref, limit=limit))
        return self._presenter.turn_catchup(catchup)

    async def _thread_history(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ConversationActions,
        context: _ApplicationContext,
    ) -> str:
        thread_ref = context.binding.thread_ref if context.binding is not None else None
        if thread_ref is None:
            raise _CommandError("No thread is selected.")
        try:
            limit, page = _history_options(command.arguments)
        except ValueError as error:
            raise _CommandError(str(error)) from error
        history = _require_read_success(
            await actions.read_history(thread_ref, limit=limit, page=page)
        )
        return self._presenter.thread_history(history)

    def _clear_views(self, message: InboundMessage) -> None:
        self._project_views.pop(message.conversation_ref, None)
        self._thread_views.pop(message.conversation_ref, None)


def include_common_commands(
    registry: CommandRegistry,
    *,
    presenter: MarkdownSlashPresenter | None = None,
    names: tuple[str, ...] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Register the selected SDK common definitions into one local registry."""

    runtime = _CommonCommandRuntime(
        presenter or MarkdownSlashPresenter(),
        registry.limits,
        clock=clock,
    )
    definitions = _common_command_definitions(runtime, registry.limits)
    selected = set(definitions) if names is None else set(names)
    unknown = selected.difference(definitions)
    if unknown:
        raise ValueError(f"unknown common command selection: {sorted(unknown)!r}")
    for name, definition in definitions.items():
        if name in selected:
            registry.register(definition)


def _common_command_definitions(
    runtime: _CommonCommandRuntime,
    limits: CommandLimits,
) -> dict[str, CommandDefinition]:
    read_only = CommandExecutionSafety.READ_ONLY
    effectful = CommandExecutionSafety.EFFECTFUL
    maximum = limits.max_arguments

    def definition(
        name: str,
        *,
        aliases: tuple[str, ...] = (),
        minimum: int = 0,
        maximum_arguments: int = 0,
        summary: str,
        usage: str,
        safety: CommandExecutionSafety,
    ) -> CommandDefinition:
        return CommandDefinition(
            name=name,
            handler=runtime.handle_command,
            aliases=aliases,
            arguments=CommandArgumentContract(minimum, maximum_arguments),
            summary=summary,
            usage=usage,
            safety=safety,
        )

    return {
        "help": definition(
            "help",
            aliases=("start",),
            summary="Show common commands.",
            usage="/help",
            safety=read_only,
        ),
        "apps": definition(
            "apps",
            summary="List Agent applications.",
            usage="/apps",
            safety=read_only,
        ),
        "app": definition(
            "app",
            maximum_arguments=maximum,
            summary="Select an Agent application.",
            usage="/app <selector>",
            safety=effectful,
        ),
        "projects": definition(
            "projects",
            maximum_arguments=maximum,
            summary="List projects.",
            usage="/projects [query]",
            safety=read_only,
        ),
        "use": definition(
            "use",
            aliases=("project",),
            minimum=1,
            maximum_arguments=maximum,
            summary="Select a project.",
            usage="/use <selector>",
            safety=effectful,
        ),
        "threads": definition(
            "threads",
            maximum_arguments=maximum,
            summary="List Threads.",
            usage="/threads [query]",
            safety=read_only,
        ),
        "pick": definition(
            "pick",
            aliases=("thread",),
            minimum=1,
            maximum_arguments=maximum,
            summary="Select a Thread.",
            usage="/pick <selector>",
            safety=effectful,
        ),
        "new": definition(
            "new",
            maximum_arguments=maximum,
            summary="Create and select a Thread.",
            usage="/new [title]",
            safety=effectful,
        ),
        "delete": definition(
            "delete",
            aliases=("archive",),
            summary="Delete or archive the selected Thread.",
            usage="/delete",
            safety=effectful,
        ),
        "status": definition(
            "status",
            summary="Read selected Thread status.",
            usage="/status",
            safety=read_only,
        ),
        "catchup": definition(
            "catchup",
            maximum_arguments=1,
            summary="Read recent Turn activity.",
            usage="/catchup [messages]",
            safety=read_only,
        ),
        "history": definition(
            "history",
            maximum_arguments=3,
            summary="Read bounded Thread history.",
            usage="/history [turns] [--page N]",
            safety=read_only,
        ),
        "respond": definition(
            "respond",
            minimum=3,
            maximum_arguments=3,
            summary="Respond to a delivered approval.",
            usage="/respond <application> <request> <choice>",
            safety=effectful,
        ),
        "answer": definition(
            "answer",
            minimum=3,
            maximum_arguments=maximum,
            summary="Respond to delivered structured input.",
            usage="/answer <application> <request> <question>=<answer> ...",
            safety=effectful,
        ),
    }


TView = TypeVar("TView")
TRead = TypeVar("TRead")


class _BoundedViewCache(Generic[TView]):
    def __init__(
        self,
        *,
        capacity: int,
        ttl_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._items: OrderedDict[ConversationRef, tuple[float, TView]] = OrderedDict()

    def get(self, key: ConversationRef) -> TView | None:
        self._expire()
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= self._clock():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value

    def put(self, key: ConversationRef, value: TView) -> None:
        self._expire()
        self._items[key] = (self._clock() + self._ttl_seconds, value)
        self._items.move_to_end(key)
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def pop(self, key: ConversationRef, default: object = None) -> TView | object:
        item = self._items.pop(key, None)
        return default if item is None else item[1]

    def _expire(self) -> None:
        now = self._clock()
        expired = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
        for key in expired:
            self._items.pop(key, None)


def _require_context_binding(context: _ApplicationContext) -> ConversationBinding:
    if context.binding is None:
        raise _CommandError("No Agent application is selected.")
    return context.binding


def _require_read_success(result: ReadOutcome[TRead]) -> TRead:
    if isinstance(result, Succeeded):
        return result.value
    if isinstance(result, Failed):
        raise _CommandError(_action_error_text(result.error))
    raise _CommandError("Read returned an incompatible result.")


def _require_action_success(result: ActionResult) -> ActionValue:
    if isinstance(result, Succeeded):
        return result.value
    if isinstance(result, Partial):
        raise _CommandError(
            f"Action completed only partially ({_action_error_text(result.error)})."
        )
    if isinstance(result, OutcomeUnknown):
        raise _CommandError(
            f"Action outcome is unknown ({_action_error_text(result.error)}); do not retry blindly."
        )
    if isinstance(result, Failed):
        raise _CommandError(_action_error_text(result.error))
    raise _CommandError("Action returned an incompatible result.")


def _action_error_text(error: _ActionErrorView) -> str:
    detail = (
        f" ({error.operation_error_code.value})" if error.operation_error_code is not None else ""
    )
    return f"{error.code.value.replace('_', ' ')}{detail}"


def _operation_id(
    message: InboundMessage,
    command: SlashCommand,
    operation_type: str,
) -> str:
    digest = hashlib.sha256()
    for value in (
        message.conversation_ref.channel_instance_id,
        message.conversation_ref.native_conversation_id,
        message.message_id,
        command.name,
        *command.arguments,
        operation_type,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return f"imagent:operation:{operation_type}:{digest.hexdigest()}"


def _select(items, query: str, *, id_of, label_of):
    normalized = query.strip().casefold()
    if normalized.isdigit():
        index = int(normalized) - 1
        if 0 <= index < len(items):
            return items[index]
    exact = [
        item for item in items if normalized in {id_of(item).casefold(), label_of(item).casefold()}
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [
        item
        for item in items
        if normalized in id_of(item).casefold() or normalized in label_of(item).casefold()
    ]
    return partial[0] if len(partial) == 1 else None


def _positive_limit(arguments: tuple[str, ...], *, default: int) -> int:
    if not arguments:
        return default
    if len(arguments) != 1 or not arguments[0].isdigit():
        raise ValueError("Use `/catchup [positive-number]`.")
    value = int(arguments[0])
    if value < 1 or value > 20:
        raise ValueError("The message limit must be between 1 and 20.")
    return value


def _history_options(arguments: tuple[str, ...]) -> tuple[int, int]:
    limit = 3
    page = 1
    positional: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--page":
            index += 1
            if index >= len(arguments) or not arguments[index].isdigit():
                raise ValueError("Use `/history [turns] [--page N]`.")
            page = int(arguments[index])
        elif argument.startswith("--page="):
            value = argument.partition("=")[2]
            if not value.isdigit():
                raise ValueError("Use `/history [turns] [--page N]`.")
            page = int(value)
        else:
            positional.append(argument)
        index += 1
    if len(positional) > 1 or (positional and not positional[0].isdigit()):
        raise ValueError("Use `/history [turns] [--page N]`.")
    if positional:
        limit = int(positional[0])
    if limit < 1 or limit > 20 or page < 1:
        raise ValueError("History turns must be between 1 and 20 and page must be positive.")
    return limit, page

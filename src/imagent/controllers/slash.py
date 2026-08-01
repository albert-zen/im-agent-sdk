from __future__ import annotations

import shlex
from dataclasses import dataclass

from ..contracts import (
    ApplicationOperationFailed,
    ApplicationRef,
    ApplicationsListed,
    ApplicationSummary,
    ApprovalResponse,
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationThread,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    CreateThread,
    DeleteThread,
    GatewayOperationFailed,
    GetThreadHistory,
    GetThreadStatus,
    GetTurnCatchup,
    InboundMessage,
    ListApplications,
    ListProjects,
    ListThreads,
    ObserveThread,
    OutboundMessage,
    ProjectMode,
    ProjectsListed,
    ProjectSummary,
    RequestRef,
    RequestResponseRouted,
    RespondToRequest,
    SelectApplication,
    TextContent,
    ThreadCreated,
    ThreadDeleted,
    ThreadDeletionCapability,
    ThreadDeletionMode,
    ThreadHistoryRead,
    ThreadObserved,
    ThreadsListed,
    ThreadStatusRead,
    ThreadSummary,
    TurnCatchupRead,
    UserInputResponse,
)
from .base import ControllerActions
from .markdown import MarkdownSlashPresenter


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    arguments: tuple[str, ...]
    raw: str


def parse_slash_command(message: InboundMessage) -> SlashCommand | None:
    text = "\n".join(part.text for part in message.content if isinstance(part, TextContent)).strip()
    if not text.startswith("/"):
        return None
    try:
        tokens = shlex.split(text)
    except ValueError as error:
        raise ValueError(f"Invalid slash command: {error}") from error
    if not tokens:
        return None
    return SlashCommand(
        name=tokens[0][1:].casefold(),
        arguments=tuple(tokens[1:]),
        raw=text,
    )


@dataclass(frozen=True, slots=True)
class _ApplicationContext:
    binding: ConversationBinding
    application: ApplicationSummary


class _CommandError(RuntimeError):
    pass


class SlashController:
    """Official optional common Slash UX over the typed operation surfaces."""

    def __init__(self, presenter: MarkdownSlashPresenter | None = None) -> None:
        self._presenter = presenter or MarkdownSlashPresenter()
        self._project_views: dict[ConversationRef, tuple[ProjectSummary, ...]] = {}
        self._thread_views: dict[ConversationRef, tuple[ThreadSummary, ...]] = {}

    async def handle(
        self,
        message: InboundMessage,
        actions: ControllerActions,
    ) -> tuple[OutboundMessage, ...] | None:
        try:
            command = parse_slash_command(message)
        except ValueError as error:
            return (self._presenter.response(message, str(error), error=True),)
        if command is None:
            return None
        try:
            text = await self._dispatch(message, command, actions)
            return (self._presenter.response(message, text),)
        except _CommandError as error:
            return (self._presenter.response(message, str(error), error=True),)

    async def _dispatch(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ControllerActions,
    ) -> str:
        if command.name in {"help", "start"}:
            return self._presenter.help()
        if command.name == "apps":
            return self._presenter.applications(await self._applications(message, actions))
        if command.name == "app":
            return await self._select_application(message, command, actions)
        if command.name in {"respond", "answer"}:
            return await self._respond_to_request(message, command, actions)

        context = await self._ensure_application_binding(message, actions)
        if command.name == "projects":
            projects = await self._list_projects(
                message,
                actions,
                context,
                query=" ".join(command.arguments) or None,
            )
            self._project_views[message.conversation_ref] = projects
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
            self._thread_views[message.conversation_ref] = threads
            return self._presenter.threads(threads)
        if command.name in {"pick", "thread"}:
            return await self._select_thread(message, command, actions, context)
        if command.name == "new":
            return await self._create_thread(message, command, actions, context)
        if command.name in {"delete", "archive"}:
            return await self._delete_thread(message, actions, context)
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
        actions: ControllerActions,
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
        result = await actions.execute_gateway(
            RespondToRequest(
                operation_id=_operation_id(
                    message,
                    "conversation.respond_request",
                ),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                request_ref=request_ref,
                response=response,
                created_at=message.created_at,
            )
        )
        if isinstance(result, GatewayOperationFailed):
            raise _CommandError(result.error.message)
        if not isinstance(result, RequestResponseRouted):
            raise _CommandError("Request response returned an incompatible result.")
        return "Response submitted to the Agent application."

    async def _applications(
        self,
        message: InboundMessage,
        actions: ControllerActions,
    ) -> tuple[ApplicationSummary, ...]:
        result = await actions.execute_gateway(
            ListApplications(
                operation_id=_operation_id(message, "application.list"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                created_at=message.created_at,
            )
        )
        if isinstance(result, GatewayOperationFailed):
            raise _CommandError(result.error.message)
        if not isinstance(result, ApplicationsListed):
            raise _CommandError("Application listing returned an incompatible result.")
        return result.applications

    async def _select_application(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ControllerActions,
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
        current = await actions.get_binding(message.conversation_ref)
        result = await actions.execute_gateway(
            SelectApplication(
                operation_id=_operation_id(message, "application.select"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                application_ref=selected.ref,
                expected_revision=current.revision if current is not None else None,
                created_at=message.created_at,
            )
        )
        _require_bound(result)
        self._clear_views(message)
        return (
            f"Selected application **{selected.display_name}** "
            f"(`{selected.ref.application_instance_id}`)."
        )

    async def _ensure_application_binding(
        self,
        message: InboundMessage,
        actions: ControllerActions,
    ) -> _ApplicationContext:
        binding = await actions.get_binding(message.conversation_ref)
        applications = await self._applications(message, actions)
        if binding is not None and binding.application_ref is not None:
            application = next(
                (item for item in applications if item.ref == binding.application_ref),
                None,
            )
            if application is None:
                raise _CommandError("Selected Agent application is unavailable.")
            return _ApplicationContext(binding=binding, application=application)
        if len(applications) != 1:
            raise _CommandError("Choose an Agent application with `/apps` and `/app <number>`.")
        application = applications[0]
        result = await actions.execute_gateway(
            SelectApplication(
                operation_id=_operation_id(message, "application.select"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                application_ref=application.ref,
                expected_revision=binding.revision if binding is not None else None,
                created_at=message.created_at,
            )
        )
        selected = _require_bound(result)
        return _ApplicationContext(binding=selected, application=application)

    async def _list_projects(
        self,
        message: InboundMessage,
        actions: ControllerActions,
        context: _ApplicationContext,
        *,
        query: str | None = None,
    ) -> tuple[ProjectSummary, ...]:
        result = await actions.execute_application(
            ListProjects(
                operation_id=_operation_id(message, "project.list"),
                application_ref=context.application.ref,
                query=query,
                created_at=message.created_at,
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            raise _CommandError(result.error.message)
        if not isinstance(result, ProjectsListed):
            raise _CommandError("Project listing returned an incompatible result.")
        return result.projects.items

    async def _select_project(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ControllerActions,
        context: _ApplicationContext,
    ) -> str:
        if not command.arguments:
            raise _CommandError("Use `/use <number-or-project>` after `/projects`.")
        projects = self._project_views.get(message.conversation_ref)
        if projects is None:
            projects = await self._list_projects(message, actions, context)
            self._project_views[message.conversation_ref] = projects
        project = _select(
            projects,
            " ".join(command.arguments),
            id_of=lambda item: item.ref.native_project_id,
            label_of=lambda item: item.display_name,
        )
        if project is None:
            raise _CommandError("Project not found.")
        result = await actions.execute_gateway(
            BindConversationToProject(
                operation_id=_operation_id(message, "conversation.bind_project"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                project_ref=project.ref,
                expected_revision=context.binding.revision,
                created_at=message.created_at,
            )
        )
        _require_bound(result)
        self._thread_views.pop(message.conversation_ref, None)
        return f"Selected project **{project.display_name}** (`{project.ref.native_project_id}`)."

    async def _list_threads(
        self,
        message: InboundMessage,
        actions: ControllerActions,
        context: _ApplicationContext,
        *,
        query: str | None = None,
    ) -> tuple[ThreadSummary, ...]:
        result = await actions.execute_application(
            ListThreads(
                operation_id=_operation_id(message, "thread.list"),
                application_ref=context.application.ref,
                project_ref=context.binding.project_ref,
                query=query,
                created_at=message.created_at,
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            raise _CommandError(result.error.message)
        if not isinstance(result, ThreadsListed):
            raise _CommandError("Thread listing returned an incompatible result.")
        return result.threads.items

    async def _select_thread(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ControllerActions,
        context: _ApplicationContext,
    ) -> str:
        if not command.arguments:
            raise _CommandError("Use `/pick <number-or-thread>` after `/threads`.")
        threads = self._thread_views.get(message.conversation_ref)
        if threads is None:
            threads = await self._list_threads(message, actions, context)
            self._thread_views[message.conversation_ref] = threads
        thread = _select(
            threads,
            " ".join(command.arguments),
            id_of=lambda item: item.ref.native_thread_id,
            label_of=lambda item: item.title or "",
        )
        if thread is None:
            raise _CommandError("Thread not found.")
        result = await actions.execute_gateway(
            BindConversationToThread(
                operation_id=_operation_id(message, "conversation.bind_thread"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                thread_ref=thread.ref,
                expected_revision=context.binding.revision,
                created_at=message.created_at,
            )
        )
        _require_bound(result)
        _require_observed(
            await actions.execute_gateway(
                ObserveThread(
                    operation_id=_operation_id(message, "thread.observe"),
                    conversation_ref=message.conversation_ref,
                    actor=message.sender,
                    thread_ref=thread.ref,
                    reply_to_message_id=message.message_id,
                    created_at=message.created_at,
                )
            )
        )
        return (
            f"Selected thread **{thread.title or thread.ref.native_thread_id}** "
            f"(`{thread.ref.native_thread_id}`)."
        )

    async def _create_thread(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ControllerActions,
        context: _ApplicationContext,
    ) -> str:
        if (
            context.application.capabilities.projects.mode is ProjectMode.MANAGED
            and context.binding.project_ref is None
        ):
            raise _CommandError("Choose a project first with `/projects` and `/use <number>`.")
        result = await actions.execute_application(
            CreateThread(
                operation_id=_operation_id(message, "thread.create"),
                application_ref=context.application.ref,
                project_ref=context.binding.project_ref,
                title=" ".join(command.arguments) or "IM task",
                created_at=message.created_at,
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            raise _CommandError(result.error.message)
        if not isinstance(result, ThreadCreated):
            raise _CommandError("Thread creation returned an incompatible result.")
        thread = result.thread
        bound = await actions.execute_gateway(
            BindConversationToThread(
                operation_id=_operation_id(message, "conversation.bind_thread"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                thread_ref=thread.ref,
                expected_revision=context.binding.revision,
                created_at=message.created_at,
            )
        )
        _require_bound(bound)
        _require_observed(
            await actions.execute_gateway(
                ObserveThread(
                    operation_id=_operation_id(message, "thread.observe"),
                    conversation_ref=message.conversation_ref,
                    actor=message.sender,
                    thread_ref=thread.ref,
                    reply_to_message_id=message.message_id,
                    created_at=message.created_at,
                )
            )
        )
        self._thread_views.pop(message.conversation_ref, None)
        return (
            f"Created thread **{thread.title or thread.ref.native_thread_id}** "
            f"(`{thread.ref.native_thread_id}`)."
        )

    async def _delete_thread(
        self,
        message: InboundMessage,
        actions: ControllerActions,
        context: _ApplicationContext,
    ) -> str:
        thread_ref = context.binding.thread_ref
        if thread_ref is None:
            raise _CommandError("No thread is selected.")
        capability = context.application.capabilities.threads.deletion
        if capability is ThreadDeletionCapability.UNSUPPORTED:
            raise _CommandError("Thread deletion is unsupported.")
        result = await actions.execute_application(
            DeleteThread(
                operation_id=_operation_id(message, "thread.delete"),
                application_ref=context.application.ref,
                thread_ref=thread_ref,
                mode=ThreadDeletionMode(capability.value),
                created_at=message.created_at,
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            raise _CommandError(result.error.message)
        if not isinstance(result, ThreadDeleted):
            raise _CommandError("Thread deletion returned an incompatible result.")
        clear = await actions.execute_gateway(
            ClearConversationThread(
                operation_id=_operation_id(message, "conversation.clear_thread"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                expected_revision=context.binding.revision,
                created_at=message.created_at,
            )
        )
        _require_bound(clear)
        self._thread_views.pop(message.conversation_ref, None)
        if result.mode is ThreadDeletionMode.ARCHIVE:
            return f"Archived thread `{thread_ref.native_thread_id}`."
        return f"Deleted thread `{thread_ref.native_thread_id}`."

    async def _thread_status(
        self,
        message: InboundMessage,
        actions: ControllerActions,
        context: _ApplicationContext,
    ) -> str:
        thread_ref = context.binding.thread_ref
        if thread_ref is None:
            raise _CommandError("No thread is selected.")
        result = await actions.execute_application(
            GetThreadStatus(
                operation_id=_operation_id(message, "thread.status"),
                application_ref=context.application.ref,
                thread_ref=thread_ref,
                created_at=message.created_at,
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            raise _CommandError(result.error.message)
        if not isinstance(result, ThreadStatusRead):
            raise _CommandError("Thread status returned an incompatible result.")
        return f"Thread `{thread_ref.native_thread_id}` is **{result.thread_status.value}**."

    async def _turn_catchup(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ControllerActions,
        context: _ApplicationContext,
    ) -> str:
        thread_ref = context.binding.thread_ref
        if thread_ref is None:
            raise _CommandError("No thread is selected.")
        try:
            limit = _positive_limit(command.arguments, default=5)
        except ValueError as error:
            raise _CommandError(str(error)) from error
        result = await actions.execute_application(
            GetTurnCatchup(
                operation_id=_operation_id(message, "turn.catchup"),
                application_ref=context.application.ref,
                thread_ref=thread_ref,
                limit=limit,
                created_at=message.created_at,
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            raise _CommandError(result.error.message)
        if not isinstance(result, TurnCatchupRead):
            raise _CommandError("Turn catch-up returned an incompatible result.")
        return self._presenter.turn_catchup(result.catchup)

    async def _thread_history(
        self,
        message: InboundMessage,
        command: SlashCommand,
        actions: ControllerActions,
        context: _ApplicationContext,
    ) -> str:
        thread_ref = context.binding.thread_ref
        if thread_ref is None:
            raise _CommandError("No thread is selected.")
        try:
            limit, page = _history_options(command.arguments)
        except ValueError as error:
            raise _CommandError(str(error)) from error
        result = await actions.execute_application(
            GetThreadHistory(
                operation_id=_operation_id(message, "thread.history"),
                application_ref=context.application.ref,
                thread_ref=thread_ref,
                limit=limit,
                page=page,
                created_at=message.created_at,
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            raise _CommandError(result.error.message)
        if not isinstance(result, ThreadHistoryRead):
            raise _CommandError("Thread history returned an incompatible result.")
        return self._presenter.thread_history(result.history)

    def _clear_views(self, message: InboundMessage) -> None:
        self._project_views.pop(message.conversation_ref, None)
        self._thread_views.pop(message.conversation_ref, None)


def _require_bound(result) -> ConversationBinding:
    if isinstance(result, GatewayOperationFailed):
        raise _CommandError(result.error.message)
    if not isinstance(result, ConversationBound):
        raise _CommandError("Binding operation returned an incompatible result.")
    return result.binding


def _require_observed(result) -> None:
    if isinstance(result, GatewayOperationFailed):
        raise _CommandError(result.error.message)
    if not isinstance(result, ThreadObserved):
        raise _CommandError("Thread observation returned an incompatible result.")


def _operation_id(message: InboundMessage, operation_type: str) -> str:
    return f"imagent:operation:{message.message_id}:{operation_type}"


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

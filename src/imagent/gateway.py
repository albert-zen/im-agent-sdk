from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

from .adapters import (
    AgentApplicationAdapter,
    BindingRepository,
    ChannelAdapter,
    IdempotencyRepository,
)
from .bindings import BindingConflict
from .commands import SlashCommand, parse_slash_command
from .contracts import (
    AgentEventType,
    AgentInput,
    AgentMessage,
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    ApplicationsListed,
    BindConversationToProject,
    BindConversationToThread,
    ChannelMessage,
    ClearConversationThread,
    ContractError,
    ConversationBinding,
    ConversationBound,
    CreateThread,
    DeleteThread,
    GatewayOperation,
    GatewayOperationFailed,
    GatewayOperationResult,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetThreadStatus,
    GetTurnCatchup,
    ListApplications,
    ListProjects,
    ListThreads,
    MessageRole,
    OperationErrorCode,
    ProjectRead,
    ProjectsListed,
    ProjectSummary,
    SelectApplication,
    TextContent,
    TextFormat,
    ThreadCreated,
    ThreadDeleted,
    ThreadDeletionCapability,
    ThreadDeletionMode,
    ThreadHistoryRead,
    ThreadRead,
    ThreadsListed,
    ThreadStatusRead,
    ThreadSummary,
    TurnCatchupRead,
    derive_client_message_id,
    operation_error,
    validate_application_operation,
    validate_application_operation_result,
    validate_gateway_operation,
    validate_gateway_operation_result,
)
from .history_rendering import render_thread_history, render_turn_catchup
from .storage import InMemoryIdempotencyRepository

logger = logging.getLogger(__name__)


class ImAgentGateway:
    """The public deep module joining Channel and Agent application seams."""

    def __init__(
        self,
        *,
        channels: list[ChannelAdapter],
        applications: list[AgentApplicationAdapter],
        bindings: BindingRepository,
        idempotency: IdempotencyRepository | None = None,
    ) -> None:
        self._channels = {channel.channel_instance_id: channel for channel in channels}
        self._applications = {
            application.summary.ref.application_instance_id: application
            for application in applications
        }
        self._bindings = bindings
        self._idempotency = idempotency or InMemoryIdempotencyRepository()
        self._locks: dict[object, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._project_views: dict[object, tuple[ProjectSummary, ...]] = {}
        self._thread_views: dict[object, tuple[ThreadSummary, ...]] = {}

    async def start(self) -> None:
        for application in self._applications.values():
            await application.start()
        for channel in self._channels.values():
            await channel.start(self._handle_message, self._handle_operation)

    async def stop(self) -> None:
        for channel in reversed(tuple(self._channels.values())):
            await channel.stop()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for application in reversed(tuple(self._applications.values())):
            await application.stop()

    async def execute_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        """Route one typed application operation without mutating a binding."""
        try:
            validate_application_operation(operation)
            application = self._applications[operation.application_ref.application_instance_id]
        except Exception as error:
            return ApplicationOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=datetime.now(UTC),
                error=_contract_error(error),
            )
        result = await application.execute(operation)
        try:
            validate_application_operation_result(operation, result)
        except Exception as error:
            return ApplicationOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=datetime.now(UTC),
                error=_contract_error(error),
            )
        return result

    async def execute_gateway(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        """Execute one typed Gateway operation under Conversation serialization."""
        lock = self._locks.setdefault(operation.conversation_ref, asyncio.Lock())
        async with lock:
            return await self._execute_gateway_locked(operation)

    async def _execute_gateway_locked(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        try:
            validate_gateway_operation(operation)
            result = await self._apply_gateway_operation(operation)
            validate_gateway_operation_result(operation, result)
            return result
        except _GatewayActionError as error:
            contract_error = error.error
        except Exception as error:
            contract_error = _contract_error(error)
        return GatewayOperationFailed(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=datetime.now(UTC),
            error=contract_error,
        )

    async def _apply_gateway_operation(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        completed_at = datetime.now(UTC)
        if isinstance(operation, ListApplications):
            return ApplicationsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                applications=tuple(
                    application.summary for application in self._applications.values()
                ),
            )
        if isinstance(operation, SelectApplication):
            self._require_application(operation.application_ref.application_instance_id)
            binding = await self._bindings.put(
                ConversationBinding(
                    conversation_ref=operation.conversation_ref,
                    application_ref=operation.application_ref,
                ),
                expected_revision=operation.expected_revision,
            )
            return ConversationBound(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=completed_at,
                binding=binding,
            )
        if isinstance(operation, BindConversationToProject):
            application = self._require_application(operation.project_ref.application_instance_id)
            read = await self.execute_application(
                GetProject(
                    operation_id=f"{operation.operation_id}:validate-project",
                    application_ref=application.summary.ref,
                    project_ref=operation.project_ref,
                    created_at=operation.created_at,
                )
            )
            if isinstance(read, ApplicationOperationFailed):
                raise _GatewayActionError(read.error)
            if not isinstance(read, ProjectRead):
                raise RuntimeError("project.get returned an incompatible result")
            binding = await self._bindings.put(
                ConversationBinding(
                    conversation_ref=operation.conversation_ref,
                    application_ref=application.summary.ref,
                    project_ref=read.project.ref,
                ),
                expected_revision=operation.expected_revision,
            )
            return ConversationBound(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=completed_at,
                binding=binding,
            )
        if isinstance(operation, BindConversationToThread):
            application = self._require_application(operation.thread_ref.application_instance_id)
            read = await self.execute_application(
                GetThread(
                    operation_id=f"{operation.operation_id}:validate-thread",
                    application_ref=application.summary.ref,
                    thread_ref=operation.thread_ref,
                    created_at=operation.created_at,
                )
            )
            if isinstance(read, ApplicationOperationFailed):
                raise _GatewayActionError(read.error)
            if not isinstance(read, ThreadRead):
                raise RuntimeError("thread.get returned an incompatible result")
            binding = await self._bindings.put(
                ConversationBinding(
                    conversation_ref=operation.conversation_ref,
                    application_ref=application.summary.ref,
                    project_ref=read.thread.ref.project_ref,
                    thread_ref=read.thread.ref,
                ),
                expected_revision=operation.expected_revision,
            )
            return ConversationBound(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=completed_at,
                binding=binding,
            )
        if isinstance(operation, ClearConversationThread):
            current = await self._bindings.get(operation.conversation_ref)
            if current is None:
                raise ValueError("Conversation has no binding")
            binding = await self._bindings.put(
                ConversationBinding(
                    conversation_ref=current.conversation_ref,
                    application_ref=current.application_ref,
                    project_ref=current.project_ref,
                ),
                expected_revision=operation.expected_revision,
            )
            return ConversationBound(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=completed_at,
                binding=binding,
            )
        raise NotImplementedError(operation.type.value)

    async def _handle_message(self, message: ChannelMessage) -> None:
        scope = f"inbound:{message.conversation_ref.channel_instance_id}"
        key = f"{message.conversation_ref.native_conversation_id}:{message.message_id}"
        if not await self._idempotency.claim(scope, key):
            return
        try:
            await self._process_message(message)
        except BaseException:
            await self._idempotency.release(scope, key)
            raise
        await self._idempotency.complete(scope, key)

    async def _process_message(self, message: ChannelMessage) -> None:
        lock = self._locks.setdefault(message.conversation_ref, asyncio.Lock())
        async with lock:
            try:
                command = parse_slash_command(message)
            except ValueError as error:
                await self._deliver_error(message, str(error))
                return
            if command is not None:
                await self._handle_command(message, command)
                return
            binding = await self._bindings.get(message.conversation_ref)
            application = self._bound_application(binding)
            if binding is None or binding.application_ref is None:
                application = self._single_application_or_none()
                if application is None:
                    await self._deliver_text(
                        message,
                        "Choose an Agent application first with `/apps` and "
                        "`/app <number-or-name>`.",
                    )
                    return
                selection = await self._execute_gateway_locked(
                    SelectApplication(
                        operation_id=_operation_id(message, "application.select"),
                        conversation_ref=message.conversation_ref,
                        actor=message.sender,
                        application_ref=application.summary.ref,
                        expected_revision=binding.revision if binding is not None else None,
                        created_at=message.created_at,
                    )
                )
                if not isinstance(selection, ConversationBound):
                    await self._deliver_operation_error(message, selection)
                    return
                binding = selection.binding
            if application is None:
                raise RuntimeError("bound Agent application is unavailable")
            if binding.thread_ref is None:
                if (
                    application.summary.capabilities.projects.mode.value == "managed"
                    and binding.project_ref is None
                ):
                    await self._deliver_text(
                        message,
                        "Choose a project first with `/projects` and `/use <number>`.",
                    )
                    return
                create = CreateThread(
                    operation_id=_operation_id(message, "thread.create"),
                    application_ref=application.summary.ref,
                    project_ref=binding.project_ref,
                    created_at=message.created_at,
                )
                result = await self.execute_application(create)
                if not isinstance(result, ThreadCreated):
                    await self._deliver_operation_error(message, result)
                    return
                bind = await self._execute_gateway_locked(
                    BindConversationToThread(
                        operation_id=_operation_id(
                            message,
                            "conversation.bind_thread",
                        ),
                        conversation_ref=message.conversation_ref,
                        actor=message.sender,
                        thread_ref=result.thread.ref,
                        expected_revision=binding.revision,
                        created_at=message.created_at,
                    )
                )
                if not isinstance(bind, ConversationBound):
                    await self._deliver_operation_error(message, bind)
                    return
                binding = bind.binding
            thread_ref = binding.thread_ref
            if thread_ref is None:
                raise RuntimeError("thread binding was not established")
            agent_input = AgentInput(
                client_message_id=derive_client_message_id(
                    message.conversation_ref,
                    message.message_id,
                ),
                content=message.content,
                sender=message.sender,
                metadata={"channel_message_id": message.message_id},
            )
            accepted = await application.send_input(thread_ref, agent_input)
            task = asyncio.create_task(
                self._project_turn(
                    message=message,
                    application=application,
                    accepted=accepted,
                )
            )
            self._tasks.add(task)
            task.add_done_callback(self._finish_task)

    def _finish_task(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.exception(
                "Agent event projection failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _handle_command(
        self,
        message: ChannelMessage,
        command: SlashCommand,
    ) -> None:
        if command.name in {"help", "start"}:
            await self._deliver_text(message, _HELP)
            return
        if command.name == "apps":
            await self._deliver_text(message, self._render_applications())
            return
        if command.name == "app":
            await self._select_application(message, command)
            return

        binding, application = await self._ensure_application_binding(message)
        if binding is None or application is None:
            return
        if command.name == "projects":
            result = await self.execute_application(
                ListProjects(
                    operation_id=_operation_id(message, "project.list"),
                    application_ref=application.summary.ref,
                    query=" ".join(command.arguments) or None,
                    created_at=message.created_at,
                )
            )
            if not isinstance(result, ProjectsListed):
                await self._deliver_operation_error(message, result)
                return
            projects = result.projects.items
            self._project_views[message.conversation_ref] = projects
            await self._deliver_text(message, _render_projects(projects))
            return
        if command.name in {"use", "project"}:
            await self._select_project(message, command, binding, application)
            return
        if command.name == "threads":
            result = await self.execute_application(
                ListThreads(
                    operation_id=_operation_id(message, "thread.list"),
                    application_ref=application.summary.ref,
                    project_ref=binding.project_ref,
                    query=" ".join(command.arguments) or None,
                    created_at=message.created_at,
                )
            )
            if not isinstance(result, ThreadsListed):
                await self._deliver_operation_error(message, result)
                return
            threads = result.threads.items
            self._thread_views[message.conversation_ref] = threads
            await self._deliver_text(message, _render_threads(threads))
            return
        if command.name in {"pick", "thread"}:
            await self._select_thread(message, command, binding, application)
            return
        if command.name == "new":
            await self._create_thread(message, command, binding, application)
            return
        if command.name in {"delete", "archive"}:
            await self._delete_thread(message, binding, application)
            return
        if command.name == "status":
            await self._thread_status(message, binding, application)
            return
        if command.name == "catchup":
            await self._turn_catchup(message, command, binding, application)
            return
        if command.name == "history":
            await self._thread_history(message, command, binding, application)
            return
        await self._deliver_error(
            message,
            f"Unknown command `/{command.name}`. Use `/help`.",
        )

    async def _select_application(
        self,
        message: ChannelMessage,
        command: SlashCommand,
    ) -> None:
        if not command.arguments:
            await self._deliver_text(message, self._render_applications())
            return
        applications = tuple(self._applications.values())
        selected = _select(
            applications,
            " ".join(command.arguments),
            id_of=lambda item: item.summary.ref.application_instance_id,
            label_of=lambda item: item.summary.display_name,
        )
        if selected is None:
            await self._deliver_error(message, "Agent application not found.")
            return
        current = await self._bindings.get(message.conversation_ref)
        result = await self._execute_gateway_locked(
            SelectApplication(
                operation_id=_operation_id(message, "application.select"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                application_ref=selected.summary.ref,
                expected_revision=current.revision if current is not None else None,
                created_at=message.created_at,
            )
        )
        if not isinstance(result, ConversationBound):
            await self._deliver_operation_error(message, result)
            return
        self._clear_views(message)
        await self._deliver_text(
            message,
            f"Selected application **{selected.summary.display_name}** "
            f"(`{selected.summary.ref.application_instance_id}`).",
        )

    async def _select_project(
        self,
        message: ChannelMessage,
        command: SlashCommand,
        binding: ConversationBinding,
        application: AgentApplicationAdapter,
    ) -> None:
        if not command.arguments:
            await self._deliver_error(
                message,
                "Use `/use <number-or-project>` after `/projects`.",
            )
            return
        projects = self._project_views.get(message.conversation_ref)
        if projects is None:
            list_result = await self.execute_application(
                ListProjects(
                    operation_id=_operation_id(message, "project.list"),
                    application_ref=application.summary.ref,
                    created_at=message.created_at,
                )
            )
            if not isinstance(list_result, ProjectsListed):
                await self._deliver_operation_error(message, list_result)
                return
            projects = list_result.projects.items
            self._project_views[message.conversation_ref] = projects
        project = _select(
            projects,
            " ".join(command.arguments),
            id_of=lambda item: item.ref.native_project_id,
            label_of=lambda item: item.display_name,
        )
        if project is None:
            await self._deliver_error(message, "Project not found.")
            return
        result = await self._execute_gateway_locked(
            BindConversationToProject(
                operation_id=_operation_id(
                    message,
                    "conversation.bind_project",
                ),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                project_ref=project.ref,
                expected_revision=binding.revision,
                created_at=message.created_at,
            )
        )
        if not isinstance(result, ConversationBound):
            await self._deliver_operation_error(message, result)
            return
        self._thread_views.pop(message.conversation_ref, None)
        await self._deliver_text(
            message,
            f"Selected project **{project.display_name}** (`{project.ref.native_project_id}`).",
        )

    async def _create_thread(
        self,
        message: ChannelMessage,
        command: SlashCommand,
        binding: ConversationBinding,
        application: AgentApplicationAdapter,
    ) -> None:
        if (
            application.summary.capabilities.projects.mode.value == "managed"
            and binding.project_ref is None
        ):
            await self._deliver_error(
                message,
                "Choose a project first with `/projects` and `/use <number>`.",
            )
            return
        result = await self.execute_application(
            CreateThread(
                operation_id=_operation_id(message, "thread.create"),
                application_ref=application.summary.ref,
                project_ref=binding.project_ref,
                title=" ".join(command.arguments) or "IM task",
                created_at=message.created_at,
            )
        )
        if not isinstance(result, ThreadCreated):
            await self._deliver_operation_error(message, result)
            return
        thread = result.thread
        bind = await self._execute_gateway_locked(
            BindConversationToThread(
                operation_id=_operation_id(message, "conversation.bind_thread"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                thread_ref=thread.ref,
                expected_revision=binding.revision,
                created_at=message.created_at,
            )
        )
        if not isinstance(bind, ConversationBound):
            await self._deliver_operation_error(message, bind)
            return
        self._thread_views.pop(message.conversation_ref, None)
        await self._deliver_text(
            message,
            f"Created thread **{thread.title or thread.ref.native_thread_id}** "
            f"(`{thread.ref.native_thread_id}`).",
        )

    async def _select_thread(
        self,
        message: ChannelMessage,
        command: SlashCommand,
        binding: ConversationBinding,
        application: AgentApplicationAdapter,
    ) -> None:
        if not command.arguments:
            await self._deliver_error(
                message,
                "Use `/pick <number-or-thread>` after `/threads`.",
            )
            return
        threads = self._thread_views.get(message.conversation_ref)
        if threads is None:
            list_result = await self.execute_application(
                ListThreads(
                    operation_id=_operation_id(message, "thread.list"),
                    application_ref=application.summary.ref,
                    project_ref=binding.project_ref,
                    created_at=message.created_at,
                )
            )
            if not isinstance(list_result, ThreadsListed):
                await self._deliver_operation_error(message, list_result)
                return
            threads = list_result.threads.items
            self._thread_views[message.conversation_ref] = threads
        thread = _select(
            threads,
            " ".join(command.arguments),
            id_of=lambda item: item.ref.native_thread_id,
            label_of=lambda item: item.title or "",
        )
        if thread is None:
            await self._deliver_error(message, "Thread not found.")
            return
        result = await self._execute_gateway_locked(
            BindConversationToThread(
                operation_id=_operation_id(message, "conversation.bind_thread"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                thread_ref=thread.ref,
                expected_revision=binding.revision,
                created_at=message.created_at,
            )
        )
        if not isinstance(result, ConversationBound):
            await self._deliver_operation_error(message, result)
            return
        await self._deliver_text(
            message,
            f"Selected thread **{thread.title or thread.ref.native_thread_id}** "
            f"(`{thread.ref.native_thread_id}`).",
        )

    async def _delete_thread(
        self,
        message: ChannelMessage,
        binding: ConversationBinding,
        application: AgentApplicationAdapter,
    ) -> None:
        if binding.thread_ref is None:
            await self._deliver_error(message, "No thread is selected.")
            return
        capability = application.summary.capabilities.threads.deletion
        if capability is ThreadDeletionCapability.UNSUPPORTED:
            await self._deliver_error(message, "Thread deletion is unsupported.")
            return
        result = await self.execute_application(
            DeleteThread(
                operation_id=_operation_id(message, "thread.delete"),
                application_ref=application.summary.ref,
                thread_ref=binding.thread_ref,
                mode=ThreadDeletionMode(capability.value),
                created_at=message.created_at,
            )
        )
        if not isinstance(result, ThreadDeleted):
            await self._deliver_operation_error(message, result)
            return
        deleted_id = binding.thread_ref.native_thread_id
        clear = await self._execute_gateway_locked(
            ClearConversationThread(
                operation_id=_operation_id(message, "conversation.clear_thread"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                expected_revision=binding.revision,
                created_at=message.created_at,
            )
        )
        if not isinstance(clear, ConversationBound):
            await self._deliver_operation_error(message, clear)
            return
        self._thread_views.pop(message.conversation_ref, None)
        await self._deliver_text(
            message,
            (
                f"Archived thread `{deleted_id}`."
                if result.mode is ThreadDeletionMode.ARCHIVE
                else f"Deleted thread `{deleted_id}`."
            ),
        )

    async def _thread_status(
        self,
        message: ChannelMessage,
        binding: ConversationBinding,
        application: AgentApplicationAdapter,
    ) -> None:
        if binding.thread_ref is None:
            await self._deliver_error(message, "No thread is selected.")
            return
        result = await self.execute_application(
            GetThreadStatus(
                operation_id=_operation_id(message, "thread.status"),
                application_ref=application.summary.ref,
                thread_ref=binding.thread_ref,
                created_at=message.created_at,
            )
        )
        if not isinstance(result, ThreadStatusRead):
            await self._deliver_operation_error(message, result)
            return
        await self._deliver_text(
            message,
            f"Thread `{binding.thread_ref.native_thread_id}` is **{result.thread_status.value}**.",
        )

    async def _turn_catchup(
        self,
        message: ChannelMessage,
        command: SlashCommand,
        binding: ConversationBinding,
        application: AgentApplicationAdapter,
    ) -> None:
        if binding.thread_ref is None:
            await self._deliver_error(message, "No thread is selected.")
            return
        try:
            limit = _positive_limit(command.arguments, default=5)
        except ValueError as error:
            await self._deliver_error(message, str(error))
            return
        result = await self.execute_application(
            GetTurnCatchup(
                operation_id=_operation_id(message, "turn.catchup"),
                application_ref=application.summary.ref,
                thread_ref=binding.thread_ref,
                limit=limit,
                created_at=message.created_at,
            )
        )
        if not isinstance(result, TurnCatchupRead):
            await self._deliver_operation_error(message, result)
            return
        await self._deliver_text(message, render_turn_catchup(result.catchup))

    async def _thread_history(
        self,
        message: ChannelMessage,
        command: SlashCommand,
        binding: ConversationBinding,
        application: AgentApplicationAdapter,
    ) -> None:
        if binding.thread_ref is None:
            await self._deliver_error(message, "No thread is selected.")
            return
        try:
            limit, page = _history_options(command.arguments)
        except ValueError as error:
            await self._deliver_error(message, str(error))
            return
        result = await self.execute_application(
            GetThreadHistory(
                operation_id=_operation_id(message, "thread.history"),
                application_ref=application.summary.ref,
                thread_ref=binding.thread_ref,
                limit=limit,
                page=page,
                created_at=message.created_at,
            )
        )
        if not isinstance(result, ThreadHistoryRead):
            await self._deliver_operation_error(message, result)
            return
        await self._deliver_text(message, render_thread_history(result.history))

    async def _ensure_application_binding(
        self,
        message: ChannelMessage,
    ) -> tuple[
        ConversationBinding | None,
        AgentApplicationAdapter | None,
    ]:
        binding = await self._bindings.get(message.conversation_ref)
        application = self._bound_application(binding)
        if application is not None and binding is not None:
            return binding, application
        application = self._single_application_or_none()
        if application is None:
            await self._deliver_text(
                message,
                "Choose an Agent application with `/apps` and `/app <number>`.",
            )
            return None, None
        result = await self._execute_gateway_locked(
            SelectApplication(
                operation_id=_operation_id(message, "application.select"),
                conversation_ref=message.conversation_ref,
                actor=message.sender,
                application_ref=application.summary.ref,
                expected_revision=binding.revision if binding is not None else None,
                created_at=message.created_at,
            )
        )
        if not isinstance(result, ConversationBound):
            await self._deliver_operation_error(message, result)
            return None, None
        return result.binding, application

    async def _handle_operation(self, operation: GatewayOperation) -> None:
        result = await self.execute_gateway(operation)
        if isinstance(result, GatewayOperationFailed):
            logger.warning(
                "Gateway operation %s failed: %s",
                operation.operation_id,
                result.error.message,
            )

    async def _project_turn(
        self,
        *,
        message: ChannelMessage,
        application: AgentApplicationAdapter,
        accepted,
    ) -> None:
        async for event in application.subscribe_thread(accepted.thread_ref):
            if event.turn_id not in {None, accepted.turn_id}:
                continue
            if event.type is AgentEventType.MESSAGE_COMPLETED:
                agent_message = event.data.get("message")
                if isinstance(agent_message, AgentMessage):
                    await self._deliver_agent_message(message, agent_message)
            if event.type in {
                AgentEventType.TURN_COMPLETED,
                AgentEventType.TURN_FAILED,
                AgentEventType.TURN_INTERRUPTED,
            }:
                return

    async def _deliver_agent_message(
        self,
        inbound: ChannelMessage,
        agent_message: AgentMessage,
    ) -> None:
        channel = self._channels[inbound.conversation_ref.channel_instance_id]
        delivery_id = (
            f"imagent:delivery:{agent_message.thread_ref.native_thread_id}:"
            f"{agent_message.agent_item_id}"
        )
        scope = f"outbound:{inbound.conversation_ref.channel_instance_id}"
        if not await self._idempotency.claim(scope, delivery_id):
            return
        try:
            await channel.send(
                ChannelMessage(
                    message_id=agent_message.agent_item_id,
                    conversation_ref=inbound.conversation_ref,
                    sender=agent_message.thread_ref.application_instance_id,
                    content=tuple(
                        TextContent(item.text, TextFormat.MARKDOWN)
                        if isinstance(item, TextContent)
                        else item
                        for item in agent_message.content
                    ),
                    created_at=agent_message.created_at,
                    role=MessageRole.ASSISTANT,
                    reply_to=inbound.message_id,
                    client_message_id=delivery_id,
                )
            )
        except BaseException:
            await self._idempotency.release(scope, delivery_id)
            raise
        await self._idempotency.complete(scope, delivery_id)

    async def _deliver_error(
        self,
        inbound: ChannelMessage,
        text: str,
    ) -> None:
        channel = self._channels[inbound.conversation_ref.channel_instance_id]
        await channel.send(
            ChannelMessage(
                message_id=f"error:{uuid4()}",
                conversation_ref=inbound.conversation_ref,
                sender="im-agent-sdk",
                content=(TextContent(f"**Error:** {text}", TextFormat.MARKDOWN),),
                created_at=datetime.now(UTC),
                role=MessageRole.SYSTEM,
                reply_to=inbound.message_id,
            )
        )

    async def _deliver_operation_error(
        self,
        inbound: ChannelMessage,
        result: ApplicationOperationResult | GatewayOperationResult,
    ) -> None:
        error = (
            result.error
            if isinstance(result, (ApplicationOperationFailed, GatewayOperationFailed))
            else None
        )
        await self._deliver_error(
            inbound,
            error.message if error is not None else "Operation returned an incompatible result.",
        )

    async def _deliver_text(
        self,
        inbound: ChannelMessage,
        text: str,
    ) -> None:
        channel = self._channels[inbound.conversation_ref.channel_instance_id]
        await channel.send(
            ChannelMessage(
                message_id=f"system:{uuid4()}",
                conversation_ref=inbound.conversation_ref,
                sender="im-agent-sdk",
                content=(TextContent(text, TextFormat.MARKDOWN),),
                created_at=datetime.now(UTC),
                role=MessageRole.SYSTEM,
                reply_to=inbound.message_id,
            )
        )

    def _bound_application(
        self,
        binding: ConversationBinding | None,
    ) -> AgentApplicationAdapter | None:
        if binding is None or binding.application_ref is None:
            return None
        try:
            return self._applications[binding.application_ref.application_instance_id]
        except KeyError as error:
            raise RuntimeError("bound Agent application is not registered") from error

    def _single_application_or_none(self) -> AgentApplicationAdapter | None:
        if len(self._applications) != 1:
            return None
        return next(iter(self._applications.values()))

    def _require_application(
        self,
        application_instance_id: str,
    ) -> AgentApplicationAdapter:
        try:
            return self._applications[application_instance_id]
        except KeyError as error:
            raise KeyError(
                f"Agent application is not registered: {application_instance_id}"
            ) from error

    def _render_applications(self) -> str:
        lines = ["## Agent applications", ""]
        for index, application in enumerate(self._applications.values(), start=1):
            lines.append(
                f"{index}. **{application.summary.display_name}** "
                f"(`{application.summary.ref.application_instance_id}`)"
            )
        lines.extend(["", "Use `/app <number-or-name>`."])
        return "\n".join(lines)

    def _clear_views(self, message: ChannelMessage) -> None:
        self._project_views.pop(message.conversation_ref, None)
        self._thread_views.pop(message.conversation_ref, None)


class _GatewayActionError(RuntimeError):
    def __init__(self, error: ContractError) -> None:
        super().__init__(error.message)
        self.error = error


def _contract_error(error: Exception) -> ContractError:
    if isinstance(error, BindingConflict):
        return operation_error(error, code=OperationErrorCode.CONFLICT)
    return operation_error(error)


def _operation_id(message: ChannelMessage, operation_type: str) -> str:
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


def _render_projects(projects: tuple[ProjectSummary, ...]) -> str:
    if not projects:
        return "No projects found."
    lines = ["## Projects", ""]
    for index, project in enumerate(projects, start=1):
        lines.append(f"{index}. **{project.display_name}** (`{project.ref.native_project_id}`)")
    lines.extend(["", "Use `/use <number-or-project>`."])
    return "\n".join(lines)


def _render_threads(threads: tuple[ThreadSummary, ...]) -> str:
    if not threads:
        return "No threads found."
    lines = ["## Threads", ""]
    for index, thread in enumerate(threads, start=1):
        lines.append(
            f"{index}. **{thread.title or 'Untitled'}** "
            f"(`{thread.ref.native_thread_id}`) — {thread.status.value}"
        )
    lines.extend(["", "Use `/pick <number-or-thread>`."])
    return "\n".join(lines)


_HELP = """## IM Agent commands

- `/apps` and `/app <selector>` — list or select an Agent application
- `/projects` and `/use <selector>` — list or select a project
- `/threads` and `/pick <selector>` — list or select a thread
- `/new [title]` — create and select a thread
- `/delete` — archive/delete the selected thread
- `/status` — show the selected thread status
- `/catchup [messages]` — show recent progress in the latest Turn
- `/history [turns] [--page N]` — restore context from recent Turns
"""


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

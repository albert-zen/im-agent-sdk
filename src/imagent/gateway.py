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
from .commands import SlashCommand, parse_slash_command
from .contracts import (
    AgentEventType,
    AgentInput,
    AgentMessage,
    ChannelMessage,
    ConversationBinding,
    MessageRole,
    Operation,
    OperationResult,
    OperationResultStatus,
    OperationTarget,
    OperationType,
    Page,
    ProjectSummary,
    TextContent,
    TextFormat,
    ThreadStatus,
    ThreadSummary,
    derive_client_message_id,
)
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
                binding = await self._bindings.put(
                    ConversationBinding(
                        conversation_ref=message.conversation_ref,
                        application_ref=application.summary.ref,
                    )
                )
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
                create = self._operation(
                    message,
                    OperationType.THREAD_CREATE,
                    OperationTarget(
                        application_ref=application.summary.ref,
                        project_ref=binding.project_ref,
                    ),
                    {},
                )
                result = await application.execute(create)
                if result.status is not OperationResultStatus.SUCCEEDED:
                    await self._deliver_operation_error(message, result)
                    return
                thread = result.value
                if not isinstance(thread, ThreadSummary):
                    await self._deliver_error(
                        message,
                        "Agent application did not return the created thread.",
                    )
                    return
                binding = await self._bindings.put(
                    ConversationBinding(
                        conversation_ref=binding.conversation_ref,
                        application_ref=binding.application_ref,
                        project_ref=binding.project_ref,
                        thread_ref=thread.ref,
                    ),
                    expected_revision=binding.revision,
                )
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
            result = await application.execute(
                self._operation(
                    message,
                    OperationType.PROJECT_LIST,
                    OperationTarget(application_ref=application.summary.ref),
                    {"query": " ".join(command.arguments)},
                )
            )
            projects = self._page_items(result, ProjectSummary)
            if projects is None:
                await self._deliver_operation_error(message, result)
                return
            self._project_views[message.conversation_ref] = projects
            await self._deliver_text(message, _render_projects(projects))
            return
        if command.name in {"use", "project"}:
            await self._select_project(message, command, binding, application)
            return
        if command.name == "threads":
            result = await application.execute(
                self._operation(
                    message,
                    OperationType.THREAD_LIST,
                    OperationTarget(
                        application_ref=application.summary.ref,
                        project_ref=binding.project_ref,
                    ),
                    {"query": " ".join(command.arguments)},
                )
            )
            threads = self._page_items(result, ThreadSummary)
            if threads is None:
                await self._deliver_operation_error(message, result)
                return
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
        await self._bindings.put(
            ConversationBinding(
                conversation_ref=message.conversation_ref,
                application_ref=selected.summary.ref,
            ),
            expected_revision=current.revision if current is not None else None,
        )
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
            result = await application.execute(
                self._operation(
                    message,
                    OperationType.PROJECT_LIST,
                    OperationTarget(application_ref=application.summary.ref),
                    {},
                )
            )
            projects = self._page_items(result, ProjectSummary)
            if projects is None:
                await self._deliver_operation_error(message, result)
                return
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
        result = await application.execute(
            self._operation(
                message,
                OperationType.PROJECT_SELECT,
                OperationTarget(
                    application_ref=application.summary.ref,
                    project_ref=project.ref,
                ),
                {},
            )
        )
        if result.status is not OperationResultStatus.SUCCEEDED:
            await self._deliver_operation_error(message, result)
            return
        await self._bindings.put(
            ConversationBinding(
                conversation_ref=binding.conversation_ref,
                application_ref=binding.application_ref,
                project_ref=project.ref,
            ),
            expected_revision=binding.revision,
        )
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
        result = await application.execute(
            self._operation(
                message,
                OperationType.THREAD_CREATE,
                OperationTarget(
                    application_ref=application.summary.ref,
                    project_ref=binding.project_ref,
                ),
                {"title": " ".join(command.arguments) or "IM task"},
            )
        )
        if result.status is not OperationResultStatus.SUCCEEDED or not isinstance(
            result.value, ThreadSummary
        ):
            await self._deliver_operation_error(message, result)
            return
        thread = result.value
        await self._bindings.put(
            ConversationBinding(
                conversation_ref=binding.conversation_ref,
                application_ref=binding.application_ref,
                project_ref=binding.project_ref,
                thread_ref=thread.ref,
            ),
            expected_revision=binding.revision,
        )
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
            result = await application.execute(
                self._operation(
                    message,
                    OperationType.THREAD_LIST,
                    OperationTarget(
                        application_ref=application.summary.ref,
                        project_ref=binding.project_ref,
                    ),
                    {},
                )
            )
            threads = self._page_items(result, ThreadSummary)
            if threads is None:
                await self._deliver_operation_error(message, result)
                return
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
        result = await application.execute(
            self._operation(
                message,
                OperationType.THREAD_SWITCH,
                OperationTarget(
                    application_ref=application.summary.ref,
                    project_ref=binding.project_ref,
                    thread_ref=thread.ref,
                ),
                {},
            )
        )
        if result.status is not OperationResultStatus.SUCCEEDED:
            await self._deliver_operation_error(message, result)
            return
        await self._bindings.put(
            ConversationBinding(
                conversation_ref=binding.conversation_ref,
                application_ref=binding.application_ref,
                project_ref=binding.project_ref,
                thread_ref=thread.ref,
            ),
            expected_revision=binding.revision,
        )
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
        result = await application.execute(
            self._operation(
                message,
                OperationType.THREAD_DELETE,
                OperationTarget(
                    application_ref=application.summary.ref,
                    project_ref=binding.project_ref,
                    thread_ref=binding.thread_ref,
                ),
                {},
            )
        )
        if result.status is not OperationResultStatus.SUCCEEDED:
            await self._deliver_operation_error(message, result)
            return
        deleted_id = binding.thread_ref.native_thread_id
        await self._bindings.put(
            ConversationBinding(
                conversation_ref=binding.conversation_ref,
                application_ref=binding.application_ref,
                project_ref=binding.project_ref,
            ),
            expected_revision=binding.revision,
        )
        self._thread_views.pop(message.conversation_ref, None)
        await self._deliver_text(message, f"Archived thread `{deleted_id}`.")

    async def _thread_status(
        self,
        message: ChannelMessage,
        binding: ConversationBinding,
        application: AgentApplicationAdapter,
    ) -> None:
        if binding.thread_ref is None:
            await self._deliver_error(message, "No thread is selected.")
            return
        result = await application.execute(
            self._operation(
                message,
                OperationType.THREAD_STATUS,
                OperationTarget(
                    application_ref=application.summary.ref,
                    project_ref=binding.project_ref,
                    thread_ref=binding.thread_ref,
                ),
                {},
            )
        )
        if result.status is not OperationResultStatus.SUCCEEDED:
            await self._deliver_operation_error(message, result)
            return
        status = result.value.value if isinstance(result.value, ThreadStatus) else str(result.value)
        await self._deliver_text(
            message,
            f"Thread `{binding.thread_ref.native_thread_id}` is **{status}**.",
        )

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
        binding = await self._bindings.put(
            ConversationBinding(
                conversation_ref=message.conversation_ref,
                application_ref=application.summary.ref,
            ),
            expected_revision=binding.revision if binding is not None else None,
        )
        return binding, application

    async def _handle_operation(self, operation: Operation) -> None:
        del operation
        raise NotImplementedError("native channel operations are not implemented yet")

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
        result: OperationResult,
    ) -> None:
        await self._deliver_error(
            inbound,
            result.error.message
            if result.error is not None
            else "Agent application operation failed.",
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

    @staticmethod
    def _page_items(result: OperationResult, item_type):
        if (
            result.status is not OperationResultStatus.SUCCEEDED
            or not isinstance(result.value, Page)
            or not all(isinstance(item, item_type) for item in result.value.items)
        ):
            return None
        return result.value.items

    @staticmethod
    def _operation(
        message: ChannelMessage,
        operation_type: OperationType,
        target: OperationTarget,
        arguments: dict[str, object],
    ) -> Operation:
        return Operation(
            operation_id=f"imagent:operation:{message.message_id}:{operation_type.value}",
            conversation_ref=message.conversation_ref,
            actor=message.sender,
            type=operation_type,
            target=target,
            arguments=arguments,
            created_at=message.created_at,
        )


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
"""

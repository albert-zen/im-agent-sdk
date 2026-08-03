from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import mimetypes
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..contracts import (
    AcceptedTurn,
    ActivateNativeThread,
    AgentEvent,
    AgentEventType,
    AgentInput,
    AgentMessage,
    ApplicationCapabilities,
    ApplicationInputDispatch,
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    ApplicationRef,
    ApplicationSummary,
    CreateThread,
    DeleteThread,
    EventSequenceScope,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetThreadStatus,
    GetTurnCatchup,
    InputContinuationPreference,
    InputDisposition,
    InteractiveRequest,
    InterruptTurn,
    ListProjects,
    ListThreads,
    Page,
    ProjectCapabilities,
    ProjectMode,
    ProjectRead,
    ProjectRef,
    ProjectsListed,
    ProjectSummary,
    RespondRequest,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
    ThreadCreated,
    ThreadDeleted,
    ThreadDeletionCapability,
    ThreadDeletionMode,
    ThreadHistory,
    ThreadHistoryRead,
    ThreadRead,
    ThreadRef,
    ThreadsListed,
    ThreadStatus,
    ThreadStatusRead,
    ThreadSummary,
    TurnCatchup,
    TurnCatchupRead,
    TurnHistoryEntry,
    TurnInterrupted,
    TurnReplyCorrelationPolicy,
    TurnStatus,
    validate_application_operation,
    validate_application_operation_result,
)
from ..diagnostics import ApplicationDiagnosticFacts
from ..events import EventBroadcaster, EventStreamGap, EventStreamReset
from ..interaction.media import (
    AttachmentContent,
    AttachmentSourceKind,
    configure_shared_filesystem_root,
    resolve_local_attachment,
)
from ..interaction.messages import MessageRole, TextContent, TextFormat
from ..interaction.operations import operation_error
from .presentation import (
    ApplicationPresentationCapacityError,
    ApplicationPresentationLimits,
    ApplicationPresentationRuntime,
    T3ActivityFacts,
    T3ActivityPresenter,
)

_ID_NAMESPACE = uuid.UUID("15440f13-a923-4a4c-8791-637914777e5e")
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _T3PresentationState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    seen_activity_ids: set[str] = field(default_factory=set)
    activity_cursor_id: str | None = None
    pinned_by_poll: bool = False


class T3Client(Protocol):
    async def shell_snapshot(self) -> Mapping[str, object]: ...

    async def thread_detail(self, thread_id: str) -> Mapping[str, object]: ...

    async def dispatch(
        self,
        command: Mapping[str, object],
    ) -> Mapping[str, object]: ...


class T3ApplicationAdapter:
    """Native T3 project, thread and turn operations behind one application seam."""

    def __init__(
        self,
        *,
        application_instance_id: str,
        client: T3Client,
        runtime_mode: str = "full-access",
        interaction_mode: str = "default",
        poll_interval: float = 0.25,
        shared_filesystem_root: str | Path | None = None,
        event_buffer_max_pending: int = 1024,
        activity_presenter: T3ActivityPresenter | None = None,
        presentation_limits: ApplicationPresentationLimits = ApplicationPresentationLimits(),
    ) -> None:
        self._application_instance_id = application_instance_id
        self._client = client
        self._runtime_mode = runtime_mode
        self._interaction_mode = interaction_mode
        self._poll_interval = poll_interval
        self._shared_filesystem_root = configure_shared_filesystem_root(shared_filesystem_root)
        self._activity_presenter = activity_presenter
        self._presentation_limits = presentation_limits
        self._presentation_runtime = (
            ApplicationPresentationRuntime(presentation_limits)
            if activity_presenter is not None
            else None
        )
        self._turn_baselines: dict[tuple[str, str], frozenset[str]] = {}
        self._events = EventBroadcaster[str, AgentEvent](max_pending=event_buffer_max_pending)
        self._poll_tasks: dict[str, asyncio.Task[None]] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._presentation_states: dict[str, _T3PresentationState] = {}
        self._seen_messages: dict[str, set[str]] = {}
        self._terminal_turns: dict[str, set[str]] = {}
        self._initialized_threads: set[str] = set()
        self._summary = ApplicationSummary(
            ref=ApplicationRef(application_instance_id),
            kind="t3",
            display_name="T3 Code",
            capabilities=ApplicationCapabilities(
                projects=ProjectCapabilities(
                    mode=ProjectMode.MANAGED,
                    discovery=SupportLevel.NATIVE,
                    reading=SupportLevel.NATIVE,
                ),
                threads=ThreadCapabilities(
                    listing=SupportLevel.NATIVE,
                    creation=SupportLevel.NATIVE,
                    reading=SupportLevel.NATIVE,
                    deletion=ThreadDeletionCapability.ARCHIVE,
                ),
                runtime=RuntimeCapabilities(
                    history=SupportLevel.NATIVE,
                    streaming=SupportLevel.FALLBACK,
                    replay_from_cursor=SupportLevel.UNSUPPORTED,
                    interruption=SupportLevel.NATIVE,
                    interactive_requests=SupportLevel.UNSUPPORTED,
                    native_thread_activation=SupportLevel.UNSUPPORTED,
                    gap_detection=SupportLevel.UNSUPPORTED,
                    event_sequence_scope=EventSequenceScope.NONE,
                ),
                attachment_sources=(
                    (AttachmentSourceKind.LOCAL_PATH,)
                    if self._shared_filesystem_root is not None
                    else ()
                ),
            ),
            metadata={
                "runtime_mode": runtime_mode,
                "interaction_mode": interaction_mode,
                "protocol": "t3-orchestration",
            },
        )

    @property
    def summary(self) -> ApplicationSummary:
        return self._summary

    def diagnostic_facts(self) -> ApplicationDiagnosticFacts:
        """T3 HTTP request/response has no meaningful long-lived connection epoch."""

        return ApplicationDiagnosticFacts(
            application_instance_id=self._application_instance_id,
            kind=self._summary.kind,
            presentation=(
                self._presentation_runtime.diagnostic_facts()
                if self._presentation_runtime is not None
                else None
            ),
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        tasks = tuple(self._poll_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._poll_tasks.clear()
        self._send_locks.clear()
        self._presentation_states.clear()
        try:
            close = getattr(self._client, "aclose", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
        finally:
            if self._presentation_runtime is not None:
                await self._presentation_runtime.close()

    async def list_pending_requests(self) -> tuple[InteractiveRequest, ...]:
        raise NotImplementedError("T3 does not expose an interactive request response API")

    async def execute(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        try:
            validate_application_operation(operation)
            result = await self._execute(operation)
            validate_application_operation_result(operation, result)
            return result
        except Exception as error:
            return ApplicationOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=datetime.now(UTC),
                error=operation_error(error),
            )

    async def _execute(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        completed_at = datetime.now(UTC)
        if isinstance(operation, ListProjects):
            snapshot = await self._client.shell_snapshot()
            query = (operation.query or "").casefold()
            projects = tuple(
                summary
                for project in _object_list(snapshot.get("projects"))
                if (summary := self._project_summary(project)) is not None
                and (
                    not query
                    or query in summary.display_name.casefold()
                    or query in summary.ref.native_project_id.casefold()
                )
            )
            return ProjectsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                projects=Page(items=projects),
            )
        if isinstance(operation, GetProject):
            project = await self._find_project(operation.project_ref.native_project_id)
            if project is None:
                raise ValueError(f"T3 project not found: {operation.project_ref.native_project_id}")
            return ProjectRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                project=project,
            )
        if isinstance(operation, ListThreads):
            snapshot = await self._client.shell_snapshot()
            project_ref = operation.project_ref
            query = (operation.query or "").casefold()
            threads = tuple(
                summary
                for thread in _object_list(snapshot.get("threads"))
                if (summary := self._thread_summary(thread)) is not None
                and (project_ref is None or summary.ref.project_ref == project_ref)
                and (
                    not query
                    or query in (summary.title or "").casefold()
                    or query in summary.ref.native_thread_id.casefold()
                )
            )
            return ThreadsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                threads=Page(items=threads),
            )
        if isinstance(operation, CreateThread):
            project_ref = operation.project_ref
            if project_ref is None:
                raise ValueError("thread.create requires project_ref")
            if operation.initial_context:
                raise NotImplementedError("initial thread context is unsupported by T3")
            project = await self._find_project(project_ref.native_project_id)
            if project is None:
                raise ValueError(f"T3 project not found: {project_ref.native_project_id}")
            model_selection = project.metadata.get("default_model_selection")
            if not isinstance(model_selection, Mapping):
                raise ValueError("T3 project has no default Agent/model selection")
            thread_id = _stable_id(operation.operation_id, "thread")
            title = _safe_title(operation.title or "IM task")
            now = _utc_now()
            await self._client.dispatch(
                {
                    "type": "thread.create",
                    "commandId": _stable_id(operation.operation_id, "create"),
                    "threadId": thread_id,
                    "projectId": project_ref.native_project_id,
                    "title": title,
                    "modelSelection": dict(model_selection),
                    "runtimeMode": self._runtime_mode,
                    "interactionMode": self._interaction_mode,
                    "branch": None,
                    "worktreePath": None,
                    "createdAt": now,
                }
            )
            return ThreadCreated(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread=ThreadSummary(
                    ref=ThreadRef(
                        application_instance_id=self._application_instance_id,
                        native_thread_id=thread_id,
                        project_ref=project_ref,
                    ),
                    title=title,
                    status=ThreadStatus.IDLE,
                    updated_at=datetime.now(UTC),
                    metadata={
                        "runtime_mode": self._runtime_mode,
                        "model_selection": dict(model_selection),
                    },
                ),
            )
        if isinstance(operation, (GetThread, GetThreadStatus)):
            thread_ref = operation.thread_ref
            self._require_own_thread(thread_ref)
            detail = await self._client.thread_detail(thread_ref.native_thread_id)
            summary = self._thread_summary(_object(detail.get("thread"), "thread"))
            if summary is None:
                raise ValueError("T3 thread is archived or deleted")
            if isinstance(operation, GetThreadStatus):
                return ThreadStatusRead(
                    operation_id=operation.operation_id,
                    completed_at=completed_at,
                    thread_ref=thread_ref,
                    thread_status=summary.status,
                )
            return ThreadRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread=summary,
            )
        if isinstance(operation, GetTurnCatchup):
            thread_ref = operation.thread_ref
            self._require_own_thread(thread_ref)
            detail = await self._client.thread_detail(thread_ref.native_thread_id)
            thread = _object(detail.get("thread"), "thread")
            latest_turn = _optional_object(thread.get("latestTurn"))
            if latest_turn is None:
                return TurnCatchupRead(
                    operation_id=operation.operation_id,
                    completed_at=completed_at,
                    catchup=TurnCatchup(
                        thread_ref=thread_ref,
                        turn_id=None,
                        status=TurnStatus.IDLE,
                        messages=(),
                    ),
                )
            turn_id = str(latest_turn.get("turnId") or latest_turn.get("id") or "")
            if not turn_id:
                raise RuntimeError("T3 latestTurn did not contain a turn id")
            messages = await self._t3_catchup_messages(
                thread_ref,
                thread,
                turn_id,
            )
            return TurnCatchupRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                catchup=TurnCatchup(
                    thread_ref=thread_ref,
                    turn_id=turn_id,
                    status=_turn_status(latest_turn.get("state") or latest_turn.get("status")),
                    messages=messages[-operation.limit :],
                    updated_at=_parse_optional_datetime(
                        latest_turn.get("completedAt")
                        or latest_turn.get("startedAt")
                        or latest_turn.get("requestedAt")
                        or thread.get("updatedAt")
                    ),
                    metadata={"native_application": "t3"},
                ),
            )
        if isinstance(operation, GetThreadHistory):
            thread_ref = operation.thread_ref
            self._require_own_thread(thread_ref)
            detail = await self._client.thread_detail(thread_ref.native_thread_id)
            thread = _object(detail.get("thread"), "thread")
            turns = await self._t3_history_entries(thread_ref, thread)
            end = max(0, len(turns) - ((operation.page - 1) * operation.limit))
            start = max(0, end - operation.limit)
            return ThreadHistoryRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                history=ThreadHistory(
                    thread_ref=thread_ref,
                    turns=turns[start:end],
                    page=operation.page,
                    has_older=start > 0,
                    metadata={"native_application": "t3"},
                ),
            )
        if isinstance(operation, DeleteThread):
            thread_ref = operation.thread_ref
            self._require_own_thread(thread_ref)
            if operation.mode is not ThreadDeletionMode.ARCHIVE:
                raise NotImplementedError("T3 supports archive, not permanent deletion")
            await self._client.dispatch(
                {
                    "type": "thread.archive",
                    "commandId": _stable_id(operation.operation_id, "archive"),
                    "threadId": thread_ref.native_thread_id,
                }
            )
            return ThreadDeleted(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread_ref=thread_ref,
                mode=ThreadDeletionMode.ARCHIVE,
            )
        if isinstance(operation, InterruptTurn):
            thread_ref = operation.thread_ref
            self._require_own_thread(thread_ref)
            command: dict[str, object] = {
                "type": "thread.turn.interrupt",
                "commandId": _stable_id(operation.operation_id, "interrupt"),
                "threadId": thread_ref.native_thread_id,
                "createdAt": _utc_now(),
            }
            turn_id = operation.turn_id
            if turn_id:
                command["turnId"] = turn_id
            await self._client.dispatch(command)
            return TurnInterrupted(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread_ref=thread_ref,
                turn_id=turn_id,
            )
        if isinstance(operation, (ActivateNativeThread, RespondRequest)):
            raise NotImplementedError(f"{operation.type.value} is unsupported by T3")
        raise NotImplementedError(f"{operation.type.value} is unsupported by T3")

    async def _t3_catchup_messages(
        self,
        thread_ref: ThreadRef,
        thread: Mapping[str, object],
        turn_id: str,
    ) -> tuple[AgentMessage, ...]:
        ordered: list[tuple[str, int, AgentMessage]] = []
        sequence = 0
        for message in _object_list(thread.get("messages")):
            if (
                str(message.get("turnId") or "") != turn_id
                or str(message.get("role") or "") != "assistant"
            ):
                continue
            projected = _t3_agent_message(thread_ref, message)
            if projected is not None:
                ordered.append(
                    (
                        str(message.get("updatedAt") or message.get("createdAt") or ""),
                        sequence,
                        projected,
                    )
                )
                sequence += 1
        for activity in _object_list(thread.get("activities")):
            if str(activity.get("turnId") or "") != turn_id:
                continue
            projected = await self._t3_activity_message(thread_ref, activity)
            if projected is not None:
                ordered.append(
                    (
                        str(activity.get("createdAt") or ""),
                        sequence,
                        projected,
                    )
                )
                sequence += 1
        ordered.sort(key=lambda item: (item[0], item[1]))
        return tuple(item[2] for item in ordered)

    async def _t3_history_entries(
        self,
        thread_ref: ThreadRef,
        thread: Mapping[str, object],
    ) -> tuple[TurnHistoryEntry, ...]:
        grouped: dict[str, list[Mapping[str, object]]] = {}
        order: list[str] = []
        for message in _object_list(thread.get("messages")):
            turn_id = str(message.get("turnId") or "")
            if not turn_id:
                continue
            if turn_id not in grouped:
                grouped[turn_id] = []
                order.append(turn_id)
            grouped[turn_id].append(message)
        for activity in _object_list(thread.get("activities")):
            turn_id = str(activity.get("turnId") or "")
            if turn_id and turn_id not in grouped:
                grouped[turn_id] = []
                order.append(turn_id)
        latest_turn = _optional_object(thread.get("latestTurn"))
        latest_id = (
            str(latest_turn.get("turnId") or latest_turn.get("id") or "")
            if latest_turn is not None
            else ""
        )
        checkpoints = {
            str(item.get("turnId") or "")
            for item in _object_list(thread.get("checkpoints"))
            if item.get("turnId")
        }
        entries: list[TurnHistoryEntry] = []
        for turn_id in order:
            messages = grouped[turn_id]
            user_message = next(
                (
                    projected
                    for item in messages
                    if str(item.get("role") or "") == "user"
                    and (projected := _t3_agent_message(thread_ref, item)) is not None
                ),
                None,
            )
            agent_messages = await self._t3_catchup_messages(
                thread_ref,
                thread,
                turn_id,
            )
            status = (
                _turn_status(latest_turn.get("state") or latest_turn.get("status"))
                if latest_turn is not None and turn_id == latest_id
                else (
                    TurnStatus.COMPLETED
                    if turn_id in checkpoints or agent_messages
                    else TurnStatus.UNKNOWN
                )
            )
            entries.append(
                TurnHistoryEntry(
                    turn_id=turn_id,
                    status=status,
                    user_message=user_message,
                    agent_messages=agent_messages,
                    error=_t3_turn_error(thread, turn_id, status),
                    metadata={"native_application": "t3"},
                )
            )
        return tuple(entries)

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: Callable[[ApplicationInputDispatch], Awaitable[None]] | None = None,
    ) -> AcceptedTurn:
        if not isinstance(continuation, InputContinuationPreference):
            raise ValueError("unknown input continuation preference")
        self._require_own_thread(thread_ref)
        lock = self._send_locks.setdefault(
            thread_ref.native_thread_id,
            asyncio.Lock(),
        )
        async with lock:
            return await self._send_input_locked(
                thread_ref,
                message,
                before_dispatch=before_dispatch,
            )

    async def _send_input_locked(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        before_dispatch: Callable[[ApplicationInputDispatch], Awaitable[None]] | None,
    ) -> AcceptedTurn:
        text = "\n".join(
            part.text for part in message.content if isinstance(part, TextContent)
        ).strip()
        attachments = tuple(part for part in message.content if isinstance(part, AttachmentContent))
        if not text and not attachments:
            raise ValueError("T3 input requires text or image")
        before = await self._client.thread_detail(thread_ref.native_thread_id)
        baseline = frozenset(
            _message_id(item)
            for item in _object_list(_object(before.get("thread"), "thread").get("messages"))
            if _message_id(item)
        )
        command = {
            "type": "thread.turn.start",
            "commandId": _stable_id(message.client_message_id, "turn"),
            "threadId": thread_ref.native_thread_id,
            "message": {
                "messageId": _stable_id(message.client_message_id, "message"),
                "role": "user",
                "text": text,
                "attachments": _encode_t3_attachments(
                    attachments,
                    shared_filesystem_root=self._shared_filesystem_root,
                ),
            },
            "runtimeMode": self._runtime_mode,
            "interactionMode": self._interaction_mode,
            "createdAt": _utc_now(),
        }
        if before_dispatch is not None:
            await before_dispatch(
                ApplicationInputDispatch(
                    thread_ref=thread_ref,
                    client_message_id=message.client_message_id,
                    disposition=InputDisposition.STARTED,
                    correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
                )
            )
        await self._client.dispatch(command)
        detail = await self._client.thread_detail(thread_ref.native_thread_id)
        thread = _object(detail.get("thread"), "thread")
        latest_turn = _object(thread.get("latestTurn"), "latestTurn")
        turn_id = str(latest_turn.get("turnId") or latest_turn.get("id") or "")
        if not turn_id:
            raise RuntimeError("T3 did not return the accepted turn id")
        self._turn_baselines[(thread_ref.native_thread_id, turn_id)] = baseline
        if self._activity_presenter is None:
            await self._publish_thread_state(
                thread_ref,
                thread,
                only_turn_id=turn_id,
            )
        return AcceptedTurn(
            thread_ref=thread_ref,
            turn_id=turn_id,
            client_message_id=message.client_message_id,
            disposition=InputDisposition.STARTED,
            correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
        )

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if after_cursor is not None:
            raise NotImplementedError("T3 does not support event replay")
        self._require_own_thread(thread_ref)
        thread_id = thread_ref.native_thread_id
        subscription = self._events.subscribe(thread_id)
        task = self._poll_tasks.get(thread_id)
        if task is None or task.done():
            task = asyncio.create_task(self._poll_thread(thread_ref))
            self._poll_tasks[thread_id] = task
            task.add_done_callback(
                lambda completed, subscribed_thread_id=thread_id: self._finish_poll_task(
                    subscribed_thread_id,
                    completed,
                )
            )
        return subscription

    async def _poll_thread(self, thread_ref: ThreadRef) -> None:
        thread_id = thread_ref.native_thread_id
        presentation_state = (
            self._presentation_state(thread_id) if self._activity_presenter is not None else None
        )
        if presentation_state is not None:
            presentation_state.pinned_by_poll = True
        try:
            while self._events.subscriber_count(thread_id):
                detail = await self._client.thread_detail(thread_ref.native_thread_id)
                thread = _object(detail.get("thread"), "thread")
                initialize = thread_id not in self._initialized_threads
                await self._publish_thread_state(
                    thread_ref,
                    thread,
                    initialize=initialize,
                    _presentation_state_override=presentation_state,
                )
                self._initialized_threads.add(thread_id)
                await asyncio.sleep(self._poll_interval)
        finally:
            if presentation_state is not None:
                presentation_state.pinned_by_poll = False

    async def _publish_thread_state(
        self,
        thread_ref: ThreadRef,
        thread: Mapping[str, object],
        *,
        only_turn_id: str | None = None,
        initialize: bool = False,
        _presentation_state_override: _T3PresentationState | None = None,
    ) -> None:
        state = (
            _presentation_state_override or self._presentation_state(thread_ref.native_thread_id)
            if self._activity_presenter is not None
            else _T3PresentationState()
        )
        async with state.lock:
            await self._publish_thread_state_locked(
                thread_ref,
                thread,
                presentation_state=state,
                only_turn_id=only_turn_id,
                initialize=initialize,
            )

    async def _publish_thread_state_locked(
        self,
        thread_ref: ThreadRef,
        thread: Mapping[str, object],
        *,
        presentation_state: _T3PresentationState,
        only_turn_id: str | None = None,
        initialize: bool = False,
    ) -> None:
        thread_id = thread_ref.native_thread_id
        seen = self._seen_messages.setdefault(thread_id, set())
        messages = _object_list(thread.get("messages"))
        if self._activity_presenter is None:
            for message in messages:
                message_id = _message_id(message)
                turn_id = str(message.get("turnId") or "")
                if not self._should_publish_t3_message(
                    thread_id,
                    message_id,
                    turn_id,
                    message,
                    seen=seen,
                    only_turn_id=only_turn_id,
                    initialize=initialize,
                ):
                    continue
                seen.add(message_id)
                self._publish_t3_message(thread_ref, message, message_id, turn_id)

        if self._activity_presenter is not None:
            seen_activities = presentation_state.seen_activity_ids
            all_activities = _object_list(thread.get("activities"))
            activities = self._new_t3_activities(
                all_activities,
                presentation_state=presentation_state,
                initialize=initialize,
            )
            ordered: list[tuple[str, int, str, Mapping[str, object]]] = []
            sequence = 0
            for message in messages:
                message_id = _message_id(message)
                turn_id = str(message.get("turnId") or "")
                if not self._should_publish_t3_message(
                    thread_id,
                    message_id,
                    turn_id,
                    message,
                    seen=seen,
                    only_turn_id=only_turn_id,
                    initialize=initialize,
                ):
                    continue
                ordered.append(
                    (
                        str(message.get("updatedAt") or message.get("createdAt") or ""),
                        sequence,
                        "message",
                        message,
                    )
                )
                sequence += 1
            for activity in activities:
                activity_id = str(activity.get("id") or "")
                activity_turn_id = str(activity.get("turnId") or "")
                if (
                    not activity_id
                    or activity_id in seen_activities
                    or (only_turn_id is not None and activity_turn_id != only_turn_id)
                ):
                    continue
                if initialize and self._turn_baselines.get((thread_id, activity_turn_id)) is None:
                    seen_activities.add(activity_id)
                    continue
                ordered.append(
                    (
                        str(activity.get("createdAt") or ""),
                        sequence,
                        "activity",
                        activity,
                    )
                )
                sequence += 1
            ordered.sort(key=lambda item: (item[0], item[1]))
            for _, _, candidate_kind, candidate in ordered:
                if candidate_kind == "message":
                    message_id = _message_id(candidate)
                    turn_id = str(candidate.get("turnId") or "")
                    seen.add(message_id)
                    self._publish_t3_message(thread_ref, candidate, message_id, turn_id)
                    continue
                activity_id = str(candidate.get("id") or "")
                activity_turn_id = str(candidate.get("turnId") or "")
                try:
                    projected = await self._t3_activity_message(thread_ref, candidate)
                except Exception:
                    raise
                seen_activities.add(activity_id)
                if projected is not None:
                    self._events.publish(
                        thread_id,
                        self._event(
                            AgentEventType.MESSAGE_COMPLETED,
                            thread_ref,
                            activity_turn_id,
                            {"message": projected},
                        ),
                    )
            if all_activities:
                final_activity_id = str(all_activities[-1].get("id") or "")
                if final_activity_id:
                    presentation_state.activity_cursor_id = final_activity_id

        latest_turn = _optional_object(thread.get("latestTurn"))
        turn_id = (
            str(latest_turn.get("turnId") or latest_turn.get("id") or "")
            if latest_turn is not None
            else ""
        )
        if only_turn_id is not None and turn_id != only_turn_id:
            return
        state = (
            str(latest_turn.get("state") or latest_turn.get("status") or "")
            .replace("-", "_")
            .casefold()
            if latest_turn is not None
            else ""
        )
        event_type = {
            "completed": AgentEventType.TURN_COMPLETED,
            "failed": AgentEventType.TURN_FAILED,
            "interrupted": AgentEventType.TURN_INTERRUPTED,
            "cancelled": AgentEventType.TURN_INTERRUPTED,
            "canceled": AgentEventType.TURN_INTERRUPTED,
        }.get(state)
        terminal_turns = self._terminal_turns.setdefault(thread_id, set())
        if event_type is None or not turn_id or turn_id in terminal_turns:
            return
        terminal_turns.add(turn_id)
        if initialize:
            return
        self._events.publish(
            thread_id,
            self._event(
                event_type,
                thread_ref,
                turn_id,
                {"status": state},
            ),
        )

    def _presentation_state(self, thread_id: str) -> _T3PresentationState:
        state = self._presentation_states.pop(thread_id, None)
        if state is None:
            if len(self._presentation_states) >= self._presentation_limits.max_seen_identities:
                eviction_key = next(
                    (
                        key
                        for key, candidate in self._presentation_states.items()
                        if not candidate.lock.locked() and not candidate.pinned_by_poll
                    ),
                    None,
                )
                if eviction_key is None:
                    raise ApplicationPresentationCapacityError(
                        "T3 presentation Thread capacity is exhausted"
                    )
                self._presentation_states.pop(eviction_key)
            state = _T3PresentationState()
        self._presentation_states[thread_id] = state
        return state

    def _new_t3_activities(
        self,
        activities: tuple[Mapping[str, object], ...],
        *,
        presentation_state: _T3PresentationState,
        initialize: bool,
    ) -> tuple[Mapping[str, object], ...]:
        if initialize:
            presentation_state.seen_activity_ids.clear()
            if activities:
                final_id = str(activities[-1].get("id") or "")
                presentation_state.activity_cursor_id = final_id or None
            return ()
        cursor_id = presentation_state.activity_cursor_id
        if cursor_id is None:
            candidates = activities
        else:
            cursor_index = next(
                (
                    index
                    for index in range(len(activities) - 1, -1, -1)
                    if str(activities[index].get("id") or "") == cursor_id
                ),
                None,
            )
            if cursor_index is None:
                if not activities:
                    return ()
                raise EventStreamReset("application_event_poll_window_gap")
            candidates = activities[cursor_index + 1 :]
        if len(candidates) > self._presentation_limits.max_seen_identities:
            raise EventStreamReset("application_event_poll_window_gap")
        candidate_ids = {
            activity_id for activity in candidates if (activity_id := str(activity.get("id") or ""))
        }
        presentation_state.seen_activity_ids.intersection_update(candidate_ids)
        return candidates

    def _should_publish_t3_message(
        self,
        thread_id: str,
        message_id: str,
        turn_id: str,
        message: Mapping[str, object],
        *,
        seen: set[str],
        only_turn_id: str | None,
        initialize: bool,
    ) -> bool:
        if (
            not message_id
            or message_id in seen
            or str(message.get("role") or "") != "assistant"
            or (only_turn_id is not None and turn_id != only_turn_id)
        ):
            return False
        baseline = self._turn_baselines.get((thread_id, turn_id))
        if initialize and baseline is None:
            seen.add(message_id)
            return False
        if baseline is not None and message_id in baseline:
            seen.add(message_id)
            return False
        return True

    def _publish_t3_message(
        self,
        thread_ref: ThreadRef,
        message: Mapping[str, object],
        message_id: str,
        turn_id: str,
    ) -> None:
        self._events.publish(
            thread_ref.native_thread_id,
            self._event(
                AgentEventType.MESSAGE_COMPLETED,
                thread_ref,
                turn_id or None,
                {
                    "message": AgentMessage(
                        agent_item_id=message_id,
                        thread_ref=thread_ref,
                        role=MessageRole.ASSISTANT,
                        content=(
                            TextContent(
                                str(message.get("text") or ""),
                                TextFormat.MARKDOWN,
                            ),
                        ),
                        created_at=_parse_datetime(message.get("createdAt")),
                        metadata={
                            "native_application": "t3",
                            "streaming": bool(message.get("streaming")),
                        },
                    )
                },
            ),
        )

    async def _t3_activity_message(
        self,
        thread_ref: ThreadRef,
        activity: Mapping[str, object],
    ) -> AgentMessage | None:
        if self._activity_presenter is None:
            return _t3_activity_message(thread_ref, activity)
        facts = _t3_activity_facts(thread_ref, activity)
        if facts is None:
            return None
        runtime = self._presentation_runtime
        if runtime is None:
            raise RuntimeError("T3 activity presenter runtime is not configured")
        presenter = self._activity_presenter
        presentation = await runtime.invoke(lambda: presenter.present_activity(facts))
        if presentation is None:
            return None
        return AgentMessage(
            agent_item_id=f"imagent:t3-activity:{facts.activity_id}",
            thread_ref=thread_ref,
            role=MessageRole.ASSISTANT,
            content=presentation.content,
            created_at=facts.created_at,
            metadata={
                "kind": facts.kind,
                "native_application": "t3",
                "source": "activity",
            },
        )

    def _finish_poll_task(
        self,
        thread_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._poll_tasks.get(thread_id) is task:
            self._poll_tasks.pop(thread_id, None)
        self._initialized_threads.discard(thread_id)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            gap_code = (
                error.gap_code
                if isinstance(error, EventStreamGap)
                else "application_event_poll_failed"
            )
            self._events.fail(
                thread_id,
                lambda: EventStreamReset(gap_code),
                discard_pending=False,
            )
            logger.error("T3 thread polling failed; subscription terminated for recovery")

    async def _find_project(
        self,
        project_id: str,
    ) -> ProjectSummary | None:
        snapshot = await self._client.shell_snapshot()
        for project in _object_list(snapshot.get("projects")):
            if str(project.get("id") or "") == project_id:
                return self._project_summary(project)
        return None

    def _project_summary(
        self,
        project: Mapping[str, object],
    ) -> ProjectSummary | None:
        if project.get("deletedAt") is not None:
            return None
        project_id = str(project.get("id") or "")
        if not project_id:
            return None
        return ProjectSummary(
            ref=ProjectRef(
                application_instance_id=self._application_instance_id,
                native_project_id=project_id,
            ),
            display_name=str(project.get("title") or project.get("name") or project_id),
            root_path=_optional_string(project.get("workspaceRoot") or project.get("path")),
            metadata={
                "default_model_selection": project.get("defaultModelSelection"),
            },
        )

    def _thread_summary(
        self,
        thread: Mapping[str, object],
    ) -> ThreadSummary | None:
        if thread.get("deletedAt") is not None or thread.get("archivedAt") is not None:
            return None
        thread_id = str(thread.get("id") or "")
        project_id = str(thread.get("projectId") or "")
        if not thread_id:
            return None
        latest_turn = _optional_object(thread.get("latestTurn"))
        state = (
            latest_turn.get("state") or latest_turn.get("status")
            if latest_turn is not None
            else "idle"
        )
        project_ref = ProjectRef(self._application_instance_id, project_id) if project_id else None
        return ThreadSummary(
            ref=ThreadRef(
                application_instance_id=self._application_instance_id,
                native_thread_id=thread_id,
                project_ref=project_ref,
            ),
            status=_thread_status(state),
            title=_optional_string(thread.get("title")),
            updated_at=_parse_optional_datetime(thread.get("updatedAt") or thread.get("createdAt")),
            metadata={
                "runtime_mode": thread.get("runtimeMode"),
                "model_selection": thread.get("modelSelection"),
            },
        )

    def _event(
        self,
        event_type: AgentEventType,
        thread_ref: ThreadRef,
        turn_id: str | None,
        data: dict[str, object],
    ) -> AgentEvent:
        message = data.get("message")
        if isinstance(message, AgentMessage):
            native_identity = f"message:{message.agent_item_id}"
        else:
            native_identity = f"turn:{turn_id or 'unknown'}:{event_type.value}"
        return AgentEvent(
            event_id=(
                f"{self._application_instance_id}:thread:{thread_ref.native_thread_id}:"
                f"{native_identity}"
            ),
            application_instance_id=self._application_instance_id,
            type=event_type,
            data=data,
            created_at=datetime.now(UTC),
            project_ref=thread_ref.project_ref,
            thread_ref=thread_ref,
            turn_id=turn_id,
        )

    def _require_own_thread(self, thread_ref: ThreadRef) -> None:
        if thread_ref.application_instance_id != self._application_instance_id:
            raise ValueError("thread belongs to a different application instance")


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"T3 result did not contain {name}")
    return value


def _optional_object(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _object_list(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _message_id(message: Mapping[str, object]) -> str:
    return str(message.get("id") or message.get("messageId") or "")


def _stable_id(*parts: str) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, "\x1f".join(parts)))


def _safe_title(value: str) -> str:
    return (" ".join(value.strip().split())[:80] or "IM task").strip()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _optional_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_datetime(value: object) -> datetime:
    return _parse_optional_datetime(value) or datetime.now(UTC)


def _parse_optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _thread_status(value: object) -> ThreadStatus:
    normalized = str(value or "").replace("-", "_").casefold()
    return {
        "idle": ThreadStatus.IDLE,
        "running": ThreadStatus.RUNNING,
        "active": ThreadStatus.RUNNING,
        "in_progress": ThreadStatus.RUNNING,
        "completed": ThreadStatus.COMPLETED,
        "failed": ThreadStatus.FAILED,
        "interrupted": ThreadStatus.INTERRUPTED,
        "cancelled": ThreadStatus.INTERRUPTED,
        "canceled": ThreadStatus.INTERRUPTED,
        "waiting_for_approval": ThreadStatus.WAITING_FOR_APPROVAL,
        "waiting_for_input": ThreadStatus.WAITING_FOR_INPUT,
    }.get(normalized, ThreadStatus.UNKNOWN)


def _turn_status(value: object) -> TurnStatus:
    normalized = str(value or "").replace("-", "_").casefold()
    return {
        "idle": TurnStatus.IDLE,
        "running": TurnStatus.RUNNING,
        "active": TurnStatus.RUNNING,
        "in_progress": TurnStatus.RUNNING,
        "completed": TurnStatus.COMPLETED,
        "failed": TurnStatus.FAILED,
        "error": TurnStatus.FAILED,
        "interrupted": TurnStatus.INTERRUPTED,
        "cancelled": TurnStatus.INTERRUPTED,
        "canceled": TurnStatus.INTERRUPTED,
    }.get(normalized, TurnStatus.UNKNOWN)


def _t3_agent_message(
    thread_ref: ThreadRef,
    message: Mapping[str, object],
) -> AgentMessage | None:
    role_value = str(message.get("role") or "")
    role = {
        "user": MessageRole.USER,
        "assistant": MessageRole.ASSISTANT,
        "system": MessageRole.SYSTEM,
    }.get(role_value)
    text = str(message.get("text") or "").strip()
    message_id = _message_id(message)
    if role is None or not text or not message_id:
        return None
    return AgentMessage(
        agent_item_id=message_id,
        thread_ref=thread_ref,
        role=role,
        content=(TextContent(text, TextFormat.MARKDOWN),),
        created_at=_parse_datetime(message.get("createdAt") or message.get("updatedAt")),
        metadata={
            "streaming": bool(message.get("streaming")),
            "native_application": "t3",
        },
    )


def _t3_activity_message(
    thread_ref: ThreadRef,
    activity: Mapping[str, object],
) -> AgentMessage | None:
    kind = str(activity.get("kind") or "")
    if kind in {
        "approval.requested",
        "approval.resolved",
        "user-input.requested",
        "user-input.resolved",
    }:
        return None
    summary = str(activity.get("summary") or "").strip()
    payload = _optional_object(activity.get("payload")) or {}
    detail = str(
        payload.get("detail") or payload.get("message") or payload.get("summary") or ""
    ).strip()
    text = "\n\n".join(part for part in (summary, detail) if part)
    activity_id = str(activity.get("id") or "")
    if not text or not activity_id:
        return None
    return AgentMessage(
        agent_item_id=activity_id,
        thread_ref=thread_ref,
        role=MessageRole.ASSISTANT,
        content=(TextContent(text, TextFormat.MARKDOWN),),
        created_at=_parse_datetime(activity.get("createdAt")),
        metadata={
            "kind": kind,
            "native_application": "t3",
            "source": "activity",
        },
    )


def _t3_activity_facts(
    thread_ref: ThreadRef,
    activity: Mapping[str, object],
) -> T3ActivityFacts | None:
    native_kind = activity.get("kind")
    kind = native_kind.strip() if isinstance(native_kind, str) else ""
    if kind in {
        "approval.requested",
        "approval.resolved",
        "user-input.requested",
        "user-input.resolved",
    }:
        return None
    native_activity_id = activity.get("id")
    native_turn_id = activity.get("turnId")
    activity_id = native_activity_id.strip() if isinstance(native_activity_id, str) else ""
    turn_id = native_turn_id.strip() if isinstance(native_turn_id, str) else ""
    created_at = _parse_optional_datetime(activity.get("createdAt"))
    if not activity_id or not turn_id or not kind or created_at is None:
        return None
    payload = _optional_object(activity.get("payload")) or {}
    summary = _bounded_activity_text(activity.get("summary"))
    detail = _bounded_activity_text(
        payload.get("detail") or payload.get("message") or payload.get("summary")
    )
    return T3ActivityFacts(
        activity_id=activity_id,
        thread_ref=thread_ref,
        turn_id=turn_id,
        kind=kind,
        summary=summary,
        detail=detail,
        created_at=created_at,
    )


def _bounded_activity_text(value: object, *, limit: int = 8_000) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] or None


def _t3_turn_error(
    thread: Mapping[str, object],
    turn_id: str,
    status: TurnStatus,
) -> str | None:
    if status is TurnStatus.FAILED:
        session = _optional_object(thread.get("session"))
        if session is not None:
            error = _optional_string(session.get("lastError"))
            if error:
                return error
    for activity in reversed(_object_list(thread.get("activities"))):
        if str(activity.get("turnId") or "") != turn_id:
            continue
        if str(activity.get("tone") or "") != "error" and str(activity.get("kind") or "") not in {
            "runtime.error",
            "turn.error",
        }:
            continue
        payload = _optional_object(activity.get("payload")) or {}
        return _optional_string(
            payload.get("message") or payload.get("detail") or activity.get("summary")
        )
    return None


def _encode_t3_attachments(
    attachments: tuple[AttachmentContent, ...],
    *,
    shared_filesystem_root: Path | None,
) -> list[dict[str, object]]:
    if len(attachments) > 8:
        raise ValueError("T3 accepts at most 8 image attachments")
    result: list[dict[str, object]] = []
    for attachment in attachments:
        media_type = attachment.media_type.strip().casefold()
        if not media_type.startswith("image/"):
            raise ValueError("T3 supports image attachments only")
        path = resolve_local_attachment(
            attachment.source,
            shared_filesystem_root=shared_filesystem_root,
            consumer="T3",
        )
        try:
            data = path.read_bytes()
        except OSError as error:
            raise ValueError("Unable to read the staged T3 image") from error
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("Each T3 image must be at most 10 MiB")
        if attachment.size_bytes is not None and len(data) != attachment.size_bytes:
            raise ValueError("The staged T3 image size changed")
        filename = attachment.filename or path.name or "image"
        if not Path(filename).suffix:
            filename += mimetypes.guess_extension(media_type) or ".img"
        result.append(
            {
                "type": "image",
                "name": filename[:255],
                "mimeType": media_type[:100],
                "sizeBytes": len(data),
                "dataUrl": (f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"),
            }
        )
    return result

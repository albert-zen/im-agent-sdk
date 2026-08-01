from __future__ import annotations

import hashlib
import inspect
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..attachments import configure_shared_filesystem_root, resolve_local_attachment
from ..contracts import (
    AcceptedTurn,
    ActivateNativeThread,
    AgentEvent,
    AgentEventType,
    AgentInput,
    AgentMessage,
    ApplicationCapabilities,
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    ApplicationRef,
    ApplicationSummary,
    AttachmentContent,
    AttachmentSourceKind,
    CreateThread,
    DeleteThread,
    EventSequenceScope,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetThreadStatus,
    GetTurnCatchup,
    InteractiveRequest,
    InterruptTurn,
    ListProjects,
    ListThreads,
    MessageRole,
    NativeThreadActivated,
    Page,
    ProjectCapabilities,
    ProjectMode,
    RespondRequest,
    RuntimeCapabilities,
    SupportLevel,
    TextContent,
    TextFormat,
    ThreadCapabilities,
    ThreadCreated,
    ThreadDeletionCapability,
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
    TurnStatus,
    operation_error,
    validate_application_operation,
    validate_application_operation_result,
)
from ..events import EventBroadcaster
from .appserver_request_runtime import (
    AppServerRequestRuntime,
    ServerRequestMapper,
)
from .appserver_requests import map_appserver_request


class AppServerClient(Protocol):
    def add_notification_handler(self, handler) -> None: ...

    async def list_threads(self, **params) -> Mapping[str, object]: ...

    async def list_thread_turns(
        self,
        thread_id: str,
        **params,
    ) -> Mapping[str, object]: ...

    async def start_thread(self, **params) -> Mapping[str, object]: ...

    async def read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool = False,
    ) -> Mapping[str, object]: ...

    async def resume_thread(self, **params) -> Mapping[str, object]: ...

    async def start_turn(
        self,
        thread_id: str,
        text: str | None = None,
        **kwargs,
    ) -> Mapping[str, object]: ...

    async def interrupt_turn(
        self,
        thread_id: str,
        turn_id: str,
    ) -> Mapping[str, object]: ...


class _AppServerApplicationAdapter:
    """Codex App Server projection shared by distinct Zen and Codex adapters."""

    def __init__(
        self,
        *,
        application_instance_id: str,
        kind: str,
        display_name: str,
        client: AppServerClient,
        cwd: str,
        shared_filesystem_root: str | Path | None = None,
        server_request_mapper: ServerRequestMapper | None = None,
        event_buffer_max_pending: int = 1024,
    ) -> None:
        self._application_instance_id = application_instance_id
        self._client = client
        self._cwd = cwd
        self._shared_filesystem_root = configure_shared_filesystem_root(shared_filesystem_root)
        self._events = EventBroadcaster[str, AgentEvent](max_pending=event_buffer_max_pending)
        self._client.add_notification_handler(self._handle_notification)
        self._request_runtime = AppServerRequestRuntime(
            application_ref=ApplicationRef(application_instance_id),
            client=self._client,
            mapper=server_request_mapper,
            publish_event=self._events.publish,
        )
        self._interactive_requests_enabled = self._request_runtime.enabled
        capabilities = ApplicationCapabilities(
            projects=ProjectCapabilities(
                mode=ProjectMode.FIXED,
                discovery=SupportLevel.UNSUPPORTED,
                reading=SupportLevel.UNSUPPORTED,
            ),
            threads=ThreadCapabilities(
                listing=SupportLevel.NATIVE,
                creation=SupportLevel.NATIVE,
                reading=SupportLevel.NATIVE,
                deletion=ThreadDeletionCapability.UNSUPPORTED,
            ),
            runtime=RuntimeCapabilities(
                history=SupportLevel.NATIVE,
                streaming=SupportLevel.NATIVE,
                replay_from_cursor=SupportLevel.UNSUPPORTED,
                interruption=SupportLevel.NATIVE,
                interactive_requests=(
                    SupportLevel.NATIVE
                    if self._interactive_requests_enabled
                    else SupportLevel.UNSUPPORTED
                ),
                native_thread_activation=SupportLevel.NATIVE,
                gap_detection=SupportLevel.UNSUPPORTED,
                event_sequence_scope=EventSequenceScope.NONE,
            ),
            attachment_sources=(
                (AttachmentSourceKind.LOCAL_PATH,)
                if self._shared_filesystem_root is not None
                else ()
            ),
        )
        self._summary = ApplicationSummary(
            ref=ApplicationRef(application_instance_id),
            kind=kind,
            display_name=display_name,
            capabilities=capabilities,
            metadata={"cwd": cwd, "protocol": "codex-app-server"},
        )

    @property
    def summary(self) -> ApplicationSummary:
        return self._summary

    async def start(self) -> None:
        connect = getattr(self._client, "connect", None)
        if callable(connect):
            result = connect()
            if inspect.isawaitable(result):
                await result

    async def stop(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    async def list_pending_requests(self) -> tuple[InteractiveRequest, ...]:
        raise NotImplementedError(
            "App Server does not expose an authoritative pending-request snapshot"
        )

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
        if isinstance(operation, ListThreads):
            result = await self._client.list_threads(
                sortKey="updated_at",
                cursor=operation.cursor,
                searchTerm=operation.query,
            )
            threads = tuple(
                self._thread_summary(item) for item in _native_list(result, "data", "threads")
            )
            return ThreadsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                threads=Page(
                    items=threads,
                    next_cursor=_optional_string(
                        result.get("nextCursor") or result.get("next_cursor")
                    ),
                ),
            )
        if isinstance(operation, CreateThread):
            if operation.initial_context:
                raise NotImplementedError("initial thread context is unsupported by App Server")
            result = await self._client.start_thread(cwd=self._cwd)
            thread = _native_object(result, "thread")
            return ThreadCreated(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread=self._thread_summary(thread),
            )
        if isinstance(operation, (GetThread, GetThreadStatus)):
            result = await self._client.read_thread(operation.thread_ref.native_thread_id)
            summary = self._thread_summary(_native_object(result, "thread"))
            if isinstance(operation, GetThreadStatus):
                return ThreadStatusRead(
                    operation_id=operation.operation_id,
                    completed_at=completed_at,
                    thread_ref=operation.thread_ref,
                    thread_status=summary.status,
                )
            return ThreadRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread=summary,
            )
        if isinstance(operation, ActivateNativeThread):
            self._require_own_thread(operation.thread_ref)
            await self._client.resume_thread(threadId=operation.thread_ref.native_thread_id)
            return NativeThreadActivated(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread_ref=operation.thread_ref,
            )
        if isinstance(operation, GetTurnCatchup):
            thread_ref = operation.thread_ref
            self._require_own_thread(thread_ref)
            turns, _has_older = await self._read_turn_page(
                thread_ref,
                limit=1,
                page=1,
            )
            if not turns:
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
            turn = turns[-1]
            commentary = tuple(
                message
                for item in _turn_items(turn)
                if _is_agent_item(item)
                and str(item.get("phase") or "").casefold() == "commentary"
                and (message := self._item_message(thread_ref, item)) is not None
            )
            return TurnCatchupRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                catchup=TurnCatchup(
                    thread_ref=thread_ref,
                    turn_id=_turn_id(turn),
                    status=_turn_status(turn.get("status")),
                    messages=commentary[-operation.limit :],
                    updated_at=_turn_updated_at(turn),
                    metadata={"native_application": self._summary.kind},
                ),
            )
        if isinstance(operation, GetThreadHistory):
            thread_ref = operation.thread_ref
            self._require_own_thread(thread_ref)
            turns, has_older = await self._read_turn_page(
                thread_ref,
                limit=operation.limit,
                page=operation.page,
            )
            return ThreadHistoryRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                history=ThreadHistory(
                    thread_ref=thread_ref,
                    turns=tuple(self._history_entry(thread_ref, turn) for turn in turns),
                    page=operation.page,
                    has_older=has_older,
                    metadata={"native_application": self._summary.kind},
                ),
            )
        if isinstance(operation, InterruptTurn):
            thread_ref = operation.thread_ref
            turn_id = operation.turn_id or ""
            if not turn_id:
                raise ValueError("turn.interrupt requires turn_id")
            await self._client.interrupt_turn(thread_ref.native_thread_id, turn_id)
            return TurnInterrupted(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread_ref=thread_ref,
                turn_id=turn_id,
            )
        if isinstance(operation, RespondRequest):
            return await self._request_runtime.respond(
                operation,
                completed_at,
            )
        if isinstance(
            operation,
            (ListProjects, GetProject, DeleteThread),
        ):
            raise NotImplementedError(f"{operation.type.value} is unsupported by this application")
        raise NotImplementedError(f"unsupported operation: {operation.type.value}")

    async def _read_turn_page(
        self,
        thread_ref: ThreadRef,
        *,
        limit: int,
        page: int,
    ) -> tuple[tuple[Mapping[str, object], ...], bool]:
        list_turns = getattr(self._client, "list_thread_turns", None)
        if callable(list_turns):
            cursor: str | None = None
            seen_cursors: set[str] = set()
            for page_number in range(1, page + 1):
                parameters: dict[str, object] = {
                    "limit": limit,
                    "items_view": "full",
                    "sort_direction": "desc",
                }
                if cursor is not None:
                    parameters["cursor"] = cursor
                try:
                    payload = list_turns(
                        thread_ref.native_thread_id,
                        **parameters,
                    )
                    result = await payload if inspect.isawaitable(payload) else payload
                except Exception as error:
                    if not _is_unsupported_method_error(error):
                        raise
                    break
                if not isinstance(result, Mapping):
                    raise RuntimeError("thread/turns/list returned an invalid result")
                turns = _turn_list(result)
                next_cursor = _optional_string(
                    result.get("nextCursor") or result.get("next_cursor")
                )
                if page_number == page:
                    return tuple(reversed(turns)), next_cursor is not None
                if next_cursor is None:
                    return (), False
                if next_cursor in seen_cursors:
                    raise RuntimeError("thread history returned a repeated pagination cursor")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
        result = await self._client.read_thread(
            thread_ref.native_thread_id,
            include_turns=True,
        )
        turns = _turn_list(result)
        end = max(0, len(turns) - ((page - 1) * limit))
        start = max(0, end - limit)
        return turns[start:end], start > 0

    def _history_entry(
        self,
        thread_ref: ThreadRef,
        turn: Mapping[str, object],
    ) -> TurnHistoryEntry:
        user_message: AgentMessage | None = None
        agent_messages: list[AgentMessage] = []
        had_compaction = False
        for item in _turn_items(turn):
            item_type = _normalized_item_type(item)
            if item_type == "contextcompaction":
                had_compaction = True
            message = self._item_message(thread_ref, item)
            if message is None:
                continue
            if message.role is MessageRole.USER and user_message is None:
                user_message = message
            if message.role is MessageRole.ASSISTANT:
                agent_messages.append(message)
        return TurnHistoryEntry(
            turn_id=_turn_id(turn),
            status=_turn_status(turn.get("status")),
            user_message=user_message,
            agent_messages=tuple(agent_messages),
            error=_turn_error(turn),
            had_compaction=had_compaction,
            metadata={"native_application": self._summary.kind},
        )

    def _item_message(
        self,
        thread_ref: ThreadRef,
        item: Mapping[str, object],
    ) -> AgentMessage | None:
        item_type = _normalized_item_type(item)
        if "user" in item_type:
            role = MessageRole.USER
        elif "agent" in item_type or "assistant" in item_type:
            role = MessageRole.ASSISTANT
        else:
            return None
        text = _item_text(item)
        if not text:
            return None
        item_id = str(item.get("id") or item.get("itemId") or "")
        if not item_id:
            digest = hashlib.sha256(
                (f"{thread_ref.native_thread_id}\x1f{role.value}\x1f{text}").encode()
            ).hexdigest()
            item_id = f"imagent:appserver-item:{digest}"
        return AgentMessage(
            agent_item_id=item_id,
            thread_ref=thread_ref,
            role=role,
            content=(TextContent(text, TextFormat.MARKDOWN),),
            created_at=_parse_datetime(item.get("createdAt") or item.get("updatedAt")),
            metadata={
                "phase": str(item.get("phase") or ""),
                "native_application": self._summary.kind,
            },
        )

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
    ) -> AcceptedTurn:
        self._require_own_thread(thread_ref)
        text = "\n".join(
            part.text for part in message.content if isinstance(part, TextContent)
        ).strip()
        attachments = tuple(part for part in message.content if isinstance(part, AttachmentContent))
        if attachments:
            input_items: list[dict[str, object]] = []
            if text:
                input_items.append({"type": "text", "text": text})
            for attachment in attachments:
                if not attachment.media_type.casefold().startswith("image/"):
                    raise ValueError("Codex App Server supports image attachments only")
                local_path = resolve_local_attachment(
                    attachment.source,
                    shared_filesystem_root=self._shared_filesystem_root,
                    consumer="Codex App Server",
                )
                input_items.append({"type": "localImage", "path": str(local_path)})
            result = await self._client.start_turn(
                thread_ref.native_thread_id,
                input_items=input_items,
            )
        else:
            if not text:
                raise ValueError("Codex App Server input requires text or image")
            result = await self._client.start_turn(
                thread_ref.native_thread_id,
                text,
            )
        turn = result.get("turn")
        turn_id = (
            str(turn.get("id") or "")
            if isinstance(turn, Mapping)
            else str(result.get("turnId") or "")
        )
        if not turn_id:
            raise RuntimeError("turn/start did not return a turn id")
        return AcceptedTurn(
            thread_ref=thread_ref,
            turn_id=turn_id,
            client_message_id=message.client_message_id,
        )

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if after_cursor is not None:
            raise NotImplementedError("Codex App Server does not support event replay")
        self._require_own_thread(thread_ref)
        return self._events.subscribe(thread_ref.native_thread_id)

    async def _handle_notification(self, notification: dict) -> None:
        method = str(notification.get("method") or "")
        params = notification.get("params")
        if not isinstance(params, dict):
            return
        if method == "serverRequest/resolved":
            await self._request_runtime.handle_resolution_notification(params)
            return
        thread_id = str(params.get("threadId") or "")
        if not thread_id:
            return
        turn = params.get("turn")
        turn_id = str(
            params.get("turnId") or (turn.get("id") if isinstance(turn, dict) else "") or ""
        )
        thread_ref = self._thread_ref(thread_id)
        if method == "item/agentMessage/delta":
            self._emit(
                thread_id,
                AgentEventType.MESSAGE_DELTA,
                {"delta": str(params.get("delta") or "")},
                event_id=(
                    str(params.get("eventId") or params.get("event_id") or "")
                    or f"{self._application_instance_id}:live:{uuid.uuid4()}"
                ),
                thread_ref=thread_ref,
                turn_id=turn_id or None,
            )
            return
        if method == "item/completed":
            item = params.get("item")
            if not isinstance(item, dict):
                return
            item_type = str(item.get("type") or "").replace("_", "").casefold()
            if item_type != "agentmessage":
                return
            text = str(item.get("text") or "")
            if not text:
                return
            item_id = str(item.get("id") or params.get("itemId") or "")
            if not item_id:
                item_id = f"live-{uuid.uuid4()}"
            message = AgentMessage(
                agent_item_id=item_id,
                thread_ref=thread_ref,
                role=MessageRole.ASSISTANT,
                content=(TextContent(text, TextFormat.MARKDOWN),),
                created_at=datetime.now(UTC),
                metadata={
                    "phase": str(item.get("phase") or ""),
                    "native_method": method,
                },
            )
            self._emit(
                thread_id,
                AgentEventType.MESSAGE_COMPLETED,
                {"message": message},
                event_id=(
                    f"{self._application_instance_id}:thread:{thread_id}:"
                    f"message:{item_id}:completed"
                ),
                thread_ref=thread_ref,
                turn_id=turn_id or None,
            )
            return
        if method == "turn/completed":
            status = str(
                params.get("status") or (turn.get("status") if isinstance(turn, dict) else "") or ""
            ).casefold()
            event_type = {
                "failed": AgentEventType.TURN_FAILED,
                "interrupted": AgentEventType.TURN_INTERRUPTED,
            }.get(status, AgentEventType.TURN_COMPLETED)
            terminal_id = turn_id or f"live-{uuid.uuid4()}"
            self._emit(
                thread_id,
                event_type,
                {"status": status or "completed"},
                event_id=(
                    f"{self._application_instance_id}:thread:{thread_id}:"
                    f"turn:{terminal_id}:{event_type.value}"
                ),
                thread_ref=thread_ref,
                turn_id=turn_id or None,
            )

    def _emit(
        self,
        thread_id: str,
        event_type: AgentEventType,
        data: dict[str, object],
        *,
        event_id: str,
        thread_ref: ThreadRef,
        turn_id: str | None,
    ) -> None:
        event = AgentEvent(
            event_id=event_id,
            application_instance_id=self._application_instance_id,
            type=event_type,
            data=data,
            created_at=datetime.now(UTC),
            thread_ref=thread_ref,
            turn_id=turn_id,
        )
        self._events.publish(thread_id, event)

    def _thread_summary(self, thread: Mapping[str, object]) -> ThreadSummary:
        thread_id = str(thread.get("id") or thread.get("threadId") or "")
        if not thread_id:
            raise RuntimeError("application did not return a thread id")
        status_value = thread.get("status")
        if isinstance(status_value, Mapping):
            status_value = status_value.get("type") or status_value.get("status")
        return ThreadSummary(
            ref=self._thread_ref(thread_id),
            status=_thread_status(status_value),
            title=_optional_string(
                thread.get("name") or thread.get("title") or thread.get("preview")
            ),
            metadata={
                "cwd": str(thread.get("cwd") or self._cwd),
                "preview": str(thread.get("preview") or ""),
            },
        )

    def _thread_ref(self, thread_id: str) -> ThreadRef:
        return ThreadRef(
            application_instance_id=self._application_instance_id,
            native_thread_id=thread_id,
        )

    def _require_own_thread(self, thread_ref: ThreadRef) -> None:
        if thread_ref.application_instance_id != self._application_instance_id:
            raise ValueError("thread belongs to a different application instance")


class ZenApplicationAdapter(_AppServerApplicationAdapter):
    def __init__(
        self,
        *,
        application_instance_id: str,
        client: AppServerClient,
        cwd: str,
        shared_filesystem_root: str | Path | None = None,
        event_buffer_max_pending: int = 1024,
    ) -> None:
        super().__init__(
            application_instance_id=application_instance_id,
            kind="zen",
            display_name="Zen",
            client=client,
            cwd=cwd,
            shared_filesystem_root=shared_filesystem_root,
            event_buffer_max_pending=event_buffer_max_pending,
        )


class CodexApplicationAdapter(_AppServerApplicationAdapter):
    def __init__(
        self,
        *,
        application_instance_id: str,
        client: AppServerClient,
        cwd: str,
        shared_filesystem_root: str | Path | None = None,
        event_buffer_max_pending: int = 1024,
    ) -> None:
        super().__init__(
            application_instance_id=application_instance_id,
            kind="codex",
            display_name="Codex",
            client=client,
            cwd=cwd,
            shared_filesystem_root=shared_filesystem_root,
            server_request_mapper=map_appserver_request,
            event_buffer_max_pending=event_buffer_max_pending,
        )


def _native_object(result: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = result.get(key)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"application result did not contain {key}")
    return value


def _native_list(
    result: Mapping[str, object],
    *keys: str,
) -> tuple[Mapping[str, object], ...]:
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, Mapping))
    raise RuntimeError("application result did not contain a thread list")


def _optional_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _thread_status(value: object) -> ThreadStatus:
    normalized = str(value or "").replace("-", "_").casefold()
    return {
        "idle": ThreadStatus.IDLE,
        "notloaded": ThreadStatus.IDLE,
        "running": ThreadStatus.RUNNING,
        "active": ThreadStatus.RUNNING,
        "inprogress": ThreadStatus.RUNNING,
        "in_progress": ThreadStatus.RUNNING,
        "waiting_for_approval": ThreadStatus.WAITING_FOR_APPROVAL,
        "waiting_for_input": ThreadStatus.WAITING_FOR_INPUT,
        "completed": ThreadStatus.COMPLETED,
        "failed": ThreadStatus.FAILED,
        "interrupted": ThreadStatus.INTERRUPTED,
    }.get(normalized, ThreadStatus.UNKNOWN)


def _turn_status(value: object) -> TurnStatus:
    if isinstance(value, Mapping):
        value = value.get("type") or value.get("status")
    normalized = str(value or "").replace("-", "_").casefold()
    return {
        "idle": TurnStatus.IDLE,
        "running": TurnStatus.RUNNING,
        "active": TurnStatus.RUNNING,
        "inprogress": TurnStatus.RUNNING,
        "in_progress": TurnStatus.RUNNING,
        "working": TurnStatus.RUNNING,
        "completed": TurnStatus.COMPLETED,
        "failed": TurnStatus.FAILED,
        "error": TurnStatus.FAILED,
        "interrupted": TurnStatus.INTERRUPTED,
        "cancelled": TurnStatus.INTERRUPTED,
        "canceled": TurnStatus.INTERRUPTED,
    }.get(normalized, TurnStatus.UNKNOWN)


def _turn_list(
    payload: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    for key in ("turns", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, Mapping))
    thread = payload.get("thread")
    if isinstance(thread, Mapping):
        turns = thread.get("turns")
        if isinstance(turns, list):
            return tuple(item for item in turns if isinstance(item, Mapping))
    return ()


def _turn_items(
    turn: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    items = turn.get("items")
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, Mapping))


def _turn_id(turn: Mapping[str, object]) -> str:
    turn_id = str(turn.get("id") or turn.get("turnId") or "")
    if not turn_id:
        raise RuntimeError("native turn did not contain an id")
    return turn_id


def _normalized_item_type(item: Mapping[str, object]) -> str:
    return (
        str(item.get("type") or item.get("kind") or "").replace("_", "").replace("-", "").casefold()
    )


def _is_agent_item(item: Mapping[str, object]) -> bool:
    item_type = _normalized_item_type(item)
    return "agent" in item_type or "assistant" in item_type


def _item_text(item: Mapping[str, object]) -> str:
    text = item.get("text")
    if isinstance(text, str):
        return text.strip()
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, Mapping) and part.get("text")
        ).strip()
    return ""


def _turn_error(turn: Mapping[str, object]) -> str | None:
    error = turn.get("error")
    if isinstance(error, Mapping):
        return _optional_string(error.get("message") or error.get("error"))
    return _optional_string(error)


def _turn_updated_at(turn: Mapping[str, object]) -> datetime | None:
    value = turn.get("updatedAt") or turn.get("completedAt") or turn.get("createdAt")
    return _parse_optional_datetime(value)


def _parse_datetime(value: object) -> datetime:
    return _parse_optional_datetime(value) or datetime.now(UTC)


def _parse_optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_unsupported_method_error(error: Exception) -> bool:
    if getattr(error, "code", None) == -32601:
        return True
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "method not found",
            "unknown method",
            "not implemented",
            "unsupported method",
            "requires experimentalapi",
            "experimentalapi capability",
            "no handler",
        )
    )

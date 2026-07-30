from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Protocol

from ..contracts import (
    AcceptedTurn,
    AgentEvent,
    AgentEventType,
    AgentInput,
    AgentMessage,
    ApplicationCapabilities,
    ApplicationRef,
    ApplicationSummary,
    AttachmentContent,
    ContractError,
    MessageRole,
    Operation,
    OperationResult,
    OperationResultStatus,
    OperationType,
    Page,
    ProjectCapabilities,
    ProjectMode,
    RuntimeCapabilities,
    SupportLevel,
    TextContent,
    TextFormat,
    ThreadCapabilities,
    ThreadDeletionCapability,
    ThreadRef,
    ThreadStatus,
    ThreadSummary,
)


class AppServerClient(Protocol):
    def add_notification_handler(self, handler) -> None: ...

    async def list_threads(self, **params) -> Mapping[str, object]: ...

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
    ) -> None:
        self._application_instance_id = application_instance_id
        self._client = client
        self._cwd = cwd
        self._sequence = 0
        self._events: dict[str, asyncio.Queue[AgentEvent]] = {}
        self._client.add_notification_handler(self._handle_notification)
        capabilities = ApplicationCapabilities(
            projects=ProjectCapabilities(
                mode=ProjectMode.FIXED,
                discovery=SupportLevel.UNSUPPORTED,
                selection=SupportLevel.UNSUPPORTED,
            ),
            threads=ThreadCapabilities(
                listing=SupportLevel.NATIVE,
                creation=SupportLevel.NATIVE,
                switching=SupportLevel.NATIVE,
                deletion=ThreadDeletionCapability.UNSUPPORTED,
            ),
            runtime=RuntimeCapabilities(
                history=SupportLevel.NATIVE,
                streaming=SupportLevel.NATIVE,
                replay_from_cursor=SupportLevel.FALLBACK,
                interruption=SupportLevel.NATIVE,
                interactive_requests=SupportLevel.NATIVE,
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

    async def execute(self, operation: Operation) -> OperationResult:
        try:
            value = await self._execute(operation)
        except Exception as error:
            return OperationResult(
                operation_id=operation.operation_id,
                status=OperationResultStatus.FAILED,
                completed_at=datetime.now(UTC),
                error=ContractError(
                    code=type(error).__name__,
                    message=str(error),
                ),
            )
        return OperationResult(
            operation_id=operation.operation_id,
            status=OperationResultStatus.SUCCEEDED,
            completed_at=datetime.now(UTC),
            value=value,
        )

    async def _execute(self, operation: Operation) -> object | None:
        if operation.type is OperationType.THREAD_LIST:
            result = await self._client.list_threads(
                sortKey="updated_at",
                cursor=operation.arguments.get("cursor"),
                searchTerm=operation.arguments.get("query"),
            )
            threads = tuple(
                self._thread_summary(item) for item in _native_list(result, "data", "threads")
            )
            return Page(
                items=threads,
                next_cursor=_optional_string(result.get("nextCursor") or result.get("next_cursor")),
            )
        if operation.type is OperationType.THREAD_CREATE:
            result = await self._client.start_thread(cwd=self._cwd)
            thread = _native_object(result, "thread")
            return self._thread_summary(thread)
        if operation.type in {
            OperationType.THREAD_SWITCH,
            OperationType.THREAD_STATUS,
        }:
            thread_ref = _required_thread(operation)
            result = await self._client.read_thread(thread_ref.native_thread_id)
            summary = self._thread_summary(_native_object(result, "thread"))
            if operation.type is OperationType.THREAD_STATUS:
                return summary.status
            await self._client.resume_thread(threadId=thread_ref.native_thread_id)
            return summary
        if operation.type is OperationType.TURN_INTERRUPT:
            thread_ref = _required_thread(operation)
            turn_id = str(operation.arguments.get("turn_id") or "")
            if not turn_id:
                raise ValueError("turn.interrupt requires turn_id")
            await self._client.interrupt_turn(thread_ref.native_thread_id, turn_id)
            return None
        if operation.type in {
            OperationType.PROJECT_LIST,
            OperationType.PROJECT_SELECT,
            OperationType.THREAD_DELETE,
        }:
            raise NotImplementedError(f"{operation.type.value} is unsupported by this application")
        raise NotImplementedError(f"unsupported operation: {operation.type.value}")

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
                local_path = str(attachment.metadata.get("local_path") or "")
                if not local_path:
                    raise ValueError("Codex App Server image requires a local_path")
                input_items.append({"type": "localImage", "path": local_path})
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

    async def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        del after_cursor
        self._require_own_thread(thread_ref)
        queue = self._events.setdefault(
            thread_ref.native_thread_id,
            asyncio.Queue(),
        )
        while True:
            yield await queue.get()

    async def _handle_notification(self, notification: dict) -> None:
        method = str(notification.get("method") or "")
        params = notification.get("params")
        if not isinstance(params, dict):
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
            await self._emit(
                thread_id,
                AgentEventType.MESSAGE_DELTA,
                {"delta": str(params.get("delta") or "")},
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
            message = AgentMessage(
                agent_item_id=item_id or f"{thread_id}:{turn_id}:assistant",
                thread_ref=thread_ref,
                role=MessageRole.ASSISTANT,
                content=(TextContent(text, TextFormat.MARKDOWN),),
                created_at=datetime.now(UTC),
                metadata={
                    "phase": str(item.get("phase") or ""),
                    "native_method": method,
                },
            )
            await self._emit(
                thread_id,
                AgentEventType.MESSAGE_COMPLETED,
                {"message": message},
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
            await self._emit(
                thread_id,
                event_type,
                {"status": status or "completed"},
                thread_ref=thread_ref,
                turn_id=turn_id or None,
            )

    async def _emit(
        self,
        thread_id: str,
        event_type: AgentEventType,
        data: dict[str, object],
        *,
        thread_ref: ThreadRef,
        turn_id: str | None,
    ) -> None:
        self._sequence += 1
        event = AgentEvent(
            event_id=f"{self._application_instance_id}:{self._sequence}",
            application_instance_id=self._application_instance_id,
            sequence=self._sequence,
            type=event_type,
            data=data,
            created_at=datetime.now(UTC),
            thread_ref=thread_ref,
            turn_id=turn_id,
            cursor=str(self._sequence),
        )
        await self._events.setdefault(thread_id, asyncio.Queue()).put(event)

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
    ) -> None:
        super().__init__(
            application_instance_id=application_instance_id,
            kind="zen",
            display_name="Zen",
            client=client,
            cwd=cwd,
        )


class CodexApplicationAdapter(_AppServerApplicationAdapter):
    def __init__(
        self,
        *,
        application_instance_id: str,
        client: AppServerClient,
        cwd: str,
    ) -> None:
        super().__init__(
            application_instance_id=application_instance_id,
            kind="codex",
            display_name="Codex",
            client=client,
            cwd=cwd,
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


def _required_thread(operation: Operation) -> ThreadRef:
    thread_ref = operation.target.thread_ref
    if thread_ref is None:
        raise ValueError(f"{operation.type.value} requires thread_ref")
    return thread_ref


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

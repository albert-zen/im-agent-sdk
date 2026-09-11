from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from ...interaction.messages import MessageRole, TextContent
from ...interaction.operations import operation_error
from ..capabilities import (
    ApplicationCapabilities,
    EventSequenceScope,
    ProjectCapabilities,
    ProjectMode,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
    ThreadDeletionCapability,
)
from ..contract import (
    AcceptedTurn,
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    ApplicationInputOutcomeUnknown,
    ApplicationRef,
    ApplicationSummary,
    InputContinuationPreference,
    InputDisposition,
    Page,
    ProjectRef,
    ProjectSummary,
    ThreadHistory,
    ThreadRef,
    ThreadStatus,
    ThreadSummary,
    TurnCatchup,
    TurnHistoryEntry,
    TurnRef,
    TurnReplyCorrelationPolicy,
    TurnStatus,
    validate_application_summary,
    validate_thread_summary,
)
from ..events import AgentEvent, AgentEventType, EventBroadcaster, EventStreamReset
from ..operations import (
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    CreateThread,
    DeleteThread,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetThreadStatus,
    GetTurnCatchup,
    InterruptTurn,
    ListProjects,
    ListThreads,
    ProjectRead,
    ProjectsListed,
    ThreadCreated,
    ThreadHistoryRead,
    ThreadRead,
    ThreadsListed,
    ThreadStatusRead,
    TurnCatchupRead,
    TurnInterrupted,
    validate_application_operation,
    validate_application_operation_result,
)

__all__ = ["DeepSeekHarnessApplicationAdapter"]


class DeepSeekHarnessClientError(RuntimeError):
    """Native DeepSeek Harness Web RPC transport or protocol failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class DeepSeekHarnessClient(Protocol):
    """Minimal native DeepSeek Harness Web Host surface consumed by the adapter."""

    async def list_workspaces(self) -> tuple[Mapping[str, object], ...]: ...

    async def list_sessions(self) -> tuple[Mapping[str, object], ...]: ...

    async def create_session(
        self,
        *,
        workspace_id: str | None = None,
        cwd: str | None = None,
        session_id: str | None = None,
    ) -> Mapping[str, object]: ...

    async def history(
        self,
        session_id: str,
        *,
        before_seq: int | None = None,
        max_messages: int | None = None,
    ) -> Mapping[str, object]: ...

    async def prompt(
        self,
        session_id: str,
        text: str,
        *,
        mode: str = "queue",
    ) -> Mapping[str, object]: ...

    async def cancel(self, session_id: str) -> Mapping[str, object]: ...


class HttpDeepSeekHarnessClient:
    """Minimal authenticated-less JSON-RPC client for the DSH Web Host /api."""

    def __init__(
        self,
        origin: str = "http://127.0.0.1:3080",
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.origin = origin.rstrip("/")
        self._headers = dict(headers or {})
        self._client = httpx.AsyncClient(
            base_url=self.origin,
            timeout=timeout,
            transport=transport,
        )
        self._next_rpc_id = 1

    async def __aenter__(self) -> HttpDeepSeekHarnessClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_workspaces(self) -> tuple[Mapping[str, object], ...]:
        value = await self._rpc("workspace.list", {})
        return _mapping_tuple(value.get("items"))

    async def list_sessions(self) -> tuple[Mapping[str, object], ...]:
        value = await self._rpc("session.list", {})
        return _mapping_tuple(value.get("items"))

    async def create_session(
        self,
        *,
        workspace_id: str | None = None,
        cwd: str | None = None,
        session_id: str | None = None,
    ) -> Mapping[str, object]:
        payload: dict[str, object] = {}
        if workspace_id is not None:
            payload["workspaceId"] = workspace_id
        if cwd is not None:
            payload["cwd"] = cwd
        if session_id is not None:
            payload["sessionId"] = session_id
        if workspace_id is not None and cwd is not None:
            raise ValueError(
                "DeepSeek Harness session.create requires workspaceId or cwd, not both"
            )
        return await self._rpc("session.create", payload)

    async def history(
        self,
        session_id: str,
        *,
        before_seq: int | None = None,
        max_messages: int | None = None,
    ) -> Mapping[str, object]:
        payload: dict[str, object] = {"sessionId": session_id}
        if before_seq is not None:
            payload["beforeSeq"] = before_seq
        if max_messages is not None:
            payload["maxMessages"] = max_messages
        return await self._rpc("session.history", payload)

    async def prompt(
        self,
        session_id: str,
        text: str,
        *,
        mode: str = "queue",
    ) -> Mapping[str, object]:
        return await self._rpc(
            "session.prompt",
            {
                "sessionId": session_id,
                "mode": mode,
                "content": [{"type": "text", "text": text}],
            },
        )

    async def cancel(self, session_id: str) -> Mapping[str, object]:
        return await self._rpc("session.cancel", {"sessionId": session_id})

    async def _rpc(
        self,
        method: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        rpc_id = f"dsh-sdk-{self._next_rpc_id}"
        self._next_rpc_id += 1
        body = {
            "type": "client-request",
            "rpcId": rpc_id,
            "method": method,
            "payload": dict(payload),
        }
        try:
            response = await self._client.post(
                f"/api/{method}",
                headers=self._headers,
                json=body,
            )
        except httpx.HTTPError as error:
            raise DeepSeekHarnessClientError(
                "Unable to connect to DeepSeek Harness Web Host",
            ) from error
        try:
            message: object = response.json()
        except ValueError:
            message = {}
        if response.is_error:
            details = message if isinstance(message, dict) else {}
            raise DeepSeekHarnessClientError(
                f"DeepSeek Harness request failed (HTTP {response.status_code})",
                status_code=response.status_code,
                code=_optional_string(details.get("code")),
            )
        if not isinstance(message, Mapping):
            raise DeepSeekHarnessClientError(
                f"DeepSeek Harness response for {method} is not a JSON object"
            )
        result = message.get("result")
        if not isinstance(result, Mapping):
            raise DeepSeekHarnessClientError(
                f"DeepSeek Harness response for {method} is missing a typed result"
            )
        ok = result.get("ok")
        if ok is False:
            error = result.get("error")
            if not isinstance(error, Mapping):
                raise DeepSeekHarnessClientError(
                    f"DeepSeek Harness {method} failed without a typed error"
                )
            raise DeepSeekHarnessClientError(
                str(error.get("message") or f"DeepSeek Harness {method} failed"),
                code=_optional_string(error.get("code")),
            )
        if ok is not True:
            raise DeepSeekHarnessClientError(
                f"DeepSeek Harness {method} returned an invalid ok marker"
            )
        value = result.get("value")
        if not isinstance(value, Mapping):
            raise DeepSeekHarnessClientError(
                f"DeepSeek Harness {method} returned a non-object value"
            )
        return value


class DeepSeekHarnessApplicationAdapter:
    """Maps DeepSeek Harness Web Host sessions/workspaces onto the typed SDK."""

    def __init__(
        self,
        *,
        application_instance_id: str,
        client: DeepSeekHarnessClient,
        poll_interval: float = 0.25,
        send_input_turn_timeout: float = 30.0,
        event_buffer_max_pending: int = 1024,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if send_input_turn_timeout <= 0:
            raise ValueError("send_input_turn_timeout must be positive")
        self._application_instance_id = application_instance_id
        self._client = client
        self._poll_interval = poll_interval
        self._send_input_turn_timeout = send_input_turn_timeout
        self._events = EventBroadcaster[ThreadRef, AgentEvent](max_pending=event_buffer_max_pending)
        self._poll_tasks: dict[ThreadRef, asyncio.Task[None]] = {}
        self._history_locks: dict[ThreadRef, asyncio.Lock] = {}
        self._seen_event_seqs: dict[ThreadRef, set[int]] = {}
        self._baseline_thread_ids: set[ThreadRef] = set()
        capabilities = ApplicationCapabilities(
            projects=ProjectCapabilities(
                mode=ProjectMode.MANAGED,
                discovery=SupportLevel.NATIVE,
                reading=SupportLevel.NATIVE,
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
                interactive_requests=SupportLevel.UNSUPPORTED,
                native_thread_activation=SupportLevel.UNSUPPORTED,
                gap_detection=SupportLevel.UNSUPPORTED,
                event_sequence_scope=EventSequenceScope.NONE,
            ),
        )
        self._summary = ApplicationSummary(
            ref=ApplicationRef(application_instance_id),
            kind="deepseek_harness",
            display_name="DeepSeek Harness",
            capabilities=capabilities,
            metadata={"protocol": "deepseek-harness-web-rpc"},
        )
        validate_application_summary(self._summary)

    @property
    def summary(self) -> ApplicationSummary:
        return self._summary

    async def start(self) -> None:
        start = getattr(self._client, "start", None)
        if callable(start):
            result = start()
            if inspect.isawaitable(result):
                await result

    async def stop(self) -> None:
        tasks = tuple(self._poll_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._poll_tasks.clear()
        self._history_locks.clear()
        self._seen_event_seqs.clear()
        self._baseline_thread_ids.clear()
        aclose = getattr(self._client, "aclose", None)
        if callable(aclose):
            result = aclose()
            if inspect.isawaitable(result):
                await result

    async def list_pending_requests(self) -> tuple:
        raise NotImplementedError(
            "DeepSeek Harness does not expose an authoritative pending-request snapshot"
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
        if isinstance(operation, ListProjects):
            workspaces = await self._list_workspaces()
            query = (operation.query or "").casefold()
            projects = tuple(
                summary
                for workspace in workspaces
                if (summary := self._project_summary(workspace)) is not None
                and (
                    not query
                    or query in summary.display_name.casefold()
                    or query in summary.ref.project_id.casefold()
                )
            )
            return ProjectsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                projects=Page(items=projects),
            )
        if isinstance(operation, GetProject):
            project = await self._require_workspace(operation.project_ref.project_id)
            return ProjectRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                project=project,
            )
        if isinstance(operation, CreateThread):
            project_ref = operation.project_ref
            project = await self._require_workspace(project_ref.project_id)
            title = (operation.title or "").strip() or project.display_name
            if operation.initial_context:
                raise NotImplementedError("initial thread context is unsupported")
            created = await self._client.create_session(workspace_id=project_ref.project_id)
            session_id = _session_id(created)
            if not session_id:
                raise RuntimeError("DeepSeek Harness session.create did not return a session id")
            thread = ThreadSummary(
                ref=ThreadRef(project_ref, session_id),
                title=title,
                status=ThreadStatus.IDLE,
                updated_at=datetime.now(UTC),
                metadata={"native_workspace": project_ref.project_id},
            )
            validate_thread_summary(thread)
            return ThreadCreated(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread=thread,
            )
        if isinstance(operation, (ListThreads,)):
            project_ref = operation.project_ref
            await self._require_workspace(project_ref.project_id)
            query = (operation.query or "").casefold()
            threads = tuple(
                summary
                for session in await self._sessions_for_project(project_ref)
                if (summary := self._thread_summary(project_ref, session)) is not None
                and (
                    not query
                    or query in (summary.title or "").casefold()
                    or query in summary.ref.thread_id.casefold()
                )
            )
            return ThreadsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                threads=Page(items=threads),
            )
        if isinstance(operation, (GetThread, GetThreadStatus)):
            thread_ref = operation.thread_ref
            summary = await self._require_thread_summary(thread_ref)
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
            await self._require_thread_summary(thread_ref)
            entries = await self._history_entries(thread_ref)
            if not entries:
                return TurnCatchupRead(
                    operation_id=operation.operation_id,
                    completed_at=completed_at,
                    catchup=TurnCatchup(
                        thread_ref=thread_ref,
                        turn_ref=None,
                        status=TurnStatus.IDLE,
                        messages=(),
                    ),
                )
            latest = entries[-1]
            return TurnCatchupRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                catchup=TurnCatchup(
                    thread_ref=thread_ref,
                    turn_ref=latest.turn_ref,
                    status=latest.status,
                    messages=latest.agent_messages[-operation.limit :],
                    metadata={"native_application": "deepseek_harness"},
                ),
            )
        if isinstance(operation, GetThreadHistory):
            thread_ref = operation.thread_ref
            await self._require_thread_summary(thread_ref)
            entries = await self._history_entries(thread_ref)
            end = max(0, len(entries) - ((operation.page - 1) * operation.limit))
            start = max(0, end - operation.limit)
            return ThreadHistoryRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                history=ThreadHistory(
                    thread_ref=thread_ref,
                    turns=entries[start:end],
                    page=operation.page,
                    has_older=start > 0,
                    metadata={"native_application": "deepseek_harness"},
                ),
            )
        if isinstance(operation, InterruptTurn):
            thread_ref = operation.thread_ref
            await self._require_thread_summary(thread_ref)
            await self._client.cancel(thread_ref.thread_id)
            return TurnInterrupted(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread_ref=thread_ref,
                turn_ref=operation.turn_ref,
            )
        if isinstance(operation, (DeleteThread,)):
            raise NotImplementedError(f"{operation.type.value} is unsupported by DeepSeek Harness")
        raise NotImplementedError(f"{operation.type.value} is unsupported by DeepSeek Harness")

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
        del continuation
        self._require_own_thread(thread_ref)
        await self._require_thread_summary(thread_ref)
        text = "\n".join(
            part.text for part in message.content if isinstance(part, TextContent)
        ).strip()
        if not text:
            raise ValueError("DeepSeek Harness input requires text")
        await self._ensure_baseline(thread_ref)
        if before_dispatch is not None:
            await before_dispatch(
                ApplicationInputDispatch(
                    thread_ref=thread_ref,
                    client_message_id=message.client_message_id,
                    disposition=InputDisposition.STARTED,
                    correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
                )
            )
        try:
            await self._client.prompt(thread_ref.thread_id, text, mode="queue")
        except Exception as cause:
            if isinstance(cause, ApplicationInputOutcomeUnknown):
                raise
            raise ApplicationInputOutcomeUnknown(
                "DeepSeek Harness input was dispatched but its native outcome is unknown",
                cause,
            ) from cause
        turn_id = await self._wait_for_turn_start(thread_ref)
        if turn_id is None:
            cause = RuntimeError(
                f"DeepSeek Harness did not admit a native Turn within "
                f"{self._send_input_turn_timeout}s"
            )
            raise ApplicationInputOutcomeUnknown(
                "DeepSeek Harness input was dispatched but its native outcome is unknown",
                cause,
            ) from cause
        return AcceptedTurn(
            turn_ref=TurnRef(thread_ref, turn_id),
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
            raise NotImplementedError("DeepSeek Harness does not support event replay")
        self._require_own_thread(thread_ref)
        subscription = self._events.subscribe(thread_ref)
        task = self._poll_tasks.get(thread_ref)
        if task is None or task.done():
            task = asyncio.create_task(self._poll_thread(thread_ref))
            self._poll_tasks[thread_ref] = task
            task.add_done_callback(
                lambda completed, subscribed_thread_ref=thread_ref: self._finish_poll_task(
                    subscribed_thread_ref,
                    completed,
                )
            )
        return subscription

    async def _poll_thread(self, thread_ref: ThreadRef) -> None:
        try:
            while self._events.subscriber_count(thread_ref):
                await self._publish_history_events(thread_ref)
                await asyncio.sleep(self._poll_interval)
        finally:
            self._events.fail(
                thread_ref,
                lambda: EventStreamReset("application_polling_stopped"),
            )

    def _finish_poll_task(self, thread_ref: ThreadRef, task: asyncio.Task[None]) -> None:
        del task
        current = self._poll_tasks.get(thread_ref)
        if current is not None and current.done():
            self._poll_tasks.pop(thread_ref, None)

    async def _require_workspace(self, project_id: str) -> ProjectSummary:
        workspaces = await self._list_workspaces()
        for workspace in workspaces:
            if str(workspace.get("workspaceId") or "") == project_id:
                summary = self._project_summary(workspace)
                if summary is not None:
                    return summary
        raise KeyError(f"DeepSeek Harness workspace not found: {project_id}")

    async def _list_workspaces(self) -> tuple[Mapping[str, object], ...]:
        return await self._client.list_workspaces()

    async def _sessions_for_project(
        self,
        project_ref: ProjectRef,
    ) -> tuple[Mapping[str, object], ...]:
        workspaces = await self._list_workspaces()
        session_ids: set[str] = set()
        for workspace in workspaces:
            if str(workspace.get("workspaceId") or "") == project_ref.project_id:
                for session_id in _object_list(workspace.get("sessionIds")):
                    if _optional_string(session_id):
                        session_ids.add(_optional_string(session_id) or "")
                break
        sessions = await self._client.list_sessions()
        return tuple(
            session
            for session in sessions
            if _optional_string(session.get("sessionId")) in session_ids
        )

    async def _require_thread_summary(self, thread_ref: ThreadRef) -> ThreadSummary:
        self._require_own_thread(thread_ref)
        sessions = await self._sessions_for_project(thread_ref.project_ref)
        for session in sessions:
            summary = self._thread_summary(thread_ref.project_ref, session)
            if summary is not None and summary.ref == thread_ref:
                return summary
        raise KeyError(f"DeepSeek Harness session not found: {thread_ref.thread_id}")

    def _project_summary(
        self,
        workspace: Mapping[str, object],
    ) -> ProjectSummary | None:
        project_id = _optional_string(workspace.get("workspaceId"))
        if not project_id:
            return None
        path = _optional_string(workspace.get("path")) or ""
        title = _optional_string(workspace.get("title")) or path.split("/")[-1] or project_id
        return ProjectSummary(
            ref=ProjectRef(self._application_instance_id, project_id),
            display_name=title,
            root_path=path,
            metadata={"native_application": "deepseek_harness"},
        )

    def _thread_summary(
        self,
        project_ref: ProjectRef,
        session: Mapping[str, object],
    ) -> ThreadSummary | None:
        session_id = _optional_string(session.get("sessionId"))
        if not session_id:
            return None
        running = session.get("running") is True
        updated_at = _parse_optional_datetime(session.get("updatedAt"))
        row = session.get("projections")
        projections = row if isinstance(row, Mapping) else None
        values = projections.get("values") if projections is not None else None
        title = None
        if isinstance(values, Mapping):
            title = _optional_string(values.get("title"))
        return ThreadSummary(
            ref=ThreadRef(project_ref, session_id),
            title=title,
            status=ThreadStatus.RUNNING if running else ThreadStatus.IDLE,
            updated_at=updated_at,
            metadata={
                "native_application": "deepseek_harness",
                "cwd": _optional_string(session.get("cwd")),
                "blank": bool(session.get("blank")) if session.get("blank") is not None else None,
            },
        )

    async def _ensure_baseline(self, thread_ref: ThreadRef) -> None:
        async with self._lock_for(thread_ref):
            if thread_ref in self._baseline_thread_ids:
                return
            page = await self._history(thread_ref)
            self._baseline_thread_ids.add(thread_ref)
            self._seen_event_seqs[thread_ref] = {_event_seq(entry) for entry in _page_events(page)}

    async def _publish_history_events(self, thread_ref: ThreadRef) -> None:
        async with self._lock_for(thread_ref):
            new_events = await self._publish_history_events_locked(thread_ref)
        for entry in new_events:
            self._publish_dsh_event(thread_ref, _entry_event(entry))

    async def _publish_history_events_locked(
        self, thread_ref: ThreadRef
    ) -> list[Mapping[str, object]]:
        page = await self._history(thread_ref)
        events = _page_events(page)
        if thread_ref not in self._baseline_thread_ids:
            self._baseline_thread_ids.add(thread_ref)
            self._seen_event_seqs[thread_ref] = {_event_seq(entry) for entry in events}
            return []
        seen = self._seen_event_seqs.setdefault(thread_ref, set())
        new_events: list[Mapping[str, object]] = []
        for entry in events:
            seq = _event_seq(entry)
            if seq in seen:
                continue
            seen.add(seq)
            new_events.append(entry)
        return new_events

    async def _wait_for_turn_start(self, thread_ref: ThreadRef) -> str | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._send_input_turn_timeout
        found_turn_id: str | None = None
        while loop.time() < deadline:
            async with self._lock_for(thread_ref):
                new_events = await self._publish_history_events_locked(thread_ref)
            for entry in new_events:
                event = _entry_event(entry)
                self._publish_dsh_event(thread_ref, event)
                if event.get("type") == "turn/start":
                    turn_number = _event_data(event).get("turn")
                    if isinstance(turn_number, int):
                        found_turn_id = str(turn_number)
            if found_turn_id is not None:
                return found_turn_id
            await asyncio.sleep(self._poll_interval)
        return None

    def _publish_dsh_event(
        self,
        thread_ref: ThreadRef,
        event: Mapping[str, object],
    ) -> str | None:
        event_type = _optional_string(event.get("type"))
        seq = _event_seq({"event": event})
        turn_number = _event_data(event).get("turn")
        turn_id = str(turn_number) if isinstance(turn_number, int) else None
        turn_ref = TurnRef(thread_ref, turn_id) if turn_id is not None else None
        data = dict(_event_data(event))
        if event_type == "assistant/message":
            message = self._agent_message(thread_ref, turn_id, data)
            if message is not None:
                self._events.publish(
                    thread_ref,
                    AgentEvent(
                        event_id=self._event_id(thread_ref, seq, AgentEventType.MESSAGE_COMPLETED),
                        application_instance_id=self._application_instance_id,
                        project_ref=thread_ref.project_ref,
                        thread_ref=thread_ref,
                        turn_ref=turn_ref,
                        type=AgentEventType.MESSAGE_COMPLETED,
                        data={"message": message},
                        created_at=datetime.now(UTC),
                    ),
                )
        elif event_type == "turn/start":
            if turn_ref is not None:
                self._events.publish(
                    thread_ref,
                    AgentEvent(
                        event_id=self._event_id(thread_ref, seq, AgentEventType.TURN_STARTED),
                        application_instance_id=self._application_instance_id,
                        project_ref=thread_ref.project_ref,
                        thread_ref=thread_ref,
                        turn_ref=turn_ref,
                        type=AgentEventType.TURN_STARTED,
                        data={},
                        created_at=datetime.now(UTC),
                    ),
                )
        elif event_type == "turn/end":
            terminal_type = _terminal_event_type(data.get("reason"))
            if turn_ref is not None and terminal_type is not None:
                self._events.publish(
                    thread_ref,
                    AgentEvent(
                        event_id=self._event_id(thread_ref, seq, terminal_type),
                        application_instance_id=self._application_instance_id,
                        project_ref=thread_ref.project_ref,
                        thread_ref=thread_ref,
                        turn_ref=turn_ref,
                        type=terminal_type,
                        data={"status": terminal_type.value.split(".")[-1]},
                        created_at=datetime.now(UTC),
                    ),
                )
        return turn_id

    def _agent_message(
        self,
        thread_ref: ThreadRef,
        turn_id: str | None,
        data: Mapping[str, object],
    ) -> AgentMessage | None:
        del turn_id
        message = data.get("message")
        if not isinstance(message, Mapping):
            return None
        role = _optional_string(message.get("role")) or "assistant"
        content = message.get("content")
        if not isinstance(content, list):
            return None
        text_parts = [
            block.get("text")
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text"
        ]
        text_parts = [part for part in text_parts if isinstance(part, str)]
        if not text_parts and role == "assistant":
            return None
        message_id = _optional_string(message.get("id")) or _short_uuid()
        try:
            role_value = MessageRole(role)
        except ValueError:
            role_value = MessageRole.ASSISTANT
        return AgentMessage(
            agent_item_id=message_id,
            thread_ref=thread_ref,
            role=role_value,
            content=tuple(TextContent(text=part) for part in text_parts),
            created_at=datetime.now(UTC),
        )

    async def _history_entries(
        self,
        thread_ref: ThreadRef,
    ) -> tuple[TurnHistoryEntry, ...]:
        events = await self._history_events(thread_ref)
        return _build_history_entries(thread_ref, events)

    async def _history_events(
        self,
        thread_ref: ThreadRef,
    ) -> tuple[Mapping[str, object], ...]:
        """Read the complete log by paging backwards over native history pages."""

        collected: dict[int, Mapping[str, object]] = {}
        page = await self._client.history(thread_ref.thread_id)
        for entry in _page_events(page):
            collected[_event_seq(entry)] = _entry_event(entry)
        has_more = page.get("hasMore") is True
        while has_more and collected:
            before_seq = min(collected)
            if before_seq <= 0:
                break
            page = await self._client.history(
                thread_ref.thread_id,
                before_seq=before_seq,
            )
            previous_size = len(collected)
            for entry in _page_events(page):
                collected[_event_seq(entry)] = _entry_event(entry)
            has_more = page.get("hasMore") is True
            if len(collected) == previous_size:
                break
        return tuple(entry for _, entry in sorted(collected.items(), key=lambda item: item[0]))

    async def _history(self, thread_ref: ThreadRef) -> Mapping[str, object]:
        return await self._client.history(thread_ref.thread_id)

    def _lock_for(self, thread_ref: ThreadRef) -> asyncio.Lock:
        lock = self._history_locks.get(thread_ref)
        if lock is None:
            lock = asyncio.Lock()
            self._history_locks[thread_ref] = lock
        return lock

    def _event_id(
        self,
        thread_ref: ThreadRef,
        seq: int,
        event_type: AgentEventType,
    ) -> str:
        return (
            f"dsh-{self._application_instance_id}-{thread_ref.thread_id}-{event_type.value}-{seq}"
        )

    def _require_own_thread(self, thread_ref: ThreadRef) -> None:
        if thread_ref.project_ref.application_instance_id != self._application_instance_id:
            raise ValueError("Thread does not belong to this application")


def _page_events(page: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    return _mapping_tuple(page.get("events"))


def _entry_event(entry: Mapping[str, object]) -> Mapping[str, object]:
    event = entry.get("event")
    return event if isinstance(event, Mapping) else entry


def _event_seq(entry: Mapping[str, object]) -> int:
    event = _entry_event(entry)
    seq = event.get("seq")
    return seq if isinstance(seq, int) else -1


@dataclass(slots=True)
class _TurnRecord:
    status: TurnStatus = TurnStatus.RUNNING
    user_message: AgentMessage | None = None
    agent_messages: list[AgentMessage] = field(default_factory=list)


def _build_history_entries(
    thread_ref: ThreadRef,
    events: tuple[Mapping[str, object], ...],
) -> tuple[TurnHistoryEntry, ...]:
    grouped: dict[str, _TurnRecord] = {}
    order: list[str] = []
    open_turn_id: str | None = None
    for entry in events:
        event = _entry_event(entry)
        event_data = _event_data(event)
        event_type = _optional_string(event.get("type"))
        if event_type == "turn/start":
            turn_number = event_data.get("turn")
            if isinstance(turn_number, int):
                open_turn_id = str(turn_number)
                grouped.setdefault(open_turn_id, _TurnRecord())
                order.append(open_turn_id)
                continue
        if open_turn_id is None:
            continue
        record = grouped.setdefault(open_turn_id, _TurnRecord())
        if event_type == "turn/end":
            record.status = _turn_status(event_data.get("reason"))
        elif event_type == "user/message":
            message = _event_agent_message(thread_ref, event_data, MessageRole.USER)
            if message is not None and record.user_message is None:
                record.user_message = message
        elif event_type == "assistant/message":
            message = _event_agent_message(thread_ref, event_data, MessageRole.ASSISTANT)
            if message is not None:
                record.agent_messages.append(message)
    entries: list[TurnHistoryEntry] = []
    for turn_id in order:
        record = grouped.get(turn_id)
        if record is None:
            continue
        entry = TurnHistoryEntry(
            turn_ref=TurnRef(thread_ref, turn_id),
            status=record.status,
            user_message=record.user_message,
            agent_messages=tuple(record.agent_messages),
            metadata={"native_application": "deepseek_harness"},
        )
        entries.append(entry)
    return tuple(entries)


def _event_agent_message(
    thread_ref: ThreadRef,
    data: Mapping[str, object],
    role: MessageRole,
) -> AgentMessage | None:
    message = data.get("message")
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    text_parts = [
        block.get("text")
        for block in content
        if isinstance(block, Mapping) and block.get("type") == "text"
    ]
    text_parts = [part for part in text_parts if isinstance(part, str)]
    if not text_parts:
        return None
    message_id = _optional_string(message.get("id")) or _short_uuid()
    return AgentMessage(
        agent_item_id=message_id,
        thread_ref=thread_ref,
        role=role,
        content=tuple(TextContent(text=part) for part in text_parts),
        created_at=datetime.now(UTC),
    )


def _terminal_event_type(
    reason: object,
) -> AgentEventType | None:
    if not isinstance(reason, Mapping):
        return None
    kind = _optional_string(reason.get("kind"))
    return {
        "completed": AgentEventType.TURN_COMPLETED,
        "error": AgentEventType.TURN_FAILED,
        "interrupted": AgentEventType.TURN_INTERRUPTED,
        "cancelled": AgentEventType.TURN_INTERRUPTED,
        "canceled": AgentEventType.TURN_INTERRUPTED,
    }.get(kind or "")


def _turn_status(reason: object) -> TurnStatus:
    if not isinstance(reason, Mapping):
        return TurnStatus.COMPLETED
    kind = _optional_string(reason.get("kind"))
    if kind == "error":
        return TurnStatus.FAILED
    if kind in {"interrupted", "cancelled", "canceled"}:
        return TurnStatus.INTERRUPTED
    return TurnStatus.COMPLETED


def _short_uuid() -> str:
    return uuid.uuid4().hex[:16]


def _session_id(payload: Mapping[str, object]) -> str | None:
    return _optional_string(payload.get("sessionId"))


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _object_list(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return value


def _mapping_tuple(value: object) -> tuple[Mapping[str, object], ...]:
    return tuple(item for item in _object_list(value) if isinstance(item, Mapping))


def _event_data(event: Mapping[str, object]) -> Mapping[str, object]:
    data = event.get("data")
    return data if isinstance(data, Mapping) else {}


def _parse_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and value >= 0:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None

from __future__ import annotations

import hashlib
import inspect
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast

from ....interaction.diagnostics import ConnectionDiagnosticFacts
from ....interaction.media import (
    AttachmentContent,
    AttachmentSourceKind,
    configure_shared_filesystem_root,
    resolve_local_attachment,
)
from ....interaction.messages import MessageRole, TextContent, TextFormat
from ....interaction.operations import operation_error
from ...capabilities import (
    ApplicationCapabilities,
    EventSequenceScope,
    ProjectCapabilities,
    ProjectMode,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
    ThreadDeletionCapability,
)
from ...contract import (
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
    ThreadHistory,
    ThreadRef,
    ThreadSummary,
    TurnCatchup,
    TurnHistoryEntry,
    TurnReplyCorrelationPolicy,
    TurnStatus,
)
from ...diagnostics import ApplicationDiagnosticFacts
from ...events import AgentEvent, AgentEventType, EventBroadcaster, EventStreamReset
from ...operations import (
    ActivateNativeThread,
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
    NativeThreadActivated,
    RespondRequest,
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
from ...presentation import ApplicationPresentationRuntime
from ...presentation.artifact_materialization import (
    ApplicationArtifactMaterialization,
    ApplicationArtifactMaterializationCancelled,
    ApplicationArtifactMaterializationCapacityError,
    ApplicationArtifactMaterializationError,
    ApplicationArtifactMaterializationFailed,
    ApplicationArtifactMaterializationTimeout,
    AppServerArtifactMaterializationLimits,
    AppServerArtifactMaterializationRuntime,
    AppServerArtifactMaterializer,
    AppServerCompletedItemFacts,
    AppServerTurnTerminalFacts,
    AppServerTurnTerminalStatus,
    appserver_completed_item_facts,
)
from ...requests import InteractiveRequest
from .mapping import (
    AppServerEvent as _AppServerEvent,
)
from .mapping import (
    AppServerMappingError as _AppServerMappingError,
)
from .mapping import (
    derive_appserver_event_id as _derive_appserver_event_id,
)
from .mapping import (
    is_agent_item as _is_agent_item,
)
from .mapping import (
    is_unsupported_method_error as _is_unsupported_method_error,
)
from .mapping import (
    item_id as _item_id,
)
from .mapping import (
    item_text as _item_text,
)
from .mapping import (
    native_list as _native_list,
)
from .mapping import (
    native_mapping as _native_mapping,
)
from .mapping import (
    native_object as _native_object,
)
from .mapping import (
    native_turn_id as _native_turn_id,
)
from .mapping import (
    normalize_appserver_message as _normalize_appserver_message,
)
from .mapping import (
    normalized_item_type as _normalized_item_type,
)
from .mapping import (
    optional_string as _optional_string,
)
from .mapping import (
    parse_datetime as _parse_datetime,
)
from .mapping import (
    thread_id as _thread_id,
)
from .mapping import (
    thread_status as _thread_status,
)
from .mapping import (
    turn_error as _turn_error,
)
from .mapping import (
    turn_id as _turn_id,
)
from .mapping import (
    turn_items as _turn_items,
)
from .mapping import (
    turn_list as _turn_list,
)
from .mapping import (
    turn_status as _turn_status,
)
from .mapping import (
    turn_updated_at as _turn_updated_at,
)
from .requests import (
    AppServerRequestRuntime,
    ServerRequestMapper,
)

_ARTIFACT_OBSERVATION_ERRORS = (
    ApplicationArtifactMaterializationError,
    ApplicationArtifactMaterializationTimeout,
    ApplicationArtifactMaterializationCapacityError,
    ApplicationArtifactMaterializationCancelled,
    ApplicationArtifactMaterializationFailed,
)


@dataclass(frozen=True, slots=True)
class _PreparedAppServerInput:
    text: str
    input_items: tuple[Mapping[str, object], ...] | None


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
    """Common App Server projection shared by distinct Zen and Codex adapters."""

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
        thread_start_options: Mapping[str, object] | None = None,
        presentation_runtime: ApplicationPresentationRuntime | None = None,
        artifact_materializer: AppServerArtifactMaterializer | None = None,
        artifact_materialization_limits: AppServerArtifactMaterializationLimits = (
            AppServerArtifactMaterializationLimits()
        ),
    ) -> None:
        self._application_instance_id = application_instance_id
        self._client = client
        self._cwd = cwd
        self._shared_filesystem_root = configure_shared_filesystem_root(shared_filesystem_root)
        self._thread_start_options = _thread_start_options(thread_start_options)
        self._events = EventBroadcaster[str, AgentEvent](max_pending=event_buffer_max_pending)
        self._presentation_runtime = presentation_runtime
        self._artifact_materializer = artifact_materializer
        self._artifact_materialization_limits = artifact_materialization_limits
        self._artifact_materialization_runtime = (
            AppServerArtifactMaterializationRuntime(artifact_materialization_limits)
            if artifact_materializer is not None
            else None
        )
        self._seen_live_artifact_identities: dict[tuple[str, str, str], None] = {}
        self._client.add_notification_handler(self._handle_notification)
        self._request_runtime = AppServerRequestRuntime(
            application_ref=ApplicationRef(application_instance_id),
            client=self._client,
            mapper=server_request_mapper,
            publish_event=self._events.publish,
        )
        add_reset_handler = getattr(self._client, "add_connection_reset_handler", None)
        if callable(add_reset_handler):
            add_reset_handler(self._handle_event_connection_reset)
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

    def diagnostic_facts(self) -> ApplicationDiagnosticFacts:
        connection_reader = getattr(self._client, "connection_diagnostics", None)
        connection = connection_reader() if callable(connection_reader) else None
        return ApplicationDiagnosticFacts(
            application_instance_id=self._application_instance_id,
            kind=self._summary.kind,
            connection=(connection if isinstance(connection, ConnectionDiagnosticFacts) else None),
            presentation=(
                self._presentation_runtime.diagnostic_facts()
                if self._presentation_runtime is not None
                else None
            ),
            artifact_materialization=(
                self._artifact_materialization_runtime.diagnostic_facts()
                if self._artifact_materialization_runtime is not None
                else None
            ),
        )

    async def start(self) -> None:
        connect = getattr(self._client, "connect", None)
        if callable(connect):
            result = connect()
            if inspect.isawaitable(result):
                await result

    async def stop(self) -> None:
        try:
            close = getattr(self._client, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
        finally:
            if self._presentation_runtime is not None:
                await self._presentation_runtime.close()
            if self._artifact_materialization_runtime is not None:
                await self._artifact_materialization_runtime.close()

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
            result = _native_mapping(
                await self._client.list_threads(
                    sortKey="updated_at",
                    cursor=operation.cursor,
                    searchTerm=operation.query,
                )
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
            return ThreadCreated(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread=await self.create_thread_with_options(),
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
                and (_optional_string(item.get("phase")) or "").casefold() == "commentary"
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
            history_entries: list[TurnHistoryEntry] = []
            for turn in turns:
                if self._artifact_materializer is None:
                    history_entries.append(self._history_entry(thread_ref, turn))
                else:
                    history_entries.append(
                        await self._history_entry_with_artifacts(thread_ref, turn)
                    )
            return ThreadHistoryRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                history=ThreadHistory(
                    thread_ref=thread_ref,
                    turns=tuple(history_entries),
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

    async def create_thread_with_options(
        self,
        *,
        thread_start_options: Mapping[str, object] | None = None,
    ) -> ThreadSummary:
        """Create a Thread with explicit adapter-native consumer options."""

        options = (
            self._thread_start_options
            if thread_start_options is None
            else _thread_start_options(thread_start_options)
        )
        # The native client receives a fresh deep copy so it cannot mutate the
        # configured default or a caller-owned per-call profile.
        native_options = deepcopy(dict(options))
        result = await self._client.start_thread(cwd=self._cwd, **native_options)
        return self._thread_summary(_native_object(result, "thread"))

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
                    raise _AppServerMappingError
                normalized_result = _native_mapping(result)
                turns = _turn_list(normalized_result)
                next_cursor = _optional_string(
                    normalized_result.get("nextCursor") or normalized_result.get("next_cursor")
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

    async def _history_entry_with_artifacts(
        self,
        thread_ref: ThreadRef,
        turn: Mapping[str, object],
    ) -> TurnHistoryEntry:
        user_message: AgentMessage | None = None
        agent_messages: list[AgentMessage] = []
        had_compaction = False
        turn_id = _turn_id(turn)
        for item in _turn_items(turn):
            item_type = _normalized_item_type(item)
            if item_type == "contextcompaction":
                had_compaction = True
            message = self._item_message(thread_ref, item)
            if self._artifact_materializer is not None and "user" not in item_type:
                _item_id(item)
                facts = self._artifact_completed_item_facts(
                    item,
                    thread_ref=thread_ref,
                    turn_id=turn_id,
                    authoritative=True,
                    default_message_id=(message.agent_item_id if message is not None else None),
                )
                message = await self._materialize_completed_item(
                    facts,
                    default_message=message,
                    created_at=_parse_datetime(item.get("createdAt") or item.get("updatedAt")),
                )
            if message is None:
                continue
            if message.role is MessageRole.USER and user_message is None:
                user_message = message
            if message.role is MessageRole.ASSISTANT:
                agent_messages.append(message)
        turn_status = _turn_status(turn.get("status"))
        if self._artifact_materializer is not None and turn_status in {
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.INTERRUPTED,
        }:
            terminal_message = await self._materialize_turn_terminal(
                self._artifact_turn_terminal_facts(
                    thread_ref=thread_ref,
                    turn_id=turn_id,
                    authoritative=True,
                    status=AppServerTurnTerminalStatus(turn_status.value),
                ),
                created_at=_turn_updated_at(turn) or datetime.now(UTC),
            )
            if terminal_message is not None:
                agent_messages.append(terminal_message)
        return TurnHistoryEntry(
            turn_id=turn_id,
            status=turn_status,
            user_message=user_message,
            agent_messages=tuple(agent_messages),
            error=_turn_error(turn),
            had_compaction=had_compaction,
            metadata={"native_application": self._summary.kind},
        )

    def _artifact_completed_item_facts(
        self,
        item: Mapping[str, object],
        *,
        thread_ref: ThreadRef,
        turn_id: str,
        authoritative: bool,
        default_message_id: str | None,
    ) -> AppServerCompletedItemFacts:
        try:
            return appserver_completed_item_facts(
                item,
                thread_ref=thread_ref,
                turn_id=turn_id,
                authoritative=authoritative,
                default_message_id=default_message_id,
                limits=self._artifact_materialization_limits,
            )
        except (TypeError, ValueError):
            self._reject_invalid_artifact_facts()
            raise ApplicationArtifactMaterializationFailed(
                "artifact materialization facts are invalid"
            ) from None

    def _artifact_turn_terminal_facts(
        self,
        *,
        thread_ref: ThreadRef,
        turn_id: str,
        authoritative: bool,
        status: AppServerTurnTerminalStatus,
    ) -> AppServerTurnTerminalFacts:
        try:
            return AppServerTurnTerminalFacts(
                thread_ref=thread_ref,
                turn_id=turn_id,
                authoritative=authoritative,
                status=status,
            )
        except (TypeError, ValueError):
            self._reject_invalid_artifact_facts()
            raise ApplicationArtifactMaterializationFailed(
                "artifact materialization facts are invalid"
            ) from None

    def _reject_invalid_artifact_facts(self) -> None:
        runtime = self._artifact_materialization_runtime
        if runtime is not None:
            runtime.reject_invalid_facts()

    async def _materialize_completed_item(
        self,
        facts: AppServerCompletedItemFacts,
        *,
        default_message: AgentMessage | None,
        created_at: datetime,
    ) -> AgentMessage | None:
        materializer = self._artifact_materializer
        runtime = self._artifact_materialization_runtime
        if materializer is None or runtime is None:
            return default_message
        output = await runtime.invoke(lambda: materializer.materialize_completed_item(facts))
        if output is None:
            return default_message
        return self._artifact_message(
            facts.item_id,
            facts.thread_ref,
            output,
            default_message=default_message,
            created_at=created_at,
            terminal=False,
        )

    async def _materialize_turn_terminal(
        self,
        facts: AppServerTurnTerminalFacts,
        *,
        created_at: datetime,
    ) -> AgentMessage | None:
        materializer = self._artifact_materializer
        runtime = self._artifact_materialization_runtime
        if materializer is None or runtime is None:
            return None
        output = await runtime.invoke(lambda: materializer.materialize_turn_terminal(facts))
        if output is None:
            return None
        identity = hashlib.sha256(
            (
                f"{self._application_instance_id}\x1f"
                f"{facts.thread_ref.native_thread_id}\x1f{facts.turn_id}"
            ).encode()
        ).hexdigest()
        return self._artifact_message(
            f"imagent:appserver-artifact-terminal:{identity}",
            facts.thread_ref,
            output,
            default_message=None,
            created_at=created_at,
            terminal=True,
        )

    def _artifact_message(
        self,
        item_id: str,
        thread_ref: ThreadRef,
        output: ApplicationArtifactMaterialization,
        *,
        default_message: AgentMessage | None,
        created_at: datetime,
        terminal: bool,
    ) -> AgentMessage:
        if default_message is not None:
            return AgentMessage(
                agent_item_id=default_message.agent_item_id,
                thread_ref=default_message.thread_ref,
                role=default_message.role,
                content=(*default_message.content, *output.attachments),
                created_at=default_message.created_at,
                client_message_id=default_message.client_message_id,
                metadata=default_message.metadata,
            )
        return AgentMessage(
            agent_item_id=item_id,
            thread_ref=thread_ref,
            role=MessageRole.ASSISTANT,
            content=output.attachments,
            created_at=created_at,
            metadata={
                "native_application": self._summary.kind,
                "kind": ("artifact_terminal_fallback" if terminal else "artifact_materialization"),
            },
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
        item_id = _item_id(item)
        return AgentMessage(
            agent_item_id=item_id,
            thread_ref=thread_ref,
            role=role,
            content=(TextContent(text, TextFormat.MARKDOWN),),
            created_at=_parse_datetime(item.get("createdAt") or item.get("updatedAt")),
            metadata={
                "phase": _optional_string(item.get("phase")) or "",
                "native_application": self._summary.kind,
            },
        )

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
        self._require_own_thread(thread_ref)
        prepared = self._prepare_input(message)
        if not isinstance(continuation, InputContinuationPreference):
            raise ValueError("unknown input continuation preference")
        return await self._start_prepared_input(
            thread_ref,
            message,
            prepared,
            before_dispatch,
        )

    async def _start_prepared_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        prepared: _PreparedAppServerInput,
        before_dispatch: Callable[[ApplicationInputDispatch], Awaitable[None]] | None,
    ) -> AcceptedTurn:
        expected_local_image_epoch = (
            await self._verified_local_image_epoch() if prepared.input_items is not None else None
        )
        if before_dispatch is not None:
            await before_dispatch(
                ApplicationInputDispatch(
                    thread_ref=thread_ref,
                    client_message_id=message.client_message_id,
                    disposition=InputDisposition.STARTED,
                    correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
                    expected_turn_id=None,
                )
            )
        result = await self._start_input(
            thread_id=thread_ref.native_thread_id,
            prepared=prepared,
            expected_local_image_epoch=expected_local_image_epoch,
        )

        try:
            turn_id = _native_turn_id(result)
        except _AppServerMappingError as cause:
            raise ApplicationInputOutcomeUnknown(
                "turn/start was accepted but its native Turn identity is unknown",
                cause,
            ) from cause
        if not turn_id:
            cause = RuntimeError("turn/start did not return a turn id")
            raise ApplicationInputOutcomeUnknown(
                "turn/start was accepted but its native Turn identity is unknown",
                cause,
            ) from cause
        return AcceptedTurn(
            thread_ref=thread_ref,
            turn_id=turn_id,
            client_message_id=message.client_message_id,
            disposition=InputDisposition.STARTED,
            correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
        )

    def _prepare_input(self, message: AgentInput) -> _PreparedAppServerInput:
        text = "\n".join(
            part.text for part in message.content if isinstance(part, TextContent)
        ).strip()
        attachments = tuple(part for part in message.content if isinstance(part, AttachmentContent))
        input_items: list[Mapping[str, object]] | None = None
        if attachments:
            input_items = []
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
        elif not text:
            raise ValueError("Codex App Server input requires text or image")
        return _PreparedAppServerInput(
            text=text,
            input_items=(tuple(input_items) if input_items is not None else None),
        )

    def _input_arguments(
        self,
        prepared: _PreparedAppServerInput,
        expected_local_image_epoch: int | None,
    ) -> tuple[str | None, dict[str, object]]:
        if prepared.input_items is None:
            return prepared.text, {}
        return None, {
            "input_items": [dict(item) for item in prepared.input_items],
            "expected_local_image_epoch": expected_local_image_epoch,
        }

    async def _start_input(
        self,
        *,
        thread_id: str,
        prepared: _PreparedAppServerInput,
        expected_local_image_epoch: int | None,
    ) -> Mapping[str, object]:
        native_text, kwargs = self._input_arguments(
            prepared,
            expected_local_image_epoch,
        )
        return await self._client.start_turn(thread_id, native_text, **kwargs)

    async def _verified_local_image_epoch(self) -> int:
        epoch_reader = getattr(self._client, "local_image_paths_epoch", None)
        if not callable(epoch_reader):
            raise RuntimeError(
                "Codex App Server local images require a verified shared filesystem epoch"
            )
        epoch = epoch_reader()
        if epoch is None:
            initialize = getattr(self._client, "initialize", None)
            if callable(initialize):
                initialized = initialize()
                if inspect.isawaitable(initialized):
                    await initialized
                epoch = epoch_reader()
        if epoch is None:
            raise RuntimeError(
                "Codex App Server local images require a verified shared filesystem epoch"
            )
        return int(cast(int, epoch))

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if after_cursor is not None:
            raise NotImplementedError("Codex App Server does not support event replay")
        self._require_own_thread(thread_ref)
        return self._events.subscribe(thread_ref.native_thread_id)

    async def _handle_event_connection_reset(self, connection_epoch: int) -> None:
        del connection_epoch
        self._events.fail_all(EventStreamReset, discard_pending=False)

    def _fail_artifact_observation(self, thread_id: str) -> None:
        self._events.fail(
            thread_id,
            lambda: EventStreamReset("application_artifact_materialization_failed"),
            discard_pending=False,
        )

    async def _handle_notification(self, notification: dict) -> None:
        try:
            event = _normalize_appserver_message(notification)
            await self._handle_mapped_notification(event)
        except _AppServerMappingError:
            self._events.fail_all(
                lambda: EventStreamReset("application_native_mapping_failed"),
                discard_pending=False,
            )

    async def _handle_mapped_notification(self, event: _AppServerEvent) -> None:
        method = event.method
        params = event.payload
        if method == "serverRequest/resolved":
            await self._request_runtime.handle_resolution_notification(event)
            return
        thread_id = event.thread_id
        if thread_id is None:
            return
        turn_id = event.turn_id
        thread_ref = self._thread_ref(thread_id)
        if method == "item/agentMessage/delta":
            self._emit(
                thread_id,
                AgentEventType.MESSAGE_DELTA,
                {"delta": _optional_string(params.get("delta")) or ""},
                event_id=(event.event_id or f"{self._application_instance_id}:live:{uuid.uuid4()}"),
                thread_ref=thread_ref,
                turn_id=turn_id,
            )
            return
        if method == "item/completed":
            item = params.get("item")
            if not isinstance(item, Mapping) or turn_id is None or event.item_id is None:
                raise _AppServerMappingError
            item_type = _normalized_item_type(item)
            message: AgentMessage | None = None
            if item_type == "agentmessage":
                text = _item_text(item)
                if text:
                    message = AgentMessage(
                        agent_item_id=event.item_id,
                        thread_ref=thread_ref,
                        role=MessageRole.ASSISTANT,
                        content=(TextContent(text, TextFormat.MARKDOWN),),
                        created_at=datetime.now(UTC),
                        metadata={
                            "phase": _optional_string(item.get("phase")) or "",
                            "native_method": method,
                        },
                    )
            if self._artifact_materializer is not None and "user" not in item_type:
                try:
                    facts = self._artifact_completed_item_facts(
                        item,
                        thread_ref=thread_ref,
                        turn_id=turn_id,
                        authoritative=False,
                        default_message_id=(message.agent_item_id if message is not None else None),
                    )
                    identity = (thread_id, facts.turn_id, facts.item_id)
                    if identity in self._seen_live_artifact_identities:
                        runtime = self._artifact_materialization_runtime
                        if runtime is not None:
                            runtime.record_live_duplicate()
                        return
                    message = await self._materialize_completed_item(
                        facts,
                        default_message=message,
                        created_at=_parse_datetime(item.get("createdAt") or item.get("updatedAt")),
                    )
                except _ARTIFACT_OBSERVATION_ERRORS:
                    self._fail_artifact_observation(thread_id)
                    return
                self._remember_live_artifact_identity(identity)
            if message is None:
                return
            self._emit(
                thread_id,
                AgentEventType.MESSAGE_COMPLETED,
                {"message": message},
                event_id=_derive_appserver_event_id(
                    self._application_instance_id,
                    event_type=AgentEventType.MESSAGE_COMPLETED.value,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_id=message.agent_item_id,
                ),
                thread_ref=thread_ref,
                turn_id=turn_id,
            )
            return
        if method == "turn/completed":
            if turn_id is None:
                raise _AppServerMappingError
            turn = params.get("turn")
            status_value = params.get("status")
            if status_value is None and isinstance(turn, Mapping):
                status_value = turn.get("status")
            if isinstance(status_value, Mapping):
                status_value = status_value.get("type") or status_value.get("status")
            status = (_optional_string(status_value) or "").casefold()
            event_type = {
                "failed": AgentEventType.TURN_FAILED,
                "interrupted": AgentEventType.TURN_INTERRUPTED,
            }.get(status, AgentEventType.TURN_COMPLETED)
            if self._artifact_materializer is not None:
                try:
                    terminal_status = AppServerTurnTerminalStatus(
                        status if status in {"failed", "interrupted"} else "completed"
                    )
                    identity = (thread_id, turn_id, f"terminal:{terminal_status.value}")
                    if identity in self._seen_live_artifact_identities:
                        runtime = self._artifact_materialization_runtime
                        if runtime is not None:
                            runtime.record_live_duplicate()
                        return
                    terminal_message = await self._materialize_turn_terminal(
                        self._artifact_turn_terminal_facts(
                            thread_ref=thread_ref,
                            turn_id=turn_id,
                            authoritative=False,
                            status=terminal_status,
                        ),
                        created_at=datetime.now(UTC),
                    )
                except _ARTIFACT_OBSERVATION_ERRORS:
                    self._fail_artifact_observation(thread_id)
                    return
                if terminal_message is not None:
                    self._emit(
                        thread_id,
                        AgentEventType.MESSAGE_COMPLETED,
                        {"message": terminal_message},
                        event_id=_derive_appserver_event_id(
                            self._application_instance_id,
                            event_type=AgentEventType.MESSAGE_COMPLETED.value,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            item_id=terminal_message.agent_item_id,
                        ),
                        thread_ref=thread_ref,
                        turn_id=turn_id,
                    )
                self._remember_live_artifact_identity(identity)
            self._emit(
                thread_id,
                event_type,
                {"status": status or "completed"},
                event_id=_derive_appserver_event_id(
                    self._application_instance_id,
                    event_type=event_type.value,
                    thread_id=thread_id,
                    turn_id=turn_id,
                ),
                thread_ref=thread_ref,
                turn_id=turn_id,
            )

    def _remember_live_artifact_identity(self, identity: tuple[str, str, str]) -> None:
        self._seen_live_artifact_identities[identity] = None
        while (
            len(self._seen_live_artifact_identities)
            > self._artifact_materialization_limits.max_seen_identities
        ):
            self._seen_live_artifact_identities.pop(next(iter(self._seen_live_artifact_identities)))

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
        thread_id = _thread_id(thread)
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
                "cwd": _optional_string(thread.get("cwd")) or self._cwd,
                "preview": _optional_string(thread.get("preview")) or "",
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


def _thread_start_options(
    options: Mapping[str, object] | None,
) -> Mapping[str, object]:
    copied = deepcopy(dict(options or {}))
    invalid_keys = [key for key in copied if not isinstance(key, str) or not key]
    if invalid_keys:
        raise ValueError("App Server thread_start_options keys must be non-empty strings")
    reserved = sorted({"cwd", "params"}.intersection(copied))
    if reserved:
        raise ValueError(
            "App Server thread_start_options cannot override adapter-owned fields: "
            + ", ".join(reserved)
        )
    aliases = {
        "approval_policy": "approvalPolicy",
        "approvals_reviewer": "approvalsReviewer",
        "sandbox_policy": "sandboxPolicy",
        "service_name": "serviceName",
        "thread_id": "threadId",
    }
    normalized_keys = [aliases.get(key, key) for key in copied]
    if len(set(normalized_keys)) != len(normalized_keys):
        raise ValueError("App Server thread_start_options contain duplicate native fields")
    return MappingProxyType(copied)

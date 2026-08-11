"""Neutral Application implementation used by the reference consumer."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from imagent.applications.capabilities import (
    ApplicationCapabilities,
    EventSequenceScope,
    ProjectCapabilities,
    ProjectMode,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
    ThreadDeletionCapability,
    validate_application_capabilities,
)
from imagent.applications.contract import (
    MAX_WORKSPACE_ROOT_LENGTH,
    AcceptedTurn,
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    ApplicationInputDispatchHandler,
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
    fingerprint_canonical_workspace_root,
    validate_application_summary,
    validate_project_summary,
)
from imagent.applications.events import (
    AgentEvent,
    AgentEventType,
    CursorExpired,
    EventBroadcaster,
    FanoutSubscription,
)
from imagent.applications.operations import (
    ActivateNativeThread,
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    CreateProject,
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
    ProjectCreated,
    ProjectRead,
    ProjectsListed,
    ThreadCreated,
    ThreadHistoryRead,
    ThreadRead,
    ThreadsListed,
    ThreadStatusRead,
    TurnCatchupRead,
    validate_application_operation,
    validate_application_operation_result,
)
from imagent.applications.requests import InteractiveRequest
from imagent.diagnostics import (
    ApplicationDiagnosticFacts,
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
)
from imagent.interaction.messages import Content, MessageRole, TextContent, TextFormat
from imagent.interaction.operations import (
    OperationErrorCode,
    operation_error,
    require_identifier,
)


def _now() -> datetime:
    return datetime.now(UTC)


_EVENTS_PER_TURN = 3
_EVENT_BROADCASTER_MAX_PENDING = 256


class _CapacityExceeded(ValueError):
    """A reference Application bound rejected before mutation."""


class _IntentConflict(ValueError):
    """A stable native operation ID was reused with different intent."""


def _require_positive_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _canonical_workspace_root(root: str | Path) -> str:
    raw = str(root)
    if not raw or len(raw) > MAX_WORKSPACE_ROOT_LENGTH:
        raise ValueError(
            "workspace root must be a non-empty path of at most "
            f"{MAX_WORKSPACE_ROOT_LENGTH} characters"
        )
    canonical = str(Path(raw).expanduser().resolve(strict=False))
    fingerprint_canonical_workspace_root(canonical)
    return canonical


class _CountingSubscription(AsyncIterator[AgentEvent]):
    """Track one public Application observation subscription until it closes."""

    def __init__(
        self,
        subscription: FanoutSubscription[ThreadRef, AgentEvent],
        on_close: Callable[[], None],
    ) -> None:
        self._subscription = subscription
        self._on_close = on_close
        self._closed = False

    def __aiter__(self) -> _CountingSubscription:
        return self

    async def __anext__(self) -> AgentEvent:
        try:
            return await anext(self._subscription)
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._subscription.aclose()
        finally:
            self._on_close()


class ReferenceApplication:
    """A bounded authoritative Application for the lifetime of this demo."""

    def __init__(
        self,
        application_instance_id: str = "reference-agent",
        *,
        max_projects: int = 4,
        max_threads: int = 8,
        max_turns_per_thread: int = 8,
        max_events_per_thread: int = 64,
    ) -> None:
        self._max_projects = _require_positive_int(max_projects, "max_projects")
        self._max_threads = _require_positive_int(max_threads, "max_threads")
        self._max_turns_per_thread = _require_positive_int(
            max_turns_per_thread,
            "max_turns_per_thread",
        )
        self._max_events_per_thread = _require_positive_int(
            max_events_per_thread,
            "max_events_per_thread",
        )
        capabilities = ApplicationCapabilities(
            projects=ProjectCapabilities(
                mode=ProjectMode.MANAGED,
                discovery=SupportLevel.NATIVE,
                reading=SupportLevel.NATIVE,
                creation=SupportLevel.NATIVE,
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
                replay_from_cursor=SupportLevel.NATIVE,
                interruption=SupportLevel.UNSUPPORTED,
                interactive_requests=SupportLevel.UNSUPPORTED,
                gap_detection=SupportLevel.NATIVE,
                event_sequence_scope=EventSequenceScope.THREAD,
            ),
        )
        validate_application_capabilities(capabilities)
        self._summary = ApplicationSummary(
            ref=ApplicationRef(application_instance_id),
            kind="reference",
            display_name="Neutral Reference Agent",
            capabilities=capabilities,
        )
        validate_application_summary(self._summary)
        self._projects: dict[ProjectRef, ProjectSummary] = {}
        self._project_creations: dict[
            str,
            tuple[str, str | None, ProjectSummary],
        ] = {}
        self._project_creation_completed_at: dict[str, datetime] = {}
        self._threads: dict[ThreadRef, ThreadSummary] = {}
        self._thread_creations: dict[
            str,
            tuple[ProjectRef, str | None, tuple[Content, ...], ThreadSummary],
        ] = {}
        self._thread_creation_completed_at: dict[str, datetime] = {}
        self._turn_history: dict[ThreadRef, list[TurnHistoryEntry]] = {}
        self._event_history: dict[ThreadRef, list[AgentEvent]] = {}
        self._sequences: dict[ThreadRef, int] = {}
        self._subscriptions: dict[ThreadRef, int] = {}
        self._subscription_calls: dict[ThreadRef, int] = {}
        self._max_active_subscriptions: dict[ThreadRef, int] = {}
        self._reserved_turns: dict[ThreadRef, int] = {}
        self._reserved_events: dict[ThreadRef, int] = {}
        self._events = EventBroadcaster[ThreadRef, AgentEvent](
            max_pending=_EVENT_BROADCASTER_MAX_PENDING
        )
        self._event_epoch = "reference-epoch-1"
        self._next_project = 1
        self._next_thread = 1
        self._next_turn = 1
        self._project_creation_calls = 0
        self._thread_creation_calls = 0
        self._input_dispatch_calls = 0
        self._start_count = 0
        self._started = False

    @property
    def summary(self) -> ApplicationSummary:
        return self._summary

    @property
    def ref(self) -> ApplicationRef:
        return self._summary.ref

    @property
    def capabilities(self) -> ApplicationCapabilities:
        return self._summary.capabilities

    @property
    def started(self) -> bool:
        return self._started

    @property
    def max_threads(self) -> int:
        return self._max_threads

    @property
    def max_projects(self) -> int:
        return self._max_projects

    @property
    def max_turns_per_thread(self) -> int:
        return self._max_turns_per_thread

    @property
    def max_events_per_thread(self) -> int:
        return self._max_events_per_thread

    @property
    def project_creation_calls(self) -> int:
        return self._project_creation_calls

    @property
    def thread_creation_calls(self) -> int:
        return self._thread_creation_calls

    @property
    def input_dispatch_calls(self) -> int:
        return self._input_dispatch_calls

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._start_count += 1

    async def stop(self) -> None:
        self._started = False

    async def list_pending_requests(self) -> tuple[InteractiveRequest, ...]:
        return ()

    async def create_project(
        self,
        cwd: str,
        *,
        operation_id: str,
        display_name: str | None = None,
    ) -> ProjectSummary:
        self._project_creation_calls += 1
        require_identifier(operation_id, "operation ID")
        canonical_root = _canonical_workspace_root(cwd)
        existing = self._project_creations.get(operation_id)
        if existing is not None:
            existing_root, existing_name, project = existing
            if (existing_root, existing_name) != (canonical_root, display_name):
                raise _IntentConflict("Project operation ID was reused with different intent")
            return project
        if len(self._projects) >= self._max_projects:
            raise _CapacityExceeded("reference Application Project capacity is exhausted")
        project_ref = ProjectRef(
            self.summary.ref.application_instance_id,
            f"reference-project-{self._next_project}",
        )
        self._next_project += 1
        project = ProjectSummary(
            ref=project_ref,
            display_name=display_name or f"Reference Project {len(self._projects) + 1}",
            root_path=canonical_root,
            workspace_root_fingerprint=fingerprint_canonical_workspace_root(canonical_root),
        )
        validate_project_summary(project)
        self._projects[project.ref] = project
        self._project_creations[operation_id] = (canonical_root, display_name, project)
        self._project_creation_completed_at[operation_id] = _now()
        return project

    async def create_thread(
        self,
        project_ref: ProjectRef,
        *,
        operation_id: str,
        title: str | None = None,
        initial_context: tuple[Content, ...] = (),
    ) -> ThreadSummary:
        self._thread_creation_calls += 1
        require_identifier(operation_id, "operation ID")
        if project_ref not in self._projects:
            raise ValueError("Thread belongs to an unknown managed Project")
        existing = self._thread_creations.get(operation_id)
        if existing is not None:
            existing_project, existing_title, existing_context, thread = existing
            if (existing_project, existing_title, existing_context) != (
                project_ref,
                title,
                initial_context,
            ):
                raise _IntentConflict("Thread operation ID was reused with different intent")
            return thread
        if initial_context:
            raise NotImplementedError("reference Thread initial context is unsupported")
        if len(self._threads) >= self._max_threads:
            raise _CapacityExceeded("reference Application Thread capacity is exhausted")
        thread_ref = ThreadRef(
            project_ref=project_ref,
            thread_id=f"reference-thread-{self._next_thread}",
        )
        self._next_thread += 1
        summary = ThreadSummary(
            ref=thread_ref,
            status=ThreadStatus.IDLE,
            title=title,
            updated_at=_now(),
        )
        self._threads[thread_ref] = summary
        self._turn_history[thread_ref] = []
        self._event_history[thread_ref] = []
        self._sequences[thread_ref] = 0
        self._subscriptions[thread_ref] = 0
        self._subscription_calls[thread_ref] = 0
        self._max_active_subscriptions[thread_ref] = 0
        self._thread_creations[operation_id] = (
            project_ref,
            title,
            initial_context,
            summary,
        )
        self._thread_creation_completed_at[operation_id] = _now()
        return summary

    async def get_thread(self, thread_ref: ThreadRef) -> ThreadSummary:
        return self._threads[thread_ref]

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn:
        require_identifier(message.client_message_id, "client message ID")
        self._input_dispatch_calls += 1
        if not isinstance(continuation, InputContinuationPreference):
            raise ValueError("unknown input continuation preference")
        current = self._threads[thread_ref]
        self._reserve_turn_and_events(thread_ref)
        try:
            if before_dispatch is not None:
                await before_dispatch(
                    ApplicationInputDispatch(
                        thread_ref=thread_ref,
                        client_message_id=message.client_message_id,
                        disposition=InputDisposition.STARTED,
                        correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
                    )
                )

            turn_id = f"reference-turn-{self._next_turn}"
            self._next_turn += 1
            started_at = _now()
            self._threads[thread_ref] = replace(
                current,
                status=ThreadStatus.RUNNING,
                updated_at=started_at,
            )
            user_message = AgentMessage(
                agent_item_id=f"{turn_id}:user",
                thread_ref=thread_ref,
                role=MessageRole.USER,
                content=message.content,
                created_at=started_at,
                client_message_id=message.client_message_id,
                metadata=dict(message.metadata),
            )
            response_text = self._input_text(message)
            agent_message = AgentMessage(
                agent_item_id=f"{turn_id}:assistant",
                thread_ref=thread_ref,
                role=MessageRole.ASSISTANT,
                content=(
                    TextContent(
                        f"Neutral response: {response_text}",
                        TextFormat.MARKDOWN,
                    ),
                ),
                created_at=_now(),
                metadata=dict(message.metadata),
            )
            self._turn_history[thread_ref].append(
                TurnHistoryEntry(
                    turn_ref=TurnRef(thread_ref, turn_id),
                    status=TurnStatus.COMPLETED,
                    user_message=user_message,
                    agent_messages=(agent_message,),
                )
            )
            self._publish(
                thread_ref,
                AgentEventType.TURN_STARTED,
                turn_id,
                {"status": TurnStatus.RUNNING.value},
            )
            self._publish(
                thread_ref,
                AgentEventType.MESSAGE_COMPLETED,
                turn_id,
                {"message": agent_message},
            )
            self._threads[thread_ref] = replace(
                self._threads[thread_ref],
                status=ThreadStatus.COMPLETED,
                updated_at=_now(),
            )
            self._publish(
                thread_ref,
                AgentEventType.TURN_COMPLETED,
                turn_id,
                {"status": TurnStatus.COMPLETED.value},
            )
            return AcceptedTurn(
                turn_ref=TurnRef(thread_ref, turn_id),
                client_message_id=message.client_message_id,
                disposition=InputDisposition.STARTED,
                correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
            )
        finally:
            self._release_turn_and_events(thread_ref)

    async def receive_native_text(
        self,
        thread_ref: ThreadRef,
        *,
        client_message_id: str,
        text: str,
        metadata: Mapping[str, object] | None = None,
    ) -> AcceptedTurn:
        """Accept native Application input outside the SDK Gateway path."""

        require_identifier(client_message_id, "client message ID")
        return await self.send_input(
            thread_ref,
            AgentInput(
                client_message_id=client_message_id,
                content=(TextContent(text, TextFormat.MARKDOWN),),
                metadata={} if metadata is None else dict(metadata),
            ),
        )

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> _CountingSubscription:
        if thread_ref not in self._threads:
            raise KeyError(thread_ref)
        initial: tuple[AgentEvent, ...] = ()
        if after_cursor is not None:
            history = self._event_history[thread_ref]
            for index, event in enumerate(history):
                if event.cursor == after_cursor:
                    initial = tuple(history[index + 1 :])
                    break
            else:
                raise CursorExpired("reference replay cursor expired or is unknown")
        if (
            sum(self._subscriptions.values()) >= self._max_threads
            or self._subscription_calls[thread_ref] >= self._max_events_per_thread
        ):
            raise _CapacityExceeded("reference Application observation capacity is exhausted")
        subscription = self._events.subscribe(thread_ref, initial=initial)
        self._subscription_calls[thread_ref] = self._subscription_calls.get(thread_ref, 0) + 1
        active = self._subscriptions.get(thread_ref, 0) + 1
        self._subscriptions[thread_ref] = active
        self._max_active_subscriptions[thread_ref] = max(
            active,
            self._max_active_subscriptions.get(thread_ref, 0),
        )

        def closed() -> None:
            self._subscriptions[thread_ref] = max(
                0,
                self._subscriptions.get(thread_ref, 1) - 1,
            )

        return _CountingSubscription(subscription, closed)

    def subscription_calls(self, thread_ref: ThreadRef) -> int:
        return self._subscription_calls.get(thread_ref, 0)

    def max_active_observation_workers(self, thread_ref: ThreadRef) -> int:
        return self._max_active_subscriptions.get(thread_ref, 0)

    def active_observation_workers(self, thread_ref: ThreadRef) -> int:
        return self._subscriptions.get(thread_ref, 0)

    async def execute(self, operation: ApplicationOperation) -> ApplicationOperationResult:
        try:
            validate_application_operation(operation)
            result = await self._execute(operation)
            validate_application_operation_result(operation, result)
            return result
        except Exception as error:
            error_code = None
            if isinstance(error, _CapacityExceeded):
                error_code = OperationErrorCode.CAPACITY_EXHAUSTED
            elif isinstance(error, _IntentConflict):
                error_code = OperationErrorCode.CONFLICT
            return ApplicationOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=_now(),
                error=operation_error(error, code=error_code),
            )

    async def _execute(self, operation: ApplicationOperation) -> ApplicationOperationResult:
        if operation.application_ref != self.ref:
            raise ValueError("operation belongs to a different Application")
        completed_at = _now()
        if isinstance(operation, ListProjects):
            if operation.cursor is not None:
                raise ValueError("reference Project listing does not support cursors")
            projects = tuple(self._projects.values())
            if operation.query:
                query = operation.query.casefold()
                projects = tuple(
                    project for project in projects if query in project.display_name.casefold()
                )
            return ProjectsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                projects=Page(projects),
            )
        if isinstance(operation, GetProject):
            return ProjectRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                project=self._projects[operation.project_ref],
            )
        if isinstance(operation, CreateProject):
            project = await self.create_project(
                operation.cwd,
                operation_id=operation.operation_id,
                display_name=operation.display_name,
            )
            return ProjectCreated(
                operation_id=operation.operation_id,
                completed_at=self._project_creation_completed_at[operation.operation_id],
                project=project,
            )
        if isinstance(operation, CreateThread):
            thread = await self.create_thread(
                operation.project_ref,
                operation_id=operation.operation_id,
                title=operation.title,
                initial_context=operation.initial_context,
            )
            return ThreadCreated(
                operation_id=operation.operation_id,
                completed_at=self._thread_creation_completed_at[operation.operation_id],
                thread=thread,
            )
        if isinstance(operation, ListThreads):
            if operation.project_ref not in self._projects:
                raise ValueError("Project belongs to a different reference Application")
            if operation.cursor is not None:
                raise ValueError("reference Thread listing does not support cursors")
            threads = tuple(
                thread
                for thread in self._threads.values()
                if thread.ref.project_ref == operation.project_ref
            )
            if operation.query:
                query = operation.query.casefold()
                threads = tuple(
                    thread for thread in threads if query in (thread.title or "").casefold()
                )
            return ThreadsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                threads=Page(threads),
            )
        if isinstance(operation, GetThread):
            return ThreadRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread=await self.get_thread(operation.thread_ref),
            )
        if isinstance(operation, ActivateNativeThread):
            raise NotImplementedError("native Thread activation is not part of the example")
        if isinstance(operation, DeleteThread):
            raise NotImplementedError("Thread deletion is not part of the example")
        if isinstance(operation, GetThreadStatus):
            return ThreadStatusRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread_ref=operation.thread_ref,
                thread_status=(await self.get_thread(operation.thread_ref)).status,
            )
        if isinstance(operation, GetThreadHistory):
            turns = self._turn_history[operation.thread_ref]
            end = max(0, len(turns) - ((operation.page - 1) * operation.limit))
            start = max(0, end - operation.limit)
            return ThreadHistoryRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                history=ThreadHistory(
                    thread_ref=operation.thread_ref,
                    turns=tuple(turns[start:end]),
                    page=operation.page,
                    has_older=start > 0,
                ),
            )
        if isinstance(operation, GetTurnCatchup):
            turns = self._turn_history[operation.thread_ref]
            latest = turns[-1] if turns else None
            return TurnCatchupRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                catchup=TurnCatchup(
                    thread_ref=operation.thread_ref,
                    turn_ref=latest.turn_ref if latest is not None else None,
                    status=latest.status if latest is not None else TurnStatus.IDLE,
                    messages=latest.agent_messages if latest is not None else (),
                    updated_at=(await self.get_thread(operation.thread_ref)).updated_at,
                ),
            )
        if isinstance(operation, InterruptTurn):
            raise NotImplementedError("turn interruption is not part of the example")
        raise NotImplementedError(operation.type.value)

    def diagnostic_facts(self) -> ApplicationDiagnosticFacts:
        return ApplicationDiagnosticFacts(
            application_instance_id=self.summary.ref.application_instance_id,
            kind=self.summary.kind,
            connection=ConnectionDiagnosticFacts(
                state=(
                    ConnectionDiagnosticState.READY
                    if self._started
                    else ConnectionDiagnosticState.DISCONNECTED
                ),
                connection_epoch=self._start_count,
                reconnect_count=max(0, self._start_count - 1),
                worker_running=self._started,
                worker_degraded=False,
            ),
        )

    @staticmethod
    def _input_text(message: AgentInput) -> str:
        texts = tuple(item.text for item in message.content if isinstance(item, TextContent))
        return " ".join(texts) or "(non-text input)"

    def _reserve_turn_and_events(self, thread_ref: ThreadRef) -> None:
        turn_count = len(self._turn_history[thread_ref]) + self._reserved_turns.get(
            thread_ref,
            0,
        )
        event_count = len(self._event_history[thread_ref]) + self._reserved_events.get(
            thread_ref,
            0,
        )
        if turn_count >= self._max_turns_per_thread:
            raise _CapacityExceeded("reference Application Turn capacity is exhausted")
        if event_count + _EVENTS_PER_TURN > self._max_events_per_thread:
            raise _CapacityExceeded("reference Application replay-event capacity is exhausted")
        self._reserved_turns[thread_ref] = self._reserved_turns.get(thread_ref, 0) + 1
        self._reserved_events[thread_ref] = (
            self._reserved_events.get(thread_ref, 0) + _EVENTS_PER_TURN
        )

    def _release_turn_and_events(self, thread_ref: ThreadRef) -> None:
        reserved_turns = self._reserved_turns.get(thread_ref, 1) - 1
        if reserved_turns > 0:
            self._reserved_turns[thread_ref] = reserved_turns
        else:
            self._reserved_turns.pop(thread_ref, None)
        reserved_events = self._reserved_events.get(thread_ref, _EVENTS_PER_TURN)
        reserved_events -= _EVENTS_PER_TURN
        if reserved_events > 0:
            self._reserved_events[thread_ref] = reserved_events
        else:
            self._reserved_events.pop(thread_ref, None)

    def _publish(
        self,
        thread_ref: ThreadRef,
        event_type: AgentEventType,
        turn_id: str,
        data: dict[str, object],
    ) -> None:
        if len(self._event_history[thread_ref]) >= self._max_events_per_thread:
            raise RuntimeError("reserved reference replay-event capacity was violated")
        sequence = self._sequences[thread_ref] + 1
        self._sequences[thread_ref] = sequence
        event = AgentEvent(
            event_id=(
                f"{self.summary.ref.application_instance_id}:"
                f"{thread_ref.thread_id}:{sequence}:{event_type.value}"
            ),
            application_instance_id=self.summary.ref.application_instance_id,
            project_ref=thread_ref.project_ref,
            type=event_type,
            data=data,
            created_at=_now(),
            thread_ref=thread_ref,
            turn_ref=TurnRef(thread_ref, turn_id),
            sequence=sequence,
            sequence_epoch=self._event_epoch,
            cursor=f"reference:{self._event_epoch}:{thread_ref.thread_id}:{sequence}",
        )
        history = self._event_history[thread_ref]
        history.append(event)
        self._events.publish(thread_ref, event)


__all__ = ["ReferenceApplication"]

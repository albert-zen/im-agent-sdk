from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from ...applications.capabilities import SupportLevel
from ...applications.contract import (
    AgentApplicationAdapter,
    AgentMessage,
    ThreadHistory,
    ThreadRef,
    TurnCatchup,
)
from ...applications.events import (
    AgentEvent,
    CursorExpired,
    EventBufferOverflow,
    EventStreamGap,
)
from ...applications.operations import (
    ApplicationOperationFailed,
    ApplicationOperationResult,
    GetThreadHistory,
    GetTurnCatchup,
    ThreadHistoryRead,
    TurnCatchupRead,
    _RuntimeApplicationOperation,
)
from ...applications.requests import InteractiveRequest
from ...interaction.messages import ConversationRef
from ..persistence.state_contracts import ThreadProjectionRoute
from ..routing.projection_routes import derive_projection_route_id


class RecoveryMode(StrEnum):
    REPLAY = "replay"
    AUTHORITATIVE = "authoritative"


@dataclass(frozen=True, slots=True)
class ThreadRecovery:
    mode: RecoveryMode
    events: AsyncIterator[AgentEvent]
    history: ThreadHistory | None = None
    catchup: TurnCatchup | None = None
    cursor_expired: bool = False


@dataclass(frozen=True, slots=True)
class ProjectedAgentMessage:
    """One normalized item supplied by live observation or bounded recovery."""

    message: AgentMessage
    turn_id: str | None
    event_id: str | None = None
    checkpoint: bool = True


@dataclass(frozen=True, slots=True)
class AuthoritativeProjectionSlice:
    """Bounded ordered recovery evidence for one destination route."""

    messages: tuple[ProjectedAgentMessage, ...]
    pages_read: int
    checkpoint_found: bool
    gap: str | None = None


ExecuteApplication = Callable[
    [_RuntimeApplicationOperation],
    Awaitable[ApplicationOperationResult],
]


class ProjectionRecoveryUnavailable(RuntimeError):
    """The Application could not provide an authoritative recovery read."""


_ActiveRoutes = Callable[
    [ThreadRef | None],
    Awaitable[tuple[ThreadProjectionRoute, ...]],
]
_BeginBootstrap = Callable[[str], Awaitable[None]]
_WaitUntilReady = Callable[[], Awaitable[object]]
_RecordGap = Callable[[ThreadRef, str, str], None]
_DeliverRequest = Callable[
    [tuple[ThreadProjectionRoute, ...], InteractiveRequest],
    Awaitable[None],
]
_ReadAuthoritativeProjection = Callable[
    [ThreadProjectionRoute],
    Awaitable[AuthoritativeProjectionSlice],
]
_ValidateLifecycle = Callable[[], None]


class _DeliverAuthoritative(Protocol):
    def __call__(
        self,
        route: ThreadProjectionRoute,
        *,
        read_projection: _ReadAuthoritativeProjection,
        retain_barrier_on_failure: bool = False,
        validate_lifecycle: _ValidateLifecycle | None = None,
    ) -> Awaitable[None]: ...


class _ReconcileRequestSnapshot(Protocol):
    def __call__(
        self,
        application: AgentApplicationAdapter,
        thread_ref: ThreadRef,
        *,
        deliver_request: _DeliverRequest,
    ) -> Awaitable[bool]: ...


def _finite_non_negative_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True, slots=True)
class _RecoveryLimits:
    baseline_history_limit: int
    recovery_history_page_size: int
    recovery_max_pages: int
    catchup_limit: int
    projection_item_limit: int
    retry_initial_seconds: float
    retry_max_seconds: float

    def __post_init__(self) -> None:
        for name, value in (
            ("baseline_history_limit", self.baseline_history_limit),
            ("recovery_history_page_size", self.recovery_history_page_size),
            ("recovery_max_pages", self.recovery_max_pages),
            ("catchup_limit", self.catchup_limit),
            ("projection_item_limit", self.projection_item_limit),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not _finite_non_negative_number(self.retry_initial_seconds):
            raise ValueError(
                "initial subscription retry delay must be a finite non-negative number"
            )
        if not _finite_non_negative_number(self.retry_max_seconds):
            raise ValueError(
                "maximum subscription retry delay must be a finite non-negative number"
            )
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("maximum subscription retry delay is below initial delay")


@dataclass(slots=True)
class _RecoveryAttempt:
    restart_count: int
    needs_reconciliation: bool
    require_checkpoint: bool
    reconcile_requests: bool = False


@dataclass(frozen=True, slots=True)
class _RecoveryHealthSnapshot:
    event_overflow_count: int = 0
    last_subscription_error: str | None = None
    last_recovery_error: str | None = None
    last_gap: str | None = None
    last_event_gap: str | None = None
    last_event_overflow: str | None = None


@dataclass(frozen=True, slots=True)
class _RecoveryFailure:
    restart_count: int
    retry_delay_seconds: float
    event_overflow_count: int
    last_subscription_error: str | None
    last_recovery_error: str | None
    last_gap: str | None
    last_event_gap: str | None
    last_event_overflow: str | None
    interactive_request_recovery_degraded: bool


class _RecoverySupervisor:
    """Own bounded recovery decisions without owning the observation worker."""

    def __init__(
        self,
        *,
        execute_application: ExecuteApplication,
        active_routes: _ActiveRoutes,
        begin_bootstrap: _BeginBootstrap,
        deliver_authoritative: _DeliverAuthoritative,
        wait_until_delivery_ready: _WaitUntilReady,
        record_gap: _RecordGap,
        reconcile_request_snapshot: _ReconcileRequestSnapshot,
        deliver_request: _DeliverRequest,
        baseline_history_limit: int,
        recovery_history_page_size: int,
        recovery_max_pages: int,
        catchup_limit: int,
        projection_item_limit: int,
        retry_initial_seconds: float,
        retry_max_seconds: float,
    ) -> None:
        self._execute_application = execute_application
        self._active_routes = active_routes
        self._begin_bootstrap = begin_bootstrap
        self._deliver_authoritative = deliver_authoritative
        self._wait_until_delivery_ready = wait_until_delivery_ready
        self._record_gap = record_gap
        self._reconcile_request_snapshot = reconcile_request_snapshot
        self._deliver_request = deliver_request
        self._limits = _RecoveryLimits(
            baseline_history_limit=baseline_history_limit,
            recovery_history_page_size=recovery_history_page_size,
            recovery_max_pages=recovery_max_pages,
            catchup_limit=catchup_limit,
            projection_item_limit=projection_item_limit,
            retry_initial_seconds=retry_initial_seconds,
            retry_max_seconds=retry_max_seconds,
        )

    @staticmethod
    def start_attempt(
        *,
        reconcile_existing: bool,
        require_checkpoint: bool,
    ) -> _RecoveryAttempt:
        return _RecoveryAttempt(
            restart_count=0,
            needs_reconciliation=reconcile_existing,
            require_checkpoint=require_checkpoint,
        )

    async def prepare_reconciliation(
        self,
        attempt: _RecoveryAttempt,
        thread_ref: ThreadRef,
    ) -> None:
        if not attempt.needs_reconciliation:
            return
        for route in await self._active_routes(thread_ref):
            await self._begin_bootstrap(route.route_id)

    async def reconcile_thread(
        self,
        attempt: _RecoveryAttempt,
        application: AgentApplicationAdapter,
        thread_ref: ThreadRef,
    ) -> bool | None:
        if not attempt.needs_reconciliation:
            return None
        await self._wait_until_delivery_ready()
        await self.reconcile_routes(
            application,
            await self._active_routes(thread_ref),
            require_checkpoint=attempt.require_checkpoint,
        )
        attempt.require_checkpoint = True
        request_recovery_degraded: bool | None = None
        if attempt.reconcile_requests:
            request_recovery_degraded = await self._reconcile_request_snapshot(
                application,
                thread_ref,
                deliver_request=self._deliver_request,
            )
            attempt.reconcile_requests = False
        attempt.needs_reconciliation = False
        return request_recovery_degraded

    async def reconcile_routes(
        self,
        application: AgentApplicationAdapter,
        routes: tuple[ThreadProjectionRoute, ...],
        *,
        require_checkpoint: bool,
    ) -> None:
        results = await asyncio.gather(
            *(
                self.reconcile_route(
                    application,
                    route,
                    require_checkpoint=require_checkpoint,
                )
                for route in routes
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def reconcile_route(
        self,
        application: AgentApplicationAdapter,
        route: ThreadProjectionRoute,
        *,
        require_checkpoint: bool,
        retain_barrier_on_failure: bool = False,
        validate_lifecycle: _ValidateLifecycle | None = None,
    ) -> None:
        async def read_projection(
            current: ThreadProjectionRoute,
        ) -> AuthoritativeProjectionSlice:
            projection = await read_bounded_authoritative_projection(
                application,
                current,
                execute_application=self._execute_application,
                baseline_history_limit=self._limits.baseline_history_limit,
                recovery_history_page_size=self._limits.recovery_history_page_size,
                recovery_max_pages=self._limits.recovery_max_pages,
                catchup_limit=self._limits.catchup_limit,
                projection_item_limit=self._limits.projection_item_limit,
                require_checkpoint=require_checkpoint,
            )
            if projection.gap is not None:
                self._record_gap(
                    route.thread_ref,
                    route.route_id,
                    projection.gap,
                )
            return projection

        await self._deliver_authoritative(
            route,
            read_projection=read_projection,
            retain_barrier_on_failure=retain_barrier_on_failure,
            validate_lifecycle=validate_lifecycle,
        )

    def record_failure(
        self,
        attempt: _RecoveryAttempt,
        error: Exception,
        *,
        application: AgentApplicationAdapter | None,
        current_health: _RecoveryHealthSnapshot | None,
    ) -> _RecoveryFailure:
        attempt.restart_count += 1
        attempt.needs_reconciliation = True
        attempt.require_checkpoint = True
        attempt.reconcile_requests = True

        event_overflow_count = (
            current_health.event_overflow_count if current_health is not None else 0
        )
        last_subscription_error = (
            current_health.last_subscription_error if current_health is not None else None
        )
        last_recovery_error = (
            current_health.last_recovery_error if current_health is not None else None
        )
        last_gap = current_health.last_gap if current_health is not None else None
        last_event_gap = current_health.last_event_gap if current_health is not None else None
        last_event_overflow = (
            current_health.last_event_overflow if current_health is not None else None
        )

        if isinstance(error, ProjectionRecoveryUnavailable):
            last_recovery_error = str(error)
            last_subscription_error = None
        elif isinstance(error, EventStreamGap):
            last_gap = error.gap_code
            last_event_gap = error.gap_code
            last_subscription_error = None
            if isinstance(error, EventBufferOverflow):
                last_event_overflow = error.gap_code
                event_overflow_count += 1
        else:
            last_subscription_error = str(error)

        exponent = min(attempt.restart_count - 1, 30)
        delay = min(
            self._limits.retry_initial_seconds * (2**exponent),
            self._limits.retry_max_seconds,
        )
        return _RecoveryFailure(
            restart_count=attempt.restart_count,
            retry_delay_seconds=delay,
            event_overflow_count=event_overflow_count,
            last_subscription_error=last_subscription_error,
            last_recovery_error=last_recovery_error,
            last_gap=last_gap,
            last_event_gap=last_event_gap,
            last_event_overflow=last_event_overflow,
            interactive_request_recovery_degraded=(
                _request_recovery_is_degraded(application) if application is not None else False
            ),
        )


async def recover_thread(
    application: AgentApplicationAdapter,
    thread_ref: ThreadRef,
    *,
    recovery_id: str,
    after_cursor: str | None = None,
    history_limit: int = 20,
    catchup_limit: int = 20,
) -> ThreadRecovery:
    """Resume native replay or establish live-first authoritative reconciliation."""
    if history_limit < 1:
        raise ValueError("history_limit must be positive")
    if catchup_limit < 1:
        raise ValueError("catchup_limit must be positive")
    if thread_ref.project_ref.application_instance_id != (
        application.summary.ref.application_instance_id
    ):
        raise ValueError("recovery Thread belongs to a different application")

    cursor_expired = False
    if (
        after_cursor is not None
        and application.summary.capabilities.runtime.replay_from_cursor
        is not SupportLevel.UNSUPPORTED
    ):
        try:
            events = application.subscribe_thread(
                thread_ref,
                after_cursor=after_cursor,
            )
        except CursorExpired:
            cursor_expired = True
        else:
            return ThreadRecovery(mode=RecoveryMode.REPLAY, events=events)

    events = application.subscribe_thread(thread_ref)
    created_at = datetime.now(UTC)
    try:
        history_result = await application.execute(
            GetThreadHistory(
                operation_id=f"{recovery_id}:thread.history",
                application_ref=application.summary.ref,
                thread_ref=thread_ref,
                limit=history_limit,
                page=1,
                created_at=created_at,
            )
        )
        catchup_result = await application.execute(
            GetTurnCatchup(
                operation_id=f"{recovery_id}:turn.catchup",
                application_ref=application.summary.ref,
                thread_ref=thread_ref,
                limit=catchup_limit,
                created_at=created_at,
            )
        )
        if isinstance(history_result, ApplicationOperationFailed):
            raise RuntimeError(
                f"authoritative history recovery failed: {history_result.error.message}"
            )
        if isinstance(catchup_result, ApplicationOperationFailed):
            raise RuntimeError(
                f"authoritative catch-up recovery failed: {catchup_result.error.message}"
            )
        if not isinstance(history_result, ThreadHistoryRead) or not isinstance(
            catchup_result,
            TurnCatchupRead,
        ):
            raise RuntimeError("authoritative recovery returned an incompatible result")
    except BaseException:
        await _close(events)
        raise
    return ThreadRecovery(
        mode=RecoveryMode.AUTHORITATIVE,
        events=events,
        history=history_result.history,
        catchup=catchup_result.catchup,
        cursor_expired=cursor_expired,
    )


async def read_bounded_authoritative_projection(
    application: AgentApplicationAdapter,
    route: ThreadProjectionRoute,
    *,
    execute_application: ExecuteApplication,
    baseline_history_limit: int,
    recovery_history_page_size: int,
    recovery_max_pages: int,
    catchup_limit: int,
    projection_item_limit: int,
    require_checkpoint: bool = False,
) -> AuthoritativeProjectionSlice:
    """Read a recent baseline or scan newest pages toward one route checkpoint."""
    if projection_item_limit < 1:
        raise ValueError("projection_item_limit must be positive")
    thread_ref = route.thread_ref
    checkpoint = route.checkpoint_agent_item_id
    history_pages: list[tuple[ProjectedAgentMessage, ...]] = []
    recovery_scope = derive_projection_route_id(
        thread_ref,
        ConversationRef("gateway", "authoritative-recovery"),
    )
    history_limit = baseline_history_limit if checkpoint is None else recovery_history_page_size
    max_pages = 1 if checkpoint is None else recovery_max_pages
    page = 1
    checkpoint_found = False
    gap = "checkpoint_missing" if checkpoint is None and require_checkpoint else None
    while page <= max_pages:
        history_result = await execute_application(
            GetThreadHistory(
                operation_id=f"{recovery_scope}:thread.history:{page}",
                application_ref=application.summary.ref,
                thread_ref=thread_ref,
                limit=history_limit,
                page=page,
                created_at=datetime.now(UTC),
            )
        )
        if not isinstance(history_result, ThreadHistoryRead):
            raise ProjectionRecoveryUnavailable("history_unavailable")
        page_messages = tuple(
            ProjectedAgentMessage(
                message=message,
                turn_id=turn.turn_ref.turn_id,
            )
            for turn in history_result.history.turns
            for message in turn.agent_messages
        )
        history_pages.append(page_messages)
        if checkpoint is not None and any(
            item.message.agent_item_id == checkpoint for item in page_messages
        ):
            checkpoint_found = True
            break
        if checkpoint is None:
            break
        if not history_result.history.has_older:
            gap = "checkpoint_missing"
            break
        if page == max_pages:
            gap = "checkpoint_out_of_window"
            break
        page += 1
    catchup_result = await execute_application(
        GetTurnCatchup(
            operation_id=f"{recovery_scope}:turn.catchup",
            application_ref=application.summary.ref,
            thread_ref=thread_ref,
            limit=catchup_limit,
            created_at=datetime.now(UTC),
        )
    )
    messages: list[ProjectedAgentMessage] = []
    for history_page in reversed(history_pages):
        messages.extend(history_page)
    if checkpoint_found and checkpoint is not None:
        checkpoint_index = next(
            index for index, item in enumerate(messages) if item.message.agent_item_id == checkpoint
        )
        messages = messages[checkpoint_index + 1 :]
    if isinstance(catchup_result, TurnCatchupRead):
        messages.extend(
            ProjectedAgentMessage(
                message=message,
                turn_id=(
                    catchup_result.catchup.turn_ref.turn_id
                    if catchup_result.catchup.turn_ref is not None
                    else None
                ),
            )
            for message in catchup_result.catchup.messages
        )
    else:
        raise ProjectionRecoveryUnavailable("catchup_unavailable")
    deduplicated: list[ProjectedAgentMessage] = []
    seen: set[str] = set()
    for item in messages:
        if item.message.agent_item_id in seen:
            continue
        seen.add(item.message.agent_item_id)
        deduplicated.append(item)
    if len(deduplicated) > projection_item_limit:
        gap = gap or "projection_window_truncated"
        deduplicated = (
            deduplicated[:projection_item_limit]
            if checkpoint_found
            else deduplicated[-projection_item_limit:]
        )
    return AuthoritativeProjectionSlice(
        messages=tuple(deduplicated),
        pages_read=len(history_pages),
        checkpoint_found=checkpoint_found,
        gap=gap,
    )


def _request_recovery_is_degraded(
    application: AgentApplicationAdapter,
) -> bool:
    runtime = application.summary.capabilities.runtime
    return (
        runtime.interactive_requests is not SupportLevel.UNSUPPORTED
        and runtime.pending_request_snapshot is not SupportLevel.NATIVE
    )


async def _close(events: AsyncIterator[AgentEvent]) -> None:
    close = getattr(events, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result

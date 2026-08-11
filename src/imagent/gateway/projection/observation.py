from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import islice
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from ...applications.contract import (
    AgentApplicationAdapter,
    AgentMessage,
    ThreadRef,
)
from ...applications.events import (
    AgentEvent,
    AgentEventType,
    EventStreamGap,
)
from ...applications.operations import ApplicationOperationResult, _LegacyApplicationOperation
from ...applications.requests import InteractiveRequest, RequestRef
from ...interaction.controllers.request_presentation import RequestPresenter
from ...interaction.messages import (
    ConversationRef,
    OutboundMessage,
    TextContent,
    TextFormat,
)
from ..input.dispatch import TurnAcceptanceOrderingGate
from ..persistence.repository_contracts import (
    BindingRepository,
    IdempotencyClaimStatus,
    ProjectionRouteRepository,
    RequestCorrelationRepository,
)
from ..persistence.state_contracts import (
    ConversationBinding,
    ThreadProjectionRoute,
)
from ..routing.projection_routes import (
    ProjectionPolicy,
    _ProjectionRouteAuthority,
    derive_projection_route_id,
    get_projection_route,
)
from .recovery import ProjectedAgentMessage
from .request_correlation import InteractiveRequestProjection, _request_expired

if TYPE_CHECKING:
    from .checkpoints import _ProjectionCheckpointAuthority
    from .recovery import _RecoveryAttempt

logger = logging.getLogger(__name__)

DeliverOutbound = Callable[[OutboundMessage, bool], Awaitable[IdempotencyClaimStatus]]
DeliverRequestOutbound = Callable[[OutboundMessage], Awaitable[IdempotencyClaimStatus]]

_PROJECTION_METADATA_MAX_ITEMS = 16
_PROJECTION_METADATA_MAX_KEY_LENGTH = 64
_PROJECTION_METADATA_MAX_TEXT_LENGTH = 256
_PROJECTION_METADATA_MIN_INTEGER = -(2**63)
_PROJECTION_METADATA_MAX_INTEGER = 2**63 - 1


class _DestinationDecisionError(RuntimeError):
    """A typed Channel/destination decision failure isolated to one route."""


class RetryableDeliveryError(_DestinationDecisionError):
    """A safely deferred destination decision with an optional retry hint."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class _ProjectionRecoveryRequired(RuntimeError):
    """A released pre-Channel projection claim that authoritative recovery may retry."""


class ProjectionWorkerState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    RETRYING = "retrying"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ProjectionWorkerHealth:
    thread_ref: ThreadRef
    state: ProjectionWorkerState
    restart_count: int = 0
    delivery_failure_count: int = 0
    event_overflow_count: int = 0
    last_subscription_error: str | None = None
    last_recovery_error: str | None = None
    last_delivery_error: str | None = None
    last_delivery_route_id: str | None = None
    last_gap: str | None = None
    last_event_gap: str | None = None
    last_event_overflow: str | None = None
    interactive_request_recovery_degraded: bool = False
    updated_at: datetime | None = None


def derive_live_projection_delivery_id(
    conversation_ref: ConversationRef,
    thread_ref: ThreadRef,
    event_id: str,
) -> str:
    """Derive a stable live-only delivery identity outside history item identity."""
    identity = json.dumps(
        [
            "live_event",
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
            thread_ref.project_ref.application_instance_id,
            thread_ref.project_ref.project_id,
            thread_ref.thread_id,
            event_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:delivery:live:sha256:{digest}"


def immutable_projection_metadata(
    metadata: Mapping[str, object],
) -> Mapping[str, object]:
    """Validate one bounded scalar metadata snapshot for outbound projection."""

    keys = list(islice(metadata, _PROJECTION_METADATA_MAX_ITEMS + 1))
    if len(keys) > _PROJECTION_METADATA_MAX_ITEMS:
        raise ValueError(
            f"AgentMessage projection metadata exceeds {_PROJECTION_METADATA_MAX_ITEMS} items"
        )
    copied: dict[str, object] = {}
    for key in keys:
        if not isinstance(key, str) or not key or len(key) > _PROJECTION_METADATA_MAX_KEY_LENGTH:
            raise ValueError("AgentMessage projection metadata keys are invalid or too long")
        value = metadata[key]
        if isinstance(value, str):
            if len(value) > _PROJECTION_METADATA_MAX_TEXT_LENGTH:
                raise ValueError(
                    "AgentMessage projection metadata text exceeds "
                    f"{_PROJECTION_METADATA_MAX_TEXT_LENGTH} characters"
                )
        elif value is None or isinstance(value, bool):
            pass
        elif isinstance(value, int):
            if not _PROJECTION_METADATA_MIN_INTEGER <= value <= _PROJECTION_METADATA_MAX_INTEGER:
                raise ValueError("AgentMessage projection metadata integer is out of range")
        elif not (isinstance(value, float) and math.isfinite(value)):
            raise ValueError("AgentMessage projection metadata values must be bounded scalars")
        copied[key] = value
    return MappingProxyType(copied)


async def deliver_projected_message(
    repository: ProjectionRouteRepository,
    route: ThreadProjectionRoute,
    projected: ProjectedAgentMessage,
    *,
    deliver_outbound: DeliverOutbound,
    checkpoint_authority: _ProjectionCheckpointAuthority,
    authoritative: bool,
    validate_lifecycle: Callable[[], None] | None = None,
) -> ThreadProjectionRoute:
    """Make one ordered route decision without adding retry/backpressure."""
    if validate_lifecycle is not None:
        validate_lifecycle()
    agent_message = projected.message
    if projected.checkpoint:
        from .checkpoints import derive_projection_delivery_id

        delivery_id = derive_projection_delivery_id(
            route.conversation_ref,
            agent_message.thread_ref,
            agent_message.agent_item_id,
        )
    else:
        if not projected.event_id:
            raise ValueError("live-only projection requires a stable event identity")
        delivery_id = derive_live_projection_delivery_id(
            route.conversation_ref,
            agent_message.thread_ref,
            projected.event_id,
        )
    reply_to = route.reply_to_message_id
    if projected.turn_id is not None:
        correlation = await repository.get_turn_reply_correlation(
            route.thread_ref,
            projected.turn_id,
        )
        if validate_lifecycle is not None:
            validate_lifecycle()
        if correlation is not None and correlation.conversation_ref == route.conversation_ref:
            reply_to = correlation.reply_to_message_id
    claim = await deliver_outbound(
        OutboundMessage(
            delivery_id=delivery_id,
            conversation_ref=route.conversation_ref,
            content=tuple(
                TextContent(item.text, TextFormat.MARKDOWN)
                if isinstance(item, TextContent)
                else item
                for item in agent_message.content
            ),
            created_at=agent_message.created_at,
            reply_to=reply_to,
            metadata=immutable_projection_metadata(agent_message.metadata),
        ),
        projected.checkpoint,
    )
    if validate_lifecycle is not None:
        validate_lifecycle()
    if claim is IdempotencyClaimStatus.IN_FLIGHT:
        raise _DestinationDecisionError(f"delivery remains in flight: {delivery_id}")
    current = await checkpoint_authority.apply_delivery_outcome(
        route,
        agent_item_id=agent_message.agent_item_id,
        checkpointable=projected.checkpoint,
        delivery_outcome=claim,
        authoritative=authoritative,
        delivery_id=delivery_id,
    )
    if validate_lifecycle is not None:
        validate_lifecycle()
    return current


ExecuteApplication = Callable[
    [_LegacyApplicationOperation],
    Awaitable[ApplicationOperationResult],
]


class ProjectionWorkerCapacityError(RuntimeError):
    """A distinct Thread worker cannot start within the configured active bound."""


class ProjectionRuntimeUnavailableError(RuntimeError):
    """Route activation cannot converge in the current projection lifecycle."""


@dataclass(slots=True)
class _ProjectionStartReservation:
    users: int = 1


class ThreadProjectionRuntime:
    """Own Thread observation and rebuildable IM projection lifecycle."""

    def __init__(
        self,
        *,
        applications: Mapping[str, AgentApplicationAdapter],
        bindings: BindingRepository,
        projections: ProjectionRouteRepository,
        request_correlations: RequestCorrelationRepository,
        request_presenter: RequestPresenter | None,
        projection_policy: ProjectionPolicy,
        execute_application: ExecuteApplication,
        deliver_outbound: DeliverOutbound,
        deliver_request_outbound: DeliverRequestOutbound,
        baseline_history_limit: int = 3,
        recovery_history_page_size: int = 10,
        recovery_max_pages: int = 5,
        catchup_limit: int = 10,
        projection_item_limit: int = 20,
        request_delivery_max_pending: int = 256,
        acceptance_gate: TurnAcceptanceOrderingGate,
        max_active_threads: int = 4096,
        subscription_retry_initial_seconds: float = 0.05,
        subscription_retry_max_seconds: float = 2.0,
        turn_correlation_retention_seconds: float = 7 * 24 * 60 * 60,
        request_correlation_retention_seconds: float = 7 * 24 * 60 * 60,
    ) -> None:
        if (
            not isinstance(max_active_threads, int)
            or isinstance(max_active_threads, bool)
            or max_active_threads < 1
        ):
            raise ValueError("max_active_threads must be a positive integer")
        if turn_correlation_retention_seconds <= 0:
            raise ValueError("turn_correlation_retention_seconds must be positive")
        if request_correlation_retention_seconds <= 0:
            raise ValueError("request_correlation_retention_seconds must be positive")
        self._applications = applications
        self._projections = projections
        self._projection_policy = projection_policy
        self._route_authority = _ProjectionRouteAuthority(
            bindings=bindings,
            projections=projections,
            policy=projection_policy,
        )
        self._turn_correlation_retention = timedelta(seconds=turn_correlation_retention_seconds)
        self._request_correlation_retention = timedelta(
            seconds=request_correlation_retention_seconds
        )
        self._acceptance_gate = acceptance_gate
        self._max_active_threads = max_active_threads
        self._tasks: dict[ThreadRef, asyncio.Task[None]] = {}
        self._ready: dict[ThreadRef, asyncio.Event] = {}
        self._pending_starts: dict[ThreadRef, _ProjectionStartReservation] = {}
        self._prepared_foreground_starts: dict[str, list[_ProjectionStartReservation]] = {}
        self._delivery_ready = asyncio.Event()
        self._health: dict[ThreadRef, ProjectionWorkerHealth] = {}
        self._ordering_recovery_gaps: dict[ThreadRef, EventStreamGap] = {}
        self._request_projection = InteractiveRequestProjection(
            applications=applications,
            projections=projections,
            correlations=request_correlations,
            request_presenter=request_presenter,
            execute_application=execute_application,
            active_routes=self._active_routes,
            deliver_request_outbound=deliver_request_outbound,
        )
        self._routes = _ProjectionRouteCoordinator(
            projections=projections,
            active_routes=self._active_routes,
            deliver_outbound=deliver_outbound,
            deliver_request_once=self._request_projection.deliver_request_once,
            wait_for_acceptance=self._acceptance_gate.wait_for_acceptance,
            record_delivery_failure=self._record_delivery_failure,
            request_delivery_max_pending=request_delivery_max_pending,
        )
        from .recovery import _RecoverySupervisor

        self._recovery = _RecoverySupervisor(
            execute_application=execute_application,
            active_routes=self._active_routes,
            begin_bootstrap=self._routes.begin_bootstrap,
            deliver_authoritative=self._routes.deliver_authoritative,
            wait_until_delivery_ready=self._delivery_ready.wait,
            record_gap=self._record_gap,
            reconcile_request_snapshot=(
                self._request_projection.reconcile_application_after_event_gap
            ),
            deliver_request=self._routes.deliver_request_to_routes,
            baseline_history_limit=baseline_history_limit,
            recovery_history_page_size=recovery_history_page_size,
            recovery_max_pages=recovery_max_pages,
            catchup_limit=catchup_limit,
            projection_item_limit=projection_item_limit,
            retry_initial_seconds=subscription_retry_initial_seconds,
            retry_max_seconds=subscription_retry_max_seconds,
        )
        self._stopping = False
        self._action_routes_available = True
        self._lifecycle_generation = 0

    async def cleanup_stale_correlations(self) -> None:
        now = datetime.now(UTC)
        await self._request_projection.cleanup_correlations(
            turn_older_than=now - self._turn_correlation_retention,
            request_older_than=now - self._request_correlation_retention,
        )

    @property
    def request_projection(self) -> InteractiveRequestProjection:
        return self._request_projection

    async def restore(self) -> None:
        self._action_routes_available = False
        self._stopping = False
        self._delivery_ready.clear()
        self._routes.reset()
        self._acceptance_gate.reset()
        self._ordering_recovery_gaps.clear()
        restored_routes = await self._route_authority.active_persisted_routes()
        for route in restored_routes:
            await self._routes.begin_bootstrap(route.route_id)
        for thread_ref in {route.thread_ref for route in restored_routes}:
            await self._ensure_projection(
                thread_ref,
                reconcile_existing=True,
                require_checkpoint=True,
            )

    def mark_delivery_ready(self) -> None:
        """Release restored workers after producers are observed and Channels can send."""

        self._delivery_ready.set()
        self._action_routes_available = True

    async def open_request_refs(self) -> frozenset[RequestRef]:
        return await self._request_projection.open_request_refs()

    async def reconcile_pending_requests(
        self,
        restart_open_refs: frozenset[RequestRef],
    ) -> None:
        await self._request_projection.reconcile_pending_requests(
            restart_open_refs,
            deliver_request=self._routes.deliver_request_to_routes,
        )

    async def stop(self) -> None:
        self._action_routes_available = False
        self._lifecycle_generation += 1
        self._stopping = True
        tasks = tuple(self._tasks.items())
        for _, task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for thread_ref, task in tasks:
            self._finish_task(thread_ref, task)
        self._pending_starts.clear()
        self._prepared_foreground_starts.clear()
        self._clear_all_worker_runtime_entries()
        await self._routes.stop()
        self._routes.reset()

    def get_health(
        self,
        thread_ref: ThreadRef,
    ) -> ProjectionWorkerHealth | None:
        return self._health.get(thread_ref)

    def list_health(self) -> tuple[ProjectionWorkerHealth, ...]:
        return tuple(self._health.values())

    async def active_routes(
        self,
        thread_ref: ThreadRef,
    ) -> tuple[ThreadProjectionRoute, ...]:
        """Resolve current destinations using the configured projection policy."""
        return await self._active_routes(thread_ref)

    async def begin_action_route(self, route_id: str) -> object:
        """Install the sole bootstrap barrier before a scoped route write is visible."""

        lifecycle_generation = self._require_action_routes_available()
        lease = await self._routes.begin_action_bootstrap(route_id)
        try:
            self._require_action_routes_available(lifecycle_generation)
        except ProjectionRuntimeUnavailableError:
            await self._routes.abort_action_bootstrap(lease)
            raise
        return lease

    def complete_action_route(
        self,
        lease: object,
        reconciled: bool | None,
    ) -> None:
        """Complete one exact action holder without releasing another baseline."""

        self._routes.complete_action_bootstrap(lease, reconciled=reconciled)

    async def reconcile_action_route(
        self,
        route_id: str | None,
        action_lease: object | None = None,
    ) -> object | None:
        """Converge live observation after one durable scoped Conversation action."""

        lifecycle_generation = (
            self._require_action_routes_available() if route_id is not None else None
        )
        self._discard_finished_tasks()
        active_routes = await self._route_authority.active_persisted_routes()
        if lifecycle_generation is not None:
            self._require_action_routes_available(lifecycle_generation)
        active_threads = {route.thread_ref for route in active_routes}
        for thread_ref in tuple(self._tasks):
            if thread_ref not in active_threads:
                await self._stop_if_unobserved(thread_ref)

        if route_id is None:
            return
        assert lifecycle_generation is not None
        route = next(
            (candidate for candidate in active_routes if candidate.route_id == route_id), None
        )
        if route is None:
            await self._routes.forget_route_ids((route_id,))
            self._require_action_routes_available(lifecycle_generation)
            return _ActionRouteReconciliationReceipt(
                lifecycle_generation=lifecycle_generation,
                thread_ref=None,
                worker_task=None,
            )

        reservation = self._reserve_projection_start(route.thread_ref)
        reconciled = False
        owned_action_lease: object | None = None
        try:
            if action_lease is None:
                owned_action_lease = await self._routes.begin_action_bootstrap(route.route_id)
                effective_action_lease = owned_action_lease
            else:
                self._routes.validate_action_bootstrap(route.route_id, action_lease)
                effective_action_lease = action_lease
            await self._routes.begin_bootstrap(
                route.route_id,
                action_lease=effective_action_lease,
            )
            self._require_action_routes_available(lifecycle_generation)
            try:
                await self._ensure_projection(route.thread_ref)
            except asyncio.CancelledError:
                if self._stopping:
                    raise ProjectionRuntimeUnavailableError(
                        "projection runtime is stopping"
                    ) from None
                raise
            self._require_observing_worker(route.thread_ref)
            try:
                await self._recovery.reconcile_route(
                    self._application(route.thread_ref.project_ref.application_instance_id),
                    route,
                    require_checkpoint=False,
                    retain_barrier_on_failure=True,
                    validate_lifecycle=lambda: self._validate_action_reconciliation(
                        route.thread_ref,
                        lifecycle_generation,
                    ),
                )
            except asyncio.CancelledError:
                if self._stopping:
                    raise ProjectionRuntimeUnavailableError(
                        "projection runtime is stopping"
                    ) from None
                raise
            self._require_action_routes_available(lifecycle_generation)
            worker_task = self._require_observing_worker(route.thread_ref)
            reconciled = True
            return _ActionRouteReconciliationReceipt(
                lifecycle_generation=lifecycle_generation,
                thread_ref=route.thread_ref,
                worker_task=worker_task,
            )
        finally:
            if owned_action_lease is not None:
                self._routes.complete_action_bootstrap(
                    owned_action_lease,
                    reconciled=reconciled,
                )
            self._release_projection_start(route.thread_ref, reservation)

    def validate_action_route_reconciliation(self, receipt: object) -> None:
        """Validate success at the synchronous public action completion boundary."""

        if not isinstance(receipt, _ActionRouteReconciliationReceipt):
            raise TypeError("action route reconciliation receipt is invalid")
        self._require_action_routes_available(receipt.lifecycle_generation)
        if receipt.thread_ref is None:
            return
        if (
            receipt.worker_task is None
            or receipt.worker_task.done()
            or self._tasks.get(receipt.thread_ref) is not receipt.worker_task
        ):
            raise ProjectionRuntimeUnavailableError("projection observation worker is not active")

    async def observe_thread(
        self,
        application: AgentApplicationAdapter,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef,
        *,
        reply_to_message_id: str | None,
    ) -> ThreadProjectionRoute:
        reservation = self._reserve_projection_start(thread_ref)
        route_id = derive_projection_route_id(thread_ref, conversation_ref)
        # Install the barrier before the durable route becomes visible to an
        # already-running Thread worker.  SQLite persistence can yield while
        # publishing the route, so creating the barrier after put() would
        # leave a live-before-baseline window.
        try:
            await self._routes.begin_bootstrap(route_id)
            route, _created = await self._remember_route(
                thread_ref,
                conversation_ref,
                reply_to_message_id=reply_to_message_id,
            )
            await self._ensure_projection(route.thread_ref)
            if await self._route_is_active(route):
                await self._recovery.reconcile_route(
                    application,
                    route,
                    require_checkpoint=False,
                )
            return route
        finally:
            self._routes.complete_bootstrap(route_id)
            self._release_projection_start(thread_ref, reservation)

    async def prepare_foreground_binding_route(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef,
    ) -> tuple[ThreadProjectionRoute, bool]:
        """Persist an inactive-until-bound route before a foreground binding CAS."""

        if self._projection_policy is not ProjectionPolicy.FOREGROUND_ONLY:
            raise RuntimeError("foreground binding route preparation requires foreground_only")
        reservation = self._reserve_projection_start(thread_ref)
        route_id = derive_projection_route_id(thread_ref, conversation_ref)
        try:
            await self._routes.begin_bootstrap(route_id)
            route, created = await self._remember_route(
                thread_ref,
                conversation_ref,
                reply_to_message_id=None,
            )
            self._prepared_foreground_starts.setdefault(route_id, []).append(reservation)
            return route, created
        except BaseException:
            self._release_projection_start(thread_ref, reservation)
            self._routes.complete_bootstrap(route_id)
            raise

    def complete_foreground_binding_route(
        self,
        route_id: str,
        *,
        complete_bootstrap: bool = True,
    ) -> None:
        """Release a route barrier after foreground binding convergence or failure."""

        self._release_foreground_start_reservation(route_id)
        if complete_bootstrap:
            self._routes.complete_bootstrap(route_id)

    def _release_foreground_start_reservation(self, route_id: str) -> None:
        reservations = self._prepared_foreground_starts.get(route_id)
        if reservations:
            reservation = reservations.pop()
            if not reservations:
                self._prepared_foreground_starts.pop(route_id, None)
            self._release_projection_start_for_reservation(reservation)

    async def prepare_input_route(
        self,
        application: AgentApplicationAdapter,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef,
        *,
        thread_was_created: bool,
    ) -> ThreadProjectionRoute:
        reservation = self._reserve_projection_start(thread_ref)
        needs_reconcile = False
        route_id = derive_projection_route_id(thread_ref, conversation_ref)
        try:
            await self.cleanup_stale_correlations()
            existing = await get_projection_route(self._projections, route_id)
            task = self._tasks.get(thread_ref)
            needs_reconcile = existing is None or task is None or task.done()
            if needs_reconcile:
                await self._routes.begin_bootstrap(route_id)
            route, created = await self._remember_route(
                thread_ref,
                conversation_ref,
                reply_to_message_id=None,
            )
            needs_reconcile = needs_reconcile or created
            await self._ensure_projection(thread_ref)
            if needs_reconcile and not thread_was_created:
                await self._recovery.reconcile_route(
                    application,
                    route,
                    require_checkpoint=existing is not None,
                )
            return route
        finally:
            if needs_reconcile:
                self._routes.complete_bootstrap(route_id)
            self._release_projection_start(thread_ref, reservation)

    async def handle_binding_change(
        self,
        previous: ConversationBinding | None,
        current: ConversationBinding,
        *,
        require_checkpoint: bool = True,
    ) -> None:
        if self._projection_policy is not ProjectionPolicy.FOREGROUND_ONLY:
            return
        previous_thread = previous.thread_ref if previous is not None else None
        if previous_thread == current.thread_ref:
            return
        if previous_thread is not None:
            await self._routes.cancel_inactive_request_deliveries(previous_thread)
            await self._stop_if_unobserved(previous_thread)
        if current.thread_ref is None:
            return
        routes = await self._active_routes(current.thread_ref)
        if not routes:
            return
        task = self._tasks.get(current.thread_ref)
        if task is not None and not task.done():
            activated_route = next(
                (route for route in routes if route.conversation_ref == current.conversation_ref),
                None,
            )
            if activated_route is None:
                return
            await self._routes.begin_bootstrap(activated_route.route_id)
            await self._recovery.reconcile_route(
                self._application(current.thread_ref.project_ref.application_instance_id),
                activated_route,
                require_checkpoint=require_checkpoint,
                retain_barrier_on_failure=True,
            )
            return
        for route in routes:
            await self._routes.begin_bootstrap(route.route_id)
        await self._ensure_projection(current.thread_ref)
        try:
            await asyncio.gather(
                *(
                    self._recovery.reconcile_route(
                        self._application(current.thread_ref.project_ref.application_instance_id),
                        route,
                        require_checkpoint=(
                            require_checkpoint
                            if route.conversation_ref == current.conversation_ref
                            else True
                        ),
                        retain_barrier_on_failure=True,
                    )
                    for route in routes
                )
            )
        except BaseException:
            task = self._tasks.get(current.thread_ref)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    async def _remember_route(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef,
        *,
        reply_to_message_id: str | None,
    ) -> tuple[ThreadProjectionRoute, bool]:
        refreshed = await self._route_authority.refresh_route(
            thread_ref,
            conversation_ref,
            reply_to_message_id=reply_to_message_id,
        )
        if self._projection_policy is ProjectionPolicy.REMEMBERED_LAST_RECIPIENT:
            for removed in refreshed.removed_routes:
                await self._request_projection.delete_destination(
                    thread_ref,
                    removed.conversation_ref,
                )
            await self._routes.forget_routes(refreshed.removed_routes)
        return refreshed.route, refreshed.created

    async def _stop_if_unobserved(self, thread_ref: ThreadRef) -> None:
        if await self._active_routes(thread_ref):
            return
        task = self._tasks.get(thread_ref)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._finish_task(thread_ref, task)

    def _reserve_projection_start(
        self,
        thread_ref: ThreadRef,
    ) -> _ProjectionStartReservation:
        self._discard_finished_tasks()
        reservation = self._pending_starts.get(thread_ref)
        if reservation is not None:
            reservation.users += 1
            return reservation
        active_thread_refs = set(self._tasks) | set(self._pending_starts)
        if (
            thread_ref not in active_thread_refs
            and len(active_thread_refs) >= self._max_active_threads
        ):
            raise ProjectionWorkerCapacityError("active Thread observation capacity is exhausted")
        reservation = _ProjectionStartReservation()
        self._pending_starts[thread_ref] = reservation
        return reservation

    def _release_projection_start(
        self,
        thread_ref: ThreadRef,
        reservation: _ProjectionStartReservation,
    ) -> None:
        if self._pending_starts.get(thread_ref) is not reservation:
            return
        reservation.users -= 1
        if reservation.users == 0:
            self._pending_starts.pop(thread_ref, None)

    def _release_projection_start_for_reservation(
        self,
        reservation: _ProjectionStartReservation,
    ) -> None:
        for thread_ref, current in tuple(self._pending_starts.items()):
            if current is reservation:
                self._release_projection_start(thread_ref, reservation)
                return

    def _discard_finished_tasks(self) -> None:
        for thread_ref, task in tuple(self._tasks.items()):
            if task.done():
                self._finish_task(thread_ref, task)

    async def _ensure_projection(
        self,
        thread_ref: ThreadRef,
        *,
        reconcile_existing: bool = False,
        require_checkpoint: bool = True,
    ) -> None:
        if self._stopping:
            raise ProjectionRuntimeUnavailableError("projection runtime is stopping")
        reservation = self._reserve_projection_start(thread_ref)
        try:
            task = self._tasks.get(thread_ref)
            if task is None:
                ready = asyncio.Event()
                task = asyncio.create_task(
                    self._project_thread(
                        thread_ref,
                        ready,
                        reconcile_existing=reconcile_existing,
                        require_checkpoint=require_checkpoint,
                    )
                )
                self._tasks[thread_ref] = task
                self._ready[thread_ref] = ready
                task.add_done_callback(
                    lambda completed, ref=thread_ref: self._finish_task(
                        ref,
                        completed,
                    )
                )
            ready = self._ready[thread_ref]
            await ready.wait()
            if task.done():
                await task
        finally:
            self._release_projection_start(thread_ref, reservation)

    def _require_action_routes_available(
        self,
        expected_generation: int | None = None,
    ) -> int:
        if (
            self._stopping
            or not self._action_routes_available
            or (
                expected_generation is not None
                and expected_generation != self._lifecycle_generation
            )
        ):
            raise ProjectionRuntimeUnavailableError(
                "projection runtime is not available for route reconciliation"
            )
        return self._lifecycle_generation

    def _require_observing_worker(self, thread_ref: ThreadRef) -> asyncio.Task[None]:
        task = self._tasks.get(thread_ref)
        if task is None or task.done():
            raise ProjectionRuntimeUnavailableError("projection observation worker is not active")
        return task

    def _validate_action_reconciliation(
        self,
        thread_ref: ThreadRef,
        lifecycle_generation: int,
    ) -> None:
        self._require_action_routes_available(lifecycle_generation)
        self._require_observing_worker(thread_ref)

    def _finish_task(
        self,
        thread_ref: ThreadRef,
        task: asyncio.Task[None],
    ) -> None:
        if self._tasks.get(thread_ref) is not task:
            return
        self._tasks.pop(thread_ref, None)
        self._ordering_recovery_gaps.pop(thread_ref, None)
        ready = self._ready.pop(thread_ref, None)
        if ready is not None:
            ready.set()
        self._clear_worker_runtime_entries(thread_ref)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.exception(
                "Agent event projection failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _clear_all_worker_runtime_entries(self) -> None:
        for thread_ref in set(self._health):
            self._clear_worker_runtime_entries(thread_ref)

    def _clear_worker_runtime_entries(self, thread_ref: ThreadRef) -> None:
        self._health.pop(thread_ref, None)
        self._acceptance_gate.worker_finished(thread_ref)

    async def _project_thread(
        self,
        thread_ref: ThreadRef,
        ready: asyncio.Event,
        *,
        reconcile_existing: bool,
        require_checkpoint: bool,
    ) -> None:
        recovery_attempt = self._recovery.start_attempt(
            reconcile_existing=reconcile_existing,
            require_checkpoint=require_checkpoint,
        )
        while not self._stopping:
            events: AsyncIterator[AgentEvent] | None = None
            application: AgentApplicationAdapter | None = None
            try:
                if not await self._observation_required(thread_ref):
                    return
                application = self._application(thread_ref.project_ref.application_instance_id)
                ordering_gap = self._ordering_recovery_gaps.pop(thread_ref, None)
                if ordering_gap is not None:
                    if not await self._recover_after_failure(
                        thread_ref,
                        recovery_attempt,
                        ordering_gap,
                        application=application,
                        ready=ready,
                    ):
                        return
                    continue
                await self._recovery.prepare_reconciliation(
                    recovery_attempt,
                    thread_ref,
                )
                self._update_health(
                    thread_ref,
                    state=(
                        ProjectionWorkerState.STARTING
                        if recovery_attempt.restart_count == 0
                        else ProjectionWorkerState.RETRYING
                    ),
                    restart_count=recovery_attempt.restart_count,
                )
                events = application.subscribe_thread(thread_ref)
                ready.set()
                request_recovery_degraded = await self._recovery.reconcile_thread(
                    recovery_attempt,
                    application,
                    thread_ref,
                )
                if request_recovery_degraded is not None:
                    self._update_health(
                        thread_ref,
                        interactive_request_recovery_degraded=request_recovery_degraded,
                    )
                self._update_health(
                    thread_ref,
                    state=ProjectionWorkerState.RUNNING,
                    restart_count=recovery_attempt.restart_count,
                    last_subscription_error=None,
                    last_recovery_error=None,
                )
                async for event in events:
                    await self._handle_event(event)
                    if not await self._observation_required(thread_ref):
                        return
                raise RuntimeError("Application Thread subscription ended")
            except asyncio.CancelledError:
                ordering_gap = self._ordering_recovery_gaps.pop(thread_ref, None)
                if ordering_gap is None or self._stopping:
                    raise
                if not await self._recover_after_failure(
                    thread_ref,
                    recovery_attempt,
                    ordering_gap,
                    application=application,
                    ready=ready,
                ):
                    return
                continue
            except Exception as error:
                if not await self._recover_after_failure(
                    thread_ref,
                    recovery_attempt,
                    error,
                    application=application,
                    ready=ready,
                ):
                    return
            finally:
                ready.set()
                if events is not None:
                    try:
                        await _close_subscription(events)
                    except asyncio.CancelledError:
                        raise
                    except Exception as close_error:
                        self._update_health(
                            thread_ref,
                            last_subscription_error=(f"subscription close failed: {close_error}"),
                        )
                        logger.warning(
                            "Agent subscription close failed for %s: %s",
                            thread_ref,
                            close_error,
                        )
        ready.set()

    async def _recover_after_failure(
        self,
        thread_ref: ThreadRef,
        recovery_attempt: _RecoveryAttempt,
        error: Exception,
        *,
        application: AgentApplicationAdapter | None,
        ready: asyncio.Event,
    ) -> bool:
        """Delegate every recovery classification and backoff to the supervisor."""

        ready.set()
        from .recovery import _RecoveryHealthSnapshot

        current_health = self._health.get(thread_ref)
        failure = self._recovery.record_failure(
            recovery_attempt,
            error,
            application=application,
            current_health=(
                _RecoveryHealthSnapshot(
                    event_overflow_count=current_health.event_overflow_count,
                    last_subscription_error=current_health.last_subscription_error,
                    last_recovery_error=current_health.last_recovery_error,
                    last_gap=current_health.last_gap,
                    last_event_gap=current_health.last_event_gap,
                    last_event_overflow=current_health.last_event_overflow,
                )
                if current_health is not None
                else None
            ),
        )
        self._update_health(
            thread_ref,
            state=ProjectionWorkerState.RETRYING,
            restart_count=failure.restart_count,
            event_overflow_count=failure.event_overflow_count,
            last_subscription_error=failure.last_subscription_error,
            last_recovery_error=failure.last_recovery_error,
            last_gap=failure.last_gap,
            last_event_gap=failure.last_event_gap,
            last_event_overflow=failure.last_event_overflow,
            interactive_request_recovery_degraded=(failure.interactive_request_recovery_degraded),
        )
        if self._stopping:
            return False
        await asyncio.sleep(failure.retry_delay_seconds)
        return True

    async def _observation_required(self, thread_ref: ThreadRef) -> bool:
        return bool(await self._active_routes(thread_ref))

    async def _handle_event(self, event: AgentEvent) -> None:
        await self._acceptance_gate.handle_event(
            event,
            event_applier=self,
        )

    async def apply_ordered_event(self, event: AgentEvent) -> None:
        """Implement the dispatch-owned typed ordered-event applier contract."""

        await self._apply_event(event)

    async def recover_ordering_gap(self, thread_ref: ThreadRef, *, gap_code: str) -> None:
        """Inject a typed external gap into this Thread's one-worker recovery loop."""

        self._discard_finished_tasks()
        gap = EventStreamGap(
            gap_code,
            f"Gateway input acceptance ordering lost continuity: {gap_code}",
        )
        self._ordering_recovery_gaps[thread_ref] = gap
        task = self._tasks.get(thread_ref)
        if task is not None and not task.done():
            task.cancel()
            return
        try:
            await self._ensure_projection(
                thread_ref,
                reconcile_existing=True,
                require_checkpoint=True,
            )
        finally:
            if self._ordering_recovery_gaps.get(thread_ref) is gap:
                self._ordering_recovery_gaps.pop(thread_ref, None)

    def has_observing_worker(self, thread_ref: ThreadRef) -> bool:
        return thread_ref in self._tasks

    async def _apply_event(self, event: AgentEvent) -> None:
        await self._request_projection.handle_event(
            event,
            deliver_request=self._routes.deliver_request_to_routes,
            cancel_request=self._routes.cancel_request_deliveries,
        )
        thread_ref = event.thread_ref
        if thread_ref is None:
            return
        if event.type in {
            AgentEventType.MESSAGE_CREATED,
            AgentEventType.MESSAGE_COMPLETED,
        }:
            agent_message = event.data.get("message")
            if isinstance(agent_message, AgentMessage):
                await self._routes.deliver_to_routes(
                    await self._active_routes(thread_ref),
                    ProjectedAgentMessage(
                        message=agent_message,
                        turn_id=(event.turn_ref.turn_id if event.turn_ref is not None else None),
                        event_id=event.event_id,
                        checkpoint=event.type is AgentEventType.MESSAGE_COMPLETED,
                    ),
                )
        if (
            event.type
            in {
                AgentEventType.TURN_COMPLETED,
                AgentEventType.TURN_FAILED,
                AgentEventType.TURN_INTERRUPTED,
            }
            and event.turn_ref is not None
        ):
            await self._request_projection.delete_terminal_turn(
                thread_ref,
                event.turn_ref.turn_id,
            )
        if event.type is AgentEventType.THREAD_DELETED:
            routes = await self._projections.list_projection_routes(thread_ref)
            await self._projections.delete_projection_routes(thread_ref)
            await self._request_projection.delete_thread(thread_ref)
            await self._routes.forget_routes(routes)

    async def _active_routes(
        self,
        thread_ref: ThreadRef | None,
    ) -> tuple[ThreadProjectionRoute, ...]:
        return await self._route_authority.active_routes(thread_ref)

    async def _route_is_active(self, route: ThreadProjectionRoute) -> bool:
        return await self._route_authority.route_is_active(route)

    def _record_delivery_failure(
        self,
        route: ThreadProjectionRoute,
        error: Exception,
    ) -> None:
        current = self._health.get(route.thread_ref)
        self._update_health(
            route.thread_ref,
            delivery_failure_count=(
                current.delivery_failure_count + 1 if current is not None else 1
            ),
            last_delivery_error=str(error),
            last_delivery_route_id=route.route_id,
        )
        logger.warning(
            "Projection delivery failed for route %s: %s",
            route.route_id,
            error,
        )

    def _record_gap(
        self,
        thread_ref: ThreadRef,
        route_id: str,
        gap: str,
    ) -> None:
        self._update_health(
            thread_ref,
            last_gap=f"{route_id}:{gap}",
        )

    def _update_health(
        self,
        thread_ref: ThreadRef,
        **changes: Any,
    ) -> None:
        current = self._health.get(
            thread_ref,
            ProjectionWorkerHealth(
                thread_ref=thread_ref,
                state=ProjectionWorkerState.STARTING,
            ),
        )
        self._health[thread_ref] = replace(
            current,
            **changes,
            updated_at=datetime.now(UTC),
        )

    def _application(
        self,
        application_instance_id: str,
    ) -> AgentApplicationAdapter:
        try:
            return self._applications[application_instance_id]
        except KeyError as error:
            raise KeyError(
                f"Agent application is not registered: {application_instance_id}"
            ) from error


async def _close_subscription(events: AsyncIterator[AgentEvent]) -> None:
    close = getattr(events, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


RecordDeliveryFailure = Callable[[ThreadProjectionRoute, Exception], None]
WaitForAcceptance = Callable[[ThreadRef], Awaitable[None]]
ActiveRoutes = Callable[
    [ThreadRef | None],
    Awaitable[tuple[ThreadProjectionRoute, ...]],
]
DeliverRequestOnce = Callable[
    [ThreadProjectionRoute, InteractiveRequest],
    Awaitable[None],
]


class _AuthoritativeProjection(Protocol):
    @property
    def messages(self) -> tuple[ProjectedAgentMessage, ...]: ...


@dataclass(slots=True, eq=False)
class _ActionRouteBootstrapLease:
    route_id: str
    barrier: asyncio.Event
    action_lock: asyncio.Lock
    completed: bool = False


@dataclass(frozen=True, slots=True)
class _ActionRouteReconciliationReceipt:
    lifecycle_generation: int
    thread_ref: ThreadRef | None
    worker_task: asyncio.Task[None] | None


class _ProjectionRouteCoordinator:
    """Serialize bootstrap, reconciliation, and delivery per destination route."""

    def __init__(
        self,
        *,
        projections: ProjectionRouteRepository,
        active_routes: ActiveRoutes,
        deliver_outbound: DeliverOutbound,
        deliver_request_once: DeliverRequestOnce,
        wait_for_acceptance: WaitForAcceptance,
        record_delivery_failure: RecordDeliveryFailure,
        request_delivery_max_pending: int,
    ) -> None:
        if request_delivery_max_pending < 1:
            raise ValueError("request_delivery_max_pending must be positive")
        from .checkpoints import _ProjectionCheckpointAuthority

        self._projections = projections
        self._checkpoint_authority = _ProjectionCheckpointAuthority(
            projections=projections,
        )
        self._active_routes = active_routes
        self._deliver_outbound = deliver_outbound
        self._deliver_request_once = deliver_request_once
        self._wait_for_acceptance = wait_for_acceptance
        self._record_delivery_failure = record_delivery_failure
        self._request_delivery_max_pending = request_delivery_max_pending
        self._request_capacity_changed = asyncio.Event()
        self._bootstrap: dict[str, asyncio.Event] = {}
        self._bootstrap_pending: set[str] = set()
        self._action_bootstrap_users: dict[str, set[_ActionRouteBootstrapLease]] = {}
        self._action_bootstrap_retained: dict[str, asyncio.Event] = {}
        self._action_locks: dict[str, asyncio.Lock] = {}
        self._action_lock_waiters: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._blocked_routes: set[str] = set()
        self._request_retry_tasks: dict[
            tuple[ThreadRef, str, RequestRef],
            asyncio.Task[None],
        ] = {}

    def reset(self) -> None:
        if self._request_retry_tasks:
            raise RuntimeError("request delivery retries must be stopped before reset")
        for barrier in self._bootstrap.values():
            barrier.set()
        self._bootstrap.clear()
        self._bootstrap_pending.clear()
        self._action_bootstrap_users.clear()
        self._action_bootstrap_retained.clear()
        self._action_locks.clear()
        self._action_lock_waiters.clear()
        self._locks.clear()
        self._blocked_routes.clear()

    async def begin_bootstrap(
        self,
        route_id: str,
        *,
        action_lease: object | None = None,
    ) -> None:
        lock = self._locks.setdefault(route_id, asyncio.Lock())
        async with lock:
            if action_lease is not None:
                self.validate_action_bootstrap(route_id, action_lease)
            current = self._bootstrap.get(route_id)
            if current is None or current.is_set():
                self._bootstrap[route_id] = asyncio.Event()
                self._action_bootstrap_retained.pop(route_id, None)
            self._bootstrap_pending.add(route_id)

    def complete_bootstrap(self, route_id: str) -> None:
        self._bootstrap.setdefault(route_id, asyncio.Event())
        self._bootstrap_pending.discard(route_id)
        self._complete_bootstrap_if_unowned(route_id)

    async def begin_action_bootstrap(self, route_id: str) -> _ActionRouteBootstrapLease:
        """Retain one exact action owner on the current route barrier generation."""

        action_lock = self._action_locks.setdefault(route_id, asyncio.Lock())
        self._action_lock_waiters[route_id] = self._action_lock_waiters.get(route_id, 0) + 1
        try:
            await action_lock.acquire()
        finally:
            waiters = self._action_lock_waiters[route_id] - 1
            if waiters == 0:
                self._action_lock_waiters.pop(route_id, None)
            else:
                self._action_lock_waiters[route_id] = waiters
            if not action_lock.locked():
                self._cleanup_action_lock(route_id, action_lock)
        lock = self._locks.setdefault(route_id, asyncio.Lock())
        try:
            async with lock:
                current = self._bootstrap.get(route_id)
                if current is None or current.is_set():
                    current = asyncio.Event()
                    self._bootstrap[route_id] = current
                    self._bootstrap_pending.discard(route_id)
                    self._action_bootstrap_retained.pop(route_id, None)
                lease = _ActionRouteBootstrapLease(route_id, current, action_lock)
                self._action_bootstrap_users.setdefault(route_id, set()).add(lease)
                return lease
        except BaseException:
            action_lock.release()
            self._cleanup_action_lock(route_id, action_lock)
            raise

    def complete_action_bootstrap(
        self,
        lease: object,
        *,
        reconciled: bool | None,
    ) -> None:
        """Release one action owner and preserve incomplete baseline fencing."""

        if not isinstance(lease, _ActionRouteBootstrapLease):
            raise TypeError("action route bootstrap lease is invalid")
        if lease.completed:
            return
        lease.completed = True
        route_id = lease.route_id
        users = self._action_bootstrap_users.get(route_id)
        if users is None or lease not in users:
            if lease.action_lock.locked():
                lease.action_lock.release()
            self._cleanup_action_lock(route_id, lease.action_lock)
            return
        users.remove(lease)
        if not users:
            self._action_bootstrap_users.pop(route_id, None)
        try:
            if self._bootstrap.get(route_id) is not lease.barrier:
                return
            if reconciled is True:
                self._action_bootstrap_retained.pop(route_id, None)
            elif reconciled is False:
                self._action_bootstrap_retained[route_id] = lease.barrier
            self._complete_bootstrap_if_unowned(route_id)
        finally:
            lease.action_lock.release()
            self._cleanup_action_lock(route_id, lease.action_lock)

    async def abort_action_bootstrap(self, lease: object) -> None:
        """Retire only the exact action generation invalidated by lifecycle change."""

        if not isinstance(lease, _ActionRouteBootstrapLease):
            raise TypeError("action route bootstrap lease is invalid")
        self.complete_action_bootstrap(lease, reconciled=None)
        route_id = lease.route_id
        lock = self._locks.get(route_id)
        if lock is None:
            return
        async with lock:
            if self._bootstrap.get(route_id) is not lease.barrier:
                return
            if self._action_bootstrap_users.get(route_id):
                return
            lease.barrier.set()
            self._bootstrap.pop(route_id, None)
            self._bootstrap_pending.discard(route_id)
            if self._action_bootstrap_retained.get(route_id) is lease.barrier:
                self._action_bootstrap_retained.pop(route_id, None)
            if self._locks.get(route_id) is lock:
                self._locks.pop(route_id, None)
            self._blocked_routes.discard(route_id)
            self._cleanup_action_lock(route_id, lease.action_lock)

    def validate_action_bootstrap(self, route_id: str, lease: object) -> None:
        if not isinstance(lease, _ActionRouteBootstrapLease):
            raise TypeError("action route bootstrap lease is invalid")
        if lease.route_id != route_id:
            raise ValueError("action route bootstrap lease belongs to a different route")
        if lease not in self._action_bootstrap_users.get(route_id, ()):
            raise RuntimeError("action route bootstrap lease is no longer active")

    def _cleanup_action_lock(self, route_id: str, action_lock: asyncio.Lock) -> None:
        if self._bootstrap.get(route_id) is not None:
            return
        if self._action_bootstrap_users.get(route_id):
            return
        if self._action_lock_waiters.get(route_id, 0) != 0:
            return
        if action_lock.locked():
            return
        if self._action_locks.get(route_id) is action_lock:
            self._action_locks.pop(route_id, None)

    def _complete_bootstrap_if_unowned(self, route_id: str) -> None:
        current = self._bootstrap.get(route_id)
        if current is None:
            return
        if any(
            lease.barrier is current for lease in self._action_bootstrap_users.get(route_id, ())
        ):
            return
        if route_id in self._bootstrap_pending:
            return
        if self._action_bootstrap_retained.get(route_id) is current:
            return
        current.set()

    async def forget_routes(
        self,
        routes: tuple[ThreadProjectionRoute, ...],
    ) -> None:
        await self.forget_route_ids(tuple(route.route_id for route in routes))

    async def forget_route_ids(self, route_ids: tuple[str, ...]) -> None:
        """Retire route coordination even when persistence already removed the row."""

        retry_tasks: list[asyncio.Task[None]] = []
        for route_id in route_ids:
            retry_tasks.extend(
                self._pop_route_request_retries(
                    route_id,
                    thread_ref=None,
                )
            )
            barrier = self._bootstrap.pop(route_id, None)
            if barrier is not None:
                barrier.set()
            self._bootstrap_pending.discard(route_id)
            self._action_bootstrap_retained.pop(route_id, None)
            self._locks.pop(route_id, None)
            self._blocked_routes.discard(route_id)
            action_lock = self._action_locks.get(route_id)
            if action_lock is not None:
                self._cleanup_action_lock(route_id, action_lock)
        for task in retry_tasks:
            task.cancel()
        if retry_tasks:
            await asyncio.gather(*retry_tasks, return_exceptions=True)
            self._request_capacity_changed.set()

    async def stop(self) -> None:
        tasks = tuple(self._request_retry_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._request_retry_tasks.clear()
        self._request_capacity_changed.set()

    async def cancel_request_deliveries(self, request_ref: RequestRef) -> None:
        keys = tuple(key for key in self._request_retry_tasks if key[2] == request_ref)
        tasks = tuple(self._request_retry_tasks.pop(key) for key in keys)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._request_capacity_changed.set()

    async def cancel_inactive_request_deliveries(
        self,
        thread_ref: ThreadRef,
    ) -> None:
        active_ids = {route.route_id for route in await self._active_routes(thread_ref)}
        for task_thread_ref, route_id, _request_ref in tuple(self._request_retry_tasks):
            if task_thread_ref != thread_ref:
                continue
            if route_id not in active_ids:
                tasks = self._pop_route_request_retries(
                    route_id,
                    thread_ref=thread_ref,
                )
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    self._request_capacity_changed.set()

    async def deliver_authoritative(
        self,
        route: ThreadProjectionRoute,
        *,
        read_projection: Callable[
            [ThreadProjectionRoute],
            Awaitable[_AuthoritativeProjection],
        ],
        retain_barrier_on_failure: bool = False,
        validate_lifecycle: Callable[[], None] | None = None,
    ) -> None:
        """Serialize one recovery-owned authoritative read and route delivery."""
        if validate_lifecycle is not None:
            validate_lifecycle()
        lock = self._locks.setdefault(route.route_id, asyncio.Lock())
        completed = False
        try:
            async with lock:
                current = await get_projection_route(
                    self._projections,
                    route.route_id,
                )
                if validate_lifecycle is not None:
                    validate_lifecycle()
                if current is None:
                    return
                projection = await read_projection(current)
                if validate_lifecycle is not None:
                    validate_lifecycle()
                await self._wait_for_acceptance(route.thread_ref)
                if validate_lifecycle is not None:
                    validate_lifecycle()
                await self._deliver_messages(
                    current,
                    projection.messages,
                    authoritative=True,
                    validate_lifecycle=validate_lifecycle,
                )
                if validate_lifecycle is not None:
                    validate_lifecycle()
                completed = True
        finally:
            if completed or not retain_barrier_on_failure:
                self.complete_bootstrap(route.route_id)

    async def deliver_to_routes(
        self,
        routes: tuple[ThreadProjectionRoute, ...],
        projected: ProjectedAgentMessage,
    ) -> None:
        await asyncio.gather(*(self._deliver_to_route(route, projected) for route in routes))

    async def deliver_request_to_routes(
        self,
        routes: tuple[ThreadProjectionRoute, ...],
        request: InteractiveRequest,
    ) -> None:
        scheduled = {
            route.route_id
            for route in routes
            if (route.thread_ref, route.route_id, request.request_ref) in self._request_retry_tasks
        }
        while True:
            pending = tuple(
                route
                for route in routes
                if route.route_id not in scheduled
                if (route.thread_ref, route.route_id, request.request_ref)
                not in self._request_retry_tasks
            )
            if not pending:
                return
            available = self._request_delivery_max_pending - len(self._request_retry_tasks)
            if available > 0:
                for route in pending[:available]:
                    self._schedule_request_delivery(route, request)
                    scheduled.add(route.route_id)
                continue
            # Without upstream replay or a native pending-request snapshot,
            # keep the one consumed event on the stack until more capacity
            # is available. Memory remains bounded at N jobs plus this event.
            self._request_capacity_changed.clear()
            await self._request_capacity_changed.wait()

    async def _run_request_delivery(
        self,
        route: ThreadProjectionRoute,
        request: InteractiveRequest,
    ) -> None:
        retry_count = 0
        retry_error: RetryableDeliveryError | None = None
        while not _request_expired(request):
            if retry_error is not None:
                delayed_error = retry_error
                retry_error = None
                retry_count += 1
                delay = min(0.05 * (2 ** min(retry_count - 1, 5)), 1.0)
                delay = max(delay, delayed_error.retry_after_seconds or 0)
                if request.expires_at is not None:
                    remaining = (request.expires_at - datetime.now(UTC)).total_seconds()
                    if remaining <= 0:
                        return
                    delay = min(delay, remaining)
                await asyncio.sleep(delay)
            barrier = self._bootstrap.get(route.route_id)
            if barrier is None:
                barrier = asyncio.Event()
                barrier.set()
                self._bootstrap[route.route_id] = barrier
            if not barrier.is_set():
                await barrier.wait()
            lock = self._locks.setdefault(route.route_id, asyncio.Lock())
            async with lock:
                current_barrier = self._bootstrap.get(route.route_id)
                if (
                    current_barrier is None
                    or current_barrier is not barrier
                    or not current_barrier.is_set()
                ):
                    continue
                if route.route_id in self._blocked_routes or _request_expired(request):
                    return
                current = await self._current_active_route(route)
                if current is None:
                    return
                try:
                    await self._deliver_request_once(current, request)
                    return
                except RetryableDeliveryError as error:
                    retry_error = error
                except Exception as error:
                    self._block_route(current, error)
                    return

    def _schedule_request_delivery(
        self,
        route: ThreadProjectionRoute,
        request: InteractiveRequest,
    ) -> None:
        key = (route.thread_ref, route.route_id, request.request_ref)
        if key in self._request_retry_tasks:
            return
        task = asyncio.create_task(
            self._run_request_delivery(route, request),
            name=f"imagent-request-delivery:{route.route_id}",
        )
        self._request_retry_tasks[key] = task
        task.add_done_callback(
            lambda completed, retry_key=key, retry_route=route: self._finish_request_delivery(
                retry_key,
                retry_route,
                completed,
            )
        )

    async def _current_active_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute | None:
        return next(
            (
                candidate
                for candidate in await self._active_routes(route.thread_ref)
                if candidate.route_id == route.route_id
            ),
            None,
        )

    def _pop_route_request_retries(
        self,
        route_id: str,
        *,
        thread_ref: ThreadRef | None,
    ) -> tuple[asyncio.Task[None], ...]:
        tasks: list[asyncio.Task[None]] = []
        for key in tuple(self._request_retry_tasks):
            if (thread_ref is None or key[0] == thread_ref) and key[1] == route_id:
                tasks.append(self._request_retry_tasks.pop(key))
        return tuple(tasks)

    def _finish_request_delivery(
        self,
        key: tuple[ThreadRef, str, RequestRef],
        route: ThreadProjectionRoute,
        task: asyncio.Task[None],
    ) -> None:
        if self._request_retry_tasks.get(key) is task:
            self._request_retry_tasks.pop(key, None)
            self._request_capacity_changed.set()
        if task.cancelled():
            return
        error = task.exception()
        if isinstance(error, Exception):
            self._block_route(route, error)

    async def _deliver_to_route(
        self,
        route: ThreadProjectionRoute,
        projected: ProjectedAgentMessage,
    ) -> None:
        while True:
            barrier = self._bootstrap.get(route.route_id)
            if barrier is None:
                barrier = asyncio.Event()
                barrier.set()
                self._bootstrap[route.route_id] = barrier
            if not barrier.is_set():
                await barrier.wait()
            lock = self._locks.setdefault(route.route_id, asyncio.Lock())
            async with lock:
                current_barrier = self._bootstrap.get(route.route_id)
                if (
                    current_barrier is None
                    or current_barrier is not barrier
                    or not current_barrier.is_set()
                ):
                    continue
                if route.route_id in self._blocked_routes:
                    return
                current = await get_projection_route(
                    self._projections,
                    route.route_id,
                )
                if current is None:
                    return
                try:
                    await deliver_projected_message(
                        self._projections,
                        current,
                        projected,
                        deliver_outbound=self._deliver_outbound,
                        checkpoint_authority=self._checkpoint_authority,
                        authoritative=False,
                    )
                except _DestinationDecisionError as error:
                    self._block_route(current, error)
                return

    async def _deliver_messages(
        self,
        route: ThreadProjectionRoute,
        messages: tuple[ProjectedAgentMessage, ...],
        *,
        authoritative: bool,
        validate_lifecycle: Callable[[], None] | None = None,
    ) -> None:
        seen: set[str] = set()
        current = route
        for projected in messages:
            if validate_lifecycle is not None:
                validate_lifecycle()
            if projected.message.agent_item_id in seen:
                continue
            seen.add(projected.message.agent_item_id)
            if route.route_id in self._blocked_routes:
                if validate_lifecycle is not None:
                    raise _DestinationDecisionError(
                        f"projection route remains blocked: {route.route_id}"
                    )
                return
            try:
                current = await deliver_projected_message(
                    self._projections,
                    current,
                    projected,
                    deliver_outbound=self._deliver_outbound,
                    checkpoint_authority=self._checkpoint_authority,
                    authoritative=authoritative,
                    validate_lifecycle=validate_lifecycle,
                )
            except _DestinationDecisionError as error:
                self._block_route(current, error)
                if validate_lifecycle is not None:
                    raise
                return

    def _block_route(
        self,
        route: ThreadProjectionRoute,
        error: Exception,
    ) -> None:
        self._blocked_routes.add(route.route_id)
        self._record_delivery_failure(route, error)

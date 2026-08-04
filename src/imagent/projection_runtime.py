from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .applications.capabilities import SupportLevel
from .applications.contract import (
    AcceptedTurn,
    AgentApplicationAdapter,
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    InputContinuationPreference,
    ThreadRef,
)
from .applications.events import (
    AgentEvent,
    AgentEventType,
    EventBufferOverflow,
    EventStreamGap,
)
from .applications.operations import ApplicationOperation, ApplicationOperationResult
from .applications.requests import RequestRef
from .gateway.persistence.repository_contracts import (
    BindingRepository,
    ProjectionRouteRepository,
    RequestCorrelationRepository,
)
from .gateway.persistence.state_contracts import (
    ConversationBinding,
    ThreadProjectionRoute,
)
from .gateway.projection.recovery import ProjectionRecoveryUnavailable
from .gateway.projection.request_correlation import InteractiveRequestProjection
from .gateway.routing.projection_routes import (
    ProjectionPolicy,
    _ProjectionRouteAuthority,
    derive_projection_route_id,
    get_projection_route,
)
from .interaction.controllers import RequestPresenter
from .interaction.messages import ConversationRef
from .projection_routes import ProjectionRouteCoordinator
from .projections import (
    DeliverOutbound,
    DeliverRequestOutbound,
    ProjectedAgentMessage,
    ProjectionWorkerHealth,
    ProjectionWorkerState,
    RetryableDeliveryError,
)

logger = logging.getLogger(__name__)

ExecuteApplication = Callable[
    [ApplicationOperation],
    Awaitable[ApplicationOperationResult],
]


class InputPostAcceptanceError(RuntimeError):
    """Bridge post-processing failed after the Application accepted input."""

    def __init__(self, accepted_turn: AcceptedTurn, cause: BaseException) -> None:
        super().__init__(
            "Agent input was accepted before bridge post-processing failed: "
            f"{accepted_turn.turn_id}"
        )
        self.accepted_turn = accepted_turn
        self.cause = cause


class TurnAcceptanceBufferOverflow(EventBufferOverflow):
    def __init__(self, *, max_pending: int) -> None:
        super().__init__("turn_acceptance_buffer_overflow", max_pending=max_pending)


class InputDispatchRejected(RuntimeError):
    """Application input was rejected by a bridge invariant before dispatch."""


class ProjectionWorkerCapacityError(RuntimeError):
    """A distinct Thread worker cannot start within the configured active bound."""


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
        turn_acceptance_event_max_pending: int = 256,
        max_active_threads: int = 4096,
        subscription_retry_initial_seconds: float = 0.05,
        subscription_retry_max_seconds: float = 2.0,
        turn_correlation_retention_seconds: float = 7 * 24 * 60 * 60,
        request_correlation_retention_seconds: float = 7 * 24 * 60 * 60,
    ) -> None:
        if baseline_history_limit < 1:
            raise ValueError("baseline_history_limit must be positive")
        if recovery_history_page_size < 1:
            raise ValueError("recovery_history_page_size must be positive")
        if recovery_max_pages < 1:
            raise ValueError("recovery_max_pages must be positive")
        if catchup_limit < 1:
            raise ValueError("catchup_limit must be positive")
        if projection_item_limit < 1:
            raise ValueError("projection_item_limit must be positive")
        if turn_acceptance_event_max_pending < 1:
            raise ValueError("turn_acceptance_event_max_pending must be positive")
        if (
            not isinstance(max_active_threads, int)
            or isinstance(max_active_threads, bool)
            or max_active_threads < 1
        ):
            raise ValueError("max_active_threads must be a positive integer")
        if subscription_retry_initial_seconds < 0:
            raise ValueError("initial subscription retry delay must be non-negative")
        if subscription_retry_max_seconds < subscription_retry_initial_seconds:
            raise ValueError("maximum subscription retry delay is below initial delay")
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
        self._subscription_retry_initial_seconds = subscription_retry_initial_seconds
        self._subscription_retry_max_seconds = subscription_retry_max_seconds
        self._turn_correlation_retention = timedelta(seconds=turn_correlation_retention_seconds)
        self._request_correlation_retention = timedelta(
            seconds=request_correlation_retention_seconds
        )
        self._turn_acceptance_event_max_pending = turn_acceptance_event_max_pending
        self._max_active_threads = max_active_threads
        self._tasks: dict[ThreadRef, asyncio.Task[None]] = {}
        self._ready: dict[ThreadRef, asyncio.Event] = {}
        self._pending_starts: dict[ThreadRef, _ProjectionStartReservation] = {}
        self._prepared_foreground_starts: dict[str, list[_ProjectionStartReservation]] = {}
        self._event_locks: dict[ThreadRef, asyncio.Lock] = {}
        self._pending_turn_acceptances: dict[ThreadRef, int] = {}
        self._acceptance_ready: dict[ThreadRef, asyncio.Event] = {}
        self._buffered_events: dict[ThreadRef, list[AgentEvent]] = {}
        self._buffered_event_overflows: set[ThreadRef] = set()
        self._delivery_ready = asyncio.Event()
        self._health: dict[ThreadRef, ProjectionWorkerHealth] = {}
        self._request_projection = InteractiveRequestProjection(
            applications=applications,
            projections=projections,
            correlations=request_correlations,
            request_presenter=request_presenter,
            execute_application=execute_application,
            active_routes=self._active_routes,
            deliver_request_outbound=deliver_request_outbound,
        )
        self._routes = ProjectionRouteCoordinator(
            projections=projections,
            execute_application=execute_application,
            active_routes=self._active_routes,
            deliver_outbound=deliver_outbound,
            deliver_request_once=self._request_projection.deliver_request_once,
            wait_for_acceptance=self._wait_for_acceptance,
            record_gap=self._record_gap,
            record_delivery_failure=self._record_delivery_failure,
            baseline_history_limit=baseline_history_limit,
            recovery_history_page_size=recovery_history_page_size,
            recovery_max_pages=recovery_max_pages,
            catchup_limit=catchup_limit,
            projection_item_limit=projection_item_limit,
            request_delivery_max_pending=request_delivery_max_pending,
        )
        self._stopping = False

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
        self._stopping = False
        self._delivery_ready.clear()
        self._routes.reset()
        self._pending_turn_acceptances.clear()
        self._acceptance_ready.clear()
        self._buffered_events.clear()
        self._buffered_event_overflows.clear()
        self._event_locks.clear()
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
                await self._routes.reconcile_route(
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
                await self._routes.reconcile_route(
                    application,
                    route,
                    require_checkpoint=existing is not None,
                )
            return route
        finally:
            if needs_reconcile:
                self._routes.complete_bootstrap(route_id)
            self._release_projection_start(thread_ref, reservation)

    async def send_input(
        self,
        application: AgentApplicationAdapter,
        thread_ref: ThreadRef,
        agent_input: AgentInput,
        *,
        conversation_ref: ConversationRef,
        reply_to_message_id: str,
        before_application_send: Callable[[], Awaitable[None]] | None = None,
    ) -> AcceptedTurn:
        if self._pending_turn_acceptances.get(thread_ref, 0) == 0:
            self._acceptance_ready[thread_ref] = asyncio.Event()
        self._pending_turn_acceptances[thread_ref] = (
            self._pending_turn_acceptances.get(thread_ref, 0) + 1
        )
        accepted: AcceptedTurn | None = None
        authorized_dispatch: ApplicationInputDispatch | None = None
        primary_error: BaseException | None = None

        async def authorize_dispatch(dispatch: ApplicationInputDispatch) -> None:
            nonlocal authorized_dispatch
            if authorized_dispatch is not None:
                raise InputDispatchRejected("Application input dispatch was declared twice")
            try:
                await self._request_projection.authorize_input_dispatch(
                    dispatch,
                    thread_ref=thread_ref,
                    client_message_id=agent_input.client_message_id,
                )
            except ValueError as error:
                raise InputDispatchRejected(str(error)) from error
            if before_application_send is not None:
                await before_application_send()
            authorized_dispatch = dispatch

        try:
            accepted = await application.send_input(
                thread_ref,
                agent_input,
                continuation=InputContinuationPreference.PREFER_ACTIVE_TURN,
                before_dispatch=authorize_dispatch,
            )
            if authorized_dispatch is None:
                raise RuntimeError("Application accepted input without declaring dispatch")
            await self._request_projection.correlate_accepted_turn(
                accepted,
                authorized_dispatch,
                thread_ref=thread_ref,
                client_message_id=agent_input.client_message_id,
                conversation_ref=conversation_ref,
                reply_to_message_id=reply_to_message_id,
            )
        except BaseException as exc:
            primary_error = exc
        finally:
            remaining = self._pending_turn_acceptances.get(thread_ref, 1) - 1
            if remaining > 0:
                self._pending_turn_acceptances[thread_ref] = remaining
            else:
                self._pending_turn_acceptances.pop(thread_ref, None)
                ready = self._acceptance_ready.pop(thread_ref, None)
                if ready is not None:
                    ready.set()
                try:
                    await self._drain_buffered_events(thread_ref)
                except BaseException as drain_error:
                    if primary_error is None:
                        primary_error = drain_error
                    else:
                        primary_error.add_note(
                            "Buffered-event draining also failed after input handling: "
                            f"{drain_error!r}"
                        )
                        logger.exception(
                            "Buffered-event draining failed while preserving the primary "
                            "input error",
                            exc_info=drain_error,
                        )
                finally:
                    self._clear_acceptance_runtime_entries(thread_ref)
        if primary_error is not None:
            if accepted is not None:
                raise InputPostAcceptanceError(accepted, primary_error) from primary_error
            raise primary_error
        if accepted is None:
            raise RuntimeError("Application input completed without an AcceptedTurn")
        return accepted

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
            await self._routes.reconcile_route(
                self._application(current.thread_ref.application_instance_id),
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
                    self._routes.reconcile_route(
                        self._application(current.thread_ref.application_instance_id),
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

    def _finish_task(
        self,
        thread_ref: ThreadRef,
        task: asyncio.Task[None],
    ) -> None:
        if self._tasks.get(thread_ref) is not task:
            return
        self._tasks.pop(thread_ref, None)
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
        for thread_ref in set(self._health) | set(self._event_locks):
            self._clear_worker_runtime_entries(thread_ref)

    def _clear_worker_runtime_entries(self, thread_ref: ThreadRef) -> None:
        self._health.pop(thread_ref, None)
        if self._pending_turn_acceptances.get(thread_ref, 0) == 0:
            self._event_locks.pop(thread_ref, None)

    def _clear_acceptance_runtime_entries(self, thread_ref: ThreadRef) -> None:
        if self._pending_turn_acceptances.get(thread_ref, 0) > 0:
            return
        self._acceptance_ready.pop(thread_ref, None)
        self._buffered_events.pop(thread_ref, None)
        self._buffered_event_overflows.discard(thread_ref)
        if thread_ref not in self._tasks:
            self._event_locks.pop(thread_ref, None)

    async def _project_thread(
        self,
        thread_ref: ThreadRef,
        ready: asyncio.Event,
        *,
        reconcile_existing: bool,
        require_checkpoint: bool,
    ) -> None:
        restart_count = 0
        needs_recovery = reconcile_existing
        recovery_requires_checkpoint = require_checkpoint
        recover_requests_after_gap = False
        while not self._stopping:
            events: AsyncIterator[AgentEvent] | None = None
            application: AgentApplicationAdapter | None = None
            try:
                if not await self._observation_required(thread_ref):
                    return
                application = self._application(thread_ref.application_instance_id)
                if needs_recovery:
                    for route in await self._active_routes(thread_ref):
                        await self._routes.begin_bootstrap(route.route_id)
                self._update_health(
                    thread_ref,
                    state=(
                        ProjectionWorkerState.STARTING
                        if restart_count == 0
                        else ProjectionWorkerState.RETRYING
                    ),
                    restart_count=restart_count,
                )
                events = application.subscribe_thread(thread_ref)
                ready.set()
                if needs_recovery:
                    await self._delivery_ready.wait()
                    await self._routes.reconcile_routes(
                        application,
                        await self._active_routes(thread_ref),
                        require_checkpoint=recovery_requires_checkpoint,
                    )
                    recovery_requires_checkpoint = True
                    if recover_requests_after_gap:
                        request_recovery_degraded = (
                            await self._request_projection.reconcile_application_after_event_gap(
                                application,
                                thread_ref,
                                deliver_request=self._routes.deliver_request_to_routes,
                            )
                        )
                        self._update_health(
                            thread_ref,
                            interactive_request_recovery_degraded=(request_recovery_degraded),
                        )
                        recover_requests_after_gap = False
                self._update_health(
                    thread_ref,
                    state=ProjectionWorkerState.RUNNING,
                    restart_count=restart_count,
                    last_subscription_error=None,
                    last_recovery_error=None,
                )
                async for event in events:
                    await self._handle_event(event)
                    if not await self._observation_required(thread_ref):
                        return
                raise RuntimeError("Application Thread subscription ended")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                restart_count += 1
                ready.set()
                recover_requests_after_gap = True
                error_changes: dict[str, Any]
                if isinstance(error, ProjectionRecoveryUnavailable):
                    error_changes = {
                        "last_recovery_error": str(error),
                        "last_subscription_error": None,
                    }
                elif isinstance(error, EventStreamGap):
                    current = self._health.get(thread_ref)
                    error_changes = {
                        "last_gap": error.gap_code,
                        "last_event_gap": error.gap_code,
                        "last_subscription_error": None,
                        "interactive_request_recovery_degraded": (
                            self._request_recovery_is_degraded(application)
                            if application is not None
                            else False
                        ),
                    }
                    if isinstance(error, EventBufferOverflow):
                        error_changes.update(
                            last_event_overflow=error.gap_code,
                            event_overflow_count=(
                                current.event_overflow_count + 1 if current is not None else 1
                            ),
                        )
                else:
                    error_changes = {
                        "last_subscription_error": str(error),
                        "interactive_request_recovery_degraded": (
                            self._request_recovery_is_degraded(application)
                            if application is not None
                            else False
                        ),
                    }
                error_changes.setdefault(
                    "interactive_request_recovery_degraded",
                    (
                        self._request_recovery_is_degraded(application)
                        if application is not None
                        else False
                    ),
                )
                self._update_health(
                    thread_ref,
                    state=ProjectionWorkerState.RETRYING,
                    restart_count=restart_count,
                    **error_changes,
                )
                if self._stopping:
                    return
                exponent = min(restart_count - 1, 30)
                delay = min(
                    self._subscription_retry_initial_seconds * (2**exponent),
                    self._subscription_retry_max_seconds,
                )
                if isinstance(error, RetryableDeliveryError):
                    delay = max(delay, error.retry_after_seconds or 0)
                await asyncio.sleep(delay)
                needs_recovery = True
                recovery_requires_checkpoint = True
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

    async def _observation_required(self, thread_ref: ThreadRef) -> bool:
        return bool(await self._active_routes(thread_ref))

    async def _handle_event(self, event: AgentEvent) -> None:
        thread_ref = event.thread_ref
        if thread_ref is None:
            return
        lock = self._event_locks.setdefault(thread_ref, asyncio.Lock())
        async with lock:
            if self._pending_turn_acceptances.get(thread_ref, 0) > 0:
                buffered = self._buffered_events.setdefault(thread_ref, [])
                if len(buffered) >= self._turn_acceptance_event_max_pending:
                    buffered.clear()
                    self._buffered_event_overflows.add(thread_ref)
                    raise TurnAcceptanceBufferOverflow(
                        max_pending=self._turn_acceptance_event_max_pending
                    )
                buffered.append(event)
                return
            await self._apply_event(event)

    async def _drain_buffered_events(self, thread_ref: ThreadRef) -> None:
        lock = self._event_locks.setdefault(thread_ref, asyncio.Lock())
        async with lock:
            if self._pending_turn_acceptances.get(thread_ref, 0) > 0:
                return
            if thread_ref in self._buffered_event_overflows:
                self._buffered_event_overflows.remove(thread_ref)
                self._buffered_events.pop(thread_ref, None)
                raise TurnAcceptanceBufferOverflow(
                    max_pending=self._turn_acceptance_event_max_pending
                )
            events = self._buffered_events.pop(thread_ref, [])
            for event in events:
                await self._apply_event(event)

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
                        turn_id=event.turn_id,
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
            and event.turn_id is not None
        ):
            await self._request_projection.delete_terminal_turn(
                thread_ref,
                event.turn_id,
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

    async def _wait_for_acceptance(self, thread_ref: ThreadRef) -> None:
        ready = self._acceptance_ready.get(thread_ref)
        if ready is not None:
            await ready.wait()

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

    @staticmethod
    def _request_recovery_is_degraded(
        application: AgentApplicationAdapter,
    ) -> bool:
        runtime = application.summary.capabilities.runtime
        return (
            runtime.interactive_requests is not SupportLevel.UNSUPPORTED
            and runtime.pending_request_snapshot is not SupportLevel.NATIVE
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

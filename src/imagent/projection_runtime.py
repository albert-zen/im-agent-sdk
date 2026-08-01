from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .adapters import (
    AgentApplicationAdapter,
    BindingRepository,
    ProjectionRouteRepository,
    RequestCorrelationRepository,
)
from .contracts import (
    AcceptedTurn,
    AgentEvent,
    AgentEventType,
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    ApplicationOperation,
    ApplicationOperationResult,
    ConversationBinding,
    ConversationRef,
    InputContinuationPreference,
    InputDisposition,
    ProjectionPolicy,
    RequestRef,
    SupportLevel,
    ThreadProjectionRoute,
    ThreadRef,
    TurnReplyCorrelation,
    TurnReplyCorrelationPolicy,
)
from .controllers import RequestPresenter
from .events import EventBufferOverflow, EventStreamGap
from .projection_routes import ProjectionRouteCoordinator
from .projections import (
    DeliverOutbound,
    ProjectedAgentMessage,
    ProjectionWorkerHealth,
    ProjectionWorkerState,
    RetryableDeliveryError,
    derive_projection_route_id,
    derive_turn_reply_correlation_id,
    get_projection_route,
)
from .recovery import ProjectionRecoveryUnavailable
from .request_projection_runtime import InteractiveRequestProjection

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
        deliver_request_outbound: DeliverOutbound,
        baseline_history_limit: int = 3,
        recovery_history_page_size: int = 10,
        recovery_max_pages: int = 5,
        catchup_limit: int = 10,
        projection_item_limit: int = 20,
        request_delivery_max_pending: int = 256,
        turn_acceptance_event_max_pending: int = 256,
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
        if subscription_retry_initial_seconds < 0:
            raise ValueError("initial subscription retry delay must be non-negative")
        if subscription_retry_max_seconds < subscription_retry_initial_seconds:
            raise ValueError("maximum subscription retry delay is below initial delay")
        if turn_correlation_retention_seconds <= 0:
            raise ValueError("turn_correlation_retention_seconds must be positive")
        if request_correlation_retention_seconds <= 0:
            raise ValueError("request_correlation_retention_seconds must be positive")
        self._applications = applications
        self._bindings = bindings
        self._projections = projections
        self._request_correlations = request_correlations
        self._projection_policy = projection_policy
        self._subscription_retry_initial_seconds = subscription_retry_initial_seconds
        self._subscription_retry_max_seconds = subscription_retry_max_seconds
        self._turn_correlation_retention = timedelta(seconds=turn_correlation_retention_seconds)
        self._request_correlation_retention = timedelta(
            seconds=request_correlation_retention_seconds
        )
        self._turn_acceptance_event_max_pending = turn_acceptance_event_max_pending
        self._tasks: dict[ThreadRef, asyncio.Task[None]] = {}
        self._ready: dict[ThreadRef, asyncio.Event] = {}
        self._event_locks: dict[ThreadRef, asyncio.Lock] = {}
        self._pending_turn_acceptances: dict[ThreadRef, int] = {}
        self._acceptance_ready: dict[ThreadRef, asyncio.Event] = {}
        self._buffered_events: dict[ThreadRef, list[AgentEvent]] = {}
        self._buffered_event_overflows: set[ThreadRef] = set()
        self._delivery_ready = asyncio.Event()
        self._health: dict[ThreadRef, ProjectionWorkerHealth] = {}
        self._routes = ProjectionRouteCoordinator(
            projections=projections,
            request_correlations=request_correlations,
            request_presenter=request_presenter,
            execute_application=execute_application,
            active_routes=self._active_routes,
            deliver_outbound=deliver_outbound,
            deliver_request_outbound=deliver_request_outbound,
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
        self._request_projection = InteractiveRequestProjection(
            applications=applications,
            correlations=request_correlations,
            active_routes=self._active_routes,
            deliver_request=self._routes.deliver_request_to_routes,
            cancel_request=self._routes.cancel_request_deliveries,
        )
        self._stopping = False

    async def cleanup_stale_correlations(self) -> None:
        await self._projections.delete_turn_reply_correlations(
            older_than=datetime.now(UTC) - self._turn_correlation_retention
        )
        await self._request_projection.cleanup_older_than(
            datetime.now(UTC) - self._request_correlation_retention
        )

    async def restore(self) -> None:
        self._stopping = False
        self._delivery_ready.clear()
        self._routes.reset()
        self._pending_turn_acceptances.clear()
        self._acceptance_ready.clear()
        self._buffered_events.clear()
        self._buffered_event_overflows.clear()
        restored_routes = await self._projections.list_projection_routes()
        if self._projection_policy is ProjectionPolicy.FOREGROUND_ONLY:
            active_routes: list[ThreadProjectionRoute] = []
            for route in restored_routes:
                binding = await self._bindings.get(route.conversation_ref)
                if binding is not None and binding.thread_ref == route.thread_ref:
                    active_routes.append(route)
            restored_routes = tuple(active_routes)
        for route in restored_routes:
            await self._routes.begin_bootstrap(route.route_id)
        for thread_ref in {route.thread_ref for route in restored_routes}:
            await self._ensure_projection(thread_ref, recover_existing=True)

    def mark_delivery_ready(self) -> None:
        """Release restored workers after producers are observed and Channels can send."""

        self._delivery_ready.set()

    async def open_request_refs(self) -> frozenset[RequestRef]:
        return await self._request_projection.open_request_refs()

    async def reconcile_pending_requests(
        self,
        restart_open_refs: frozenset[RequestRef],
    ) -> None:
        await self._request_projection.reconcile_pending_requests(restart_open_refs)

    async def stop(self) -> None:
        self._stopping = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._ready.clear()
        await self._routes.stop()
        self._routes.reset()
        for thread_ref in tuple(self._health):
            self._update_health(
                thread_ref,
                state=ProjectionWorkerState.STOPPED,
            )

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
        route_id = derive_projection_route_id(thread_ref, conversation_ref)
        # Install the barrier before the durable route becomes visible to an
        # already-running Thread worker.  SQLite persistence can yield while
        # publishing the route, so creating the barrier after put() would
        # leave a live-before-baseline window.
        await self._routes.begin_bootstrap(route_id)
        try:
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

    async def prepare_input_route(
        self,
        application: AgentApplicationAdapter,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef,
        *,
        thread_was_created: bool,
    ) -> ThreadProjectionRoute:
        await self.cleanup_stale_correlations()
        route_id = derive_projection_route_id(thread_ref, conversation_ref)
        existing = await get_projection_route(self._projections, route_id)
        task = self._tasks.get(thread_ref)
        needs_reconcile = existing is None or task is None or task.done()
        if needs_reconcile:
            await self._routes.begin_bootstrap(route_id)
        try:
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
            if dispatch.thread_ref != thread_ref:
                raise InputDispatchRejected("Application input dispatch belongs to another Thread")
            if dispatch.client_message_id != agent_input.client_message_id:
                raise InputDispatchRejected(
                    "Application input dispatch client_message_id does not match input"
                )
            if dispatch.disposition is InputDisposition.STARTED:
                if (
                    dispatch.correlation_policy is not TurnReplyCorrelationPolicy.CREATE_NEW
                    or dispatch.expected_turn_id is not None
                ):
                    raise InputDispatchRejected("started input must create a new Turn correlation")
            elif dispatch.disposition is InputDisposition.STEERED:
                if (
                    dispatch.correlation_policy is not TurnReplyCorrelationPolicy.PRESERVE_EXISTING
                    or not dispatch.expected_turn_id
                ):
                    raise InputDispatchRejected(
                        "steered input must preserve an expected Turn correlation"
                    )
                existing = await self._projections.get_turn_reply_correlation(
                    thread_ref,
                    dispatch.expected_turn_id,
                )
                if existing is None:
                    raise InputDispatchRejected(
                        "cannot steer a Turn without an existing reply correlation"
                    )
            else:
                raise InputDispatchRejected("unknown Application input disposition")
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
            if accepted.thread_ref != thread_ref:
                raise ValueError("AcceptedTurn belongs to a different Thread")
            if accepted.client_message_id != agent_input.client_message_id:
                raise ValueError("AcceptedTurn client_message_id does not match input")
            if authorized_dispatch is None:
                raise RuntimeError("Application accepted input without declaring dispatch")
            if (
                accepted.disposition is not authorized_dispatch.disposition
                or accepted.correlation_policy is not authorized_dispatch.correlation_policy
            ):
                raise RuntimeError(
                    "AcceptedTurn disposition does not match the authorized dispatch"
                )
            if accepted.disposition is InputDisposition.STARTED:
                await self._projections.put_turn_reply_correlation(
                    TurnReplyCorrelation(
                        correlation_id=derive_turn_reply_correlation_id(
                            thread_ref,
                            accepted.turn_id,
                        ),
                        thread_ref=thread_ref,
                        turn_id=accepted.turn_id,
                        client_message_id=accepted.client_message_id,
                        conversation_ref=conversation_ref,
                        reply_to_message_id=reply_to_message_id,
                        created_at=datetime.now(UTC),
                    )
                )
            else:
                expected_turn_id = authorized_dispatch.expected_turn_id
                if accepted.turn_id != expected_turn_id:
                    raise RuntimeError("native steer accepted a different Turn than was authorized")
                preserved = await self._projections.get_turn_reply_correlation(
                    thread_ref,
                    accepted.turn_id,
                )
                if preserved is None:
                    raise RuntimeError(
                        "authorized steer reply correlation disappeared after dispatch"
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
            try:
                await self._routes.reconcile_route(
                    self._application(current.thread_ref.application_instance_id),
                    activated_route,
                    require_checkpoint=True,
                )
            finally:
                self._routes.complete_bootstrap(activated_route.route_id)
            return
        for route in routes:
            await self._routes.begin_bootstrap(route.route_id)
        await self._ensure_projection(current.thread_ref, recover_existing=True)

    async def _remember_route(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef,
        *,
        reply_to_message_id: str | None,
    ) -> tuple[ThreadProjectionRoute, bool]:
        existing = await self._projections.list_projection_routes(thread_ref)
        current = next(
            (route for route in existing if route.conversation_ref == conversation_ref),
            None,
        )
        route = ThreadProjectionRoute(
            route_id=derive_projection_route_id(thread_ref, conversation_ref),
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
            reply_to_message_id=(
                reply_to_message_id
                if reply_to_message_id is not None
                else current.reply_to_message_id
                if current is not None
                else None
            ),
            updated_at=datetime.now(UTC),
        )
        if self._projection_policy is ProjectionPolicy.REMEMBERED_LAST_RECIPIENT:
            stored = await self._projections.replace_thread_projection_routes(route)
            for removed in existing:
                if removed.conversation_ref != stored.conversation_ref:
                    await self._projections.delete_turn_reply_correlations(
                        thread_ref=thread_ref,
                        conversation_ref=removed.conversation_ref,
                    )
                    await self._request_correlations.delete_request_correlations(
                        thread_ref=thread_ref,
                        conversation_ref=removed.conversation_ref,
                    )
            await self._routes.forget_routes(
                tuple(
                    removed
                    for removed in existing
                    if removed.conversation_ref != stored.conversation_ref
                )
            )
        else:
            stored = await self._projections.put_projection_route(route)
        return stored, current is None

    async def _stop_if_unobserved(self, thread_ref: ThreadRef) -> None:
        if await self._active_routes(thread_ref):
            return
        task = self._tasks.get(thread_ref)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._update_health(
            thread_ref,
            state=ProjectionWorkerState.STOPPED,
        )

    async def _ensure_projection(
        self,
        thread_ref: ThreadRef,
        *,
        recover_existing: bool = False,
    ) -> None:
        task = self._tasks.get(thread_ref)
        if task is None or task.done():
            ready = asyncio.Event()
            task = asyncio.create_task(
                self._project_thread(
                    thread_ref,
                    ready,
                    recover_existing=recover_existing,
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

    def _finish_task(
        self,
        thread_ref: ThreadRef,
        task: asyncio.Task[None],
    ) -> None:
        if self._tasks.get(thread_ref) is task:
            self._tasks.pop(thread_ref, None)
            self._ready.pop(thread_ref, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._update_health(
                thread_ref,
                state=ProjectionWorkerState.STOPPED,
                last_subscription_error=str(error),
            )
            logger.exception(
                "Agent event projection failed",
                exc_info=(type(error), error, error.__traceback__),
            )
        else:
            self._update_health(
                thread_ref,
                state=ProjectionWorkerState.STOPPED,
            )

    async def _project_thread(
        self,
        thread_ref: ThreadRef,
        ready: asyncio.Event,
        *,
        recover_existing: bool,
    ) -> None:
        restart_count = 0
        needs_recovery = recover_existing
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
                        require_checkpoint=True,
                    )
                    if recover_requests_after_gap:
                        request_recovery_degraded = (
                            await self._request_projection.reconcile_application_after_event_gap(
                                application,
                                thread_ref,
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
        await self._request_projection.handle_event(event)
        thread_ref = event.thread_ref
        if thread_ref is None:
            return
        if event.type is AgentEventType.MESSAGE_COMPLETED:
            agent_message = event.data.get("message")
            if isinstance(agent_message, AgentMessage):
                await self._routes.deliver_to_routes(
                    await self._active_routes(thread_ref),
                    ProjectedAgentMessage(
                        message=agent_message,
                        turn_id=event.turn_id,
                        event_id=event.event_id,
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
            await self._projections.delete_turn_reply_correlation(
                thread_ref,
                event.turn_id,
            )
        if event.type is AgentEventType.THREAD_DELETED:
            routes = await self._projections.list_projection_routes(thread_ref)
            await self._projections.delete_projection_routes(thread_ref)
            await self._projections.delete_turn_reply_correlations(thread_ref=thread_ref)
            await self._request_correlations.delete_request_correlations(thread_ref=thread_ref)
            await self._routes.forget_routes(routes)

    async def _active_routes(
        self,
        thread_ref: ThreadRef | None,
    ) -> tuple[ThreadProjectionRoute, ...]:
        if thread_ref is None:
            return ()
        routes = await self._projections.list_projection_routes(thread_ref)
        if self._projection_policy is not ProjectionPolicy.FOREGROUND_ONLY:
            return routes
        active: list[ThreadProjectionRoute] = []
        for route in routes:
            binding = await self._bindings.get(route.conversation_ref)
            if binding is not None and binding.thread_ref == thread_ref:
                active.append(route)
        return tuple(active)

    async def _route_is_active(self, route: ThreadProjectionRoute) -> bool:
        return any(
            candidate.route_id == route.route_id
            for candidate in await self._active_routes(route.thread_ref)
        )

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

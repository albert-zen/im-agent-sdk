from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from .applications.contract import AgentApplicationAdapter, ThreadRef
from .applications.operations import ApplicationOperation, ApplicationOperationResult
from .applications.requests import (
    InteractiveRequest,
    RequestRef,
    derive_request_response_shape,
)
from .gateway.persistence.repository_contracts import (
    IdempotencyClaimStatus,
    ProjectionRouteRepository,
    RequestCorrelationRepository,
)
from .gateway.persistence.state_contracts import (
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
)
from .gateway.projection.checkpoints import _ProjectionCheckpointAuthority
from .gateway.projection.recovery import read_bounded_authoritative_projection
from .gateway.projection.request_correlation import (
    derive_request_correlation_id,
    derive_request_delivery_id,
)
from .gateway.routing.projection_routes import get_projection_route
from .interaction.controllers import RequestPresenter
from .projections import (
    DeliverOutbound,
    DeliverRequestOutbound,
    ProjectedAgentMessage,
    RetryableDeliveryError,
    deliver_projected_message,
)

RecordGap = Callable[[ThreadRef, str, str], None]
RecordDeliveryFailure = Callable[[ThreadProjectionRoute, Exception], None]
WaitForAcceptance = Callable[[ThreadRef], Awaitable[None]]
ExecuteApplication = Callable[
    [ApplicationOperation],
    Awaitable[ApplicationOperationResult],
]
ActiveRoutes = Callable[
    [ThreadRef | None],
    Awaitable[tuple[ThreadProjectionRoute, ...]],
]


class ProjectionRouteCoordinator:
    """Serialize bootstrap, reconciliation, and delivery per destination route."""

    def __init__(
        self,
        *,
        projections: ProjectionRouteRepository,
        request_correlations: RequestCorrelationRepository,
        request_presenter: RequestPresenter | None,
        execute_application: ExecuteApplication,
        active_routes: ActiveRoutes,
        deliver_outbound: DeliverOutbound,
        deliver_request_outbound: DeliverRequestOutbound,
        wait_for_acceptance: WaitForAcceptance,
        record_gap: RecordGap,
        record_delivery_failure: RecordDeliveryFailure,
        baseline_history_limit: int,
        recovery_history_page_size: int,
        recovery_max_pages: int,
        catchup_limit: int,
        projection_item_limit: int,
        request_delivery_max_pending: int,
    ) -> None:
        if request_delivery_max_pending < 1:
            raise ValueError("request_delivery_max_pending must be positive")
        self._projections = projections
        self._checkpoint_authority = _ProjectionCheckpointAuthority(
            projections=projections,
        )
        self._request_correlations = request_correlations
        self._request_presenter = request_presenter
        self._execute_application = execute_application
        self._active_routes = active_routes
        self._deliver_outbound = deliver_outbound
        self._deliver_request_outbound = deliver_request_outbound
        self._wait_for_acceptance = wait_for_acceptance
        self._record_gap = record_gap
        self._record_delivery_failure = record_delivery_failure
        self._baseline_history_limit = baseline_history_limit
        self._recovery_history_page_size = recovery_history_page_size
        self._recovery_max_pages = recovery_max_pages
        self._catchup_limit = catchup_limit
        self._projection_item_limit = projection_item_limit
        self._request_delivery_max_pending = request_delivery_max_pending
        self._request_capacity_changed = asyncio.Event()
        self._bootstrap: dict[str, asyncio.Event] = {}
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
        self._locks.clear()
        self._blocked_routes.clear()

    async def begin_bootstrap(self, route_id: str) -> None:
        lock = self._locks.setdefault(route_id, asyncio.Lock())
        async with lock:
            current = self._bootstrap.get(route_id)
            if current is None or current.is_set():
                self._bootstrap[route_id] = asyncio.Event()

    def complete_bootstrap(self, route_id: str) -> None:
        barrier = self._bootstrap.setdefault(route_id, asyncio.Event())
        barrier.set()

    async def forget_routes(
        self,
        routes: tuple[ThreadProjectionRoute, ...],
    ) -> None:
        retry_tasks: list[asyncio.Task[None]] = []
        for route in routes:
            retry_tasks.extend(
                self._pop_route_request_retries(
                    route.route_id,
                    thread_ref=route.thread_ref,
                )
            )
            barrier = self._bootstrap.pop(route.route_id, None)
            if barrier is not None:
                barrier.set()
            self._locks.pop(route.route_id, None)
            self._blocked_routes.discard(route.route_id)
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
    ) -> None:
        lock = self._locks.setdefault(route.route_id, asyncio.Lock())
        completed = False
        try:
            async with lock:
                current = await get_projection_route(
                    self._projections,
                    route.route_id,
                )
                if current is None:
                    return
                projection = await read_bounded_authoritative_projection(
                    application,
                    current,
                    execute_application=self._execute_application,
                    baseline_history_limit=self._baseline_history_limit,
                    recovery_history_page_size=self._recovery_history_page_size,
                    recovery_max_pages=self._recovery_max_pages,
                    catchup_limit=self._catchup_limit,
                    projection_item_limit=self._projection_item_limit,
                    require_checkpoint=require_checkpoint,
                )
                if projection.gap is not None:
                    self._record_gap(
                        route.thread_ref,
                        route.route_id,
                        projection.gap,
                    )
                await self._wait_for_acceptance(route.thread_ref)
                await self._deliver_messages(
                    current,
                    projection.messages,
                    authoritative=True,
                )
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
        barrier = self._bootstrap.get(route.route_id)
        if barrier is None:
            barrier = asyncio.Event()
            barrier.set()
            self._bootstrap[route.route_id] = barrier
        if not barrier.is_set():
            await barrier.wait()
        retry_count = 0
        retry_error: RetryableDeliveryError | None = None
        while not _request_expired(request):
            if retry_error is not None:
                retry_count += 1
                delay = min(0.05 * (2 ** min(retry_count - 1, 5)), 1.0)
                delay = max(delay, retry_error.retry_after_seconds or 0)
                if request.expires_at is not None:
                    remaining = (request.expires_at - datetime.now(UTC)).total_seconds()
                    if remaining <= 0:
                        return
                    delay = min(delay, remaining)
                await asyncio.sleep(delay)
            lock = self._locks.setdefault(route.route_id, asyncio.Lock())
            async with lock:
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

    async def _deliver_request_once(
        self,
        route: ThreadProjectionRoute,
        request: InteractiveRequest,
    ) -> None:
        if _request_expired(request):
            return
        presenter = self._request_presenter
        if presenter is None:
            raise RuntimeError("interactive request presenter is not configured")
        reply_correlation = await self._projections.get_turn_reply_correlation(
            request.thread_ref,
            request.turn_id,
        )
        reply_to = (
            reply_correlation.reply_to_message_id
            if reply_correlation is not None
            and reply_correlation.conversation_ref == route.conversation_ref
            else None
        )
        delivery_id = derive_request_delivery_id(
            request.request_ref,
            route.conversation_ref,
        )
        presentation = presenter.present_request(
            request,
            conversation_ref=route.conversation_ref,
            delivery_id=delivery_id,
            reply_to_message_id=reply_to,
        )
        if request.expires_at is None:
            outcome = await self._deliver_request_outbound(presentation.message)
        else:
            remaining = (request.expires_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return
            try:
                async with asyncio.timeout(remaining):
                    outcome = await self._deliver_request_outbound(presentation.message)
            except TimeoutError:
                return
        if outcome is IdempotencyClaimStatus.IN_FLIGHT:
            raise RuntimeError(f"interactive request delivery is already in flight: {delivery_id}")
        if not presentation.response_supported or _request_expired(request):
            return
        current = await self._current_active_route(route)
        if current is None:
            return
        now = datetime.now(UTC)
        await self._request_correlations.put_request_correlation(
            RequestRouteCorrelation(
                correlation_id=derive_request_correlation_id(
                    request.request_ref,
                    current.conversation_ref,
                ),
                request_ref=request.request_ref,
                thread_ref=request.thread_ref,
                turn_id=request.turn_id,
                conversation_ref=current.conversation_ref,
                delivery_id=delivery_id,
                response_shape=derive_request_response_shape(request),
                state=RequestRouteState.OPEN,
                created_at=now,
                updated_at=now,
                expires_at=request.expires_at,
            )
        )

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
        thread_ref: ThreadRef,
    ) -> tuple[asyncio.Task[None], ...]:
        tasks: list[asyncio.Task[None]] = []
        for key in tuple(self._request_retry_tasks):
            if key[0] == thread_ref and key[1] == route_id:
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
        barrier = self._bootstrap.get(route.route_id)
        if barrier is None:
            barrier = asyncio.Event()
            barrier.set()
            self._bootstrap[route.route_id] = barrier
        if not barrier.is_set():
            await barrier.wait()
        lock = self._locks.setdefault(route.route_id, asyncio.Lock())
        async with lock:
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
            except RetryableDeliveryError:
                raise
            except Exception as error:
                self._block_route(current, error)

    async def _deliver_messages(
        self,
        route: ThreadProjectionRoute,
        messages: tuple[ProjectedAgentMessage, ...],
        *,
        authoritative: bool,
    ) -> None:
        seen: set[str] = set()
        current = route
        for projected in messages:
            if projected.message.agent_item_id in seen:
                continue
            seen.add(projected.message.agent_item_id)
            if route.route_id in self._blocked_routes:
                return
            try:
                current = await deliver_projected_message(
                    self._projections,
                    current,
                    projected,
                    deliver_outbound=self._deliver_outbound,
                    checkpoint_authority=self._checkpoint_authority,
                    authoritative=authoritative,
                )
            except RetryableDeliveryError:
                raise
            except Exception as error:
                self._block_route(current, error)
                return

    def _block_route(
        self,
        route: ThreadProjectionRoute,
        error: Exception,
    ) -> None:
        self._blocked_routes.add(route.route_id)
        self._record_delivery_failure(route, error)


def _request_expired(request: InteractiveRequest) -> bool:
    return request.expires_at is not None and request.expires_at <= datetime.now(UTC)

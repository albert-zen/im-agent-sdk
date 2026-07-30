from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .adapters import AgentApplicationAdapter, ProjectionRouteRepository
from .contracts import (
    ApplicationOperation,
    ApplicationOperationResult,
    ThreadProjectionRoute,
    ThreadRef,
)
from .projections import (
    DeliverOutbound,
    ProjectedAgentMessage,
    deliver_projected_message,
    get_projection_route,
)
from .recovery import read_bounded_authoritative_projection

RecordGap = Callable[[ThreadRef, str, str], None]
RecordDeliveryFailure = Callable[[ThreadProjectionRoute, Exception], None]
WaitForAcceptance = Callable[[ThreadRef], Awaitable[None]]
ExecuteApplication = Callable[
    [ApplicationOperation],
    Awaitable[ApplicationOperationResult],
]


class ProjectionRouteCoordinator:
    """Serialize bootstrap, reconciliation, and delivery per destination route."""

    def __init__(
        self,
        *,
        projections: ProjectionRouteRepository,
        execute_application: ExecuteApplication,
        deliver_outbound: DeliverOutbound,
        wait_for_acceptance: WaitForAcceptance,
        record_gap: RecordGap,
        record_delivery_failure: RecordDeliveryFailure,
        baseline_history_limit: int,
        recovery_history_page_size: int,
        recovery_max_pages: int,
        catchup_limit: int,
        projection_item_limit: int,
    ) -> None:
        self._projections = projections
        self._execute_application = execute_application
        self._deliver_outbound = deliver_outbound
        self._wait_for_acceptance = wait_for_acceptance
        self._record_gap = record_gap
        self._record_delivery_failure = record_delivery_failure
        self._baseline_history_limit = baseline_history_limit
        self._recovery_history_page_size = recovery_history_page_size
        self._recovery_max_pages = recovery_max_pages
        self._catchup_limit = catchup_limit
        self._projection_item_limit = projection_item_limit
        self._bootstrap: dict[str, asyncio.Event] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._blocked_routes: set[str] = set()

    def reset(self) -> None:
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

    def forget_routes(
        self,
        routes: tuple[ThreadProjectionRoute, ...],
    ) -> None:
        for route in routes:
            barrier = self._bootstrap.pop(route.route_id, None)
            if barrier is not None:
                barrier.set()
            self._locks.pop(route.route_id, None)
            self._blocked_routes.discard(route.route_id)

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
    ) -> None:
        lock = self._locks.setdefault(route.route_id, asyncio.Lock())
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
        finally:
            self.complete_bootstrap(route.route_id)

    async def deliver_to_routes(
        self,
        routes: tuple[ThreadProjectionRoute, ...],
        projected: ProjectedAgentMessage,
    ) -> None:
        await asyncio.gather(*(self._deliver_to_route(route, projected) for route in routes))

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
                    authoritative=False,
                )
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
                    authoritative=authoritative,
                )
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

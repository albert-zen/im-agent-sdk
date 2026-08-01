from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from .adapters import (
    AgentApplicationAdapter,
    RequestCorrelationConflict,
    RequestCorrelationRepository,
)
from .contracts import (
    AgentEvent,
    AgentEventType,
    InteractiveRequest,
    RequestRef,
    RequestResolutionStatus,
    RequestRouteState,
    SupportLevel,
    ThreadProjectionRoute,
    ThreadRef,
    validate_interactive_request,
)

logger = logging.getLogger(__name__)

ActiveRoutes = Callable[
    [ThreadRef | None],
    Awaitable[tuple[ThreadProjectionRoute, ...]],
]
DeliverRequest = Callable[
    [tuple[ThreadProjectionRoute, ...], InteractiveRequest],
    Awaitable[None],
]
CancelRequest = Callable[[RequestRef], Awaitable[None]]


class InteractiveRequestProjection:
    """Request-only projection and authoritative restart reconciliation."""

    def __init__(
        self,
        *,
        applications: Mapping[str, AgentApplicationAdapter],
        correlations: RequestCorrelationRepository,
        active_routes: ActiveRoutes,
        deliver_request: DeliverRequest,
        cancel_request: CancelRequest,
    ) -> None:
        self._applications = applications
        self._correlations = correlations
        self._active_routes = active_routes
        self._deliver_request = deliver_request
        self._cancel_request = cancel_request

    async def cleanup_older_than(self, older_than: datetime) -> None:
        await self._correlations.delete_request_correlations(older_than=older_than)

    async def open_request_refs(self) -> frozenset[RequestRef]:
        """Snapshot pre-start bridge correlations without claiming native truth."""

        return frozenset(
            correlation.request_ref
            for correlation in await self._correlations.list_request_correlations()
            if correlation.state is RequestRouteState.OPEN
        )

    async def reconcile_pending_requests(
        self,
        restart_open_refs: frozenset[RequestRef],
    ) -> None:
        """Reproject only native-authoritative pending requests after restart."""

        pending_refs: set[RequestRef] = set()
        for application in self._applications.values():
            application_id = application.summary.ref.application_instance_id
            supports_snapshot = (
                application.summary.capabilities.runtime.pending_request_snapshot
                is SupportLevel.NATIVE
            )
            if not supports_snapshot:
                for request_ref in restart_open_refs:
                    if request_ref.application_ref.application_instance_id == application_id:
                        await self._mark_stale(request_ref)
                continue
            pending = await application.list_pending_requests()
            for request in pending:
                validate_interactive_request(request)
                if request.request_ref.application_ref != application.summary.ref:
                    raise ValueError("pending request snapshot belongs to a different application")
                pending_refs.add(request.request_ref)
                await self._deliver_request(
                    await self._active_routes(request.thread_ref),
                    request,
                )
        for request_ref in restart_open_refs - pending_refs:
            await self._mark_stale(request_ref)

    async def handle_event(self, event: AgentEvent) -> None:
        resolution = event.request_resolution
        if event.type is AgentEventType.REQUEST_RESOLVED and resolution is not None:
            await self._cancel_request(resolution.request_ref)
            state = (
                RequestRouteState.RESOLVED
                if resolution.status is RequestResolutionStatus.RESOLVED
                else RequestRouteState.STALE
            )
            try:
                await self._correlations.transition_request_correlations(
                    resolution.request_ref,
                    expected_states=(
                        (
                            RequestRouteState.OPEN,
                            RequestRouteState.RESPONDED,
                            RequestRouteState.STALE,
                        )
                        if state is RequestRouteState.RESOLVED
                        else (
                            RequestRouteState.OPEN,
                            RequestRouteState.RESPONDED,
                        )
                    ),
                    state=state,
                    updated_at=resolution.resolved_at,
                )
            except KeyError:
                pass
            except RequestCorrelationConflict:
                logger.debug(
                    "Ignoring non-progressing request resolution for %s",
                    resolution.request_ref,
                )
        if event.type is AgentEventType.REQUEST_OPENED and event.request is not None:
            await self._deliver_request(
                await self._active_routes(event.thread_ref),
                event.request,
            )

    async def _mark_stale(self, request_ref: RequestRef) -> None:
        try:
            await self._correlations.transition_request_correlations(
                request_ref,
                expected_states=(RequestRouteState.OPEN,),
                state=RequestRouteState.STALE,
                updated_at=datetime.now(UTC),
            )
        except (KeyError, RequestCorrelationConflict):
            pass

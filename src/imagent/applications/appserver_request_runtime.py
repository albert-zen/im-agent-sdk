from __future__ import annotations

import inspect
import logging
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from ..contracts import (
    AgentEvent,
    AgentEventType,
    ApplicationRef,
    RequestDuplicateError,
    RequestRef,
    RequestResolution,
    RequestResolutionStatus,
    RequestResolvedError,
    RequestResponded,
    RequestStaleError,
    RespondRequest,
    ThreadRef,
)
from .appserver_requests import (
    PendingAppServerRequest,
    UnsupportedAppServerRequest,
    build_appserver_response,
    derive_appserver_request_ref,
)

_TERMINAL_REQUEST_CACHE_LIMIT = 256

logger = logging.getLogger(__name__)

ServerRequestMapper = Callable[
    [ApplicationRef, Mapping[str, object]],
    PendingAppServerRequest,
]
PublishEvent = Callable[[str, AgentEvent], None]


@dataclass(frozen=True, slots=True)
class _RecentRequestOutcome:
    state: str
    pending: PendingAppServerRequest


class AppServerRequestRuntime:
    """Codex request handles and bounded terminal diagnostics for one client."""

    def __init__(
        self,
        *,
        application_ref: ApplicationRef,
        client: object,
        mapper: ServerRequestMapper | None,
        publish_event: PublishEvent,
    ) -> None:
        self._application_ref = application_ref
        self._client = client
        self._mapper = mapper
        self._publish_event = publish_event
        self._pending: dict[RequestRef, PendingAppServerRequest] = {}
        self._outcomes: OrderedDict[
            RequestRef,
            _RecentRequestOutcome,
        ] = OrderedDict()
        add_request_handler = getattr(
            self._client,
            "add_server_request_handler",
            None,
        )
        add_reset_handler = getattr(
            self._client,
            "add_connection_reset_handler",
            None,
        )
        self.enabled = mapper is not None and all(
            callable(candidate)
            for candidate in (
                add_request_handler,
                add_reset_handler,
                getattr(
                    self._client,
                    "reply_to_transport_request",
                    None,
                ),
                getattr(
                    self._client,
                    "reply_error_to_transport_request",
                    None,
                ),
            )
        )
        if self.enabled:
            assert callable(add_request_handler)
            assert callable(add_reset_handler)
            add_request_handler(self.handle_server_request)
            add_reset_handler(self.handle_connection_reset)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def terminal_count(self) -> int:
        return len(self._outcomes)

    async def respond(
        self,
        operation: RespondRequest,
        completed_at: datetime,
    ) -> RequestResponded:
        if not self.enabled:
            raise NotImplementedError("this App Server client cannot answer native requests")
        request_ref = operation.request_ref
        outcome = self._get_outcome(request_ref)
        if outcome is not None and outcome.state == "responded":
            raise RequestDuplicateError("App Server request already has a response")
        if outcome is not None and outcome.state == "resolved":
            raise RequestResolvedError("App Server request is already resolved")
        if outcome is not None and outcome.state == "stale":
            raise RequestStaleError("App Server request belongs to an expired connection")
        pending = self._pending.get(request_ref)
        if pending is None:
            raise RequestStaleError("App Server request is not pending")
        if operation.thread_ref is not None and operation.thread_ref != pending.request.thread_ref:
            raise ValueError("request.respond belongs to a different Thread")
        current_epoch = getattr(self._client, "connection_epoch", None)
        if (
            isinstance(current_epoch, int)
            and current_epoch > 0
            and current_epoch != pending.connection_epoch
        ):
            await self._mark_stale(pending)
            raise RequestStaleError("App Server request belongs to an expired connection")
        response = build_appserver_response(pending, operation.response)
        reply = getattr(self._client, "reply_to_transport_request")
        try:
            result = reply(
                pending.transport_request_id,
                response,
                expected_connection_epoch=pending.connection_epoch,
            )
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            current_epoch = getattr(self._client, "connection_epoch", None)
            if isinstance(current_epoch, int) and current_epoch != pending.connection_epoch:
                await self._mark_stale(pending)
                raise RequestStaleError(
                    "App Server request became stale while responding"
                ) from error
            raise
        outcome = self._get_outcome(request_ref)
        if outcome is None:
            self._remember_outcome(pending, "responded")
        return RequestResponded(
            operation_id=operation.operation_id,
            completed_at=completed_at,
            request_ref=request_ref,
        )

    async def handle_server_request(self, message: dict) -> None:
        mapper = self._mapper
        if mapper is None:
            raise RuntimeError("native interactive requests are not configured")
        try:
            pending = mapper(self._application_ref, message)
        except UnsupportedAppServerRequest as error:
            await self._reject(message, error, code=-32601)
            return
        except ValueError as error:
            await self._reject(message, error, code=-32602)
            return
        request_ref = pending.request.request_ref
        self._pending[request_ref] = pending
        self._outcomes.pop(request_ref, None)
        self._publish(
            pending.request.thread_ref,
            pending.request.turn_id,
            AgentEventType.REQUEST_OPENED,
            event_id=(
                f"{self._application_ref.application_instance_id}:request:"
                f"{request_ref.native_request_id}:opened"
            ),
            request=pending.request,
        )

    async def handle_connection_reset(self, connection_epoch: int) -> None:
        active = tuple(
            request
            for request in self._pending.values()
            if request.connection_epoch == connection_epoch
        )
        responded = tuple(
            outcome.pending
            for outcome in self._outcomes.values()
            if outcome.state == "responded" and outcome.pending.connection_epoch == connection_epoch
        )
        for request in (*active, *responded):
            await self._mark_stale(request)

    async def handle_resolution_notification(
        self,
        params: Mapping[str, object],
    ) -> None:
        transport_request_id = params.get("requestId")
        if isinstance(transport_request_id, bool) or not isinstance(
            transport_request_id,
            (str, int),
        ):
            return
        notification_epoch = params.get("_connection_epoch")
        if isinstance(notification_epoch, bool) or not isinstance(
            notification_epoch,
            int,
        ):
            notification_epoch = None
        candidates = tuple(
            pending
            for pending in (
                *self._pending.values(),
                *(
                    outcome.pending
                    for outcome in self._outcomes.values()
                    if outcome.state == "responded"
                ),
            )
            if pending.transport_request_id == transport_request_id
            and (notification_epoch is None or pending.connection_epoch == notification_epoch)
        )
        pending = candidates[0] if len(candidates) == 1 else None
        if pending is not None:
            request_ref = pending.request.request_ref
            thread_ref = pending.request.thread_ref
            turn_id = pending.request.turn_id
        else:
            if notification_epoch is None or notification_epoch < 1:
                logger.warning(
                    "Ignoring unscoped App Server request resolution for %r",
                    transport_request_id,
                )
                return
            request_ref = derive_appserver_request_ref(
                self._application_ref,
                connection_epoch=notification_epoch,
                transport_request_id=transport_request_id,
            )
            thread_id = str(params.get("threadId") or "")
            if not thread_id:
                logger.warning(
                    "Ignoring App Server request resolution without pending or Thread scope for %s",
                    request_ref,
                )
                return
            thread_ref = ThreadRef(
                application_instance_id=(self._application_ref.application_instance_id),
                native_thread_id=thread_id,
            )
            turn_id = str(params.get("turnId") or "") or None
        if pending is not None:
            self._remember_outcome(pending, "resolved")
        resolution = RequestResolution(
            request_ref=request_ref,
            status=RequestResolutionStatus.RESOLVED,
            resolved_at=datetime.now(UTC),
        )
        self._publish(
            thread_ref,
            turn_id,
            AgentEventType.REQUEST_RESOLVED,
            event_id=(
                f"{self._application_ref.application_instance_id}:request:"
                f"{request_ref.native_request_id}:resolved"
            ),
            resolution=resolution,
        )

    async def _reject(
        self,
        message: Mapping[str, object],
        error: Exception,
        *,
        code: int,
    ) -> None:
        params = message.get("params")
        if not isinstance(params, Mapping):
            raise error
        request_id = params.get("_transport_request_id")
        epoch = params.get("_connection_epoch")
        if isinstance(request_id, bool) or not isinstance(
            request_id,
            (str, int),
        ):
            raise error
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise error
        reply_error = getattr(
            self._client,
            "reply_error_to_transport_request",
        )
        result = reply_error(
            request_id,
            code=code,
            message=str(error),
            expected_connection_epoch=epoch,
        )
        if inspect.isawaitable(result):
            await result

    async def _mark_stale(
        self,
        pending: PendingAppServerRequest,
    ) -> None:
        request_ref = pending.request.request_ref
        self._remember_outcome(pending, "stale")
        resolution = RequestResolution(
            request_ref=request_ref,
            status=RequestResolutionStatus.STALE,
            resolved_at=datetime.now(UTC),
        )
        self._publish(
            pending.request.thread_ref,
            pending.request.turn_id,
            AgentEventType.REQUEST_RESOLVED,
            event_id=(
                f"{self._application_ref.application_instance_id}:request:"
                f"{request_ref.native_request_id}:stale"
            ),
            resolution=resolution,
        )

    def _get_outcome(
        self,
        request_ref: RequestRef,
    ) -> _RecentRequestOutcome | None:
        outcome = self._outcomes.get(request_ref)
        if outcome is not None:
            self._outcomes.move_to_end(request_ref)
        return outcome

    def _remember_outcome(
        self,
        pending: PendingAppServerRequest,
        state: str,
    ) -> None:
        request_ref = pending.request.request_ref
        self._pending.pop(request_ref, None)
        self._outcomes.pop(request_ref, None)
        self._outcomes[request_ref] = _RecentRequestOutcome(
            state=state,
            pending=pending,
        )
        while len(self._outcomes) > _TERMINAL_REQUEST_CACHE_LIMIT:
            _evicted_ref, evicted = self._outcomes.popitem(last=False)
            if evicted.state == "responded":
                self._publish_retention_stale(evicted.pending)

    def _publish_retention_stale(
        self,
        pending: PendingAppServerRequest,
    ) -> None:
        request_ref = pending.request.request_ref
        resolution = RequestResolution(
            request_ref=request_ref,
            status=RequestResolutionStatus.STALE,
            resolved_at=datetime.now(UTC),
        )
        self._publish(
            pending.request.thread_ref,
            pending.request.turn_id,
            AgentEventType.REQUEST_RESOLVED,
            event_id=(
                f"{self._application_ref.application_instance_id}:request:"
                f"{request_ref.native_request_id}:retention-stale"
            ),
            resolution=resolution,
        )

    def _publish(
        self,
        thread_ref: ThreadRef,
        turn_id: str | None,
        event_type: AgentEventType,
        *,
        event_id: str,
        request=None,
        resolution: RequestResolution | None = None,
    ) -> None:
        event = AgentEvent(
            event_id=event_id,
            application_instance_id=(self._application_ref.application_instance_id),
            type=event_type,
            data={},
            created_at=datetime.now(UTC),
            thread_ref=thread_ref,
            turn_id=turn_id,
            request=request,
            request_resolution=resolution,
        )
        self._publish_event(thread_ref.native_thread_id, event)

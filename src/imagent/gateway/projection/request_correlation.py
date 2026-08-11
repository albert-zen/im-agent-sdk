"""Canonical Turn and interactive-request correlation runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from ...applications.capabilities import SupportLevel
from ...applications.contract import (
    AcceptedTurn,
    AgentApplicationAdapter,
    ApplicationInputDispatch,
    InputDisposition,
    ThreadRef,
    TurnRef,
    TurnReplyCorrelationPolicy,
)
from ...applications.events import AgentEvent, AgentEventType
from ...applications.operations import (
    ApplicationOperationFailed,
    ApplicationOperationResult,
    RequestResponded,
    RespondRequest,
    _RuntimeApplicationOperation,
)
from ...applications.requests import (
    InteractiveRequest,
    RequestDuplicateError,
    RequestRef,
    RequestResolutionStatus,
    RequestResolvedError,
    RequestResponse,
    RequestStaleError,
    derive_request_response_shape,
    validate_interactive_request,
    validate_request_ref,
    validate_request_response,
    validate_request_response_admission,
)
from ...interaction.controllers.request_presentation import RequestPresenter
from ...interaction.messages import ConversationRef, OutboundMessage
from ...interaction.operations import (
    ContractError,
    ContractViolation,
    OperationErrorCode,
    _MappedOperationError,
)
from ..concurrency import KeyedLockRegistry
from ..persistence.repository_contracts import (
    IdempotencyClaimStatus,
    ProjectionRouteRepository,
    RequestCorrelationConflict,
    RequestCorrelationRepository,
)
from ..persistence.state_contracts import (
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
    validate_request_route_correlation,
)
from ..routing.operations import GatewayOperationType as _GatewayOperationType
from ..routing.operations import (
    _complete_gateway_union,
    _GatewayOperation,
    _GatewayOperationSucceeded,
)

logger = logging.getLogger(__name__)

ActiveRoutes = Callable[
    [ThreadRef | None],
    Awaitable[tuple[ThreadProjectionRoute, ...]],
]
CancelRequest = Callable[[RequestRef], Awaitable[None]]
DeliverRequest = Callable[
    [tuple[ThreadProjectionRoute, ...], InteractiveRequest],
    Awaitable[None],
]
DeliverRequestOutbound = Callable[
    [OutboundMessage],
    Awaitable[IdempotencyClaimStatus],
]
ExecuteApplication = Callable[
    [_RuntimeApplicationOperation],
    Awaitable[ApplicationOperationResult],
]


@dataclass(frozen=True, slots=True, kw_only=True)
class RespondToRequest(_GatewayOperation):
    request_ref: RequestRef
    response: RequestResponse
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.CONVERSATION_RESPOND_REQUEST,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestResponseRouted(_GatewayOperationSucceeded):
    request_ref: RequestRef
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.CONVERSATION_RESPOND_REQUEST,
    )


class _RequestResponseRejected(_MappedOperationError):
    """Carry an exact owner-selected contract error to aggregate dispatch."""

    def __init__(self, error: ContractError) -> None:
        super().__init__(error.message)
        self.error = error
        try:
            self.operation_error_code = OperationErrorCode(error.code)
        except ValueError:
            self.operation_error_code = OperationErrorCode.ADAPTER_FAILURE


def _validate_respond_operation(operation: RespondToRequest) -> None:
    validate_request_ref(operation.request_ref)
    validate_request_response_admission(operation.response)


def _validate_respond_operation_result(
    operation: RespondToRequest,
    result: object,
) -> None:
    if not isinstance(result, RequestResponseRouted):
        raise ContractViolation("conversation.respond_request must return RequestResponseRouted")
    if result.request_ref != operation.request_ref:
        raise ContractViolation("Gateway response routed a different request")


def derive_turn_reply_correlation_id(
    turn_ref: TurnRef,
) -> str:
    thread_ref = turn_ref.thread_ref
    identity = json.dumps(
        [
            thread_ref.project_ref.application_instance_id,
            thread_ref.project_ref.project_id,
            thread_ref.thread_id,
            turn_ref.turn_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:turn-reply:sha256:{digest}"


def derive_request_correlation_id(
    request_ref: RequestRef,
    conversation_ref: ConversationRef,
) -> str:
    identity = json.dumps(
        [
            request_ref.application_ref.application_instance_id,
            request_ref.native_request_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:request-route:sha256:{digest}"


def derive_request_delivery_id(
    request_ref: RequestRef,
    conversation_ref: ConversationRef,
) -> str:
    identity = json.dumps(
        [
            request_ref.application_ref.application_instance_id,
            request_ref.native_request_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:request-delivery:sha256:{digest}"


class InteractiveRequestProjection:
    """Own all Turn/request correlation decisions and persistence orchestration."""

    def __init__(
        self,
        *,
        applications: Mapping[str, AgentApplicationAdapter],
        projections: ProjectionRouteRepository,
        correlations: RequestCorrelationRepository,
        request_presenter: RequestPresenter | None,
        execute_application: ExecuteApplication,
        active_routes: ActiveRoutes,
        deliver_request_outbound: DeliverRequestOutbound,
    ) -> None:
        self._applications = applications
        self._projections = projections
        self._correlations = correlations
        self._request_presenter = request_presenter
        self._execute_application = execute_application
        self._active_routes = active_routes
        self._deliver_request_outbound = deliver_request_outbound
        self._request_locks = KeyedLockRegistry()

    async def cleanup_correlations(
        self,
        *,
        turn_older_than: datetime,
        request_older_than: datetime,
    ) -> None:
        await self._projections.delete_turn_reply_correlations(older_than=turn_older_than)
        await self._correlations.delete_request_correlations(older_than=request_older_than)

    async def authorize_input_dispatch(
        self,
        dispatch: ApplicationInputDispatch,
        *,
        thread_ref: ThreadRef,
        client_message_id: str,
    ) -> None:
        if dispatch.thread_ref != thread_ref:
            raise ValueError("Application input dispatch belongs to another Thread")
        if dispatch.client_message_id != client_message_id:
            raise ValueError("Application input dispatch client_message_id does not match input")
        if dispatch.disposition is InputDisposition.STARTED:
            if (
                dispatch.correlation_policy is not TurnReplyCorrelationPolicy.CREATE_NEW
                or dispatch.expected_turn_ref is not None
            ):
                raise ValueError("started input must create a new Turn correlation")
            return
        if dispatch.disposition is InputDisposition.STEERED:
            if (
                dispatch.correlation_policy is not TurnReplyCorrelationPolicy.PRESERVE_EXISTING
                or dispatch.expected_turn_ref is None
                or dispatch.expected_turn_ref.thread_ref != thread_ref
            ):
                raise ValueError("steered input must preserve an expected Turn correlation")
            existing = await self._projections.get_turn_reply_correlation(
                thread_ref,
                dispatch.expected_turn_ref.turn_id,
            )
            if existing is None:
                raise ValueError("cannot steer a Turn without an existing reply correlation")
            return
        raise ValueError("unknown Application input disposition")

    async def correlate_accepted_turn(
        self,
        accepted: AcceptedTurn,
        dispatch: ApplicationInputDispatch,
        *,
        thread_ref: ThreadRef,
        client_message_id: str,
        conversation_ref: ConversationRef,
        reply_to_message_id: str,
    ) -> None:
        if accepted.turn_ref.thread_ref != thread_ref:
            raise ValueError("AcceptedTurn belongs to a different Thread")
        if accepted.client_message_id != client_message_id:
            raise ValueError("AcceptedTurn client_message_id does not match input")
        if (
            accepted.disposition is not dispatch.disposition
            or accepted.correlation_policy is not dispatch.correlation_policy
        ):
            raise RuntimeError("AcceptedTurn disposition does not match the authorized dispatch")
        if accepted.disposition is InputDisposition.STARTED:
            await self._projections.put_turn_reply_correlation(
                TurnReplyCorrelation(
                    correlation_id=derive_turn_reply_correlation_id(
                        accepted.turn_ref,
                    ),
                    turn_ref=accepted.turn_ref,
                    client_message_id=accepted.client_message_id,
                    conversation_ref=conversation_ref,
                    reply_to_message_id=reply_to_message_id,
                    created_at=datetime.now(UTC),
                )
            )
            return
        expected_turn_ref = dispatch.expected_turn_ref
        if accepted.turn_ref != expected_turn_ref:
            raise RuntimeError("native steer accepted a different Turn than was authorized")
        preserved = await self._projections.get_turn_reply_correlation(
            thread_ref,
            accepted.turn_ref.turn_id,
        )
        if preserved is None:
            raise RuntimeError("authorized steer reply correlation disappeared after dispatch")

    async def reply_to_message_id(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
        conversation_ref: ConversationRef,
    ) -> str | None:
        correlation = await self._projections.get_turn_reply_correlation(
            thread_ref,
            turn_id,
        )
        if correlation is None or correlation.conversation_ref != conversation_ref:
            return None
        return correlation.reply_to_message_id

    async def delete_terminal_turn(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> None:
        await self._projections.delete_turn_reply_correlation(thread_ref, turn_id)

    async def delete_destination(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef,
    ) -> None:
        await self._projections.delete_turn_reply_correlations(
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
        )
        await self._correlations.delete_request_correlations(
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
        )

    async def delete_thread(self, thread_ref: ThreadRef) -> None:
        await self._projections.delete_turn_reply_correlations(thread_ref=thread_ref)
        await self._correlations.delete_request_correlations(thread_ref=thread_ref)

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
        *,
        deliver_request: DeliverRequest,
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
                self._validate_snapshot_request(application, request)
                pending_refs.add(request.request_ref)
                await deliver_request(
                    await self._active_routes(request.turn_ref.thread_ref),
                    request,
                )
        for request_ref in restart_open_refs - pending_refs:
            await self._mark_stale(request_ref)

    async def reconcile_application_after_event_gap(
        self,
        application: AgentApplicationAdapter,
        thread_ref: ThreadRef,
        *,
        deliver_request: DeliverRequest,
    ) -> bool:
        """Reconcile pending truth or report truthful request recovery degradation."""

        runtime = application.summary.capabilities.runtime
        if runtime.interactive_requests is SupportLevel.UNSUPPORTED:
            return False
        if runtime.pending_request_snapshot is not SupportLevel.NATIVE:
            return True
        application_ref = application.summary.ref
        open_refs = {
            correlation.request_ref
            for correlation in await self._correlations.list_request_correlations()
            if correlation.request_ref.application_ref == application_ref
            and correlation.turn_ref.thread_ref == thread_ref
            and correlation.state is RequestRouteState.OPEN
        }
        pending_refs: set[RequestRef] = set()
        for request in await application.list_pending_requests():
            self._validate_snapshot_request(application, request)
            if request.turn_ref.thread_ref != thread_ref:
                continue
            pending_refs.add(request.request_ref)
            await deliver_request(
                await self._active_routes(request.turn_ref.thread_ref),
                request,
            )
        for request_ref in open_refs - pending_refs:
            await self._mark_stale(request_ref)
        return False

    async def handle_event(
        self,
        event: AgentEvent,
        *,
        deliver_request: DeliverRequest,
        cancel_request: CancelRequest,
    ) -> None:
        resolution = event.request_resolution
        if event.type is AgentEventType.REQUEST_RESOLVED and resolution is not None:
            await cancel_request(resolution.request_ref)
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
            await deliver_request(
                await self._active_routes(event.thread_ref),
                event.request,
            )

    async def deliver_request_once(
        self,
        route: ThreadProjectionRoute,
        request: InteractiveRequest,
    ) -> None:
        """Present one routed request and persist authority after accepted delivery."""

        if _request_expired(request):
            return
        presenter = self._request_presenter
        if presenter is None:
            raise RuntimeError("interactive request presenter is not configured")
        delivery_id = derive_request_delivery_id(
            request.request_ref,
            route.conversation_ref,
        )
        presentation = presenter.present_request(
            request,
            conversation_ref=route.conversation_ref,
            delivery_id=delivery_id,
            reply_to_message_id=await self.reply_to_message_id(
                request.turn_ref.thread_ref,
                request.turn_ref.turn_id,
                route.conversation_ref,
            ),
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
        current = next(
            (
                candidate
                for candidate in await self._active_routes(route.thread_ref)
                if candidate.route_id == route.route_id
            ),
            None,
        )
        if current is None:
            return
        now = datetime.now(UTC)
        await self._correlations.put_request_correlation(
            RequestRouteCorrelation(
                correlation_id=derive_request_correlation_id(
                    request.request_ref,
                    current.conversation_ref,
                ),
                request_ref=request.request_ref,
                turn_ref=request.turn_ref,
                conversation_ref=current.conversation_ref,
                delivery_id=delivery_id,
                response_shape=derive_request_response_shape(request),
                state=RequestRouteState.OPEN,
                created_at=now,
                updated_at=now,
                expires_at=request.expires_at,
            )
        )

    async def route_response(
        self,
        operation: RespondToRequest,
        *,
        completed_at: datetime,
    ) -> RequestResponseRouted:
        async with self._request_locks.hold(operation.request_ref):
            return await self._respond_to_request(
                operation,
                completed_at=completed_at,
            )

    async def authorize_response(self, operation: RespondToRequest) -> None:
        """Validate delivered-destination authority before a native effect fence."""

        async with self._request_locks.hold(operation.request_ref):
            await self._authorized_response_destination(operation)

    async def _respond_to_request(
        self,
        operation: RespondToRequest,
        *,
        completed_at: datetime,
    ) -> RequestResponseRouted:
        destination = await self._authorized_response_destination(operation)
        application = self._application(
            operation.request_ref.application_ref.application_instance_id
        )
        native = await self._execute_application(
            RespondRequest(
                operation_id=f"{operation.operation_id}:request.respond",
                application_ref=application.summary.ref,
                request_ref=operation.request_ref,
                response=operation.response,
                turn_ref=destination.turn_ref,
                created_at=operation.created_at,
            )
        )
        if isinstance(native, ApplicationOperationFailed):
            await self._converge_native_request_failure(operation, native)
            raise _RequestResponseRejected(native.error)
        if not isinstance(native, RequestResponded):
            raise RuntimeError("request.respond returned an incompatible result")
        try:
            await self._transition_request_state(
                operation.request_ref,
                state=RequestRouteState.RESPONDED,
                expected_states=(RequestRouteState.OPEN,),
                updated_at=completed_at,
            )
        except RequestCorrelationConflict:
            current = await self._correlations.list_request_correlations(
                request_ref=operation.request_ref
            )
            if not current or any(
                correlation.state
                not in {
                    RequestRouteState.RESPONDED,
                    RequestRouteState.RESOLVED,
                }
                for correlation in current
            ):
                raise
        return RequestResponseRouted(
            operation_id=operation.operation_id,
            request_ref=operation.request_ref,
            completed_at=completed_at,
        )

    async def _authorized_response_destination(
        self,
        operation: RespondToRequest,
    ) -> RequestRouteCorrelation:
        correlations = await self._correlations.list_request_correlations(
            request_ref=operation.request_ref
        )
        if not correlations:
            raise RequestStaleError("request is unknown, expired, or no longer answerable")
        destination = next(
            (
                correlation
                for correlation in correlations
                if correlation.conversation_ref == operation.conversation_ref
            ),
            None,
        )
        if destination is None:
            raise _RequestResponseRejected(
                ContractError(
                    code=OperationErrorCode.UNAUTHORIZED_DESTINATION.value,
                    message="this Conversation did not receive the request",
                )
            )
        if destination.state is RequestRouteState.RESPONDED:
            raise RequestDuplicateError("request already has a submitted response")
        if destination.state is RequestRouteState.RESOLVED:
            raise RequestResolvedError("request is already resolved")
        if destination.state is RequestRouteState.STALE:
            raise RequestStaleError("request response handle is stale")
        now = datetime.now(UTC)
        if destination.expires_at is not None and destination.expires_at <= now:
            await self._transition_request_state(
                operation.request_ref,
                state=RequestRouteState.STALE,
                expected_states=(RequestRouteState.OPEN,),
                updated_at=now,
            )
            raise RequestStaleError("request has expired")
        validate_request_response(operation.response, destination.response_shape)
        return destination

    async def _converge_native_request_failure(
        self,
        operation: RespondToRequest,
        result: ApplicationOperationFailed,
    ) -> None:
        target = {
            OperationErrorCode.REQUEST_DUPLICATE.value: RequestRouteState.RESPONDED,
            OperationErrorCode.REQUEST_RESOLVED.value: RequestRouteState.RESOLVED,
            OperationErrorCode.REQUEST_STALE.value: RequestRouteState.STALE,
        }.get(result.error.code)
        if target is None:
            return
        try:
            await self._transition_request_state(
                operation.request_ref,
                state=target,
                expected_states=(RequestRouteState.OPEN,),
                updated_at=result.completed_at,
            )
        except (KeyError, RequestCorrelationConflict):
            pass

    async def _transition_request_state(
        self,
        request_ref: RequestRef,
        *,
        state: RequestRouteState,
        expected_states: tuple[RequestRouteState, ...],
        updated_at: datetime,
    ) -> tuple[RequestRouteCorrelation, ...]:
        return await self._correlations.transition_request_correlations(
            request_ref,
            expected_states=expected_states,
            state=state,
            updated_at=updated_at,
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

    def _validate_snapshot_request(
        self,
        application: AgentApplicationAdapter,
        request: InteractiveRequest,
    ) -> None:
        validate_interactive_request(request)
        if request.request_ref.application_ref != application.summary.ref:
            raise ValueError("pending request snapshot belongs to a different application")

    def _application(self, application_instance_id: str) -> AgentApplicationAdapter:
        try:
            return self._applications[application_instance_id]
        except KeyError as error:
            raise KeyError(
                f"Agent application is not registered: {application_instance_id}"
            ) from error


def _request_expired(request: InteractiveRequest) -> bool:
    return request.expires_at is not None and request.expires_at <= datetime.now(UTC)


def _merge_correlation(
    existing: RequestRouteCorrelation | None,
    replacement: RequestRouteCorrelation,
    *,
    request_correlations: tuple[RequestRouteCorrelation, ...],
) -> RequestRouteCorrelation:
    if existing is None:
        terminal = max(
            (
                correlation
                for correlation in request_correlations
                if correlation.state is not RequestRouteState.OPEN
            ),
            key=lambda correlation: (
                _REQUEST_STATE_PRECEDENCE[correlation.state],
                correlation.updated_at,
            ),
            default=None,
        )
        if terminal is None:
            return replacement
        return replace(
            replacement,
            state=terminal.state,
            updated_at=max(replacement.updated_at, terminal.updated_at),
        )
    if (
        existing.correlation_id != replacement.correlation_id
        or existing.request_ref != replacement.request_ref
        or existing.turn_ref != replacement.turn_ref
        or existing.conversation_ref != replacement.conversation_ref
        or existing.response_shape != replacement.response_shape
    ):
        raise RequestCorrelationConflict(
            f"request correlation identity changed: {replacement.correlation_id}"
        )
    if existing.state in {
        RequestRouteState.RESPONDED,
        RequestRouteState.RESOLVED,
        RequestRouteState.STALE,
    }:
        return existing
    return replacement


_REQUEST_STATE_PRECEDENCE = {
    RequestRouteState.OPEN: 0,
    RequestRouteState.RESPONDED: 1,
    RequestRouteState.STALE: 2,
    RequestRouteState.RESOLVED: 3,
}


def _transition_correlations(
    correlations: tuple[RequestRouteCorrelation, ...],
    *,
    expected_states: tuple[RequestRouteState, ...],
    state: RequestRouteState,
    updated_at: datetime,
) -> tuple[RequestRouteCorrelation, ...]:
    if not correlations:
        raise KeyError("request correlation does not exist")
    if all(correlation.state is state for correlation in correlations):
        return correlations
    expected = set(expected_states)
    if any(correlation.state not in expected for correlation in correlations):
        raise RequestCorrelationConflict("request correlation state changed")
    target_precedence = _REQUEST_STATE_PRECEDENCE[state]
    if any(
        target_precedence < _REQUEST_STATE_PRECEDENCE[correlation.state]
        for correlation in correlations
    ):
        raise RequestCorrelationConflict("request correlation state cannot regress")
    transitioned = tuple(
        replace(correlation, state=state, updated_at=updated_at) for correlation in correlations
    )
    for correlation in transitioned:
        validate_request_route_correlation(correlation)
    return transitioned


def _reject_conflicting_endpoint(
    correlations,
    replacement: RequestRouteCorrelation,
) -> None:
    for existing in correlations:
        if (
            existing.request_ref == replacement.request_ref
            and existing.conversation_ref == replacement.conversation_ref
            and existing.correlation_id != replacement.correlation_id
        ):
            raise RequestCorrelationConflict(
                "request destination belongs to a different correlation"
            )


def _select_correlations(
    correlations: tuple[RequestRouteCorrelation, ...],
    *,
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
) -> tuple[RequestRouteCorrelation, ...]:
    return tuple(
        correlation
        for correlation in correlations
        if _matches(
            correlation,
            request_ref=request_ref,
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
            older_than=None,
        )
    )


def _matches(
    correlation: RequestRouteCorrelation,
    *,
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
    older_than: datetime | None,
) -> bool:
    return (
        (request_ref is None or correlation.request_ref == request_ref)
        and (thread_ref is None or correlation.turn_ref.thread_ref == thread_ref)
        and (conversation_ref is None or correlation.conversation_ref == conversation_ref)
        and (older_than is None or correlation.updated_at < older_than)
    )


def _require_delete_selector(
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
    older_than: datetime | None,
) -> None:
    if (
        request_ref is None
        and thread_ref is None
        and conversation_ref is None
        and older_than is None
    ):
        raise ValueError("request correlation deletion requires at least one selector")


_complete_gateway_union()


__all__ = [
    "InteractiveRequestProjection",
    "RequestResponseRouted",
    "RespondToRequest",
]

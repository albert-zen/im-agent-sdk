"""Typed Gateway repository Ports and conflict outcomes."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from ...applications.contract import ThreadRef
from ...applications.requests import RequestRef
from ...contracts import (
    ConversationBinding,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
)
from ...contracts.delivery import (
    DeliveryReservation,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
)
from ...interaction.messages import ConversationRef


class BindingConflict(RuntimeError):
    """An expected Conversation binding revision did not match."""


class IdempotencyClaimStatus(StrEnum):
    ACQUIRED = "acquired"
    ALREADY_COMPLETED = "already_completed"
    IN_FLIGHT = "in_flight"


class ProjectionCheckpointConflict(RuntimeError):
    """The stored route checkpoint no longer matches the caller's expectation."""


class ProjectionRouteConflict(RuntimeError):
    """A stable route ID was reused for different immutable endpoints."""


class RequestCorrelationConflict(RuntimeError):
    """A request route correlation changed outside the expected state."""


class TurnReplyCorrelationConflict(RuntimeError):
    """A Thread/Turn correlation was reused for a different IM destination."""


class DeliverySubmissionConflict(RuntimeError):
    """A stable delivery ID was reused for a different immutable submission."""


class DeliverySubmissionCapacityError(RuntimeError):
    """A new process-local delivery identity exceeded its finite record bound."""


class BindingRepository(Protocol):
    async def get(
        self,
        conversation: ConversationRef,
    ) -> ConversationBinding | None: ...

    async def put(
        self,
        binding: ConversationBinding,
        expected_revision: int | None = None,
    ) -> ConversationBinding: ...

    async def delete(
        self,
        conversation: ConversationRef,
        expected_revision: int | None = None,
    ) -> None: ...


class ProjectionRouteRepository(Protocol):
    async def list_projection_routes(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[ThreadProjectionRoute, ...]: ...

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute: ...

    async def replace_thread_projection_routes(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute: ...

    async def advance_projection_checkpoint(
        self,
        route_id: str,
        *,
        expected_agent_item_id: str | None,
        agent_item_id: str,
        checkpointed_at: datetime,
    ) -> ThreadProjectionRoute: ...

    async def delete_projection_routes(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef | None = None,
    ) -> int: ...

    async def get_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> TurnReplyCorrelation | None: ...

    async def list_turn_reply_correlations(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[TurnReplyCorrelation, ...]: ...

    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation: ...

    async def delete_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> bool: ...

    async def delete_turn_reply_correlations(
        self,
        *,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int: ...


class IdempotencyRepository(Protocol):
    async def claim(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> IdempotencyClaimStatus: ...

    async def mark_side_effect_started(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None: ...

    async def refresh(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None: ...

    async def complete(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None: ...

    async def release(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None: ...


class DeliverySubmissionRepository(Protocol):
    async def get_delivery_submission(
        self,
        submission_id: str,
    ) -> DeliverySubmissionRecord | None: ...

    async def reserve_delivery_submission(
        self,
        record: DeliverySubmissionRecord,
    ) -> DeliveryReservation: ...

    async def update_delivery_destination(
        self,
        submission_id: str,
        destination_delivery_id: str,
        *,
        expected_state: DeliverySubmissionState,
        destination: DestinationDeliveryRecord,
    ) -> DeliverySubmissionRecord: ...


class RequestCorrelationRepository(Protocol):
    async def list_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
    ) -> tuple[RequestRouteCorrelation, ...]: ...

    async def put_request_correlation(
        self,
        correlation: RequestRouteCorrelation,
    ) -> RequestRouteCorrelation: ...

    async def transition_request_correlations(
        self,
        request_ref: RequestRef,
        *,
        expected_states: tuple[RequestRouteState, ...],
        state: RequestRouteState,
        updated_at: datetime,
    ) -> tuple[RequestRouteCorrelation, ...]: ...

    async def delete_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int: ...


__all__ = [
    "BindingConflict",
    "BindingRepository",
    "DeliverySubmissionCapacityError",
    "DeliverySubmissionConflict",
    "DeliverySubmissionRepository",
    "IdempotencyClaimStatus",
    "IdempotencyRepository",
    "ProjectionCheckpointConflict",
    "ProjectionRouteConflict",
    "ProjectionRouteRepository",
    "RequestCorrelationConflict",
    "RequestCorrelationRepository",
    "TurnReplyCorrelationConflict",
]

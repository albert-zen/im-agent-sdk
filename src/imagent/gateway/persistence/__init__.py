"""Gateway-owned bridge-state persistence contracts and implementations."""

from .idempotency import InMemoryIdempotencyRepository as InMemoryIdempotencyRepository
from .repository_contracts import (
    BindingConflict,
    BindingRepository,
    DeliverySubmissionCapacityError,
    DeliverySubmissionConflict,
    DeliverySubmissionRepository,
    IdempotencyClaimStatus,
    IdempotencyRepository,
    ProjectionCheckpointConflict,
    ProjectionRouteConflict,
    ProjectionRouteRepository,
    RequestCorrelationConflict,
    RequestCorrelationRepository,
    TurnReplyCorrelationConflict,
)

__all__ = [
    "BindingConflict",
    "BindingRepository",
    "DeliverySubmissionCapacityError",
    "DeliverySubmissionConflict",
    "DeliverySubmissionRepository",
    "IdempotencyClaimStatus",
    "IdempotencyRepository",
    "InMemoryIdempotencyRepository",
    "ProjectionCheckpointConflict",
    "ProjectionRouteConflict",
    "ProjectionRouteRepository",
    "RequestCorrelationConflict",
    "RequestCorrelationRepository",
    "TurnReplyCorrelationConflict",
]

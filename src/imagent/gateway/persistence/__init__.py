"""Gateway-owned bridge-state persistence contracts and implementations."""

from typing import TYPE_CHECKING

from .idempotency import InMemoryIdempotencyRepository as InMemoryIdempotencyRepository
from .repository_contracts import (
    BindingConflict,
    BindingRepository,
    DeliverySubmissionCapacityError,
    DeliverySubmissionConflict,
    DeliverySubmissionRepository,
    IdempotencyCapacityError,
    IdempotencyClaimStatus,
    IdempotencyRepository,
    ProjectionCheckpointConflict,
    ProjectionRouteConflict,
    ProjectionRouteRepository,
    RequestCorrelationConflict,
    RequestCorrelationRepository,
    TurnReplyCorrelationConflict,
)
from .state_contracts import (
    MAX_DELIVERY_SUBMISSION_DESTINATIONS,
    ConversationBinding,
    DeliveryReservation,
    DeliveryRouteSnapshot,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
    validate_binding,
    validate_delivery_route_snapshot,
    validate_delivery_submission_destination_count,
    validate_delivery_submission_record,
    validate_projection_route,
    validate_request_route_correlation,
    validate_turn_reply_correlation,
)
from .store import (
    EffectReceiptCapacityError,
    EffectReceiptConflict,
    GatewayNamespaceConflict,
    GatewayStore,
    GatewayStoreError,
    RuntimeLease,
    RuntimeLeaseUnavailable,
    StaleRuntimeFence,
    WorkspaceIdentityConflict,
)

if TYPE_CHECKING:
    from .memory_store import MemoryGatewayStore
    from .sqlite_store import SQLiteGatewayStore

__all__ = [
    "BindingConflict",
    "BindingRepository",
    "ConversationBinding",
    "DeliverySubmissionCapacityError",
    "DeliverySubmissionConflict",
    "DeliverySubmissionRepository",
    "DeliveryReservation",
    "DeliveryRouteSnapshot",
    "DeliverySubmissionRecord",
    "DeliverySubmissionState",
    "DestinationDeliveryRecord",
    "EffectReceiptCapacityError",
    "EffectReceiptConflict",
    "GatewayNamespaceConflict",
    "GatewayStore",
    "GatewayStoreError",
    "IdempotencyCapacityError",
    "IdempotencyClaimStatus",
    "IdempotencyRepository",
    "InMemoryIdempotencyRepository",
    "MemoryGatewayStore",
    "MAX_DELIVERY_SUBMISSION_DESTINATIONS",
    "ProjectionCheckpointConflict",
    "ProjectionRouteConflict",
    "ProjectionRouteRepository",
    "RequestCorrelationConflict",
    "RequestCorrelationRepository",
    "RequestRouteCorrelation",
    "RequestRouteState",
    "RuntimeLease",
    "RuntimeLeaseUnavailable",
    "SQLiteGatewayStore",
    "StaleRuntimeFence",
    "ThreadProjectionRoute",
    "TurnReplyCorrelationConflict",
    "TurnReplyCorrelation",
    "WorkspaceIdentityConflict",
    "validate_binding",
    "validate_delivery_route_snapshot",
    "validate_delivery_submission_destination_count",
    "validate_delivery_submission_record",
    "validate_projection_route",
    "validate_request_route_correlation",
    "validate_turn_reply_correlation",
]


def __getattr__(name: str) -> object:
    if name == "MemoryGatewayStore":
        from .memory_store import MemoryGatewayStore

        globals()[name] = MemoryGatewayStore
        return MemoryGatewayStore
    if name == "SQLiteGatewayStore":
        from .sqlite_store import SQLiteGatewayStore

        globals()[name] = SQLiteGatewayStore
        return SQLiteGatewayStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

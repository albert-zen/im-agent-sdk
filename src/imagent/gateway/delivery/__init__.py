"""Gateway delivery contracts and implementations."""

from .coordination import DeliveryCoordinator, DeliveryCoordinatorConfig, DeliveryHandle
from .outcome_observation import (
    DeliveryOutcome,
    DeliveryOutcomeContext,
    DeliveryOutcomeErrorCode,
    DeliveryOutcomeObserver,
)
from .planning import (
    DeliveryPlan,
    DeliveryPlanner,
    DeliveryPlanningError,
    PlannedDeliverySegment,
)
from .proactive_authorization import (
    DeliveryAuthorizer,
    DeliveryPrincipal,
    ScopedDeliveryAuthorizer,
    validate_delivery_principal,
)
from .submissions import (
    DeliverySubmissionOrigin,
    derive_delivery_payload_fingerprint,
    derive_delivery_submission_id,
    derive_delivery_target_fingerprint,
    derive_destination_delivery_id,
)

__all__ = [
    "DeliveryCoordinator",
    "DeliveryCoordinatorConfig",
    "DeliveryAuthorizer",
    "DeliveryHandle",
    "DeliveryOutcome",
    "DeliveryOutcomeContext",
    "DeliveryOutcomeErrorCode",
    "DeliveryOutcomeObserver",
    "DeliveryPlan",
    "DeliveryPlanner",
    "DeliveryPlanningError",
    "DeliveryPrincipal",
    "DeliverySubmissionOrigin",
    "PlannedDeliverySegment",
    "ScopedDeliveryAuthorizer",
    "derive_delivery_payload_fingerprint",
    "derive_delivery_submission_id",
    "derive_delivery_target_fingerprint",
    "derive_destination_delivery_id",
    "validate_delivery_principal",
]

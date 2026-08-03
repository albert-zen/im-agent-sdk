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
    "DeliveryHandle",
    "DeliveryOutcome",
    "DeliveryOutcomeContext",
    "DeliveryOutcomeErrorCode",
    "DeliveryOutcomeObserver",
    "DeliveryPlan",
    "DeliveryPlanner",
    "DeliveryPlanningError",
    "DeliverySubmissionOrigin",
    "PlannedDeliverySegment",
    "derive_delivery_payload_fingerprint",
    "derive_delivery_submission_id",
    "derive_delivery_target_fingerprint",
    "derive_destination_delivery_id",
]

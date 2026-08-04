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
from .proactive import (
    ConversationDeliveryTarget,
    DeliveryIntent,
    DeliveryTarget,
    DeliveryTargetKind,
    DestinationDeliveryResult,
    ProactiveDeliveryResult,
    ProactiveDeliveryService,
    ThreadRouteDeliveryTarget,
    validate_delivery_intent,
)
from .proactive_authorization import (
    DeliveryAuthorizer,
    DeliveryPrincipal,
    ScopedDeliveryAuthorizer,
    validate_delivery_principal,
)
from .proactive_ingress import ProactiveDeliveryJsonHandler
from .submissions import (
    DeliverySubmissionOrigin,
    derive_delivery_payload_fingerprint,
    derive_delivery_submission_id,
    derive_delivery_target_fingerprint,
    derive_destination_delivery_id,
)

__all__ = [
    "ConversationDeliveryTarget",
    "DeliveryCoordinator",
    "DeliveryCoordinatorConfig",
    "DeliveryAuthorizer",
    "DeliveryHandle",
    "DeliveryIntent",
    "DeliveryOutcome",
    "DeliveryOutcomeContext",
    "DeliveryOutcomeErrorCode",
    "DeliveryOutcomeObserver",
    "DeliveryPlan",
    "DeliveryPlanner",
    "DeliveryPlanningError",
    "DeliveryPrincipal",
    "DeliverySubmissionOrigin",
    "DeliveryTarget",
    "DeliveryTargetKind",
    "DestinationDeliveryResult",
    "PlannedDeliverySegment",
    "ProactiveDeliveryResult",
    "ProactiveDeliveryJsonHandler",
    "ProactiveDeliveryService",
    "ScopedDeliveryAuthorizer",
    "ThreadRouteDeliveryTarget",
    "derive_delivery_payload_fingerprint",
    "derive_delivery_submission_id",
    "derive_delivery_target_fingerprint",
    "derive_destination_delivery_id",
    "validate_delivery_intent",
    "validate_delivery_principal",
]

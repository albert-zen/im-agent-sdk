from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gateway.delivery.proactive_authorization import (
        DeliveryAuthorizer as DeliveryAuthorizer,
    )
    from .gateway.persistence.repository_contracts import (
        BindingRepository as BindingRepository,
    )
    from .gateway.persistence.repository_contracts import (
        DeliverySubmissionCapacityError as DeliverySubmissionCapacityError,
    )
    from .gateway.persistence.repository_contracts import (
        DeliverySubmissionConflict as DeliverySubmissionConflict,
    )
    from .gateway.persistence.repository_contracts import (
        DeliverySubmissionRepository as DeliverySubmissionRepository,
    )
    from .gateway.persistence.repository_contracts import (
        IdempotencyClaimStatus as IdempotencyClaimStatus,
    )
    from .gateway.persistence.repository_contracts import (
        IdempotencyRepository as IdempotencyRepository,
    )
    from .gateway.persistence.repository_contracts import (
        ProjectionCheckpointConflict as ProjectionCheckpointConflict,
    )
    from .gateway.persistence.repository_contracts import (
        ProjectionRouteConflict as ProjectionRouteConflict,
    )
    from .gateway.persistence.repository_contracts import (
        ProjectionRouteRepository as ProjectionRouteRepository,
    )
    from .gateway.persistence.repository_contracts import (
        RequestCorrelationConflict as RequestCorrelationConflict,
    )
    from .gateway.persistence.repository_contracts import (
        RequestCorrelationRepository as RequestCorrelationRepository,
    )
    from .gateway.persistence.repository_contracts import (
        TurnReplyCorrelationConflict as TurnReplyCorrelationConflict,
    )


def __getattr__(name: str) -> object:
    if name == "DeliveryAuthorizer":
        from .gateway.delivery.proactive_authorization import (
            DeliveryAuthorizer,
        )

        globals()[name] = DeliveryAuthorizer
        return DeliveryAuthorizer
    if name in {
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
    }:
        from .gateway.persistence.repository_contracts import (
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

        value = {
            "BindingRepository": BindingRepository,
            "DeliverySubmissionCapacityError": DeliverySubmissionCapacityError,
            "DeliverySubmissionConflict": DeliverySubmissionConflict,
            "DeliverySubmissionRepository": DeliverySubmissionRepository,
            "IdempotencyClaimStatus": IdempotencyClaimStatus,
            "IdempotencyRepository": IdempotencyRepository,
            "ProjectionCheckpointConflict": ProjectionCheckpointConflict,
            "ProjectionRouteConflict": ProjectionRouteConflict,
            "ProjectionRouteRepository": ProjectionRouteRepository,
            "RequestCorrelationConflict": RequestCorrelationConflict,
            "RequestCorrelationRepository": RequestCorrelationRepository,
            "TurnReplyCorrelationConflict": TurnReplyCorrelationConflict,
        }[name]
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

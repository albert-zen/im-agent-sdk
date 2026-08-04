from __future__ import annotations

from dataclasses import dataclass

from ..interaction.controllers import InboundController, RequestPresenter
from .delivery.outcome_observation import DeliveryOutcomeObserver
from .input import InboundContentTransformer
from .input.failure_presentation import InboundFailurePresenter
from .persistence.repository_contracts import (
    BindingRepository,
    DeliverySubmissionRepository,
    IdempotencyRepository,
    ProjectionRouteRepository,
    RequestCorrelationRepository,
)
from .presentation import OutboundPresentationPolicy


@dataclass(frozen=True, slots=True)
class GatewayRepositories:
    """Bridge-state repositories grouped by persistence ownership."""

    bindings: BindingRepository
    idempotency: IdempotencyRepository | None = None
    projections: ProjectionRouteRepository | None = None
    request_correlations: RequestCorrelationRepository | None = None
    delivery_submissions: DeliverySubmissionRepository | None = None


@dataclass(frozen=True, slots=True)
class GatewayLimits:
    """Bounded runtime capacities, recovery work, retries, and retention."""

    baseline_history_limit: int = 3
    recovery_history_page_size: int = 10
    recovery_max_pages: int = 5
    catchup_limit: int = 10
    projection_item_limit: int = 20
    request_delivery_max_pending: int = 256
    startup_buffer_max_pending: int = 256
    turn_acceptance_event_max_pending: int = 256
    subscription_retry_initial_seconds: float = 0.05
    subscription_retry_max_seconds: float = 2.0
    turn_correlation_retention_seconds: float = 7 * 24 * 60 * 60
    request_correlation_retention_seconds: float = 7 * 24 * 60 * 60
    inbound_content_transform_timeout_seconds: float = 30.0
    inbound_content_transform_max_items: int = 64
    inbound_content_transform_max_concurrency: int = 16
    inbound_failure_present_timeout_seconds: float = 30.0
    inbound_failure_present_max_items: int = 64
    inbound_failure_present_max_text_characters: int = 16_384
    inbound_failure_present_max_concurrency: int = 16
    outbound_presentation_timeout_seconds: float = 30.0
    outbound_presentation_max_items: int = 64
    outbound_presentation_max_text_characters: int = 16_384
    outbound_presentation_max_concurrency: int = 16
    delivery_outcome_observer_timeout_seconds: float = 30.0
    delivery_outcome_observer_max_items: int = 256
    delivery_outcome_observer_max_text_characters: int = 65_536
    delivery_outcome_observer_max_concurrency: int = 16
    delivery_submission_max_records: int = 4096
    conversation_serialization_max_active_keys: int = 4096
    idempotency_max_records: int = 4096

    def __post_init__(self) -> None:
        if (
            not isinstance(self.idempotency_max_records, int)
            or isinstance(self.idempotency_max_records, bool)
            or self.idempotency_max_records < 1
        ):
            raise ValueError("idempotency_max_records must be a positive integer")
        if (
            not isinstance(self.delivery_submission_max_records, int)
            or isinstance(self.delivery_submission_max_records, bool)
            or self.delivery_submission_max_records < 1
        ):
            raise ValueError("delivery_submission_max_records must be a positive integer")
        if (
            not isinstance(self.conversation_serialization_max_active_keys, int)
            or isinstance(self.conversation_serialization_max_active_keys, bool)
            or self.conversation_serialization_max_active_keys < 1
        ):
            raise ValueError(
                "conversation_serialization_max_active_keys must be a positive integer"
            )


@dataclass(frozen=True, slots=True)
class GatewayExtensions:
    """Optional typed Gateway-owned policy seams; never a service locator."""

    controller: InboundController | None = None
    request_presenter: RequestPresenter | None = None
    inbound_content_transformer: InboundContentTransformer | None = None
    inbound_failure_presenter: InboundFailurePresenter | None = None
    outbound_presentation: OutboundPresentationPolicy | None = None
    delivery_outcome_observer: DeliveryOutcomeObserver | None = None

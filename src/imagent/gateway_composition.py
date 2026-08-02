from __future__ import annotations

from dataclasses import dataclass

from .adapters import (
    BindingRepository,
    DeliverySubmissionRepository,
    IdempotencyRepository,
    ProjectionRouteRepository,
    RequestCorrelationRepository,
)
from .controllers import InboundController, RequestPresenter
from .inbound_content import InboundContentTransformer
from .inbound_failures import InboundFailurePresenter


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


@dataclass(frozen=True, slots=True)
class GatewayExtensions:
    """Optional typed Gateway-owned policy seams; never a service locator."""

    controller: InboundController | None = None
    request_presenter: RequestPresenter | None = None
    inbound_content_transformer: InboundContentTransformer | None = None
    inbound_failure_presenter: InboundFailurePresenter | None = None

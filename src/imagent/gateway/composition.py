from __future__ import annotations

import math
from dataclasses import dataclass

from ..interaction.controllers.contract import InboundController
from ..interaction.controllers.request_presentation import RequestPresenter
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
class _GatewayRuntimeDependencies:
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
    projection_max_active_threads: int = 4096
    lifecycle_owner_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            "baseline_history_limit",
            "recovery_history_page_size",
            "recovery_max_pages",
            "catchup_limit",
            "projection_item_limit",
            "request_delivery_max_pending",
            "startup_buffer_max_pending",
            "turn_acceptance_event_max_pending",
            "inbound_content_transform_max_items",
            "inbound_content_transform_max_concurrency",
            "inbound_failure_present_max_items",
            "inbound_failure_present_max_text_characters",
            "inbound_failure_present_max_concurrency",
            "outbound_presentation_max_items",
            "outbound_presentation_max_text_characters",
            "outbound_presentation_max_concurrency",
            "delivery_outcome_observer_max_items",
            "delivery_outcome_observer_max_text_characters",
            "delivery_outcome_observer_max_concurrency",
            "delivery_submission_max_records",
            "conversation_serialization_max_active_keys",
            "idempotency_max_records",
            "projection_max_active_threads",
        ):
            _require_positive_integer(getattr(self, name), name)
        for name in (
            "turn_correlation_retention_seconds",
            "request_correlation_retention_seconds",
            "inbound_content_transform_timeout_seconds",
            "inbound_failure_present_timeout_seconds",
            "outbound_presentation_timeout_seconds",
            "delivery_outcome_observer_timeout_seconds",
            "lifecycle_owner_timeout_seconds",
        ):
            _require_positive_finite_number(getattr(self, name), name)
        for name in (
            "subscription_retry_initial_seconds",
            "subscription_retry_max_seconds",
        ):
            _require_non_negative_finite_number(getattr(self, name), name)
        if self.subscription_retry_max_seconds < self.subscription_retry_initial_seconds:
            raise ValueError(
                "subscription_retry_max_seconds must be greater than or equal to "
                "subscription_retry_initial_seconds"
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


def _require_positive_integer(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _require_positive_finite_number(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive finite number")


def _require_non_negative_finite_number(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite non-negative number")

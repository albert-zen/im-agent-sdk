from __future__ import annotations

from ..applications.contract import validate_thread_ref
from ..applications.requests import (
    validate_request_ref as _validate_request_ref,
)
from ..applications.requests import (
    validate_request_response_shape as _validate_request_response_shape,
)
from ..interaction.operations import ContractViolation, require_identifier
from .model import RequestRouteCorrelation, RequestRouteState


def validate_request_route_correlation(
    correlation: RequestRouteCorrelation,
) -> None:
    require_identifier(correlation.correlation_id, "correlation_id")
    _validate_request_ref(correlation.request_ref)
    validate_thread_ref(correlation.thread_ref)
    if (
        correlation.thread_ref.application_instance_id
        != correlation.request_ref.application_ref.application_instance_id
    ):
        raise ContractViolation("request correlation belongs to a different application")
    require_identifier(correlation.turn_id, "turn_id")
    require_identifier(
        correlation.conversation_ref.channel_instance_id,
        "channel_instance_id",
    )
    require_identifier(
        correlation.conversation_ref.native_conversation_id,
        "native_conversation_id",
    )
    require_identifier(correlation.delivery_id, "delivery_id")
    _validate_request_response_shape(correlation.response_shape)
    if correlation.created_at.tzinfo is None or correlation.updated_at.tzinfo is None:
        raise ContractViolation("request correlation times must include a timezone")
    if correlation.updated_at < correlation.created_at:
        raise ContractViolation("request correlation update precedes creation")
    if correlation.expires_at is not None and correlation.expires_at.tzinfo is None:
        raise ContractViolation("request correlation expiry must include a timezone")
    if correlation.state not in RequestRouteState:
        raise ContractViolation("invalid request correlation state")

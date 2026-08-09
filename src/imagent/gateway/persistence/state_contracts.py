"""Passive, validated values for minimal Gateway bridge state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from ...applications.contract import (
    ApplicationRef,
    ProjectRef,
    ThreadRef,
    TurnRef,
    validate_thread_ref,
    validate_turn_ref,
)
from ...applications.requests import (
    RequestRef as _RequestRef,
)
from ...applications.requests import (
    RequestResponseShape as _RequestResponseShape,
)
from ...applications.requests import (
    validate_request_ref as _validate_request_ref,
)
from ...applications.requests import (
    validate_request_response_shape as _validate_request_response_shape,
)
from ...interaction.channels.contract import DeliveryReceipt, validate_delivery_receipt
from ...interaction.messages import ConversationRef
from ...interaction.operations import ContractViolation, require_identifier

if TYPE_CHECKING:
    from ...applications.capabilities import ApplicationCapabilities


class RequestRouteState(StrEnum):
    OPEN = "open"
    RESPONDED = "responded"
    RESOLVED = "resolved"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class RequestRouteCorrelation:
    correlation_id: str
    request_ref: _RequestRef
    turn_ref: TurnRef
    conversation_ref: ConversationRef
    delivery_id: str
    response_shape: _RequestResponseShape
    state: RequestRouteState
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConversationBinding:
    conversation_ref: ConversationRef
    application_ref: ApplicationRef | None = None
    project_ref: ProjectRef | None = None
    thread_ref: ThreadRef | None = None
    generation: int = 0
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ThreadProjectionRoute:
    route_id: str
    thread_ref: ThreadRef
    conversation_ref: ConversationRef
    reply_to_message_id: str | None = None
    checkpoint_agent_item_id: str | None = None
    checkpointed_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TurnReplyCorrelation:
    correlation_id: str
    turn_ref: TurnRef
    client_message_id: str
    conversation_ref: ConversationRef
    reply_to_message_id: str
    created_at: datetime


MAX_DELIVERY_SUBMISSION_DESTINATIONS = 64


class DeliverySubmissionState(StrEnum):
    IN_FLIGHT = "in_flight"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RETRYABLE = "retryable"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class DeliverySubmissionOrigin(StrEnum):
    EXTERNAL = "external"
    GATEWAY_INTERNAL = "gateway_internal"


@dataclass(frozen=True, slots=True)
class DeliveryRouteSnapshot:
    conversation_ref: ConversationRef
    thread_ref: ThreadRef | None = None
    route_id: str | None = None
    route_updated_at: datetime | None = None
    reply_to_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class DestinationDeliveryRecord:
    delivery_id: str
    snapshot: DeliveryRouteSnapshot
    state: DeliverySubmissionState
    updated_at: datetime
    receipt: DeliveryReceipt | None = None


@dataclass(frozen=True, slots=True)
class DeliverySubmissionRecord:
    submission_id: str
    delivery_id: str
    origin: DeliverySubmissionOrigin
    principal_id: str
    target_fingerprint: str
    payload_fingerprint: str
    destinations: tuple[DestinationDeliveryRecord, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DeliveryReservation:
    acquired: bool
    record: DeliverySubmissionRecord


def validate_binding(
    binding: ConversationBinding,
    capabilities: ApplicationCapabilities | None = None,
) -> None:
    require_identifier(binding.conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(binding.conversation_ref.native_conversation_id, "native_conversation_id")
    if binding.generation < 0:
        raise ContractViolation("binding generation cannot be negative")

    application_id = (
        binding.application_ref.application_instance_id if binding.application_ref else None
    )
    if binding.project_ref is not None:
        if application_id != binding.project_ref.application_instance_id:
            raise ContractViolation("binding project belongs to a different application")
    if binding.thread_ref is not None:
        validate_thread_ref(binding.thread_ref)
        if binding.application_ref is None or binding.project_ref is None:
            raise ContractViolation("binding Thread requires Application and Project")
        if application_id != binding.thread_ref.project_ref.application_instance_id:
            raise ContractViolation("binding thread belongs to a different application")
    if binding.thread_ref is not None and binding.project_ref != binding.thread_ref.project_ref:
        raise ContractViolation("binding thread belongs to a different project")
    del capabilities


def validate_projection_route(route: ThreadProjectionRoute) -> None:
    require_identifier(route.route_id, "route_id")
    validate_thread_ref(route.thread_ref)
    require_identifier(
        route.conversation_ref.channel_instance_id,
        "channel_instance_id",
    )
    require_identifier(
        route.conversation_ref.native_conversation_id,
        "native_conversation_id",
    )
    if route.reply_to_message_id is not None:
        require_identifier(route.reply_to_message_id, "reply_to_message_id")
    if route.checkpoint_agent_item_id is not None:
        require_identifier(
            route.checkpoint_agent_item_id,
            "checkpoint_agent_item_id",
        )
    if (route.checkpoint_agent_item_id is None) != (route.checkpointed_at is None):
        raise ContractViolation(
            "checkpoint_agent_item_id and checkpointed_at must be present together"
        )


def validate_turn_reply_correlation(correlation: TurnReplyCorrelation) -> None:
    require_identifier(correlation.correlation_id, "correlation_id")
    validate_turn_ref(correlation.turn_ref)
    require_identifier(correlation.client_message_id, "client_message_id")
    require_identifier(
        correlation.conversation_ref.channel_instance_id,
        "channel_instance_id",
    )
    require_identifier(
        correlation.conversation_ref.native_conversation_id,
        "native_conversation_id",
    )
    require_identifier(correlation.reply_to_message_id, "reply_to_message_id")


def validate_request_route_correlation(
    correlation: RequestRouteCorrelation,
) -> None:
    require_identifier(correlation.correlation_id, "correlation_id")
    _validate_request_ref(correlation.request_ref)
    validate_turn_ref(correlation.turn_ref)
    if (
        correlation.turn_ref.thread_ref.project_ref.application_instance_id
        != correlation.request_ref.application_ref.application_instance_id
    ):
        raise ContractViolation("request correlation belongs to a different application")
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


def validate_delivery_route_snapshot(snapshot: DeliveryRouteSnapshot) -> None:
    _validate_conversation_ref(snapshot.conversation_ref)
    if snapshot.thread_ref is None:
        if (
            snapshot.route_id is not None
            or snapshot.route_updated_at is not None
            or snapshot.reply_to_message_id is not None
        ):
            raise ContractViolation(
                "explicit Conversation snapshots cannot contain Thread route fields"
            )
        return
    validate_thread_ref(snapshot.thread_ref)
    require_identifier(snapshot.route_id or "", "route_id")
    if snapshot.reply_to_message_id is not None:
        require_identifier(snapshot.reply_to_message_id, "reply_to_message_id")


def validate_delivery_submission_record(record: DeliverySubmissionRecord) -> None:
    require_identifier(record.submission_id, "submission_id")
    require_identifier(record.delivery_id, "delivery_id")
    if not isinstance(record.origin, DeliverySubmissionOrigin):
        raise ContractViolation("delivery submission origin is invalid")
    require_identifier(record.principal_id, "principal_id")
    require_identifier(record.target_fingerprint, "target_fingerprint")
    require_identifier(record.payload_fingerprint, "payload_fingerprint")
    if record.created_at.tzinfo is None or record.updated_at.tzinfo is None:
        raise ContractViolation("delivery submission times must include a timezone")
    if record.updated_at < record.created_at:
        raise ContractViolation("delivery submission update precedes creation")
    if not record.destinations:
        raise ContractViolation("delivery submission requires at least one destination")
    validate_delivery_submission_destination_count(len(record.destinations))
    seen: set[str] = set()
    for destination in record.destinations:
        require_identifier(destination.delivery_id, "destination.delivery_id")
        if destination.delivery_id in seen:
            raise ContractViolation("destination delivery IDs must be unique")
        seen.add(destination.delivery_id)
        if not isinstance(destination.state, DeliverySubmissionState):
            raise ContractViolation("delivery destination state is invalid")
        if destination.updated_at.tzinfo is None:
            raise ContractViolation("delivery destination time must include a timezone")
        validate_delivery_route_snapshot(destination.snapshot)
        if destination.receipt is not None:
            validate_delivery_receipt(destination.receipt)
            _validate_durable_delivery_receipt(destination.receipt)


def validate_delivery_submission_destination_count(count: int) -> None:
    if count > MAX_DELIVERY_SUBMISSION_DESTINATIONS:
        raise ContractViolation(
            "delivery submission destinations exceed the maximum of "
            f"{MAX_DELIVERY_SUBMISSION_DESTINATIONS}"
        )


def _validate_durable_delivery_receipt(receipt: DeliveryReceipt) -> None:
    if receipt.detail is not None:
        raise ContractViolation("durable delivery receipt cannot contain detail text")
    if any(item.detail is not None for item in receipt.items):
        raise ContractViolation("durable delivery item receipt cannot contain detail text")
    if any(segment.detail is not None for segment in receipt.segments):
        raise ContractViolation("durable delivery segment receipt cannot contain detail text")


def _validate_conversation_ref(conversation_ref: ConversationRef) -> None:
    require_identifier(
        conversation_ref.channel_instance_id,
        "channel_instance_id",
    )
    require_identifier(
        conversation_ref.native_conversation_id,
        "native_conversation_id",
    )


__all__ = [
    "ConversationBinding",
    "DeliveryReservation",
    "DeliveryRouteSnapshot",
    "DeliverySubmissionRecord",
    "DeliverySubmissionState",
    "DestinationDeliveryRecord",
    "MAX_DELIVERY_SUBMISSION_DESTINATIONS",
    "RequestRouteCorrelation",
    "RequestRouteState",
    "ThreadProjectionRoute",
    "TurnReplyCorrelation",
    "validate_binding",
    "validate_delivery_route_snapshot",
    "validate_delivery_submission_destination_count",
    "validate_delivery_submission_record",
    "validate_projection_route",
    "validate_request_route_correlation",
    "validate_turn_reply_correlation",
]

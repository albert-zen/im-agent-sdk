from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..applications.contract import ThreadRef, validate_thread_ref
from ..interaction.channels import DeliveryReceipt, validate_delivery_receipt
from ..interaction.messages import ConversationRef, Metadata
from ..interaction.operations import ContractViolation, require_identifier

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
    error: str | None = None


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


def validate_delivery_submission_destination_count(count: int) -> None:
    if count > MAX_DELIVERY_SUBMISSION_DESTINATIONS:
        raise ContractViolation(
            "delivery submission destinations exceed the maximum of "
            f"{MAX_DELIVERY_SUBMISSION_DESTINATIONS}"
        )


def _canonical_metadata(metadata: Metadata) -> object:
    try:
        return json.loads(
            json.dumps(
                dict(metadata),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as error:
        raise ContractViolation("delivery metadata must be JSON-compatible") from error


def _validate_conversation_ref(conversation_ref: ConversationRef) -> None:
    require_identifier(
        conversation_ref.channel_instance_id,
        "channel_instance_id",
    )
    require_identifier(
        conversation_ref.native_conversation_id,
        "native_conversation_id",
    )

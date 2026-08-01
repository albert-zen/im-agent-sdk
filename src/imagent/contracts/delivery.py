from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from ._validation import ContractViolation, require_identifier, validate_thread_ref
from .model import (
    AttachmentContent,
    AttachmentHandle,
    Content,
    ConversationRef,
    DeliveryItemStatus,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySegmentStatus,
    LocalPath,
    Metadata,
    RemoteUrl,
    TextContent,
    ThreadRef,
)


class DeliveryTargetKind(StrEnum):
    CONVERSATION = "conversation"
    THREAD_ROUTES = "thread_routes"


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
class ConversationDeliveryTarget:
    conversation_ref: ConversationRef
    kind: DeliveryTargetKind = field(
        init=False,
        default=DeliveryTargetKind.CONVERSATION,
    )


@dataclass(frozen=True, slots=True)
class ThreadRouteDeliveryTarget:
    thread_ref: ThreadRef
    route_id: str | None = None
    kind: DeliveryTargetKind = field(
        init=False,
        default=DeliveryTargetKind.THREAD_ROUTES,
    )


DeliveryTarget: TypeAlias = ConversationDeliveryTarget | ThreadRouteDeliveryTarget


@dataclass(frozen=True, slots=True)
class DeliveryIntent:
    delivery_id: str
    target: DeliveryTarget
    content: tuple[Content, ...]
    created_at: datetime
    reply_to: str | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeliveryPrincipal:
    principal_id: str
    allowed_threads: tuple[ThreadRef, ...] = ()
    allowed_conversations: tuple[ConversationRef, ...] = ()


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


@dataclass(frozen=True, slots=True)
class DestinationDeliveryResult:
    delivery_id: str
    state: DeliverySubmissionState
    route_id: str | None = None
    conversation_ref: ConversationRef | None = None
    receipt: DeliveryReceipt | None = None
    error: str | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ProactiveDeliveryResult:
    delivery_id: str
    state: DeliverySubmissionState
    destinations: tuple[DestinationDeliveryResult, ...]
    error: str | None = None


def validate_delivery_intent(intent: DeliveryIntent) -> None:
    require_identifier(intent.delivery_id, "delivery_id")
    if isinstance(intent.target, ConversationDeliveryTarget):
        _validate_conversation_ref(intent.target.conversation_ref)
    elif isinstance(intent.target, ThreadRouteDeliveryTarget):
        validate_thread_ref(intent.target.thread_ref)
        if intent.target.route_id is not None:
            require_identifier(intent.target.route_id, "route_id")
    else:
        raise ContractViolation("delivery target is unsupported")
    if not intent.content:
        raise ContractViolation("delivery content cannot be empty")
    if intent.reply_to is not None:
        require_identifier(intent.reply_to, "reply_to")
    _canonical_metadata(intent.metadata)
    attachment_ids: set[str] = set()
    for index, item in enumerate(intent.content):
        if isinstance(item, TextContent):
            if not item.text:
                raise ContractViolation(f"content[{index}] text cannot be empty")
            continue
        if not isinstance(item, AttachmentContent):
            raise ContractViolation(f"content[{index}] has an unsupported content type")
        require_identifier(item.attachment_id, f"content[{index}].attachment_id")
        if item.attachment_id in attachment_ids:
            raise ContractViolation("delivery attachment IDs must be unique")
        attachment_ids.add(item.attachment_id)
        if not item.media_type:
            raise ContractViolation(f"content[{index}].media_type cannot be empty")
        if item.filename is not None and not item.filename.strip():
            raise ContractViolation(f"content[{index}].filename cannot be empty")
        if item.size_bytes is not None and item.size_bytes < 0:
            raise ContractViolation(f"content[{index}].size_bytes cannot be negative")
        _canonical_metadata(item.metadata)
        if isinstance(item.source, LocalPath):
            if not item.source.path:
                raise ContractViolation(f"content[{index}].source.path cannot be empty")
        elif isinstance(item.source, RemoteUrl):
            if not item.source.url:
                raise ContractViolation(f"content[{index}].source.url cannot be empty")
        elif isinstance(item.source, AttachmentHandle):
            require_identifier(
                item.source.handle_id,
                f"content[{index}].source.handle_id",
            )
        else:
            raise ContractViolation(f"content[{index}] has an unsupported attachment source")


def validate_delivery_principal(principal: DeliveryPrincipal) -> None:
    require_identifier(principal.principal_id, "principal_id")
    if len(set(principal.allowed_threads)) != len(principal.allowed_threads):
        raise ContractViolation("allowed_threads must be unique")
    if len(set(principal.allowed_conversations)) != len(principal.allowed_conversations):
        raise ContractViolation("allowed_conversations must be unique")
    for thread_ref in principal.allowed_threads:
        validate_thread_ref(thread_ref)
    for conversation_ref in principal.allowed_conversations:
        _validate_conversation_ref(conversation_ref)


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
    if not record.destinations:
        raise ContractViolation("delivery submission requires at least one destination")
    seen: set[str] = set()
    for destination in record.destinations:
        require_identifier(destination.delivery_id, "destination.delivery_id")
        if destination.delivery_id in seen:
            raise ContractViolation("destination delivery IDs must be unique")
        seen.add(destination.delivery_id)
        validate_delivery_route_snapshot(destination.snapshot)
        if destination.receipt is not None:
            validate_delivery_receipt(destination.receipt)


def validate_delivery_receipt(receipt: DeliveryReceipt) -> None:
    if not isinstance(receipt.status, DeliveryReceiptStatus):
        raise ContractViolation("delivery receipt status is invalid")
    if receipt.retry_after_seconds is not None:
        if receipt.status is not DeliveryReceiptStatus.RETRYABLE_FAILURE:
            raise ContractViolation(
                "retry_after_seconds is valid only for retryable delivery failures"
            )
        if not math.isfinite(receipt.retry_after_seconds):
            raise ContractViolation("retry_after_seconds must be finite")
        if receipt.retry_after_seconds < 0:
            raise ContractViolation("retry_after_seconds cannot be negative")
    seen_indexes: set[int] = set()
    for item in receipt.items:
        if item.content_index < 0:
            raise ContractViolation("delivery receipt content_index cannot be negative")
        if item.content_index in seen_indexes:
            raise ContractViolation("delivery receipt content indexes must be unique")
        seen_indexes.add(item.content_index)
        if not isinstance(item.status, DeliveryItemStatus):
            raise ContractViolation("delivery item receipt status is invalid")
        if (
            item.status is DeliveryItemStatus.RETRYABLE_FAILURE
            and item.native_message_id is not None
        ):
            raise ContractViolation(
                "retryable delivery item cannot contain native acceptance identity"
            )
        if item.attachment_id is not None:
            require_identifier(item.attachment_id, "attachment_id")
    seen_segment_indexes: set[int] = set()
    seen_segment_ids: set[str] = set()
    for segment in receipt.segments:
        if segment.segment_index < 0:
            raise ContractViolation("delivery segment index cannot be negative")
        if segment.segment_index in seen_segment_indexes:
            raise ContractViolation("delivery segment indexes must be unique")
        seen_segment_indexes.add(segment.segment_index)
        require_identifier(segment.delivery_id, "segment.delivery_id")
        if segment.delivery_id in seen_segment_ids:
            raise ContractViolation("delivery segment IDs must be unique")
        seen_segment_ids.add(segment.delivery_id)
        if not segment.source_content_indexes:
            raise ContractViolation("delivery segment requires source content indexes")
        if len(set(segment.source_content_indexes)) != len(segment.source_content_indexes):
            raise ContractViolation("delivery segment source indexes must be unique")
        if any(index < 0 for index in segment.source_content_indexes):
            raise ContractViolation("delivery segment source index cannot be negative")
        if not isinstance(segment.status, DeliverySegmentStatus):
            raise ContractViolation("delivery segment status is invalid")
        if (
            segment.status is DeliverySegmentStatus.RETRYABLE_FAILURE
            and segment.native_message_id is not None
        ):
            raise ContractViolation(
                "retryable delivery segment cannot contain native acceptance identity"
            )
        if segment.retry_after_seconds is not None:
            if segment.status is not DeliverySegmentStatus.RETRYABLE_FAILURE:
                raise ContractViolation(
                    "segment retry_after_seconds is valid only for retryable failures"
                )
            if not math.isfinite(segment.retry_after_seconds):
                raise ContractViolation("segment retry_after_seconds must be finite")
            if segment.retry_after_seconds < 0:
                raise ContractViolation("segment retry_after_seconds cannot be negative")
    if receipt.status is DeliveryReceiptStatus.RETRYABLE_FAILURE:
        if receipt.native_message_id is not None:
            raise ContractViolation(
                "retryable delivery receipt cannot contain native acceptance identity"
            )
        if any(
            item.status in {DeliveryItemStatus.ACCEPTED, DeliveryItemStatus.UNKNOWN}
            for item in receipt.items
        ):
            raise ContractViolation(
                "retryable delivery receipt cannot contain accepted or unknown items"
            )
        if any(item.native_message_id is not None for item in receipt.items):
            raise ContractViolation(
                "retryable delivery receipt items cannot contain native acceptance identity"
            )
        if any(
            segment.status
            in {
                DeliverySegmentStatus.ACCEPTED_BY_PLATFORM,
                DeliverySegmentStatus.UNKNOWN,
            }
            for segment in receipt.segments
        ):
            raise ContractViolation(
                "retryable delivery receipt cannot contain accepted or unknown segments"
            )
        if any(segment.native_message_id is not None for segment in receipt.segments):
            raise ContractViolation(
                "retryable delivery receipt segments cannot contain native acceptance identity"
            )


def validate_delivery_receipt_for_content(
    receipt: DeliveryReceipt,
    content: tuple[Content, ...],
) -> None:
    validate_delivery_receipt(receipt)
    for item in receipt.items:
        if item.content_index >= len(content):
            raise ContractViolation(
                "delivery item receipt content_index is outside submitted content"
            )
        submitted = content[item.content_index]
        if item.attachment_id is None:
            continue
        if not isinstance(submitted, AttachmentContent):
            raise ContractViolation(
                "delivery item receipt attachment_id refers to non-attachment content"
            )
        if item.attachment_id != submitted.attachment_id:
            raise ContractViolation(
                "delivery item receipt attachment_id does not match submitted content"
            )
    for segment in receipt.segments:
        if any(index >= len(content) for index in segment.source_content_indexes):
            raise ContractViolation("delivery segment source index is outside submitted content")


def derive_delivery_target_fingerprint(target: DeliveryTarget) -> str:
    if isinstance(target, ConversationDeliveryTarget):
        identity: object = [
            target.kind.value,
            target.conversation_ref.channel_instance_id,
            target.conversation_ref.native_conversation_id,
        ]
    else:
        identity = [
            target.kind.value,
            _thread_identity(target.thread_ref),
            target.route_id,
        ]
    return _sha256_identity("target", identity)


def derive_delivery_payload_fingerprint(intent: DeliveryIntent) -> str:
    validate_delivery_intent(intent)
    identity = {
        "content": [_content_identity(item) for item in intent.content],
        "reply_to": intent.reply_to,
        "metadata": _canonical_metadata(intent.metadata),
    }
    return _sha256_identity("payload", identity)


def derive_destination_delivery_id(
    root_submission_id: str,
    conversation_ref: ConversationRef,
) -> str:
    require_identifier(root_submission_id, "submission_id")
    _validate_conversation_ref(conversation_ref)
    return _sha256_identity(
        "destination",
        [
            root_submission_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
    )


def derive_delivery_submission_id(
    origin: DeliverySubmissionOrigin,
    principal_id: str,
    delivery_id: str,
) -> str:
    if not isinstance(origin, DeliverySubmissionOrigin):
        raise ContractViolation("delivery submission origin is invalid")
    require_identifier(principal_id, "principal_id")
    require_identifier(delivery_id, "delivery_id")
    return _sha256_identity(
        "submission",
        [origin.value, principal_id, delivery_id],
    )


def _content_identity(content: Content) -> object:
    if isinstance(content, TextContent):
        return {
            "kind": "text",
            "text": content.text,
            "format": content.format.value,
        }
    source = content.source
    if isinstance(source, LocalPath):
        digest = content.metadata.get("sha256")
        source_identity: object = (
            {"kind": source.kind.value, "sha256": digest}
            if isinstance(digest, str) and digest
            else {"kind": source.kind.value, "path": source.path}
        )
    elif isinstance(source, RemoteUrl):
        source_identity = {"kind": source.kind.value, "url": source.url}
    else:
        source_identity = {
            "kind": source.kind.value,
            "handle_id": source.handle_id,
        }
    return {
        "kind": "attachment",
        "attachment_id": content.attachment_id,
        "media_type": content.media_type,
        "source": source_identity,
        "filename": content.filename,
        "size_bytes": content.size_bytes,
        "metadata": _canonical_metadata(content.metadata),
    }


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


def _sha256_identity(label: str, identity: object) -> str:
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return f"imagent:delivery-{label}:sha256:{digest}"


def _thread_identity(thread_ref: ThreadRef) -> object:
    return [
        thread_ref.application_instance_id,
        (thread_ref.project_ref.native_project_id if thread_ref.project_ref is not None else None),
        thread_ref.native_thread_id,
    ]


def _validate_conversation_ref(conversation_ref: ConversationRef) -> None:
    require_identifier(
        conversation_ref.channel_instance_id,
        "channel_instance_id",
    )
    require_identifier(
        conversation_ref.native_conversation_id,
        "native_conversation_id",
    )

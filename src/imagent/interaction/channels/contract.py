from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..media import AttachmentContent
from ..messages import Content
from ..operations import ContractViolation, require_identifier


class DeliveryReceiptStatus(StrEnum):
    ACCEPTED_BY_PLATFORM = "accepted_by_platform"
    REJECTED_BY_PLATFORM = "rejected_by_platform"
    RETRYABLE_FAILURE = "retryable_failure"
    UNKNOWN = "unknown"


class DeliveryItemStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RETRYABLE_FAILURE = "retryable_failure"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


class DeliverySegmentStatus(StrEnum):
    ACCEPTED_BY_PLATFORM = "accepted_by_platform"
    REJECTED_BY_PLATFORM = "rejected_by_platform"
    RETRYABLE_FAILURE = "retryable_failure"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class DeliveryItemReceipt:
    content_index: int
    status: DeliveryItemStatus
    attachment_id: str | None = None
    native_message_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DeliverySegmentReceipt:
    segment_index: int
    delivery_id: str
    source_content_indexes: tuple[int, ...]
    status: DeliverySegmentStatus
    native_message_id: str | None = None
    detail: str | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    status: DeliveryReceiptStatus
    native_message_id: str | None = None
    detail: str | None = None
    items: tuple[DeliveryItemReceipt, ...] = ()
    segments: tuple[DeliverySegmentReceipt, ...] = ()
    retry_after_seconds: float | None = None


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

"""Low-dependency proactive delivery contract seam."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from ...applications.contract import ThreadRef, validate_thread_ref
from ...contracts.delivery import (
    DeliverySubmissionState,
    _canonical_metadata,
    _validate_conversation_ref,
)
from ...interaction.channels import DeliveryReceipt
from ...interaction.media import (
    AttachmentContent,
    AttachmentHandle,
    LocalPath,
    RemoteUrl,
)
from ...interaction.messages import Content, ConversationRef, Metadata, TextContent
from ...interaction.operations import ContractViolation, require_identifier

__all__ = [
    "ConversationDeliveryTarget",
    "DeliveryIntent",
    "DeliveryTarget",
    "DeliveryTargetKind",
    "DestinationDeliveryResult",
    "ProactiveDeliveryResult",
    "ThreadRouteDeliveryTarget",
    "validate_delivery_intent",
]


class DeliveryTargetKind(StrEnum):
    CONVERSATION = "conversation"
    THREAD_ROUTES = "thread_routes"


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

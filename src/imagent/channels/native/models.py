from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class InboundAttachment:
    kind: Literal["image", "file"]
    content_type: str
    local_path: str
    size_bytes: int
    filename: str = ""
    source_channel_id: str = ""
    source_message_id: str = ""


@dataclass(frozen=True, slots=True)
class InboundQuoteAttachment:
    kind: Literal["image", "voice", "video", "file", "attachment"]
    filename: str | None = None
    transcript: str | None = None


@dataclass(frozen=True, slots=True)
class InboundQuote:
    reference_id: str | None = None
    text: str = ""
    attachments: tuple[InboundQuoteAttachment, ...] = ()


@dataclass(slots=True)
class InboundMessage:
    channel_id: str
    conversation_id: str
    user_id: str
    message_id: str
    text: str
    attachments: tuple[InboundAttachment, ...] = ()
    quote: InboundQuote | None = None
    input_error: str | None = None
    reply_to_message_id: str | None = None
    sent_at: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class OutboundArtifact:
    kind: Literal["image", "file"]
    local_path: str
    content_type: str
    filename: str
    size_bytes: int
    sha256: str = ""
    attachment_id: str = ""


@dataclass(frozen=True, slots=True)
class NativeDeliveryResult:
    native_message_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class OutboundMessage:
    channel_id: str
    conversation_id: str
    message_type: str
    text: str
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[OutboundArtifact] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.artifacts = [
            item if isinstance(item, OutboundArtifact) else OutboundArtifact(**item)
            for item in self.artifacts
        ]

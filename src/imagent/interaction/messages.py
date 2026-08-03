from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from .media import AttachmentContent

Metadata: TypeAlias = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ConversationRef:
    channel_instance_id: str
    native_conversation_id: str


class TextLengthUnit(StrEnum):
    CODE_POINTS = "code_points"
    UTF16_CODE_UNITS = "utf16_code_units"
    UTF8_BYTES = "utf8_bytes"


class TextFormat(StrEnum):
    PLAIN = "plain"
    MARKDOWN = "markdown"


@dataclass(frozen=True, slots=True)
class TextContent:
    text: str
    format: TextFormat = TextFormat.PLAIN


Content: TypeAlias = TextContent | AttachmentContent


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class InboundMessage:
    message_id: str
    conversation_ref: ConversationRef
    sender: str
    content: tuple[Content, ...]
    created_at: datetime
    reply_to: str | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    delivery_id: str
    conversation_ref: ConversationRef
    content: tuple[Content, ...]
    created_at: datetime
    reply_to: str | None = None
    metadata: Metadata = field(default_factory=dict)


__all__ = [
    "Content",
    "ConversationRef",
    "InboundMessage",
    "MessageRole",
    "Metadata",
    "OutboundMessage",
    "TextContent",
    "TextFormat",
    "TextLengthUnit",
]

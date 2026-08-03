from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


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


def split_text(text: str, *, limit: int) -> list[str]:
    """Split text for an IM platform without splitting Unicode code points."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    remaining = text.strip()
    if not remaining:
        return []
    chunks: list[str] = []
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        minimum_soft_break = max(1, limit // 2)
        break_at = max(
            window.rfind("\n\n", minimum_soft_break, limit + 1),
            window.rfind("\n", minimum_soft_break, limit + 1),
            window.rfind(" ", minimum_soft_break, limit + 1),
        )
        if break_at < minimum_soft_break:
            break_at = limit
        chunk = remaining[:break_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            break_at = limit
        chunks.append(chunk)
        remaining = remaining[break_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks

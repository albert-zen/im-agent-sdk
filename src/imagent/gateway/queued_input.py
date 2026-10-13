"""Scoped queued-input status and bounded product-presentation contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..interaction.messages import ConversationRef, OutboundMessage
from ..interaction.operations import ContractViolation, require_identifier


class QueuedInputStatusKind(StrEnum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    ADMITTED = "admitted"
    CANCELLED = "cancelled"


class QueuedInputNoticeReason(StrEnum):
    ADMISSION = "admission"
    STATE_CHANGED = "state_changed"
    ATTEMPT_FAILED = "attempt_failed"


@dataclass(frozen=True, slots=True)
class QueuedInputStatus:
    message_id: str
    state: QueuedInputStatusKind

    def __post_init__(self) -> None:
        require_identifier(self.message_id, "message_id")
        if not self.message_id.strip() or len(self.message_id) > 512:
            raise ContractViolation("queued status message_id exceeds its bound")
        if not isinstance(self.state, QueuedInputStatusKind):
            raise ContractViolation("queued status requires a closed state")


class QueuedInputPresenter(Protocol):
    """Render bounded facts without native IDs, effect or routing authority."""

    async def present_queued_input(
        self,
        status: QueuedInputStatus,
        *,
        reason: QueuedInputNoticeReason,
        conversation_ref: ConversationRef,
        delivery_id: str,
        reply_to_message_id: str,
    ) -> OutboundMessage: ...


__all__ = [
    "QueuedInputNoticeReason",
    "QueuedInputPresenter",
    "QueuedInputStatus",
    "QueuedInputStatusKind",
]

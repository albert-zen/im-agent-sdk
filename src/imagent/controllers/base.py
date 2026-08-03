from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..contracts import InteractiveRequest
from ..interaction.messages import ConversationRef, OutboundMessage


@dataclass(frozen=True, slots=True)
class RequestPresentation:
    message: OutboundMessage
    response_supported: bool


class RequestPresenter(Protocol):
    def present_request(
        self,
        request: InteractiveRequest,
        *,
        conversation_ref: ConversationRef,
        delivery_id: str,
        reply_to_message_id: str | None,
    ) -> RequestPresentation:
        """Render one typed request for one already-selected destination."""
        ...

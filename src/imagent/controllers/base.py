from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ..contracts import (
    ApplicationOperation,
    ApplicationOperationResult,
    Content,
    ConversationBinding,
    ConversationRef,
    GatewayOperation,
    GatewayOperationResult,
    InboundMessage,
    InteractiveRequest,
    OutboundMessage,
)

InboundContentAdapter = Callable[
    [InboundMessage],
    tuple[Content, ...] | Awaitable[tuple[Content, ...]],
]


class ControllerActions(Protocol):
    async def execute_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult: ...

    async def execute_gateway(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult: ...

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None: ...


class InboundController(Protocol):
    async def handle(
        self,
        message: InboundMessage,
        actions: ControllerActions,
    ) -> tuple[OutboundMessage, ...] | None:
        """Return None to pass through, otherwise deliveries for a consumed input."""
        ...


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

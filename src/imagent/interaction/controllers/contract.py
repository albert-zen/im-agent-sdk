from __future__ import annotations

from typing import Protocol

from ...contracts import (
    ApplicationOperation,
    ApplicationOperationResult,
    ConversationBinding,
    GatewayOperation,
    GatewayOperationResult,
)
from ..messages import ConversationRef, InboundMessage, OutboundMessage


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

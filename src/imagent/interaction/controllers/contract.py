from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Protocol, runtime_checkable

from ...contracts import (
    ApplicationOperation,
    ApplicationOperationResult,
    ConversationBinding,
    GatewayOperation,
    GatewayOperationResult,
)
from ..messages import ConversationRef, InboundMessage, OutboundMessage


class CommandInvocationFacts(Protocol):
    @property
    def invocation_id(self) -> str: ...

    @property
    def conversation_ref(self) -> ConversationRef: ...

    @property
    def message_id(self) -> str: ...

    @property
    def actor(self) -> str: ...

    @property
    def command_name(self) -> str: ...

    @property
    def arguments(self) -> tuple[str, ...]: ...

    @property
    def created_at(self) -> datetime: ...


class CommandHandlerActions(Protocol):
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


class ControllerActions(CommandHandlerActions, Protocol):
    async def enter_effectful_command(
        self,
        invocation: CommandInvocationFacts,
    ) -> None:
        """Durably fence one effectful handler for the current owned inbound claim."""
        ...


class _CommandHandlerActionsView:
    __slots__ = ("_actions",)

    def __init__(self, actions: ControllerActions) -> None:
        self._actions = actions

    async def execute_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        return await self._actions.execute_application(operation)

    async def execute_gateway(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        return await self._actions.execute_gateway(operation)

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None:
        return await self._actions.get_binding(conversation_ref)


def _narrow_handler_actions(actions: ControllerActions) -> CommandHandlerActions:
    return _CommandHandlerActionsView(actions)


class InboundController(Protocol):
    async def handle(
        self,
        message: InboundMessage,
        actions: ControllerActions,
    ) -> tuple[OutboundMessage, ...] | None:
        """Return None to pass through, otherwise deliveries for a consumed input."""
        ...


@runtime_checkable
class ControllerLifecycle(Protocol):
    def validate_startup(self) -> None: ...

    async def close(self) -> None: ...


def _derive_command_invocation_id(
    conversation_ref: ConversationRef,
    message_id: str,
    command_name: str,
    arguments: tuple[str, ...],
) -> str:
    digest = hashlib.sha256()
    for value in (
        conversation_ref.channel_instance_id,
        conversation_ref.native_conversation_id,
        message_id,
        command_name,
        *arguments,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return f"imagent:command:sha256:{digest.hexdigest()}"

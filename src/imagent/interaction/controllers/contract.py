from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, ForwardRef, Protocol, runtime_checkable

from ..messages import ConversationRef, InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from ...gateway.actions import ConversationActions


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


class InboundController(Protocol):
    async def handle(
        self,
        message: InboundMessage,
        actions: ConversationActions,
    ) -> tuple[OutboundMessage, ...] | None:
        """Receive the exact scoped surface; return None to pass through."""
        ...


InboundController.handle.__annotations__["actions"] = ForwardRef(
    "ConversationActions",
    module="imagent.gateway.actions",
)


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

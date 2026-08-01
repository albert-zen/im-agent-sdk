from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from .adapters import (
    ChannelAdapter,
    IdempotencyClaimStatus,
    IdempotencyRepository,
    InboundAdmission,
    InboundAdmissionHandler,
    MessageHandler,
    OperationHandler,
)
from .contracts import ConversationRef, InboundMessage


@dataclass(frozen=True, slots=True)
class ClaimedInbound:
    message: InboundMessage
    scope: str
    key: str
    owner_token: str


ClaimedInboundHandler = Callable[[ClaimedInbound], Awaitable[None]]


async def start_channel_with_admission(
    channel: ChannelAdapter,
    on_message: MessageHandler,
    on_operation: OperationHandler,
    on_admission: InboundAdmissionHandler,
) -> None:
    """Start a modern Channel, preserving the pre-admission migration fallback."""

    supports_admission = True
    try:
        signature = inspect.signature(channel.start)
    except (TypeError, ValueError):
        pass
    else:
        try:
            signature.bind(on_message, on_operation, on_admission)
        except TypeError:
            supports_admission = False
    if supports_admission:
        await channel.start(on_message, on_operation, on_admission)
    else:
        await channel.start(on_message, on_operation)


class InboundAdmissionService:
    """Acquire fenced inbound claims and transfer them to Gateway orchestration."""

    def __init__(
        self,
        repository: IdempotencyRepository,
        handoff: ClaimedInboundHandler,
    ) -> None:
        self._repository = repository
        self._handoff = handoff

    async def begin(
        self,
        channel_instance_id: str,
        conversation_ref: ConversationRef,
        message_id: str,
    ) -> InboundAdmission | None:
        if conversation_ref.channel_instance_id != channel_instance_id:
            raise ValueError("Channel attempted admission for a different channel instance")
        scope, key = inbound_idempotency_identity(conversation_ref, message_id)
        owner_token = uuid4().hex
        claim = await self._repository.claim(scope, key, owner_token=owner_token)
        if claim is not IdempotencyClaimStatus.ACQUIRED:
            return None
        return _InboundAdmissionLease(
            repository=self._repository,
            handoff=self._handoff,
            conversation_ref=conversation_ref,
            message_id=message_id,
            scope=scope,
            key=key,
            owner_token=owner_token,
        )


class _InboundAdmissionLease:
    __slots__ = (
        "_conversation_ref",
        "_handoff",
        "_key",
        "_message_id",
        "_owner_token",
        "_repository",
        "_scope",
        "_state",
    )

    def __init__(
        self,
        *,
        repository: IdempotencyRepository,
        handoff: ClaimedInboundHandler,
        conversation_ref: ConversationRef,
        message_id: str,
        scope: str,
        key: str,
        owner_token: str,
    ) -> None:
        self._repository = repository
        self._handoff = handoff
        self._conversation_ref = conversation_ref
        self._message_id = message_id
        self._scope = scope
        self._key = key
        self._owner_token = owner_token
        self._state = "open"

    async def deliver(self, message: InboundMessage) -> None:
        if self._state != "open":
            raise RuntimeError("inbound admission lease is no longer open")
        if (
            message.conversation_ref != self._conversation_ref
            or message.message_id != self._message_id
        ):
            await self.release()
            raise ValueError("prepared inbound message does not match its admission identity")
        self._state = "refreshing"
        try:
            await self._repository.refresh(
                self._scope,
                self._key,
                owner_token=self._owner_token,
            )
        except BaseException as error:
            self._state = "closed"
            try:
                await self._repository.release(
                    self._scope,
                    self._key,
                    owner_token=self._owner_token,
                )
            except BaseException as release_error:
                error.add_note(
                    "Failed to release an inbound claim after handoff fencing failed: "
                    f"{release_error!r}"
                )
            raise
        self._state = "transferred"
        await self._handoff(
            ClaimedInbound(
                message=message,
                scope=self._scope,
                key=self._key,
                owner_token=self._owner_token,
            )
        )

    async def release(self) -> None:
        if self._state != "open":
            return
        self._state = "releasing"
        try:
            await self._repository.release(
                self._scope,
                self._key,
                owner_token=self._owner_token,
            )
        except BaseException:
            self._state = "open"
            raise
        self._state = "released"


def inbound_idempotency_identity(
    conversation_ref: ConversationRef,
    message_id: str,
) -> tuple[str, str]:
    return (
        f"inbound:{conversation_ref.channel_instance_id}",
        f"{conversation_ref.native_conversation_id}:{message_id}",
    )

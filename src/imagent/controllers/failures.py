from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..contracts import InboundMessage, OutboundMessage
from .base import InboundFailurePhase, InboundFailurePresenter


async def present_inbound_failure(
    presenter: InboundFailurePresenter,
    inbound: InboundMessage,
    error: BaseException,
    phase: InboundFailurePhase,
    deliver: Callable[[OutboundMessage], Awaitable[object]],
) -> None:
    delivery_id = (
        f"imagent:gateway:{inbound.conversation_ref.channel_instance_id}:"
        f"{inbound.conversation_ref.native_conversation_id}:"
        f"{inbound.message_id}:inbound-failure"
    )
    output = presenter.present_failure(
        error,
        phase=phase,
        conversation_ref=inbound.conversation_ref,
        delivery_id=delivery_id,
        reply_to_message_id=inbound.message_id,
    )
    if output.conversation_ref != inbound.conversation_ref:
        raise ValueError("Inbound failure output belongs to a different Conversation")
    if output.delivery_id != delivery_id:
        raise ValueError("Inbound failure output changed its stable delivery identity")
    if output.reply_to != inbound.message_id:
        raise ValueError("Inbound failure output changed its reply identity")
    await deliver(output)

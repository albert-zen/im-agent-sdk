from __future__ import annotations

from .adapters import IdempotencyRepository, OutboundPresentationPolicy
from .contracts import ContractViolation, OutboundMessage


async def present_outbound(
    policy: OutboundPresentationPolicy | None,
    message: OutboundMessage,
    idempotency: IdempotencyRepository,
    scope: str,
    owner_token: str,
) -> OutboundMessage | None:
    """Apply one destination policy and durably consume explicit suppression."""

    try:
        presented = await policy.present(message) if policy is not None else message
        if presented is not None and (
            presented.delivery_id != message.delivery_id
            or presented.conversation_ref != message.conversation_ref
        ):
            raise ContractViolation(
                "outbound presentation cannot change delivery identity or destination"
            )
        if presented is None:
            await idempotency.complete(
                scope,
                message.delivery_id,
                owner_token=owner_token,
            )
    except BaseException:
        await idempotency.release(
            scope,
            message.delivery_id,
            owner_token=owner_token,
        )
        raise
    return presented

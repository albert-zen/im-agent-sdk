from __future__ import annotations

from dataclasses import replace

from .adapters import IdempotencyRepository, OutboundPresentationPolicy
from .contracts import ContractViolation, OutboundMessage

PROJECTION_ORIGIN_METADATA_KEY = "imagent_projection_origin"
PROJECTION_CHECKPOINT_METADATA_KEY = "imagent_projection_checkpoint"
PROJECTION_ORIGIN_LIVE = "live"
PROJECTION_ORIGIN_AUTHORITATIVE = "authoritative"


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
    if presented is None:
        return None
    metadata = dict(presented.metadata)
    metadata.pop(PROJECTION_ORIGIN_METADATA_KEY, None)
    metadata.pop(PROJECTION_CHECKPOINT_METADATA_KEY, None)
    return replace(presented, metadata=metadata)

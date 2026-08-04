"""Per-route completed-delivery checkpoint authority."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from ...applications.contract import ThreadRef
from ...interaction.messages import ConversationRef
from ..persistence.repository_contracts import (
    IdempotencyClaimStatus,
    ProjectionRouteRepository,
)
from ..persistence.state_contracts import ThreadProjectionRoute


def derive_projection_delivery_id(
    conversation_ref: ConversationRef,
    thread_ref: ThreadRef,
    agent_item_id: str,
    *,
    segment_index: int = 0,
) -> str:
    """Derive one stable per-destination authoritative-item delivery identity."""
    identity = json.dumps(
        [
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
            thread_ref.application_instance_id,
            (
                thread_ref.project_ref.native_project_id
                if thread_ref.project_ref is not None
                else None
            ),
            thread_ref.native_thread_id,
            agent_item_id,
            segment_index,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:delivery:sha256:{digest}"


class _ProjectionCheckpointAuthority:
    """Apply typed completed-delivery evidence to one route checkpoint."""

    def __init__(
        self,
        *,
        projections: ProjectionRouteRepository,
    ) -> None:
        self._projections = projections

    async def apply_delivery_outcome(
        self,
        route: ThreadProjectionRoute,
        *,
        agent_item_id: str,
        checkpointable: bool,
        delivery_outcome: IdempotencyClaimStatus,
        authoritative: bool,
        delivery_id: str,
    ) -> ThreadProjectionRoute:
        """Advance only from completed checkpointable destination evidence."""

        if delivery_outcome is IdempotencyClaimStatus.IN_FLIGHT:
            raise RuntimeError(f"delivery remains in flight: {delivery_id}")
        if not checkpointable:
            return route
        if delivery_outcome is IdempotencyClaimStatus.ALREADY_COMPLETED and not authoritative:
            return route
        if (
            delivery_outcome is not IdempotencyClaimStatus.ACQUIRED
            and delivery_outcome is not IdempotencyClaimStatus.ALREADY_COMPLETED
        ):
            raise RuntimeError(f"delivery did not durably complete: {delivery_id}")
        if route.checkpoint_agent_item_id == agent_item_id:
            return route
        return await self._projections.advance_projection_checkpoint(
            route.route_id,
            expected_agent_item_id=route.checkpoint_agent_item_id,
            agent_item_id=agent_item_id,
            checkpointed_at=datetime.now(UTC),
        )

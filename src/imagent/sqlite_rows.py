"""SQLite-owned route mutation policy kept outside the pure row mapper."""

from __future__ import annotations

from dataclasses import replace

from .gateway.persistence.repository_contracts import (
    ProjectionCheckpointConflict,
    ProjectionRouteConflict,
)
from .gateway.persistence.state_contracts import ThreadProjectionRoute


def merge_projection_route(
    existing: ThreadProjectionRoute | None,
    replacement: ThreadProjectionRoute,
) -> ThreadProjectionRoute:
    if existing is None:
        return replacement
    if (
        existing.route_id != replacement.route_id
        or existing.thread_ref != replacement.thread_ref
        or existing.conversation_ref != replacement.conversation_ref
    ):
        raise ProjectionRouteConflict(
            f"route ID belongs to different endpoints: {replacement.route_id}"
        )
    if replacement.checkpoint_agent_item_id is not None:
        if (
            replacement.checkpoint_agent_item_id != existing.checkpoint_agent_item_id
            or replacement.checkpointed_at != existing.checkpointed_at
        ):
            raise ProjectionCheckpointConflict(
                f"route refresh cannot change checkpoint: {replacement.route_id}"
            )
        return replacement
    if existing.checkpoint_agent_item_id is None:
        return replacement
    return replace(
        replacement,
        checkpoint_agent_item_id=existing.checkpoint_agent_item_id,
        checkpointed_at=existing.checkpointed_at,
    )

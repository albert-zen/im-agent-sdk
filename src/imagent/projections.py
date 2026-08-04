from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import islice
from types import MappingProxyType
from typing import TYPE_CHECKING

from .applications.contract import AgentMessage, ThreadRef
from .interaction.messages import ConversationRef, OutboundMessage, TextContent, TextFormat

if TYPE_CHECKING:
    from .gateway.persistence.repository_contracts import (
        IdempotencyClaimStatus,
        ProjectionRouteRepository,
    )
    from .gateway.persistence.state_contracts import ThreadProjectionRoute
    from .gateway.projection.checkpoints import _ProjectionCheckpointAuthority

DeliverOutbound = Callable[
    [OutboundMessage, bool],
    Awaitable["IdempotencyClaimStatus"],
]
DeliverRequestOutbound = Callable[[OutboundMessage], Awaitable["IdempotencyClaimStatus"]]

_PROJECTION_METADATA_MAX_ITEMS = 16
_PROJECTION_METADATA_MAX_KEY_LENGTH = 64
_PROJECTION_METADATA_MAX_TEXT_LENGTH = 256
_PROJECTION_METADATA_MIN_INTEGER = -(2**63)
_PROJECTION_METADATA_MAX_INTEGER = 2**63 - 1


class RetryableDeliveryError(RuntimeError):
    """A zero/known-outcome delivery deferral that authoritative recovery may retry."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProjectionWorkerState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    RETRYING = "retrying"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ProjectionWorkerHealth:
    thread_ref: ThreadRef
    state: ProjectionWorkerState
    restart_count: int = 0
    delivery_failure_count: int = 0
    event_overflow_count: int = 0
    last_subscription_error: str | None = None
    last_recovery_error: str | None = None
    last_delivery_error: str | None = None
    last_delivery_route_id: str | None = None
    last_gap: str | None = None
    last_event_gap: str | None = None
    last_event_overflow: str | None = None
    interactive_request_recovery_degraded: bool = False
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProjectedAgentMessage:
    message: AgentMessage
    turn_id: str | None
    event_id: str | None = None
    checkpoint: bool = True


@dataclass(frozen=True, slots=True)
class AuthoritativeProjectionSlice:
    messages: tuple[ProjectedAgentMessage, ...]
    pages_read: int
    checkpoint_found: bool
    gap: str | None = None


def derive_live_projection_delivery_id(
    conversation_ref: ConversationRef,
    thread_ref: ThreadRef,
    event_id: str,
) -> str:
    """Derive a stable live-only delivery identity outside history item identity."""
    identity = json.dumps(
        [
            "live_event",
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
            thread_ref.application_instance_id,
            (
                thread_ref.project_ref.native_project_id
                if thread_ref.project_ref is not None
                else None
            ),
            thread_ref.native_thread_id,
            event_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:delivery:live:sha256:{digest}"


def immutable_projection_metadata(
    metadata: Mapping[str, object],
) -> Mapping[str, object]:
    """Validate one bounded scalar metadata snapshot for outbound projection."""

    keys = list(islice(metadata, _PROJECTION_METADATA_MAX_ITEMS + 1))
    if len(keys) > _PROJECTION_METADATA_MAX_ITEMS:
        raise ValueError(
            f"AgentMessage projection metadata exceeds {_PROJECTION_METADATA_MAX_ITEMS} items"
        )
    copied: dict[str, object] = {}
    for key in keys:
        if not isinstance(key, str) or not key or len(key) > _PROJECTION_METADATA_MAX_KEY_LENGTH:
            raise ValueError("AgentMessage projection metadata keys are invalid or too long")
        value = metadata[key]
        if isinstance(value, str):
            if len(value) > _PROJECTION_METADATA_MAX_TEXT_LENGTH:
                raise ValueError(
                    "AgentMessage projection metadata text exceeds "
                    f"{_PROJECTION_METADATA_MAX_TEXT_LENGTH} characters"
                )
        elif value is None or isinstance(value, bool):
            pass
        elif isinstance(value, int):
            if not _PROJECTION_METADATA_MIN_INTEGER <= value <= _PROJECTION_METADATA_MAX_INTEGER:
                raise ValueError("AgentMessage projection metadata integer is out of range")
        elif not (isinstance(value, float) and math.isfinite(value)):
            raise ValueError("AgentMessage projection metadata values must be bounded scalars")
        copied[key] = value
    return MappingProxyType(copied)


async def deliver_projected_message(
    repository: ProjectionRouteRepository,
    route: ThreadProjectionRoute,
    projected: ProjectedAgentMessage,
    *,
    deliver_outbound: DeliverOutbound,
    checkpoint_authority: _ProjectionCheckpointAuthority,
    authoritative: bool,
) -> ThreadProjectionRoute:
    """Make one ordered route decision without adding retry/backpressure."""
    agent_message = projected.message
    if projected.checkpoint:
        from .gateway.projection.checkpoints import derive_projection_delivery_id

        delivery_id = derive_projection_delivery_id(
            route.conversation_ref,
            agent_message.thread_ref,
            agent_message.agent_item_id,
        )
    else:
        if not projected.event_id:
            raise ValueError("live-only projection requires a stable event identity")
        delivery_id = derive_live_projection_delivery_id(
            route.conversation_ref,
            agent_message.thread_ref,
            projected.event_id,
        )
    reply_to = route.reply_to_message_id
    if projected.turn_id is not None:
        correlation = await repository.get_turn_reply_correlation(
            route.thread_ref,
            projected.turn_id,
        )
        if correlation is not None and correlation.conversation_ref == route.conversation_ref:
            reply_to = correlation.reply_to_message_id
    claim = await deliver_outbound(
        OutboundMessage(
            delivery_id=delivery_id,
            conversation_ref=route.conversation_ref,
            content=tuple(
                TextContent(item.text, TextFormat.MARKDOWN)
                if isinstance(item, TextContent)
                else item
                for item in agent_message.content
            ),
            created_at=agent_message.created_at,
            reply_to=reply_to,
            metadata=immutable_projection_metadata(agent_message.metadata),
        ),
        projected.checkpoint,
    )
    return await checkpoint_authority.apply_delivery_outcome(
        route,
        agent_item_id=agent_message.agent_item_id,
        checkpointable=projected.checkpoint,
        delivery_outcome=claim,
        authoritative=authoritative,
        delivery_id=delivery_id,
    )

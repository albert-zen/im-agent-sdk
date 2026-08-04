from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import islice
from types import MappingProxyType
from typing import TYPE_CHECKING

from .adapters import (
    IdempotencyClaimStatus,
    ProjectionRouteRepository,
)
from .contracts import (
    AgentMessage,
    ThreadProjectionRoute,
    ThreadRef,
)
from .interaction.messages import ConversationRef, OutboundMessage, TextContent, TextFormat

if TYPE_CHECKING:
    from .gateway.presentation import OutboundPresentationContext

DeliverOutbound = Callable[
    [OutboundMessage, "OutboundPresentationContext"],
    Awaitable[IdempotencyClaimStatus],
]
DeliverRequestOutbound = Callable[[OutboundMessage], Awaitable[IdempotencyClaimStatus]]

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


def derive_projection_route_id(
    thread_ref: ThreadRef,
    conversation_ref: ConversationRef,
) -> str:
    identity = json.dumps(
        [
            thread_ref.application_instance_id,
            (
                thread_ref.project_ref.native_project_id
                if thread_ref.project_ref is not None
                else None
            ),
            thread_ref.native_thread_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:projection:sha256:{digest}"


def derive_projection_delivery_id(
    conversation_ref: ConversationRef,
    thread_ref: ThreadRef,
    agent_item_id: str,
    *,
    segment_index: int = 0,
) -> str:
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


def derive_turn_reply_correlation_id(
    thread_ref: ThreadRef,
    turn_id: str,
) -> str:
    identity = json.dumps(
        [
            thread_ref.application_instance_id,
            (
                thread_ref.project_ref.native_project_id
                if thread_ref.project_ref is not None
                else None
            ),
            thread_ref.native_thread_id,
            turn_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:turn-reply:sha256:{digest}"


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


async def get_projection_route(
    repository: ProjectionRouteRepository,
    route_id: str,
) -> ThreadProjectionRoute | None:
    return next(
        (
            route
            for route in await repository.list_projection_routes()
            if route.route_id == route_id
        ),
        None,
    )


async def deliver_projected_message(
    repository: ProjectionRouteRepository,
    route: ThreadProjectionRoute,
    projected: ProjectedAgentMessage,
    *,
    deliver_outbound: DeliverOutbound,
    authoritative: bool,
) -> ThreadProjectionRoute:
    """Make one ordered route decision without adding retry/backpressure."""
    from .gateway.presentation import (
        OutboundPresentationContext,
        ProjectionPresentationOrigin,
    )

    agent_message = projected.message
    if projected.checkpoint:
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
        OutboundPresentationContext(
            origin=(
                ProjectionPresentationOrigin.AUTHORITATIVE
                if projected.checkpoint
                else ProjectionPresentationOrigin.LIVE_ONLY
            )
        ),
    )
    if claim is IdempotencyClaimStatus.IN_FLIGHT:
        raise RuntimeError(f"delivery remains in flight: {delivery_id}")
    if not projected.checkpoint:
        return route
    if claim is IdempotencyClaimStatus.ALREADY_COMPLETED and not authoritative:
        return route
    if route.checkpoint_agent_item_id == agent_message.agent_item_id:
        return route
    return await repository.advance_projection_checkpoint(
        route.route_id,
        expected_agent_item_id=route.checkpoint_agent_item_id,
        agent_item_id=agent_message.agent_item_id,
        checkpointed_at=datetime.now(UTC),
    )

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from .adapters import (
    IdempotencyClaimStatus,
    ProjectionCheckpointConflict,
    ProjectionRouteConflict,
    ProjectionRouteRepository,
    TurnReplyCorrelationConflict,
)
from .contracts import (
    AgentMessage,
    ConversationRef,
    OutboundMessage,
    TextContent,
    TextFormat,
    ThreadProjectionRoute,
    ThreadRef,
    TurnReplyCorrelation,
    validate_projection_route,
    validate_turn_reply_correlation,
)
from .outbound_presentation import (
    PROJECTION_CHECKPOINT_METADATA_KEY,
    PROJECTION_ORIGIN_AUTHORITATIVE,
    PROJECTION_ORIGIN_LIVE,
    PROJECTION_ORIGIN_METADATA_KEY,
)

DeliverOutbound = Callable[
    [OutboundMessage],
    Awaitable[IdempotencyClaimStatus],
]


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


def _same_turn_reply_correlation(
    left: TurnReplyCorrelation,
    right: TurnReplyCorrelation,
) -> bool:
    return (
        left.correlation_id == right.correlation_id
        and left.thread_ref == right.thread_ref
        and left.turn_id == right.turn_id
        and left.client_message_id == right.client_message_id
        and left.conversation_ref == right.conversation_ref
        and left.reply_to_message_id == right.reply_to_message_id
    )


class InMemoryProjectionRouteRepository:
    """Process-local minimal Thread-to-Conversation delivery routing."""

    def __init__(self) -> None:
        self._routes: dict[
            tuple[ThreadRef, ConversationRef],
            ThreadProjectionRoute,
        ] = {}
        self._turn_correlations: dict[
            tuple[ThreadRef, str],
            TurnReplyCorrelation,
        ] = {}
        self._lock = asyncio.Lock()

    async def list_projection_routes(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[ThreadProjectionRoute, ...]:
        async with self._lock:
            routes = tuple(self._routes.values())
        if thread_ref is None:
            return routes
        return tuple(route for route in routes if route.thread_ref == thread_ref)

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            _reject_conflicting_route_id(self._routes.values(), route)
            key = (route.thread_ref, route.conversation_ref)
            stored = merge_projection_route(self._routes.get(key), route)
            self._routes[key] = stored
        return stored

    async def replace_thread_projection_routes(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            _reject_conflicting_route_id(self._routes.values(), route)
            key = (route.thread_ref, route.conversation_ref)
            stored = merge_projection_route(self._routes.get(key), route)
            for key in tuple(self._routes):
                if key[0] == route.thread_ref:
                    self._routes.pop(key)
            self._routes[(route.thread_ref, route.conversation_ref)] = stored
        return stored

    async def advance_projection_checkpoint(
        self,
        route_id: str,
        *,
        expected_agent_item_id: str | None,
        agent_item_id: str,
        checkpointed_at: datetime,
    ) -> ThreadProjectionRoute:
        async with self._lock:
            for key, route in self._routes.items():
                if route.route_id == route_id:
                    if route.checkpoint_agent_item_id != expected_agent_item_id:
                        raise ProjectionCheckpointConflict(
                            f"projection checkpoint changed for route {route_id}"
                        )
                    advanced = replace(
                        route,
                        checkpoint_agent_item_id=agent_item_id,
                        checkpointed_at=checkpointed_at,
                    )
                    validate_projection_route(advanced)
                    self._routes[key] = advanced
                    return advanced
        raise KeyError(f"projection route does not exist: {route_id}")

    async def delete_projection_routes(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef | None = None,
    ) -> int:
        async with self._lock:
            keys = tuple(
                key
                for key in self._routes
                if key[0] == thread_ref and (conversation_ref is None or key[1] == conversation_ref)
            )
            for key in keys:
                self._routes.pop(key)
            return len(keys)

    async def get_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> TurnReplyCorrelation | None:
        async with self._lock:
            return self._turn_correlations.get((thread_ref, turn_id))

    async def list_turn_reply_correlations(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[TurnReplyCorrelation, ...]:
        async with self._lock:
            correlations = tuple(self._turn_correlations.values())
        if thread_ref is None:
            return correlations
        return tuple(
            correlation for correlation in correlations if correlation.thread_ref == thread_ref
        )

    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation:
        validate_turn_reply_correlation(correlation)
        async with self._lock:
            key = (correlation.thread_ref, correlation.turn_id)
            current = self._turn_correlations.get(key)
            if current is None:
                self._turn_correlations[key] = correlation
                return correlation
            if not _same_turn_reply_correlation(current, correlation):
                raise TurnReplyCorrelationConflict(
                    "Turn reply correlation already belongs to another IM input"
                )
            return current

    async def delete_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> bool:
        async with self._lock:
            return self._turn_correlations.pop((thread_ref, turn_id), None) is not None

    async def delete_turn_reply_correlations(
        self,
        *,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int:
        if thread_ref is None and conversation_ref is None and older_than is None:
            raise ValueError("correlation deletion requires at least one selector")
        async with self._lock:
            keys = tuple(
                key
                for key, correlation in self._turn_correlations.items()
                if (thread_ref is None or correlation.thread_ref == thread_ref)
                and (conversation_ref is None or correlation.conversation_ref == conversation_ref)
                and (older_than is None or correlation.created_at < older_than)
            )
            for key in keys:
                self._turn_correlations.pop(key)
            return len(keys)


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
    """Keep a live observation distinct from a later completion of the same item."""

    return derive_projection_delivery_id(
        conversation_ref,
        thread_ref,
        f"imagent:live-event:{event_id}",
    )


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


def _reject_conflicting_route_id(
    routes: Iterable[ThreadProjectionRoute],
    replacement: ThreadProjectionRoute,
) -> None:
    for existing in routes:
        if existing.route_id == replacement.route_id and (
            existing.thread_ref != replacement.thread_ref
            or existing.conversation_ref != replacement.conversation_ref
        ):
            raise ProjectionRouteConflict(
                f"route ID belongs to different endpoints: {replacement.route_id}"
            )


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
    agent_message = projected.message
    delivery_id = (
        derive_projection_delivery_id(
            route.conversation_ref,
            agent_message.thread_ref,
            agent_message.agent_item_id,
        )
        if projected.checkpoint
        else derive_live_projection_delivery_id(
            route.conversation_ref,
            agent_message.thread_ref,
            projected.event_id or agent_message.agent_item_id,
        )
    )
    reply_to = route.reply_to_message_id
    if projected.turn_id is not None:
        correlation = await repository.get_turn_reply_correlation(
            route.thread_ref,
            projected.turn_id,
        )
        if correlation is not None and correlation.conversation_ref == route.conversation_ref:
            reply_to = correlation.reply_to_message_id
    metadata = dict(agent_message.metadata)
    metadata.update(
        {
            PROJECTION_ORIGIN_METADATA_KEY: (
                PROJECTION_ORIGIN_AUTHORITATIVE if authoritative else PROJECTION_ORIGIN_LIVE
            ),
            PROJECTION_CHECKPOINT_METADATA_KEY: projected.checkpoint,
        }
    )
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
            metadata=metadata,
        )
    )
    if claim is IdempotencyClaimStatus.IN_FLIGHT:
        raise RuntimeError(f"delivery remains in flight: {delivery_id}")
    if claim is IdempotencyClaimStatus.ALREADY_COMPLETED and not authoritative:
        return route
    if not projected.checkpoint:
        return route
    if route.checkpoint_agent_item_id == agent_message.agent_item_id:
        return route
    return await repository.advance_projection_checkpoint(
        route.route_id,
        expected_agent_item_id=route.checkpoint_agent_item_id,
        agent_item_id=agent_message.agent_item_id,
        checkpointed_at=datetime.now(UTC),
    )

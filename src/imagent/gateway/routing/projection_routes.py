"""Canonical Gateway projection-route contracts and persistence authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ...applications.contract import ThreadRef, validate_thread_ref
from ...interaction.messages import ConversationRef
from ...interaction.operations import ContractViolation, require_identifier
from ..persistence.repository_contracts import BindingRepository, ProjectionRouteRepository
from ..persistence.state_contracts import ThreadProjectionRoute, validate_projection_route
from .operations import (
    GatewayOperationType as _GatewayOperationType,
)
from .operations import (
    _complete_gateway_union,
    _GatewayOperation,
    _GatewayOperationSucceeded,
)


class ProjectionPolicy(StrEnum):
    FOREGROUND_ONLY = "foreground_only"
    REMEMBERED_LAST_RECIPIENT = "remembered_last_recipient"
    ALL_OBSERVERS = "all_observers"


@dataclass(frozen=True, slots=True, kw_only=True)
class ObserveThread(_GatewayOperation):
    thread_ref: ThreadRef
    reply_to_message_id: str | None = None
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.THREAD_OBSERVE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadObserved(_GatewayOperationSucceeded):
    route: ThreadProjectionRoute
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.THREAD_OBSERVE,
    )


def derive_projection_route_id(
    thread_ref: ThreadRef,
    conversation_ref: ConversationRef,
) -> str:
    """Derive a stable route identity from endpoint identities only."""

    identity = json.dumps(
        [
            thread_ref.project_ref.application_instance_id,
            thread_ref.project_ref.project_id,
            thread_ref.thread_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:projection:sha256:{digest}"


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


def _validate_observe_operation(operation: ObserveThread) -> None:
    validate_thread_ref(operation.thread_ref)
    if operation.reply_to_message_id is not None:
        require_identifier(operation.reply_to_message_id, "reply_to_message_id")


def _validate_observe_operation_result(
    operation: ObserveThread,
    result: object,
) -> None:
    if not isinstance(result, ThreadObserved):
        raise ContractViolation("thread.observe must return ThreadObserved")
    validate_projection_route(result.route)
    if result.route.thread_ref != operation.thread_ref:
        raise ContractViolation("thread.observe returned a different Thread")
    if result.route.conversation_ref != operation.conversation_ref:
        raise ContractViolation("thread.observe returned a different Conversation")
    if result.route.reply_to_message_id != operation.reply_to_message_id:
        raise ContractViolation("thread.observe returned different reply correlation")


@dataclass(frozen=True, slots=True)
class _RouteRefresh:
    route: ThreadProjectionRoute
    created: bool
    removed_routes: tuple[ThreadProjectionRoute, ...]


class _ProjectionRouteAuthority:
    """Resolve policy, route persistence, and active destinations only."""

    def __init__(
        self,
        *,
        bindings: BindingRepository,
        projections: ProjectionRouteRepository,
        policy: ProjectionPolicy,
    ) -> None:
        self._bindings = bindings
        self._projections = projections
        self._policy = policy

    async def active_routes(
        self,
        thread_ref: ThreadRef | None,
    ) -> tuple[ThreadProjectionRoute, ...]:
        if thread_ref is None:
            return ()
        routes = await self._projections.list_projection_routes(thread_ref)
        return await self._filter_active_routes(routes)

    async def active_persisted_routes(self) -> tuple[ThreadProjectionRoute, ...]:
        routes = await self._projections.list_projection_routes()
        return await self._filter_active_routes(routes)

    async def _filter_active_routes(
        self,
        routes: tuple[ThreadProjectionRoute, ...],
    ) -> tuple[ThreadProjectionRoute, ...]:
        if self._policy is not ProjectionPolicy.FOREGROUND_ONLY:
            return routes
        active: list[ThreadProjectionRoute] = []
        for route in routes:
            binding = await self._bindings.get(route.conversation_ref)
            if binding is not None and binding.thread_ref == route.thread_ref:
                active.append(route)
        return tuple(active)

    async def route_is_active(self, route: ThreadProjectionRoute) -> bool:
        return any(
            candidate.route_id == route.route_id
            for candidate in await self.active_routes(route.thread_ref)
        )

    async def refresh_route(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef,
        *,
        reply_to_message_id: str | None,
    ) -> _RouteRefresh:
        existing = await self._projections.list_projection_routes(thread_ref)
        current = next(
            (route for route in existing if route.conversation_ref == conversation_ref),
            None,
        )
        route = ThreadProjectionRoute(
            route_id=derive_projection_route_id(thread_ref, conversation_ref),
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
            reply_to_message_id=(
                reply_to_message_id
                if reply_to_message_id is not None
                else current.reply_to_message_id
                if current is not None
                else None
            ),
            updated_at=datetime.now(UTC),
        )
        if self._policy is ProjectionPolicy.REMEMBERED_LAST_RECIPIENT:
            stored = await self._projections.replace_thread_projection_routes(route)
            removed_routes = tuple(
                removed
                for removed in existing
                if removed.conversation_ref != stored.conversation_ref
            )
        else:
            stored = await self._projections.put_projection_route(route)
            removed_routes = ()
        return _RouteRefresh(
            route=stored,
            created=current is None,
            removed_routes=removed_routes,
        )


_complete_gateway_union()


__all__ = [
    "ObserveThread",
    "ProjectionPolicy",
    "ThreadObserved",
]

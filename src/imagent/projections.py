from __future__ import annotations

import asyncio
import hashlib
import json

from .contracts import (
    ConversationRef,
    ThreadProjectionRoute,
    ThreadRef,
    validate_projection_route,
)


class InMemoryProjectionRouteRepository:
    """Process-local minimal Thread-to-Conversation delivery routing."""

    def __init__(self) -> None:
        self._routes: dict[
            tuple[ThreadRef, ConversationRef],
            ThreadProjectionRoute,
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
            self._routes[(route.thread_ref, route.conversation_ref)] = route
        return route

    async def replace_thread_projection_routes(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            for key in tuple(self._routes):
                if key[0] == route.thread_ref:
                    self._routes.pop(key)
            self._routes[(route.thread_ref, route.conversation_ref)] = route
        return route


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

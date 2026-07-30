from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .contracts import (
    AcceptedTurn,
    AgentEvent,
    AgentInput,
    ApplicationOperation,
    ApplicationOperationResult,
    ApplicationSummary,
    ChannelCapabilities,
    ConversationBinding,
    ConversationRef,
    DeliveryReceipt,
    GatewayOperation,
    InboundMessage,
    OutboundMessage,
    ThreadProjectionRoute,
    ThreadRef,
    TurnReplyCorrelation,
)

MessageHandler = Callable[[InboundMessage], Awaitable[None]]
OperationHandler = Callable[[GatewayOperation], Awaitable[None]]


class IdempotencyClaimStatus(StrEnum):
    ACQUIRED = "acquired"
    ALREADY_COMPLETED = "already_completed"
    IN_FLIGHT = "in_flight"


class ProjectionCheckpointConflict(RuntimeError):
    """The stored route checkpoint no longer matches the caller's expectation."""


class ProjectionRouteConflict(RuntimeError):
    """A stable route ID was reused for different immutable endpoints."""


class ChannelAdapter(Protocol):
    @property
    def channel_instance_id(self) -> str: ...

    @property
    def capabilities(self) -> ChannelCapabilities: ...

    async def start(
        self,
        on_message: MessageHandler,
        on_operation: OperationHandler,
    ) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, message: OutboundMessage) -> DeliveryReceipt: ...


class AgentApplicationAdapter(Protocol):
    @property
    def summary(self) -> ApplicationSummary: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def execute(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult: ...

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
    ) -> AcceptedTurn: ...

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]: ...


class BindingRepository(Protocol):
    async def get(
        self,
        conversation: ConversationRef,
    ) -> ConversationBinding | None: ...

    async def put(
        self,
        binding: ConversationBinding,
        expected_revision: int | None = None,
    ) -> ConversationBinding: ...

    async def delete(
        self,
        conversation: ConversationRef,
        expected_revision: int | None = None,
    ) -> None: ...


class ProjectionRouteRepository(Protocol):
    async def list_projection_routes(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[ThreadProjectionRoute, ...]: ...

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute: ...

    async def replace_thread_projection_routes(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute: ...

    async def advance_projection_checkpoint(
        self,
        route_id: str,
        *,
        expected_agent_item_id: str | None,
        agent_item_id: str,
        checkpointed_at: datetime,
    ) -> ThreadProjectionRoute: ...

    async def delete_projection_routes(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef | None = None,
    ) -> int: ...

    async def get_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> TurnReplyCorrelation | None: ...

    async def list_turn_reply_correlations(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[TurnReplyCorrelation, ...]: ...

    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation: ...

    async def delete_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> bool: ...

    async def delete_turn_reply_correlations(
        self,
        *,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int: ...


class IdempotencyRepository(Protocol):
    async def claim(self, scope: str, key: str) -> IdempotencyClaimStatus: ...

    async def complete(self, scope: str, key: str) -> None: ...

    async def release(self, scope: str, key: str) -> None: ...

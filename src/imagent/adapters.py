from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from .contracts import (
    AcceptedTurn,
    AgentEvent,
    AgentInput,
    ApplicationSummary,
    ChannelCapabilities,
    ChannelMessage,
    ConversationBinding,
    ConversationRef,
    DeliveryReceipt,
    Operation,
    OperationResult,
    ThreadRef,
)

MessageHandler = Callable[[ChannelMessage], Awaitable[None]]
OperationHandler = Callable[[Operation], Awaitable[None]]


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

    async def send(self, message: ChannelMessage) -> DeliveryReceipt: ...


class AgentApplicationAdapter(Protocol):
    @property
    def summary(self) -> ApplicationSummary: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def execute(self, operation: Operation) -> OperationResult: ...

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


class IdempotencyRepository(Protocol):
    async def claim(self, scope: str, key: str) -> bool: ...

    async def complete(self, scope: str, key: str) -> None: ...

    async def release(self, scope: str, key: str) -> None: ...

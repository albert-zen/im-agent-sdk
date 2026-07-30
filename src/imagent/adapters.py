from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from .contracts import (
    AcceptedTurn,
    AgentEvent,
    AgentInput,
    ApplicationCapabilities,
    ApplicationSummary,
    ChannelCapabilities,
    ChannelMessage,
    ConversationBinding,
    ConversationRef,
    DeliveryReceipt,
    Operation,
    Page,
    ProjectRef,
    ProjectSummary,
    ThreadRef,
    ThreadSnapshot,
    ThreadStatus,
    ThreadSummary,
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

    @property
    def capabilities(self) -> ApplicationCapabilities: ...

    async def list_projects(self, cursor: str | None = None) -> Page[ProjectSummary]: ...

    async def get_project(self, project_ref: ProjectRef) -> ProjectSummary: ...

    async def list_threads(
        self,
        project_ref: ProjectRef | None = None,
        cursor: str | None = None,
    ) -> Page[ThreadSummary]: ...

    async def create_thread(
        self,
        project_ref: ProjectRef | None = None,
        title: str | None = None,
    ) -> ThreadSummary: ...

    async def get_thread(self, thread_ref: ThreadRef) -> ThreadSummary: ...

    async def delete_thread(self, thread_ref: ThreadRef) -> None: ...

    async def read_thread(
        self,
        thread_ref: ThreadRef,
        cursor: str | None = None,
    ) -> ThreadSnapshot: ...

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

    async def get_thread_status(self, thread_ref: ThreadRef) -> ThreadStatus: ...

    async def interrupt_turn(
        self,
        thread_ref: ThreadRef,
        turn_id: str | None = None,
    ) -> None: ...

    async def respond_request(self, request_id: str, response: object) -> None: ...


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

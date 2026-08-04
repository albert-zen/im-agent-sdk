from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from ..contracts import (
    AcceptedTurn,
    AgentInput,
    ApplicationInputDispatch,
    ApplicationOperation,
    ApplicationOperationResult,
    ApplicationSummary,
    InputContinuationPreference,
    InteractiveRequest,
    ThreadRef,
)
from .events import AgentEvent

ApplicationInputDispatchHandler = Callable[[ApplicationInputDispatch], Awaitable[None]]


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
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn: ...

    async def list_pending_requests(self) -> tuple[InteractiveRequest, ...]: ...

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

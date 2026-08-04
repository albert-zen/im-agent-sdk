from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

from ..interaction.messages import Content, MessageRole, Metadata
from ..interaction.operations import ContractViolation, require_identifier
from .capabilities import ApplicationCapabilities

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ApplicationRef:
    application_instance_id: str


@dataclass(frozen=True, slots=True)
class ProjectRef:
    application_instance_id: str
    native_project_id: str


@dataclass(frozen=True, slots=True)
class ThreadRef:
    application_instance_id: str
    native_thread_id: str
    project_ref: ProjectRef | None = None


def validate_thread_ref(thread: ThreadRef) -> None:
    require_identifier(
        thread.application_instance_id,
        "application_instance_id",
    )
    require_identifier(thread.native_thread_id, "native_thread_id")
    if (
        thread.project_ref is not None
        and thread.project_ref.application_instance_id != thread.application_instance_id
    ):
        raise ContractViolation("thread and project belong to different application instances")


class InputContinuationPreference(StrEnum):
    PREFER_ACTIVE_TURN = "prefer_active_turn"
    START_NEW_TURN = "start_new_turn"


class InputDisposition(StrEnum):
    STARTED = "started"
    STEERED = "steered"


class TurnReplyCorrelationPolicy(StrEnum):
    CREATE_NEW = "create_new"
    PRESERVE_EXISTING = "preserve_existing"


class ThreadStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_INPUT = "waiting_for_input"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ApplicationSummary:
    ref: ApplicationRef
    kind: str
    display_name: str
    capabilities: ApplicationCapabilities
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    ref: ProjectRef
    display_name: str
    root_path: str | None = None
    repo_url: str | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ThreadSummary:
    ref: ThreadRef
    status: ThreadStatus
    title: str | None = None
    updated_at: datetime | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentInput:
    client_message_id: str
    content: tuple[Content, ...]
    sender: str | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentMessage:
    agent_item_id: str
    thread_ref: ThreadRef
    role: MessageRole
    content: tuple[Content, ...]
    created_at: datetime
    client_message_id: str | None = None
    metadata: Metadata = field(default_factory=dict)


class TurnStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TurnCatchup:
    thread_ref: ThreadRef
    turn_id: str | None
    status: TurnStatus
    messages: tuple[AgentMessage, ...]
    updated_at: datetime | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnHistoryEntry:
    turn_id: str
    status: TurnStatus
    user_message: AgentMessage | None = None
    agent_messages: tuple[AgentMessage, ...] = ()
    error: str | None = None
    had_compaction: bool = False
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ThreadHistory:
    thread_ref: ThreadRef
    turns: tuple[TurnHistoryEntry, ...]
    page: int = 1
    has_older: bool = False
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ThreadSnapshot:
    thread: ThreadSummary
    messages: tuple[AgentMessage, ...]
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptedTurn:
    thread_ref: ThreadRef
    turn_id: str
    client_message_id: str
    disposition: InputDisposition = InputDisposition.STARTED
    correlation_policy: TurnReplyCorrelationPolicy = TurnReplyCorrelationPolicy.CREATE_NEW


@dataclass(frozen=True, slots=True)
class ApplicationInputDispatch:
    thread_ref: ThreadRef
    client_message_id: str
    disposition: InputDisposition
    correlation_policy: TurnReplyCorrelationPolicy
    expected_turn_id: str | None = None


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


# Bind dependent Application contracts only after this owner has defined the
# complete model family. These module-level imports preserve runtime type hints
# without a function-local reverse import or a second model implementation.
from .events import AgentEvent  # noqa: E402
from .operations import (  # noqa: E402
    ApplicationOperation,
    ApplicationOperationResult,
)
from .requests import InteractiveRequest  # noqa: E402

__all__ = [
    "AcceptedTurn",
    "AgentApplicationAdapter",
    "AgentInput",
    "AgentMessage",
    "ApplicationInputDispatch",
    "ApplicationInputDispatchHandler",
    "ApplicationRef",
    "ApplicationSummary",
    "InputContinuationPreference",
    "InputDisposition",
    "Page",
    "ProjectRef",
    "ProjectSummary",
    "ThreadHistory",
    "ThreadRef",
    "ThreadSnapshot",
    "ThreadStatus",
    "ThreadSummary",
    "TurnCatchup",
    "TurnHistoryEntry",
    "TurnReplyCorrelationPolicy",
    "TurnStatus",
    "validate_thread_ref",
]

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

from ..interaction.messages import Content, MessageRole, Metadata
from ..interaction.operations import ContractViolation, require_identifier
from .capabilities import (
    ApplicationCapabilities,
    ProjectMode,
    validate_application_capabilities,
)

T = TypeVar("T")
MAX_WORKSPACE_ROOT_LENGTH = 4096


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
    project_id: str


@dataclass(frozen=True, slots=True)
class ThreadRef:
    project_ref: ProjectRef
    thread_id: str


@dataclass(frozen=True, slots=True)
class TurnRef:
    thread_ref: ThreadRef
    turn_id: str


@dataclass(frozen=True, slots=True)
class WorkspaceIdentity:
    project_ref: ProjectRef
    root_fingerprint: str


def validate_project_ref(project: ProjectRef) -> None:
    require_identifier(project.application_instance_id, "application_instance_id")
    require_identifier(project.project_id, "project_id")


def validate_thread_ref(thread: ThreadRef) -> None:
    validate_project_ref(thread.project_ref)
    require_identifier(thread.thread_id, "thread_id")


def validate_turn_ref(turn: TurnRef) -> None:
    validate_thread_ref(turn.thread_ref)
    require_identifier(turn.turn_id, "turn_id")


def validate_workspace_identity(identity: WorkspaceIdentity) -> None:
    validate_project_ref(identity.project_ref)
    _validate_workspace_root_fingerprint(identity.root_fingerprint)


class InputContinuationPreference(StrEnum):
    PREFER_ACTIVE_TURN = "prefer_active_turn"
    START_NEW_TURN = "start_new_turn"


class ApplicationInputOutcomeUnknown(RuntimeError):
    """Native input dispatch may have succeeded, so automatic retry is unsafe."""

    def __init__(self, message: str, cause: BaseException) -> None:
        super().__init__(message)
        self.cause = cause


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
    workspace_identity: WorkspaceIdentity | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    ref: ProjectRef
    display_name: str
    root_path: str | None = None
    repo_url: str | None = None
    workspace_root_fingerprint: str | None = None
    metadata: Metadata = field(default_factory=dict)


def fingerprint_canonical_workspace_root(canonical_root: str) -> str:
    """Fingerprint one already-canonical execution root without retaining it."""

    try:
        encoded_root = canonical_root.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ContractViolation("canonical workspace root must be valid UTF-8 text") from error
    if not encoded_root or len(encoded_root) > MAX_WORKSPACE_ROOT_LENGTH:
        raise ContractViolation(
            "canonical workspace root must be between 1 and "
            f"{MAX_WORKSPACE_ROOT_LENGTH} UTF-8 bytes"
        )
    return hashlib.sha256(encoded_root).hexdigest()


def validate_project_summary(project: ProjectSummary) -> None:
    validate_project_ref(project.ref)
    if not project.display_name:
        raise ContractViolation("project display_name cannot be empty")
    if project.root_path is not None and not project.root_path:
        raise ContractViolation("project root_path cannot be empty")
    fingerprint = project.workspace_root_fingerprint
    if fingerprint is not None:
        _validate_workspace_root_fingerprint(fingerprint)


def validate_application_summary(summary: ApplicationSummary) -> None:
    require_identifier(summary.ref.application_instance_id, "application_instance_id")
    require_identifier(summary.kind, "kind")
    if not summary.display_name:
        raise ContractViolation("application display_name cannot be empty")
    validate_application_capabilities(summary.capabilities)
    identity = summary.workspace_identity
    mode = summary.capabilities.projects.mode
    if mode is ProjectMode.MANAGED:
        if identity is not None:
            raise ContractViolation("managed application cannot declare one workspace identity")
        return
    if identity is None:
        raise ContractViolation(f"{mode.value} application requires workspace identity")
    validate_workspace_identity(identity)
    if identity.project_ref.application_instance_id != summary.ref.application_instance_id:
        raise ContractViolation("workspace identity belongs to a different application")


def _validate_workspace_root_fingerprint(fingerprint: str) -> None:
    if (
        len(fingerprint) != 64
        or fingerprint != fingerprint.lower()
        or any(character not in "0123456789abcdef" for character in fingerprint)
    ):
        raise ContractViolation("workspace root fingerprint must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class ThreadSummary:
    ref: ThreadRef
    status: ThreadStatus
    title: str | None = None
    updated_at: datetime | None = None
    metadata: Metadata = field(default_factory=dict)


def validate_thread_summary(summary: ThreadSummary) -> None:
    validate_thread_ref(summary.ref)


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


def validate_agent_message(message: AgentMessage) -> None:
    require_identifier(message.agent_item_id, "agent_item_id")
    validate_thread_ref(message.thread_ref)


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
    turn_ref: TurnRef | None
    status: TurnStatus
    messages: tuple[AgentMessage, ...]
    updated_at: datetime | None = None
    metadata: Metadata = field(default_factory=dict)


def validate_turn_catchup(catchup: TurnCatchup) -> None:
    validate_thread_ref(catchup.thread_ref)
    if catchup.turn_ref is None:
        if catchup.status is not TurnStatus.IDLE:
            raise ContractViolation("non-idle catch-up requires a Turn")
    else:
        validate_turn_ref(catchup.turn_ref)
        if catchup.turn_ref.thread_ref != catchup.thread_ref:
            raise ContractViolation("catch-up Turn belongs to a different Thread")
    for message in catchup.messages:
        validate_agent_message(message)
        if message.thread_ref != catchup.thread_ref:
            raise ContractViolation("catch-up message belongs to a different Thread")


@dataclass(frozen=True, slots=True)
class TurnHistoryEntry:
    turn_ref: TurnRef
    status: TurnStatus
    user_message: AgentMessage | None = None
    agent_messages: tuple[AgentMessage, ...] = ()
    error: str | None = None
    had_compaction: bool = False
    metadata: Metadata = field(default_factory=dict)


def validate_turn_history_entry(entry: TurnHistoryEntry) -> None:
    validate_turn_ref(entry.turn_ref)
    messages = (
        *((entry.user_message,) if entry.user_message is not None else ()),
        *entry.agent_messages,
    )
    for message in messages:
        validate_agent_message(message)
        if message.thread_ref != entry.turn_ref.thread_ref:
            raise ContractViolation("history message belongs to a different Thread")


@dataclass(frozen=True, slots=True)
class ThreadHistory:
    thread_ref: ThreadRef
    turns: tuple[TurnHistoryEntry, ...]
    page: int = 1
    has_older: bool = False
    metadata: Metadata = field(default_factory=dict)


def validate_thread_history(history: ThreadHistory) -> None:
    validate_thread_ref(history.thread_ref)
    if history.page < 1:
        raise ContractViolation("history page must be positive")
    for entry in history.turns:
        validate_turn_history_entry(entry)
        if entry.turn_ref.thread_ref != history.thread_ref:
            raise ContractViolation("history Turn belongs to a different Thread")


@dataclass(frozen=True, slots=True)
class ThreadSnapshot:
    thread: ThreadSummary
    messages: tuple[AgentMessage, ...]
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptedTurn:
    turn_ref: TurnRef
    client_message_id: str
    disposition: InputDisposition = InputDisposition.STARTED
    correlation_policy: TurnReplyCorrelationPolicy = TurnReplyCorrelationPolicy.CREATE_NEW


@dataclass(frozen=True, slots=True)
class ApplicationInputDispatch:
    thread_ref: ThreadRef
    client_message_id: str
    disposition: InputDisposition
    correlation_policy: TurnReplyCorrelationPolicy
    expected_turn_ref: TurnRef | None = None


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
    "ApplicationInputOutcomeUnknown",
    "ApplicationRef",
    "ApplicationSummary",
    "MAX_WORKSPACE_ROOT_LENGTH",
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
    "TurnRef",
    "TurnCatchup",
    "TurnHistoryEntry",
    "TurnReplyCorrelationPolicy",
    "TurnStatus",
    "WorkspaceIdentity",
    "fingerprint_canonical_workspace_root",
    "validate_agent_message",
    "validate_application_summary",
    "validate_project_ref",
    "validate_project_summary",
    "validate_thread_ref",
    "validate_thread_history",
    "validate_thread_summary",
    "validate_turn_catchup",
    "validate_turn_history_entry",
    "validate_turn_ref",
    "validate_workspace_identity",
]

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeAlias, TypeVar

from ..interaction.media import (
    AttachmentSourceKind,
)
from ..interaction.messages import (
    Content,
    ConversationRef,
    MessageRole,
    Metadata,
)

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


class SupportLevel(StrEnum):
    NATIVE = "native"
    FALLBACK = "fallback"
    UNSUPPORTED = "unsupported"


class EventSequenceScope(StrEnum):
    NONE = "none"
    THREAD = "thread"
    APPLICATION = "application"


class ProjectionPolicy(StrEnum):
    FOREGROUND_ONLY = "foreground_only"
    REMEMBERED_LAST_RECIPIENT = "remembered_last_recipient"
    ALL_OBSERVERS = "all_observers"


class InputContinuationPreference(StrEnum):
    PREFER_ACTIVE_TURN = "prefer_active_turn"
    START_NEW_TURN = "start_new_turn"


class InputDisposition(StrEnum):
    STARTED = "started"
    STEERED = "steered"


class TurnReplyCorrelationPolicy(StrEnum):
    CREATE_NEW = "create_new"
    PRESERVE_EXISTING = "preserve_existing"


class InteractiveRequestKind(StrEnum):
    APPROVAL = "approval"
    USER_INPUT = "user_input"


class RequestResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    STALE = "stale"


class RequestRouteState(StrEnum):
    OPEN = "open"
    RESPONDED = "responded"
    RESOLVED = "resolved"
    STALE = "stale"


class ProjectMode(StrEnum):
    MANAGED = "managed"
    FLAT = "flat"
    FIXED = "fixed"


class ThreadDeletionCapability(StrEnum):
    UNSUPPORTED = "unsupported"
    ARCHIVE = "archive"
    PERMANENT = "permanent"


@dataclass(frozen=True, slots=True)
class ProjectCapabilities:
    mode: ProjectMode
    discovery: SupportLevel
    reading: SupportLevel
    creation: SupportLevel = SupportLevel.UNSUPPORTED
    deletion: SupportLevel = SupportLevel.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class ThreadCapabilities:
    listing: SupportLevel
    creation: SupportLevel
    reading: SupportLevel
    deletion: ThreadDeletionCapability = ThreadDeletionCapability.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    history: SupportLevel
    streaming: SupportLevel
    replay_from_cursor: SupportLevel
    interruption: SupportLevel
    interactive_requests: SupportLevel
    pending_request_snapshot: SupportLevel = SupportLevel.UNSUPPORTED
    native_thread_activation: SupportLevel = SupportLevel.UNSUPPORTED
    gap_detection: SupportLevel = SupportLevel.UNSUPPORTED
    event_sequence_scope: EventSequenceScope = EventSequenceScope.NONE


@dataclass(frozen=True, slots=True)
class ApplicationCapabilities:
    projects: ProjectCapabilities
    threads: ThreadCapabilities
    runtime: RuntimeCapabilities
    attachment_sources: tuple[AttachmentSourceKind, ...] = ()


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


@dataclass(frozen=True, slots=True)
class RequestRef:
    """Application-scoped opaque identity for an interactive request."""

    application_ref: ApplicationRef
    native_request_id: str


@dataclass(frozen=True, slots=True)
class RequestChoice:
    choice_id: str
    label: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    request_ref: RequestRef
    thread_ref: ThreadRef
    turn_id: str
    prompt: str
    choices: tuple[RequestChoice, ...]
    expires_at: datetime | None = None
    metadata: Metadata = field(default_factory=dict)
    kind: InteractiveRequestKind = field(
        init=False,
        default=InteractiveRequestKind.APPROVAL,
    )


@dataclass(frozen=True, slots=True)
class UserInputQuestion:
    question_id: str
    prompt: str
    header: str | None = None
    choices: tuple[RequestChoice, ...] = ()
    allows_other: bool = False
    secret: bool = False
    min_answers: int = 1
    max_answers: int = 1


@dataclass(frozen=True, slots=True)
class UserInputRequest:
    request_ref: RequestRef
    thread_ref: ThreadRef
    turn_id: str
    questions: tuple[UserInputQuestion, ...]
    prompt: str | None = None
    expires_at: datetime | None = None
    metadata: Metadata = field(default_factory=dict)
    kind: InteractiveRequestKind = field(
        init=False,
        default=InteractiveRequestKind.USER_INPUT,
    )


InteractiveRequest: TypeAlias = ApprovalRequest | UserInputRequest


@dataclass(frozen=True, slots=True)
class ApprovalResponseShape:
    choice_ids: tuple[str, ...]
    kind: InteractiveRequestKind = field(
        init=False,
        default=InteractiveRequestKind.APPROVAL,
    )


@dataclass(frozen=True, slots=True)
class UserInputQuestionShape:
    question_id: str
    choice_ids: tuple[str, ...]
    allows_other: bool
    min_answers: int
    max_answers: int


@dataclass(frozen=True, slots=True)
class UserInputResponseShape:
    questions: tuple[UserInputQuestionShape, ...]
    kind: InteractiveRequestKind = field(
        init=False,
        default=InteractiveRequestKind.USER_INPUT,
    )


RequestResponseShape: TypeAlias = ApprovalResponseShape | UserInputResponseShape


@dataclass(frozen=True, slots=True)
class RequestResolution:
    request_ref: RequestRef
    status: RequestResolutionStatus
    resolved_at: datetime


@dataclass(frozen=True, slots=True)
class RequestRouteCorrelation:
    correlation_id: str
    request_ref: RequestRef
    thread_ref: ThreadRef
    turn_id: str
    conversation_ref: ConversationRef
    delivery_id: str
    response_shape: RequestResponseShape
    state: RequestRouteState
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class AgentEventType(StrEnum):
    MESSAGE_CREATED = "message.created"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    THREAD_CREATED = "thread.created"
    THREAD_UPDATED = "thread.updated"
    THREAD_DELETED = "thread.deleted"
    TURN_STARTED = "turn.started"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_INTERRUPTED = "turn.interrupted"
    STATUS_CHANGED = "status.changed"
    REQUEST_OPENED = "request.opened"
    REQUEST_RESOLVED = "request.resolved"
    BINDING_CHANGED = "binding.changed"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_id: str
    application_instance_id: str
    type: AgentEventType
    data: Metadata
    created_at: datetime
    project_ref: ProjectRef | None = None
    thread_ref: ThreadRef | None = None
    turn_id: str | None = None
    sequence: int | None = None
    sequence_epoch: str | None = None
    cursor: str | None = None
    request: InteractiveRequest | None = None
    request_resolution: RequestResolution | None = None


@dataclass(frozen=True, slots=True)
class ConversationBinding:
    conversation_ref: ConversationRef
    application_ref: ApplicationRef | None = None
    project_ref: ProjectRef | None = None
    thread_ref: ThreadRef | None = None
    revision: int = 0
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ThreadProjectionRoute:
    route_id: str
    thread_ref: ThreadRef
    conversation_ref: ConversationRef
    reply_to_message_id: str | None = None
    checkpoint_agent_item_id: str | None = None
    checkpointed_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TurnReplyCorrelation:
    correlation_id: str
    thread_ref: ThreadRef
    turn_id: str
    client_message_id: str
    conversation_ref: ConversationRef
    reply_to_message_id: str
    created_at: datetime

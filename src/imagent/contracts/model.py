from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeAlias, TypeVar

Metadata: TypeAlias = Mapping[str, object]
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


@dataclass(frozen=True, slots=True)
class ConversationRef:
    channel_instance_id: str
    native_conversation_id: str


class SupportLevel(StrEnum):
    NATIVE = "native"
    FALLBACK = "fallback"
    UNSUPPORTED = "unsupported"


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
    native_thread_activation: SupportLevel = SupportLevel.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class ApplicationCapabilities:
    projects: ProjectCapabilities
    threads: ThreadCapabilities
    runtime: RuntimeCapabilities


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    plain_text: SupportLevel = SupportLevel.NATIVE
    markdown: SupportLevel = SupportLevel.UNSUPPORTED
    message_edits: SupportLevel = SupportLevel.UNSUPPORTED
    message_deletion: SupportLevel = SupportLevel.UNSUPPORTED
    typing_indicators: SupportLevel = SupportLevel.UNSUPPORTED
    interactive_actions: SupportLevel = SupportLevel.UNSUPPORTED
    attachments: SupportLevel = SupportLevel.UNSUPPORTED
    reply_references: SupportLevel = SupportLevel.UNSUPPORTED
    native_threads_or_topics: SupportLevel = SupportLevel.UNSUPPORTED
    max_text_length: int | None = None
    max_attachment_size: int | None = None


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


class TextFormat(StrEnum):
    PLAIN = "plain"
    MARKDOWN = "markdown"


@dataclass(frozen=True, slots=True)
class TextContent:
    text: str
    format: TextFormat = TextFormat.PLAIN


@dataclass(frozen=True, slots=True)
class AttachmentContent:
    attachment_id: str
    media_type: str
    filename: str | None = None
    size_bytes: int | None = None
    url: str | None = None
    metadata: Metadata = field(default_factory=dict)


Content: TypeAlias = TextContent | AttachmentContent


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class InboundMessage:
    message_id: str
    conversation_ref: ConversationRef
    sender: str
    content: tuple[Content, ...]
    created_at: datetime
    reply_to: str | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    delivery_id: str
    conversation_ref: ConversationRef
    content: tuple[Content, ...]
    created_at: datetime
    reply_to: str | None = None
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
    agent_message: AgentMessage | None = None
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


@dataclass(frozen=True, slots=True)
class ContractError:
    code: str
    message: str
    retryable: bool = False
    metadata: Metadata = field(default_factory=dict)


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
    sequence: int
    type: AgentEventType
    data: Metadata
    created_at: datetime
    project_ref: ProjectRef | None = None
    thread_ref: ThreadRef | None = None
    turn_id: str | None = None
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationBinding:
    conversation_ref: ConversationRef
    application_ref: ApplicationRef | None = None
    project_ref: ProjectRef | None = None
    thread_ref: ThreadRef | None = None
    revision: int = 0
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    status: str
    native_message_id: str | None = None
    detail: str | None = None

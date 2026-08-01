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


class DeliveryReceiptStatus(StrEnum):
    ACCEPTED_BY_PLATFORM = "accepted_by_platform"
    REJECTED_BY_PLATFORM = "rejected_by_platform"
    RETRYABLE_FAILURE = "retryable_failure"
    UNKNOWN = "unknown"


class DeliveryItemStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RETRYABLE_FAILURE = "retryable_failure"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


class DeliverySegmentStatus(StrEnum):
    ACCEPTED_BY_PLATFORM = "accepted_by_platform"
    REJECTED_BY_PLATFORM = "rejected_by_platform"
    RETRYABLE_FAILURE = "retryable_failure"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


class ProjectMode(StrEnum):
    MANAGED = "managed"
    FLAT = "flat"
    FIXED = "fixed"


class ThreadDeletionCapability(StrEnum):
    UNSUPPORTED = "unsupported"
    ARCHIVE = "archive"
    PERMANENT = "permanent"


class AttachmentSourceKind(StrEnum):
    LOCAL_PATH = "local_path"
    REMOTE_URL = "remote_url"
    ATTACHMENT_HANDLE = "attachment_handle"


class TextLengthUnit(StrEnum):
    CODE_POINTS = "code_points"
    UTF16_CODE_UNITS = "utf16_code_units"
    UTF8_BYTES = "utf8_bytes"


class AttachmentGrouping(StrEnum):
    NONE = "none"
    SAME_MEDIA_FAMILY = "same_media_family"
    MIXED = "mixed"


class ReplyReferenceScope(StrEnum):
    FIRST_SEGMENT = "first_segment"
    EVERY_SEGMENT = "every_segment"


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


@dataclass(frozen=True, slots=True)
class DeliveryProfile:
    plain_text: SupportLevel = SupportLevel.NATIVE
    markdown: SupportLevel = SupportLevel.UNSUPPORTED
    attachments: SupportLevel = SupportLevel.UNSUPPORTED
    attachment_sources: tuple[AttachmentSourceKind, ...] = ()
    attachment_media_types: tuple[str, ...] = ()
    attachment_grouping: AttachmentGrouping = AttachmentGrouping.NONE
    reply_references: SupportLevel = SupportLevel.UNSUPPORTED
    reply_reference_scope: ReplyReferenceScope = ReplyReferenceScope.FIRST_SEGMENT
    text_length_unit: TextLengthUnit = TextLengthUnit.CODE_POINTS
    max_text_length: int | None = None
    max_attachment_size: int | None = None
    max_attachment_count: int | None = None
    max_attachment_group_size: int | None = None


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    # Keep the v1 flat constructor/attribute surface. ``delivery`` below is a
    # derived planning view, so there is one source of capability truth and
    # existing Channel adapters do not need a flag-day migration.
    plain_text: SupportLevel = SupportLevel.NATIVE
    markdown: SupportLevel = SupportLevel.UNSUPPORTED
    message_edits: SupportLevel = SupportLevel.UNSUPPORTED
    message_deletion: SupportLevel = SupportLevel.UNSUPPORTED
    typing_indicators: SupportLevel = SupportLevel.UNSUPPORTED
    interactive_actions: SupportLevel = SupportLevel.UNSUPPORTED
    attachments: SupportLevel = SupportLevel.UNSUPPORTED
    reply_references: SupportLevel = SupportLevel.UNSUPPORTED
    native_threads_or_topics: SupportLevel = SupportLevel.UNSUPPORTED
    attachment_sources: tuple[AttachmentSourceKind, ...] = ()
    max_text_length: int | None = None
    max_attachment_size: int | None = None
    max_attachment_count: int | None = None
    attachment_media_types: tuple[str, ...] = ()
    attachment_grouping: AttachmentGrouping = AttachmentGrouping.NONE
    reply_reference_scope: ReplyReferenceScope = ReplyReferenceScope.FIRST_SEGMENT
    text_length_unit: TextLengthUnit = TextLengthUnit.CODE_POINTS
    max_attachment_group_size: int | None = None

    @property
    def delivery(self) -> DeliveryProfile:
        """Return the deterministic planner view without duplicating state."""

        return DeliveryProfile(
            plain_text=self.plain_text,
            markdown=self.markdown,
            attachments=self.attachments,
            attachment_sources=self.attachment_sources,
            attachment_media_types=self.attachment_media_types,
            attachment_grouping=self.attachment_grouping,
            reply_references=self.reply_references,
            reply_reference_scope=self.reply_reference_scope,
            text_length_unit=self.text_length_unit,
            max_text_length=self.max_text_length,
            max_attachment_size=self.max_attachment_size,
            max_attachment_count=self.max_attachment_count,
            max_attachment_group_size=self.max_attachment_group_size,
        )


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
class LocalPath:
    path: str
    kind: AttachmentSourceKind = field(
        init=False,
        default=AttachmentSourceKind.LOCAL_PATH,
    )


@dataclass(frozen=True, slots=True)
class RemoteUrl:
    url: str
    kind: AttachmentSourceKind = field(
        init=False,
        default=AttachmentSourceKind.REMOTE_URL,
    )


@dataclass(frozen=True, slots=True)
class AttachmentHandle:
    handle_id: str
    kind: AttachmentSourceKind = field(
        init=False,
        default=AttachmentSourceKind.ATTACHMENT_HANDLE,
    )


AttachmentSource: TypeAlias = LocalPath | RemoteUrl | AttachmentHandle


@dataclass(frozen=True, slots=True)
class AttachmentContent:
    attachment_id: str
    media_type: str
    source: AttachmentSource
    filename: str | None = None
    size_bytes: int | None = None
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


@dataclass(frozen=True, slots=True)
class DeliveryItemReceipt:
    content_index: int
    status: DeliveryItemStatus
    attachment_id: str | None = None
    native_message_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DeliverySegmentReceipt:
    segment_index: int
    delivery_id: str
    source_content_indexes: tuple[int, ...]
    status: DeliverySegmentStatus
    native_message_id: str | None = None
    detail: str | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    status: DeliveryReceiptStatus
    native_message_id: str | None = None
    detail: str | None = None
    items: tuple[DeliveryItemReceipt, ...] = ()
    segments: tuple[DeliverySegmentReceipt, ...] = ()
    retry_after_seconds: float | None = None

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from .model import (
    ApplicationRef,
    ApplicationSummary,
    Content,
    ContractError,
    ConversationBinding,
    ConversationRef,
    Page,
    ProjectRef,
    ProjectSummary,
    ThreadHistory,
    ThreadRef,
    ThreadStatus,
    ThreadSummary,
    TurnCatchup,
)


class OperationResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ApplicationOperationType(StrEnum):
    PROJECT_LIST = "project.list"
    PROJECT_GET = "project.get"
    THREAD_CREATE = "thread.create"
    THREAD_LIST = "thread.list"
    THREAD_GET = "thread.get"
    THREAD_ACTIVATE_NATIVE = "thread.activate_native"
    THREAD_DELETE = "thread.delete"
    THREAD_STATUS = "thread.status"
    THREAD_HISTORY = "thread.history"
    TURN_CATCHUP = "turn.catchup"
    TURN_INTERRUPT = "turn.interrupt"
    REQUEST_RESPOND = "request.respond"


class GatewayOperationType(StrEnum):
    APPLICATION_LIST = "application.list"
    APPLICATION_SELECT = "application.select"
    CONVERSATION_BIND_PROJECT = "conversation.bind_project"
    CONVERSATION_BIND_THREAD = "conversation.bind_thread"
    CONVERSATION_CLEAR_THREAD = "conversation.clear_thread"


class ThreadDeletionMode(StrEnum):
    ARCHIVE = "archive"
    PERMANENT = "permanent"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ApprovalResponse:
    decision: ApprovalDecision
    kind: str = field(init=False, default="approval")


@dataclass(frozen=True, slots=True)
class UserInputResponse:
    values: Mapping[str, str]
    kind: str = field(init=False, default="user_input")


RequestResponse: TypeAlias = ApprovalResponse | UserInputResponse


@dataclass(frozen=True, slots=True, kw_only=True)
class _ApplicationOperation:
    operation_id: str
    application_ref: ApplicationRef
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ListProjects(_ApplicationOperation):
    query: str | None = None
    cursor: str | None = None
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.PROJECT_LIST,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class GetProject(_ApplicationOperation):
    project_ref: ProjectRef
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.PROJECT_GET,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateThread(_ApplicationOperation):
    project_ref: ProjectRef | None = None
    title: str | None = None
    initial_context: tuple[Content, ...] = ()
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_CREATE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ListThreads(_ApplicationOperation):
    project_ref: ProjectRef | None = None
    query: str | None = None
    cursor: str | None = None
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_LIST,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class GetThread(_ApplicationOperation):
    thread_ref: ThreadRef
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_GET,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivateNativeThread(_ApplicationOperation):
    thread_ref: ThreadRef
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_ACTIVATE_NATIVE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteThread(_ApplicationOperation):
    thread_ref: ThreadRef
    mode: ThreadDeletionMode
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_DELETE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class GetThreadStatus(_ApplicationOperation):
    thread_ref: ThreadRef
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_STATUS,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class GetThreadHistory(_ApplicationOperation):
    thread_ref: ThreadRef
    limit: int = 3
    page: int = 1
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_HISTORY,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class GetTurnCatchup(_ApplicationOperation):
    thread_ref: ThreadRef
    limit: int = 5
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.TURN_CATCHUP,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class InterruptTurn(_ApplicationOperation):
    thread_ref: ThreadRef
    turn_id: str | None = None
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.TURN_INTERRUPT,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RespondRequest(_ApplicationOperation):
    request_id: str
    response: RequestResponse
    thread_ref: ThreadRef | None = None
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.REQUEST_RESPOND,
    )


ApplicationOperation: TypeAlias = (
    ListProjects
    | GetProject
    | CreateThread
    | ListThreads
    | GetThread
    | ActivateNativeThread
    | DeleteThread
    | GetThreadStatus
    | GetThreadHistory
    | GetTurnCatchup
    | InterruptTurn
    | RespondRequest
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _GatewayOperation:
    operation_id: str
    conversation_ref: ConversationRef
    actor: str
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ListApplications(_GatewayOperation):
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.APPLICATION_LIST,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SelectApplication(_GatewayOperation):
    application_ref: ApplicationRef
    expected_revision: int | None = None
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.APPLICATION_SELECT,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class BindConversationToProject(_GatewayOperation):
    project_ref: ProjectRef
    expected_revision: int | None = None
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.CONVERSATION_BIND_PROJECT,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class BindConversationToThread(_GatewayOperation):
    thread_ref: ThreadRef
    expected_revision: int | None = None
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.CONVERSATION_BIND_THREAD,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ClearConversationThread(_GatewayOperation):
    expected_revision: int | None = None
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.CONVERSATION_CLEAR_THREAD,
    )


GatewayOperation: TypeAlias = (
    ListApplications
    | SelectApplication
    | BindConversationToProject
    | BindConversationToThread
    | ClearConversationThread
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _ApplicationOperationSucceeded:
    operation_id: str
    completed_at: datetime
    status: OperationResultStatus = field(
        init=False,
        default=OperationResultStatus.SUCCEEDED,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectsListed(_ApplicationOperationSucceeded):
    projects: Page[ProjectSummary]
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.PROJECT_LIST,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectRead(_ApplicationOperationSucceeded):
    project: ProjectSummary
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.PROJECT_GET,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadCreated(_ApplicationOperationSucceeded):
    thread: ThreadSummary
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_CREATE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadsListed(_ApplicationOperationSucceeded):
    threads: Page[ThreadSummary]
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_LIST,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadRead(_ApplicationOperationSucceeded):
    thread: ThreadSummary
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_GET,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class NativeThreadActivated(_ApplicationOperationSucceeded):
    thread_ref: ThreadRef
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_ACTIVATE_NATIVE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadDeleted(_ApplicationOperationSucceeded):
    thread_ref: ThreadRef
    mode: ThreadDeletionMode
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_DELETE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadStatusRead(_ApplicationOperationSucceeded):
    thread_ref: ThreadRef
    thread_status: ThreadStatus
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_STATUS,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadHistoryRead(_ApplicationOperationSucceeded):
    history: ThreadHistory
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_HISTORY,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnCatchupRead(_ApplicationOperationSucceeded):
    catchup: TurnCatchup
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.TURN_CATCHUP,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnInterrupted(_ApplicationOperationSucceeded):
    thread_ref: ThreadRef
    turn_id: str | None
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.TURN_INTERRUPT,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestResponded(_ApplicationOperationSucceeded):
    request_id: str
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.REQUEST_RESPOND,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationOperationFailed:
    operation_id: str
    type: ApplicationOperationType
    error: ContractError
    completed_at: datetime
    status: OperationResultStatus = field(
        init=False,
        default=OperationResultStatus.FAILED,
    )


ApplicationOperationResult: TypeAlias = (
    ProjectsListed
    | ProjectRead
    | ThreadCreated
    | ThreadsListed
    | ThreadRead
    | NativeThreadActivated
    | ThreadDeleted
    | ThreadStatusRead
    | ThreadHistoryRead
    | TurnCatchupRead
    | TurnInterrupted
    | RequestResponded
    | ApplicationOperationFailed
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _GatewayOperationSucceeded:
    operation_id: str
    completed_at: datetime
    status: OperationResultStatus = field(
        init=False,
        default=OperationResultStatus.SUCCEEDED,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationsListed(_GatewayOperationSucceeded):
    applications: tuple[ApplicationSummary, ...]
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.APPLICATION_LIST,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationBound(_GatewayOperationSucceeded):
    type: GatewayOperationType
    binding: ConversationBinding


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayOperationFailed:
    operation_id: str
    type: GatewayOperationType
    error: ContractError
    completed_at: datetime
    status: OperationResultStatus = field(
        init=False,
        default=OperationResultStatus.FAILED,
    )


GatewayOperationResult: TypeAlias = ApplicationsListed | ConversationBound | GatewayOperationFailed

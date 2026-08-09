from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from ..interaction.messages import Content
from ..interaction.operations import (
    ContractError,
    ContractViolation,
    OperationResultStatus,
    require_identifier,
)
from .requests import ApprovalResponse, UserInputResponse, validate_request_ref

MAX_PROJECT_CWD_LENGTH = 4096
MAX_PROJECT_DISPLAY_NAME_LENGTH = 128


class ApplicationOperationType(StrEnum):
    PROJECT_LIST = "project.list"
    PROJECT_GET = "project.get"
    PROJECT_CREATE = "project.create"
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


class ThreadDeletionMode(StrEnum):
    ARCHIVE = "archive"
    PERMANENT = "permanent"


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
class CreateProject(_ApplicationOperation):
    cwd: str
    display_name: str | None = None
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.PROJECT_CREATE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateThread(_ApplicationOperation):
    project_ref: ProjectRef
    title: str | None = None
    initial_context: tuple[Content, ...] = ()
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.THREAD_CREATE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ListThreads(_ApplicationOperation):
    project_ref: ProjectRef
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
    turn_ref: TurnRef | None = None
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.TURN_INTERRUPT,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RespondRequest(_ApplicationOperation):
    request_ref: RequestRef
    response: RequestResponse
    turn_ref: TurnRef
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.REQUEST_RESPOND,
    )


ApplicationOperation: TypeAlias = (
    ListProjects
    | GetProject
    | CreateProject
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
class ProjectCreated(_ApplicationOperationSucceeded):
    project: ProjectSummary
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.PROJECT_CREATE,
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
    turn_ref: TurnRef | None
    type: ApplicationOperationType = field(
        init=False,
        default=ApplicationOperationType.TURN_INTERRUPT,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestResponded(_ApplicationOperationSucceeded):
    request_ref: RequestRef
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
    | ProjectCreated
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


def validate_application_operation(operation: ApplicationOperation) -> None:
    # Application operation validation delegates shared resource and request
    # invariants to their focused leaves without changing operation semantics.
    require_identifier(operation.operation_id, "operation_id")
    application_id = operation.application_ref.application_instance_id
    require_identifier(application_id, "application_instance_id")

    project_ref = None
    if isinstance(operation, (GetProject, CreateThread, ListThreads)):
        project_ref = operation.project_ref
    if project_ref is not None:
        validate_project_ref(project_ref)
        if project_ref.application_instance_id != application_id:
            raise ContractViolation("operation project belongs to a different application")

    thread_ref = None
    if isinstance(
        operation,
        (
            GetThread,
            ActivateNativeThread,
            DeleteThread,
            GetThreadStatus,
            GetThreadHistory,
            GetTurnCatchup,
            InterruptTurn,
        ),
    ):
        thread_ref = operation.thread_ref
    elif isinstance(operation, RespondRequest):
        validate_request_ref(operation.request_ref)
        if operation.request_ref.application_ref != operation.application_ref:
            raise ContractViolation("request belongs to a different application")
        validate_turn_ref(operation.turn_ref)
        thread_ref = operation.turn_ref.thread_ref
    if thread_ref is not None:
        validate_thread_ref(thread_ref)
        if thread_ref.project_ref.application_instance_id != application_id:
            raise ContractViolation("operation thread belongs to a different application")

    if isinstance(operation, CreateProject):
        _require_bounded_text(operation.cwd, "cwd", MAX_PROJECT_CWD_LENGTH)
        if operation.display_name is not None:
            _require_bounded_text(
                operation.display_name,
                "display_name",
                MAX_PROJECT_DISPLAY_NAME_LENGTH,
            )

    if isinstance(operation, GetThreadHistory):
        if not 1 <= operation.limit <= 20:
            raise ContractViolation("history limit must be between 1 and 20")
        if operation.page < 1:
            raise ContractViolation("history page must be positive")
    if isinstance(operation, GetTurnCatchup) and not 1 <= operation.limit <= 20:
        raise ContractViolation("catch-up limit must be between 1 and 20")
    if isinstance(operation, InterruptTurn) and operation.turn_ref is not None:
        validate_turn_ref(operation.turn_ref)
        if operation.turn_ref.thread_ref != operation.thread_ref:
            raise ContractViolation("turn.interrupt Turn belongs to a different Thread")
    if isinstance(operation, RespondRequest) and isinstance(
        operation.response,
        UserInputResponse,
    ):
        if not operation.response.answers:
            raise ContractViolation("user input response requires answers")
        for question_id, answers in operation.response.answers.items():
            require_identifier(question_id, "question_id")
            if not answers or any(not answer for answer in answers):
                raise ContractViolation("each user input question requires non-empty answers")
    if isinstance(operation, RespondRequest) and isinstance(
        operation.response,
        ApprovalResponse,
    ):
        require_identifier(operation.response.choice_id, "choice_id")


def validate_application_operation_result(
    operation: ApplicationOperation,
    result: ApplicationOperationResult,
) -> None:
    require_identifier(result.operation_id, "operation_id")
    if result.operation_id != operation.operation_id:
        raise ContractViolation("operation result ID does not match the request")
    if result.type is not operation.type:
        raise ContractViolation("operation result type does not match the request")
    if isinstance(result, ApplicationOperationFailed):
        _validate_error(result.error)
        return

    expected_result: type[object]
    expected_result = {
        ListProjects: ProjectsListed,
        GetProject: ProjectRead,
        CreateProject: ProjectCreated,
        CreateThread: ThreadCreated,
        ListThreads: ThreadsListed,
        GetThread: ThreadRead,
        ActivateNativeThread: NativeThreadActivated,
        DeleteThread: ThreadDeleted,
        GetThreadStatus: ThreadStatusRead,
        GetThreadHistory: ThreadHistoryRead,
        GetTurnCatchup: TurnCatchupRead,
        InterruptTurn: TurnInterrupted,
        RespondRequest: RequestResponded,
    }[type(operation)]
    if not isinstance(result, expected_result):
        raise ContractViolation(
            f"{operation.type.value} returned {type(result).__name__}, "
            f"expected {expected_result.__name__}"
        )

    application_id = operation.application_ref.application_instance_id
    if isinstance(result, ProjectsListed):
        for project in result.projects.items:
            validate_project_summary(project)
        refs = tuple(item.ref for item in result.projects.items)
    elif isinstance(result, (ProjectRead, ProjectCreated)):
        validate_project_summary(result.project)
        refs = (result.project.ref,)
        if isinstance(result, ProjectRead):
            if not isinstance(operation, GetProject):
                raise ContractViolation("project.get returned for a different operation")
            if result.project.ref != operation.project_ref:
                raise ContractViolation("project.get returned a different project")
        elif not isinstance(operation, CreateProject):
            raise ContractViolation("project.create returned for a different operation")
    elif isinstance(result, ThreadCreated):
        if not isinstance(operation, CreateThread):
            raise ContractViolation("thread.create returned for a different operation")
        validate_thread_summary(result.thread)
        refs = (result.thread.ref,)
        if result.thread.ref.project_ref != operation.project_ref:
            raise ContractViolation("thread.create returned a different project scope")
    elif isinstance(result, ThreadsListed):
        if not isinstance(operation, ListThreads):
            raise ContractViolation("thread.list returned for a different operation")
        for thread in result.threads.items:
            validate_thread_summary(thread)
        refs = tuple(item.ref for item in result.threads.items)
        if any(item.ref.project_ref != operation.project_ref for item in result.threads.items):
            raise ContractViolation("thread.list returned a different project scope")
    elif isinstance(result, ThreadRead):
        if not isinstance(operation, GetThread):
            raise ContractViolation("thread.get returned for a different operation")
        validate_thread_summary(result.thread)
        refs = (result.thread.ref,)
        if result.thread.ref != operation.thread_ref:
            raise ContractViolation("thread.get returned a different thread")
    elif isinstance(result, NativeThreadActivated):
        if not isinstance(operation, ActivateNativeThread):
            raise ContractViolation("thread.activate_native returned for a different operation")
        validate_thread_ref(result.thread_ref)
        refs = (result.thread_ref,)
        if result.thread_ref != operation.thread_ref:
            raise ContractViolation("thread.activate_native returned a different thread")
    elif isinstance(result, ThreadDeleted):
        if not isinstance(operation, DeleteThread):
            raise ContractViolation("thread.delete returned for a different operation")
        validate_thread_ref(result.thread_ref)
        refs = (result.thread_ref,)
        if result.thread_ref != operation.thread_ref:
            raise ContractViolation("thread.delete returned a different thread")
        if result.mode is not operation.mode:
            raise ContractViolation("thread.delete returned a different deletion mode")
    elif isinstance(result, ThreadStatusRead):
        if not isinstance(operation, GetThreadStatus):
            raise ContractViolation("thread.status returned for a different operation")
        validate_thread_ref(result.thread_ref)
        refs = (result.thread_ref,)
        if result.thread_ref != operation.thread_ref:
            raise ContractViolation("thread.status returned a different thread")
    elif isinstance(result, ThreadHistoryRead):
        if not isinstance(operation, GetThreadHistory):
            raise ContractViolation("thread.history returned for a different operation")
        validate_thread_history(result.history)
        refs = (result.history.thread_ref,)
        if result.history.thread_ref != operation.thread_ref:
            raise ContractViolation("thread.history returned a different thread")
    elif isinstance(result, TurnCatchupRead):
        if not isinstance(operation, GetTurnCatchup):
            raise ContractViolation("turn.catchup returned for a different operation")
        validate_turn_catchup(result.catchup)
        refs = (result.catchup.thread_ref,)
        if result.catchup.thread_ref != operation.thread_ref:
            raise ContractViolation("turn.catchup returned a different thread")
    elif isinstance(result, TurnInterrupted):
        if not isinstance(operation, InterruptTurn):
            raise ContractViolation("turn.interrupt returned for a different operation")
        validate_thread_ref(result.thread_ref)
        if result.turn_ref is not None:
            validate_turn_ref(result.turn_ref)
        refs = (result.thread_ref,)
        if result.thread_ref != operation.thread_ref:
            raise ContractViolation("turn.interrupt returned a different thread")
        if result.turn_ref != operation.turn_ref:
            raise ContractViolation("turn.interrupt returned a different Turn")
    elif isinstance(result, RequestResponded):
        if not isinstance(operation, RespondRequest):
            raise ContractViolation("request.respond returned for a different operation")
        if result.request_ref != operation.request_ref:
            raise ContractViolation("request.respond returned a different request")
        refs = ()
    else:
        refs = ()
    if any(
        (
            ref.project_ref.application_instance_id
            if isinstance(ref, ThreadRef)
            else ref.application_instance_id
        )
        != application_id
        for ref in refs
    ):
        raise ContractViolation("operation result belongs to a different application")


def _validate_error(error: ContractError) -> None:
    require_identifier(error.code, "error.code")
    if not error.message:
        raise ContractViolation("operation error message cannot be empty")


def _require_bounded_text(value: str, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ContractViolation(f"{name} must be a non-empty string of at most {limit} characters")


from .contract import (  # noqa: E402
    ApplicationRef,
    Page,
    ProjectRef,
    ProjectSummary,
    ThreadHistory,
    ThreadRef,
    ThreadStatus,
    ThreadSummary,
    TurnCatchup,
    TurnRef,
    validate_project_ref,
    validate_project_summary,
    validate_thread_history,
    validate_thread_ref,
    validate_thread_summary,
    validate_turn_catchup,
    validate_turn_ref,
)
from .requests import RequestRef, RequestResponse  # noqa: E402

__all__ = [
    "ApplicationOperation",
    "ApplicationOperationResult",
    "ActivateNativeThread",
    "ApplicationOperationFailed",
    "ApplicationOperationType",
    "CreateProject",
    "CreateThread",
    "DeleteThread",
    "GetProject",
    "GetThread",
    "GetThreadHistory",
    "GetThreadStatus",
    "GetTurnCatchup",
    "InterruptTurn",
    "ListProjects",
    "ListThreads",
    "NativeThreadActivated",
    "ProjectCreated",
    "ProjectRead",
    "ProjectsListed",
    "RespondRequest",
    "RequestResponded",
    "ThreadCreated",
    "ThreadDeleted",
    "ThreadDeletionMode",
    "ThreadHistoryRead",
    "ThreadRead",
    "ThreadsListed",
    "ThreadStatusRead",
    "TurnCatchupRead",
    "TurnInterrupted",
    "MAX_PROJECT_CWD_LENGTH",
    "MAX_PROJECT_DISPLAY_NAME_LENGTH",
    "validate_application_operation",
    "validate_application_operation_result",
]

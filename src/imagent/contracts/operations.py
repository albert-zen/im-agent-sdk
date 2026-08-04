from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ForwardRef, TypeAlias

from ..applications.contract import ApplicationRef, ApplicationSummary, ThreadRef
from ..applications.requests import (
    RequestRef as _RequestRef,
)
from ..applications.requests import (
    RequestResponse as _RequestResponse,
)
from ..interaction.messages import ConversationRef
from ..interaction.operations import ContractError, OperationResultStatus

if TYPE_CHECKING:
    from ..gateway.persistence.state_contracts import ThreadProjectionRoute


class GatewayOperationType(StrEnum):
    APPLICATION_LIST = "application.list"
    APPLICATION_SELECT = "application.select"
    CONVERSATION_BIND_PROJECT = "conversation.bind_project"
    CONVERSATION_BIND_THREAD = "conversation.bind_thread"
    CONVERSATION_CLEAR_THREAD = "conversation.clear_thread"
    CONVERSATION_RESPOND_REQUEST = "conversation.respond_request"
    THREAD_OBSERVE = "thread.observe"


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
class ObserveThread(_GatewayOperation):
    thread_ref: ThreadRef
    reply_to_message_id: str | None = None
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.THREAD_OBSERVE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RespondToRequest(_GatewayOperation):
    request_ref: _RequestRef
    response: _RequestResponse
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.CONVERSATION_RESPOND_REQUEST,
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
class ThreadObserved(_GatewayOperationSucceeded):
    route: ThreadProjectionRoute
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.THREAD_OBSERVE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestResponseRouted(_GatewayOperationSucceeded):
    request_ref: _RequestRef
    type: GatewayOperationType = field(
        init=False,
        default=GatewayOperationType.CONVERSATION_RESPOND_REQUEST,
    )


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


# Bind binding-owned variants only after this historical aggregate has defined
# the shared Gateway discriminator and operation bases. This preserves the
# runtime union without a second binding contract or an import cycle.
from ..gateway.routing.bindings import (  # noqa: E402, I001
    BindConversationToProject as _BindConversationToProject,
    BindConversationToThread as _BindConversationToThread,
    ClearConversationThread as _ClearConversationThread,
    ConversationBound as _ConversationBound,
)

GatewayOperation: TypeAlias = (
    ListApplications
    | SelectApplication
    | _BindConversationToProject
    | _BindConversationToThread
    | _ClearConversationThread
    | ObserveThread
    | RespondToRequest
)

GatewayOperationResult: TypeAlias = (
    ApplicationsListed
    | _ConversationBound
    | ThreadObserved
    | RequestResponseRouted
    | GatewayOperationFailed
)

ThreadObserved.__annotations__["route"] = ForwardRef(
    "ThreadProjectionRoute",
    module="imagent.gateway.persistence.state_contracts",
)

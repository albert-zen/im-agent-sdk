"""Canonical aggregate Gateway operation contracts and dispatch owner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, TypeAlias

from ...applications.contract import ApplicationRef, ApplicationSummary
from ...interaction.messages import ConversationRef
from ...interaction.operations import (
    ContractError,
    ContractViolation,
    OperationResultStatus,
    require_identifier,
)
from ...keyed_locks import KeyedLockCapacityError, KeyedLockRegistry

if TYPE_CHECKING:
    from ...contracts.operations import (
        ObserveThread,
        RequestResponseRouted,
        RespondToRequest,
        ThreadObserved,
    )
    from .bindings import (
        BindConversationToProject,
        BindConversationToThread,
        ClearConversationThread,
        ConversationBound,
    )


_UNION_COMPLETED = False
_UNION_COMPLETING = False


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
class GatewayOperationFailed:
    operation_id: str
    type: GatewayOperationType
    error: ContractError
    completed_at: datetime
    status: OperationResultStatus = field(
        init=False,
        default=OperationResultStatus.FAILED,
    )


if TYPE_CHECKING:
    GatewayOperation: TypeAlias = (
        ListApplications
        | SelectApplication
        | BindConversationToProject
        | BindConversationToThread
        | ClearConversationThread
        | ObserveThread
        | RespondToRequest
    )
    GatewayOperationResult: TypeAlias = (
        ApplicationsListed
        | ConversationBound
        | ThreadObserved
        | RequestResponseRouted
        | GatewayOperationFailed
    )


class _GatewayOperationDelegates(Protocol):
    def _list_applications(
        self,
        operation: ListApplications,
        *,
        completed_at: datetime,
    ) -> tuple[ApplicationSummary, ...]: ...

    def _select_application(
        self,
        operation: SelectApplication,
        *,
        completed_at: datetime,
    ) -> Awaitable[ConversationBound]: ...

    def _bind_conversation_to_project(
        self,
        operation: BindConversationToProject,
        *,
        completed_at: datetime,
    ) -> Awaitable[ConversationBound]: ...

    def _bind_conversation_to_thread(
        self,
        operation: BindConversationToThread,
        *,
        completed_at: datetime,
    ) -> Awaitable[ConversationBound]: ...

    def _clear_conversation_thread(
        self,
        operation: ClearConversationThread,
        *,
        completed_at: datetime,
    ) -> Awaitable[ConversationBound]: ...

    def _observe_thread(
        self,
        operation: ObserveThread,
        *,
        completed_at: datetime,
    ) -> Awaitable[ThreadObserved]: ...

    def _route_request_response(
        self,
        operation: RespondToRequest,
        *,
        completed_at: datetime,
    ) -> Awaitable[RequestResponseRouted]: ...


ContractErrorMapper: TypeAlias = Callable[[Exception], ContractError]


class _GatewayActionError(RuntimeError):
    """An owner supplied a deliberate contract failure for aggregate dispatch."""

    def __init__(self, error: ContractError) -> None:
        super().__init__(error.message)
        self.error = error


def _complete_gateway_union() -> None:
    """Complete the one aggregate union after all exact owner leaves load."""

    global _UNION_COMPLETED, _UNION_COMPLETING
    if _UNION_COMPLETED or _UNION_COMPLETING:
        return

    _UNION_COMPLETING = True
    try:
        from ...contracts import operations as pending_operations
        from . import bindings as binding_owner

        binding_names = (
            "BindConversationToProject",
            "BindConversationToThread",
            "ClearConversationThread",
            "ConversationBound",
        )
        pending_names = (
            "ObserveThread",
            "ThreadObserved",
            "RespondToRequest",
            "RequestResponseRouted",
        )
        binding_values = vars(binding_owner)
        pending_values = vars(pending_operations)
        if not all(name in binding_values for name in binding_names):
            return
        if not all(name in pending_values for name in pending_names):
            return

        operation_union = (
            ListApplications
            | SelectApplication
            | binding_values["BindConversationToProject"]
            | binding_values["BindConversationToThread"]
            | binding_values["ClearConversationThread"]
            | pending_values["ObserveThread"]
            | pending_values["RespondToRequest"]
        )
        result_union = (
            ApplicationsListed
            | binding_values["ConversationBound"]
            | pending_values["ThreadObserved"]
            | pending_values["RequestResponseRouted"]
            | GatewayOperationFailed
        )
        globals().update({name: binding_values[name] for name in binding_names})
        globals().update({name: pending_values[name] for name in pending_names})
        globals()["GatewayOperation"] = operation_union
        globals()["GatewayOperationResult"] = result_union
        _UNION_COMPLETED = True
    finally:
        _UNION_COMPLETING = False


_AGGREGATE_EXPORTS = frozenset(
    {
        "GatewayOperation",
        "GatewayOperationResult",
    }
)


def __getattr__(name: str) -> object:
    if name in _AGGREGATE_EXPORTS:
        _complete_gateway_union()
        try:
            return globals()[name]
        except KeyError as error:
            raise AttributeError(
                f"module {__name__!r} has no completed aggregate {name!r}"
            ) from error
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def validate_gateway_operation(operation: GatewayOperation) -> None:
    """Validate aggregate/common fields and delegate owner-specific fields."""

    require_identifier(operation.operation_id, "operation_id")
    require_identifier(operation.actor, "actor")
    require_identifier(operation.conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(operation.conversation_ref.native_conversation_id, "native_conversation_id")
    expected_revision = getattr(operation, "expected_revision", None)
    if expected_revision is not None and expected_revision < 0:
        raise ContractViolation("expected_revision cannot be negative")
    if isinstance(operation, SelectApplication):
        require_identifier(
            operation.application_ref.application_instance_id,
            "application_instance_id",
        )
    if isinstance(
        operation,
        (BindConversationToProject, BindConversationToThread, ClearConversationThread),
    ):
        from .bindings import _validate_binding_operation

        _validate_binding_operation(operation)
    elif isinstance(operation, ObserveThread):
        from ...contracts.validators import _validate_observe_operation

        _validate_observe_operation(operation)
    elif isinstance(operation, RespondToRequest):
        from ...contracts.validators import _validate_respond_operation

        _validate_respond_operation(operation)


def _validate_error(error: ContractError) -> None:
    require_identifier(error.code, "error.code")
    if not error.message:
        raise ContractViolation("operation error message cannot be empty")


def validate_gateway_operation_result(
    operation: GatewayOperation,
    result: GatewayOperationResult,
) -> None:
    """Validate aggregate identity and delegate each owner-specific result."""

    require_identifier(result.operation_id, "operation_id")
    if result.operation_id != operation.operation_id:
        raise ContractViolation("Gateway result ID does not match the request")
    if result.type is not operation.type:
        raise ContractViolation("Gateway result type does not match the request")
    if isinstance(result, GatewayOperationFailed):
        _validate_error(result.error)
        return
    if isinstance(operation, ListApplications):
        if not isinstance(result, ApplicationsListed):
            raise ContractViolation("application.list must return ApplicationsListed")
        return
    if isinstance(operation, SelectApplication):
        _validate_application_selection_result(operation, result)
        return
    if isinstance(
        operation,
        (BindConversationToProject, BindConversationToThread, ClearConversationThread),
    ):
        from .bindings import _validate_binding_operation_result

        _validate_binding_operation_result(operation, result)
        return
    if isinstance(operation, ObserveThread):
        from ...contracts.validators import _validate_observe_operation_result

        _validate_observe_operation_result(operation, result)
        return
    if isinstance(operation, RespondToRequest):
        from ...contracts.validators import _validate_respond_operation_result

        _validate_respond_operation_result(operation, result)
        return
    raise ContractViolation(f"unsupported Gateway operation: {operation.type.value}")


def _validate_application_selection_result(
    operation: SelectApplication,
    result: object,
) -> None:
    """Validate the aggregate select postcondition using the binding value owner."""

    if not isinstance(operation, SelectApplication):
        raise ContractViolation("application.select requires SelectApplication")
    if not isinstance(result, ConversationBound):
        raise ContractViolation("application.select must return ConversationBound")
    if result.binding.conversation_ref != operation.conversation_ref:
        raise ContractViolation("Gateway result belongs to a different Conversation")

    from ...gateway.persistence.state_contracts import validate_binding

    validate_binding(result.binding)
    if (
        result.binding.application_ref != operation.application_ref
        or result.binding.project_ref is not None
        or result.binding.thread_ref is not None
    ):
        raise ContractViolation("application.select returned an incompatible binding")


class _GatewayOperationExecutor:
    """Validate and dispatch Gateway operations under bounded Conversation locks."""

    def __init__(
        self,
        *,
        delegates: _GatewayOperationDelegates,
        max_active_conversation_keys: int,
        contract_error: ContractErrorMapper,
    ) -> None:
        self._delegates = delegates
        self._conversation_locks = KeyedLockRegistry(
            max_active_keys=max_active_conversation_keys,
        )
        self._contract_error = contract_error

    @property
    def conversation_locks(self) -> KeyedLockRegistry:
        return self._conversation_locks

    def hold_conversation(
        self,
        conversation_ref: ConversationRef,
    ) -> AbstractAsyncContextManager[None]:
        return self._conversation_locks.hold(conversation_ref)

    async def execute(self, operation: GatewayOperation) -> GatewayOperationResult:
        try:
            async with self._conversation_locks.hold(operation.conversation_ref):
                return await self.execute_locked(operation)
        except KeyedLockCapacityError as error:
            return GatewayOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=datetime.now(UTC),
                error=self._contract_error(error),
            )

    async def execute_locked(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        try:
            validate_gateway_operation(operation)
            result = await self._dispatch(operation)
            validate_gateway_operation_result(operation, result)
            return result
        except _GatewayActionError as error:
            contract_error = error.error
        except Exception as error:
            contract_error = self._contract_error(error)
        return GatewayOperationFailed(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=datetime.now(UTC),
            error=contract_error,
        )

    async def _dispatch(self, operation: GatewayOperation) -> GatewayOperationResult:
        completed_at = datetime.now(UTC)
        if isinstance(operation, ListApplications):
            return ApplicationsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                applications=self._delegates._list_applications(
                    operation,
                    completed_at=completed_at,
                ),
            )
        if isinstance(operation, SelectApplication):
            return await self._delegates._select_application(
                operation,
                completed_at=completed_at,
            )
        if isinstance(operation, BindConversationToProject):
            return await self._delegates._bind_conversation_to_project(
                operation,
                completed_at=completed_at,
            )
        if isinstance(operation, BindConversationToThread):
            return await self._delegates._bind_conversation_to_thread(
                operation,
                completed_at=completed_at,
            )
        if isinstance(operation, ClearConversationThread):
            return await self._delegates._clear_conversation_thread(
                operation,
                completed_at=completed_at,
            )
        if isinstance(operation, ObserveThread):
            return await self._delegates._observe_thread(
                operation,
                completed_at=completed_at,
            )
        if isinstance(operation, RespondToRequest):
            return await self._delegates._route_request_response(
                operation,
                completed_at=completed_at,
            )
        raise NotImplementedError(operation.type.value)


_complete_gateway_union()


__all__ = [
    "ApplicationsListed",
    "GatewayOperation",
    "GatewayOperationFailed",
    "GatewayOperationResult",
    "GatewayOperationType",
    "ListApplications",
    "SelectApplication",
    "validate_gateway_operation",
    "validate_gateway_operation_result",
]

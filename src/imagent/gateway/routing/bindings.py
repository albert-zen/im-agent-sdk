"""Gateway-owned Conversation binding operation and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ForwardRef, TypeAlias

from ...applications.contract import ProjectRef, ThreadRef
from ...contracts.operations import (
    GatewayOperationType,
    _GatewayOperation,
    _GatewayOperationSucceeded,
)
from ...interaction.operations import ContractViolation, require_identifier

if TYPE_CHECKING:
    from ...gateway.persistence.state_contracts import ConversationBinding


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


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationBound(_GatewayOperationSucceeded):
    type: GatewayOperationType
    binding: ConversationBinding


ConversationBound.__annotations__["binding"] = ForwardRef(
    "ConversationBinding",
    module="imagent.gateway.persistence.state_contracts",
)

BindingOperation: TypeAlias = (
    BindConversationToProject | BindConversationToThread | ClearConversationThread
)


def _validate_binding_operation(operation: BindingOperation) -> None:
    """Validate the binding-specific fields after common Gateway checks."""

    if isinstance(operation, BindConversationToProject):
        require_identifier(operation.project_ref.application_instance_id, "application_instance_id")
        require_identifier(operation.project_ref.native_project_id, "native_project_id")
    elif isinstance(operation, BindConversationToThread):
        from ...applications.contract import validate_thread_ref

        validate_thread_ref(operation.thread_ref)


def _validate_binding_operation_result(operation: BindingOperation, result: object) -> None:
    """Validate a binding result without owning Gateway execution or CAS."""

    if not isinstance(result, ConversationBound):
        raise ContractViolation(f"{operation.type.value} must return ConversationBound")
    if result.binding.conversation_ref != operation.conversation_ref:
        raise ContractViolation("Gateway result belongs to a different Conversation")

    from ...gateway.persistence.state_contracts import validate_binding

    validate_binding(result.binding)
    if isinstance(operation, BindConversationToProject):
        if (
            result.binding.project_ref != operation.project_ref
            or result.binding.thread_ref is not None
        ):
            raise ContractViolation("conversation.bind_project returned an incompatible binding")
    elif isinstance(operation, BindConversationToThread):
        if result.binding.thread_ref != operation.thread_ref:
            raise ContractViolation("conversation.bind_thread returned a different thread")
    elif result.binding.thread_ref is not None:
        raise ContractViolation("conversation.clear_thread did not clear the thread")


__all__ = [
    "BindConversationToProject",
    "BindConversationToThread",
    "ClearConversationThread",
    "ConversationBound",
]

from typing import TYPE_CHECKING

from . import operations as _operations
from .bindings import (
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationThread,
    ConversationBound,
)
from .projection_routes import ObserveThread, ProjectionPolicy, ThreadObserved

_operations._complete_gateway_union()

if TYPE_CHECKING:
    from .operations import (
        ApplicationsListed,
        GatewayOperation,
        GatewayOperationFailed,
        GatewayOperationResult,
        GatewayOperationType,
        ListApplications,
        SelectApplication,
        validate_gateway_operation,
        validate_gateway_operation_result,
    )

_OPERATIONS_EXPORTS = frozenset(
    {
        "ApplicationsListed",
        "GatewayOperation",
        "GatewayOperationFailed",
        "GatewayOperationResult",
        "GatewayOperationType",
        "ListApplications",
        "SelectApplication",
        "validate_gateway_operation",
        "validate_gateway_operation_result",
    }
)


def __getattr__(name: str) -> object:
    if name in _OPERATIONS_EXPORTS:
        _operations._complete_gateway_union()
        return getattr(_operations, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ApplicationsListed",
    "BindConversationToProject",
    "BindConversationToThread",
    "ClearConversationThread",
    "ConversationBound",
    "GatewayOperation",
    "GatewayOperationFailed",
    "GatewayOperationResult",
    "GatewayOperationType",
    "ListApplications",
    "ObserveThread",
    "ProjectionPolicy",
    "SelectApplication",
    "ThreadObserved",
    "validate_gateway_operation",
    "validate_gateway_operation_result",
]

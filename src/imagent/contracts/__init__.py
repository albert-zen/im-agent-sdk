from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..gateway.delivery.proactive_authorization import (
        DeliveryPrincipal,
        validate_delivery_principal,
    )
    from ..gateway.persistence.state_contracts import ConversationBinding
    from ..gateway.routing.bindings import (
        BindConversationToProject,
        BindConversationToThread,
        ClearConversationThread,
        ConversationBound,
    )
    from ..gateway.routing.operations import (
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
    from ..interaction.messages import ConversationRef
    from .validators import (
        derive_client_message_id,
    )

from ..applications.contract import ApplicationInputOutcomeUnknown
from ..applications.events import AgentEvent, AgentEventType, validate_agent_event
from ..interaction.media import (
    AttachmentContent,
    AttachmentGrouping,
    AttachmentHandle,
    AttachmentSource,
    AttachmentSourceKind,
    LocalPath,
    RemoteUrl,
)
from ..interaction.messages import (
    Content,
    ConversationRef,
    InboundMessage,
    MessageRole,
    Metadata,
    OutboundMessage,
    TextContent,
    TextFormat,
    TextLengthUnit,
)
from ..interaction.operations import (
    ContractError,
    ContractViolation,
    OperationErrorCode,
    OperationResultStatus,
    operation_error,
    require_identifier,
)

_BINDING_EXPORTS = frozenset(
    {
        "BindConversationToProject",
        "BindConversationToThread",
        "ClearConversationThread",
        "ConversationBound",
    }
)
_GATEWAY_OPERATION_EXPORTS = frozenset(
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
_GATEWAY_VALIDATOR_EXPORTS = frozenset(
    {
        "derive_client_message_id",
    }
)
_GATEWAY_PERSISTENCE_EXPORTS = frozenset(
    {
        "ConversationBinding",
    }
)

__all__ = [
    "AgentEvent",
    "AgentEventType",
    "ApplicationInputOutcomeUnknown",
    "AttachmentHandle",
    "AttachmentContent",
    "AttachmentGrouping",
    "AttachmentSource",
    "AttachmentSourceKind",
    "Content",
    "ContractError",
    "ContractViolation",
    "ConversationBinding",
    "ConversationRef",
    "DeliveryPrincipal",
    "InboundMessage",
    "LocalPath",
    "MessageRole",
    "Metadata",
    "OutboundMessage",
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
    "OperationResultStatus",
    "OperationErrorCode",
    "RemoteUrl",
    "SelectApplication",
    "TextContent",
    "TextFormat",
    "TextLengthUnit",
    "derive_client_message_id",
    "operation_error",
    "require_identifier",
    "validate_agent_event",
    "validate_delivery_principal",
    "validate_gateway_operation",
    "validate_gateway_operation_result",
]


def __getattr__(name: str) -> object:
    if name in _BINDING_EXPORTS:
        module = import_module("..gateway.routing.bindings", __name__)
    elif name in _GATEWAY_OPERATION_EXPORTS:
        module = import_module("..gateway.routing.operations", __name__)
    elif name in _GATEWAY_VALIDATOR_EXPORTS:
        module = import_module(".validators", __name__)
    elif name in _GATEWAY_PERSISTENCE_EXPORTS:
        module = import_module("..gateway.persistence.state_contracts", __name__)
    elif name in {"DeliveryPrincipal", "validate_delivery_principal"}:
        from ..gateway.delivery.proactive_authorization import (
            DeliveryPrincipal,
            validate_delivery_principal,
        )

        value = DeliveryPrincipal if name == "DeliveryPrincipal" else validate_delivery_principal
        globals()[name] = value
        return value
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value

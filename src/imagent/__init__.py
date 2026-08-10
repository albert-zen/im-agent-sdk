"""Reference contracts and test kit for IM Agent SDK."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

__version__ = "0.1.0a1"

if TYPE_CHECKING:
    from . import adapters, contracts, diagnostics, events
    from .gateway.actions import (
        ActionResult,
        ActionValue,
        ApplicationActions,
        ConversationActions,
        ReadOutcome,
    )
    from .gateway.composition import GatewayExtensions, GatewayLimits
    from .gateway.delivery import coordination as delivery_coordination
    from .gateway.delivery import planning as delivery_planning
    from .gateway.outcomes import Failed, OutcomeUnknown, Partial, Succeeded
    from .gateway.persistence import GatewayStore, MemoryGatewayStore, SQLiteGatewayStore
    from .gateway.routing import ProjectionPolicy
    from .gateway.runtime import Gateway
    from .interaction.controllers import (
        CommandArgumentContract,
        CommandDefinition,
        CommandExecutionSafety,
        CommandHandler,
        CommandLimits,
        CommandRegistry,
        CommandResult,
        include_common_commands,
    )

__all__ = [
    "ActionResult",
    "ActionValue",
    "ApplicationActions",
    "CommandArgumentContract",
    "CommandDefinition",
    "CommandExecutionSafety",
    "CommandHandler",
    "CommandLimits",
    "CommandRegistry",
    "CommandResult",
    "ConversationActions",
    "Failed",
    "Gateway",
    "GatewayExtensions",
    "GatewayLimits",
    "GatewayStore",
    "MemoryGatewayStore",
    "OutcomeUnknown",
    "Partial",
    "ProjectionPolicy",
    "ReadOutcome",
    "SQLiteGatewayStore",
    "Succeeded",
    "adapters",
    "contracts",
    "delivery_coordination",
    "delivery_planning",
    "diagnostics",
    "events",
    "include_common_commands",
]

_ACTION_EXPORTS = frozenset(
    {"ActionResult", "ActionValue", "ApplicationActions", "ConversationActions", "ReadOutcome"}
)
_COMMAND_EXPORTS = frozenset(
    {
        "CommandArgumentContract",
        "CommandDefinition",
        "CommandExecutionSafety",
        "CommandHandler",
        "CommandLimits",
        "CommandRegistry",
        "CommandResult",
        "include_common_commands",
    }
)
_COMPOSITION_EXPORTS = frozenset({"GatewayExtensions", "GatewayLimits"})
_OUTCOME_EXPORTS = frozenset({"Failed", "OutcomeUnknown", "Partial", "Succeeded"})
_STORE_EXPORTS = frozenset({"GatewayStore", "MemoryGatewayStore", "SQLiteGatewayStore"})


def __getattr__(name: str) -> object:
    if name in _ACTION_EXPORTS:
        module = import_module(".gateway.actions", __name__)
    elif name in _COMMAND_EXPORTS:
        module = import_module(".interaction.controllers", __name__)
    elif name == "Gateway":
        module = import_module(".gateway.runtime", __name__)
    elif name in _COMPOSITION_EXPORTS:
        module = import_module(".gateway.composition", __name__)
    elif name in _OUTCOME_EXPORTS:
        module = import_module(".gateway.outcomes", __name__)
    elif name in _STORE_EXPORTS:
        module = import_module(".gateway.persistence", __name__)
    elif name == "ProjectionPolicy":
        module = import_module(".gateway.routing", __name__)
    elif name == "adapters":
        module = import_module(".adapters", __name__)
    elif name == "contracts":
        module = import_module(".contracts", __name__)
    elif name == "delivery_coordination":
        module = import_module(".gateway.delivery.coordination", __name__)
    elif name == "delivery_planning":
        module = import_module(".gateway.delivery.planning", __name__)
    elif name == "diagnostics":
        module = import_module(".diagnostics", __name__)
    elif name == "events":
        module = import_module(".events", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value_exports = (
        _ACTION_EXPORTS
        | _COMMAND_EXPORTS
        | _COMPOSITION_EXPORTS
        | _OUTCOME_EXPORTS
        | _STORE_EXPORTS
        | {"Gateway", "ProjectionPolicy"}
    )
    value = getattr(module, name) if name in value_exports else module
    globals()[name] = value
    return value

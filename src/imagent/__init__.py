"""Finite public facade for the IM Agent SDK v1 bridge."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

__version__ = "0.1.0a1"

if TYPE_CHECKING:
    from .gateway.actions import (
        ActionResult,
        ActionValue,
        ApplicationActions,
        ConversationActions,
        ReadOutcome,
    )
    from .gateway.composition import GatewayExtensions, GatewayLimits
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
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value

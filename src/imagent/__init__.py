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
    from .gateway.delivery import coordination as delivery_coordination
    from .gateway.delivery import planning as delivery_planning
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
    "ReadOutcome",
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


def __getattr__(name: str) -> object:
    if name in _ACTION_EXPORTS:
        module = import_module(".gateway.actions", __name__)
    elif name in _COMMAND_EXPORTS:
        module = import_module(".interaction.controllers", __name__)
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
    value = getattr(module, name) if name in _ACTION_EXPORTS | _COMMAND_EXPORTS else module
    globals()[name] = value
    return value

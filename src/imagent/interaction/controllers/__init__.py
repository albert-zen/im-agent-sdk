"""Optional typed Controller contracts for Interaction composition."""

from importlib import import_module
from typing import TYPE_CHECKING

from .contract import (
    CommandHandlerActions,
    CommandInvocationFacts,
    ControllerActions,
    ControllerLifecycle,
    InboundController,
)
from .registry import (
    CommandArgumentContract,
    CommandDefinition,
    CommandExecutionSafety,
    CommandHandler,
    CommandHandlerTimeout,
    CommandInvocation,
    CommandRegistry,
    CommandRegistryDiagnostics,
    CommandRegistryError,
    CommandRegistryFailureCode,
    CommandRegistryFrozenError,
    CommandRegistryLimits,
    CommandRegistryNotFrozenError,
    CommandResult,
    CommandResultError,
    CommandResultStatus,
    derive_command_invocation_id,
)
from .request_presentation import (
    MarkdownRequestPresenter,
    RequestPresentation,
    RequestPresenter,
)

if TYPE_CHECKING:
    from .common import SlashController, register_common_commands

_COMMON_COMMAND_EXPORTS = frozenset({"SlashController", "register_common_commands"})

__all__ = [
    "ControllerActions",
    "ControllerLifecycle",
    "CommandArgumentContract",
    "CommandDefinition",
    "CommandExecutionSafety",
    "CommandHandler",
    "CommandHandlerActions",
    "CommandHandlerTimeout",
    "CommandInvocation",
    "CommandInvocationFacts",
    "CommandRegistry",
    "CommandRegistryDiagnostics",
    "CommandRegistryError",
    "CommandRegistryFailureCode",
    "CommandRegistryFrozenError",
    "CommandRegistryLimits",
    "CommandRegistryNotFrozenError",
    "CommandResult",
    "CommandResultError",
    "CommandResultStatus",
    "InboundController",
    "MarkdownRequestPresenter",
    "RequestPresentation",
    "RequestPresenter",
    "SlashController",
    "derive_command_invocation_id",
    "register_common_commands",
]


def __getattr__(name: str) -> object:
    """Resolve common-command exports only when callers request them."""
    if name not in _COMMON_COMMAND_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".common", __name__), name)
    globals()[name] = value
    return value

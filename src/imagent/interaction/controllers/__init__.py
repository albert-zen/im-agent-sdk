"""Optional typed Controller contracts for Interaction composition."""

from importlib import import_module
from typing import TYPE_CHECKING

from .command_names import resolve_command_name
from .contract import (
    CommandInvocationFacts,
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
    CommandLimits,
    CommandRegistry,
    CommandRegistryDiagnostics,
    CommandRegistryError,
    CommandRegistryFailureCode,
    CommandRegistryFrozenError,
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
    from .common import include_common_commands

_COMMON_COMMAND_EXPORTS = frozenset({"include_common_commands"})

__all__ = [
    "ControllerLifecycle",
    "CommandArgumentContract",
    "CommandDefinition",
    "CommandExecutionSafety",
    "CommandHandler",
    "CommandHandlerTimeout",
    "CommandInvocation",
    "CommandInvocationFacts",
    "CommandRegistry",
    "CommandRegistryDiagnostics",
    "CommandRegistryError",
    "CommandRegistryFailureCode",
    "CommandRegistryFrozenError",
    "CommandLimits",
    "CommandRegistryNotFrozenError",
    "CommandResult",
    "CommandResultError",
    "CommandResultStatus",
    "InboundController",
    "MarkdownRequestPresenter",
    "RequestPresentation",
    "RequestPresenter",
    "derive_command_invocation_id",
    "resolve_command_name",
    "include_common_commands",
]


def __getattr__(name: str) -> object:
    """Resolve common-command exports only when callers request them."""
    if name not in _COMMON_COMMAND_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".common", __name__), name)
    globals()[name] = value
    return value

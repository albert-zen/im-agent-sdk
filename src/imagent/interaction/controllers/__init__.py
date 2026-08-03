"""Optional typed Controller contracts for Interaction composition."""

from .common import SlashController, register_common_commands
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

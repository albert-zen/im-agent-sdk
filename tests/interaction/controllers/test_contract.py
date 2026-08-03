from __future__ import annotations

import unittest
from importlib.util import find_spec

from imagent import controllers as controllers_facade
from imagent.interaction.controllers import (
    CommandInvocationFacts,
    ControllerActions,
    InboundController,
    SlashController,
    register_common_commands,
)


class ControllerContractOwnershipTests(unittest.TestCase):
    def test_controller_facade_reexports_exact_owner_objects(self) -> None:
        self.assertIs(controllers_facade.ControllerActions, ControllerActions)
        self.assertIs(controllers_facade.CommandInvocationFacts, CommandInvocationFacts)
        self.assertIs(controllers_facade.InboundController, InboundController)
        self.assertIs(controllers_facade.SlashController, SlashController)
        self.assertIs(controllers_facade.register_common_commands, register_common_commands)

    def test_legacy_base_is_removed_after_its_leaves_move(self) -> None:
        self.assertIsNone(find_spec("imagent.controllers.base"))
        self.assertIsNone(find_spec("imagent.controllers.markdown"))
        self.assertIsNone(find_spec("imagent.controllers.slash"))
        self.assertEqual(
            set(controllers_facade.__all__),
            {
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
                "ControllerActions",
                "ControllerLifecycle",
                "InboundController",
                "MarkdownRequestPresenter",
                "RequestPresentation",
                "RequestPresenter",
                "SlashController",
                "register_common_commands",
            },
        )


if __name__ == "__main__":
    unittest.main()

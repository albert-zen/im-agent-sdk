from __future__ import annotations

import importlib.util
import inspect
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import get_type_hints

import imagent
import imagent.interaction.controllers as facade
import imagent.interaction.controllers.common as common_owner
import imagent.interaction.controllers.contract as contract_owner
import imagent.interaction.controllers.registry as registry_owner
from imagent.gateway.actions import ConversationActions
from imagent.interaction.controllers import (
    CommandLimits,
    CommandRegistry,
    include_common_commands,
)

ROOT = Path(__file__).resolve().parents[3]


class ControllerPublicContractTests(unittest.TestCase):
    def test_registry_and_common_composition_have_one_normative_public_surface(self) -> None:
        expected_owners = {
            **{
                name: registry_owner
                for name in (
                    "CommandArgumentContract",
                    "CommandDefinition",
                    "CommandExecutionSafety",
                    "CommandHandler",
                    "CommandHandlerTimeout",
                    "CommandInvocation",
                    "CommandLimits",
                    "CommandRegistry",
                    "CommandRegistryDiagnostics",
                    "CommandRegistryError",
                    "CommandRegistryFailureCode",
                    "CommandRegistryFrozenError",
                    "CommandRegistryNotFrozenError",
                    "CommandResult",
                    "CommandResultError",
                    "CommandResultStatus",
                    "derive_command_invocation_id",
                )
            },
            "include_common_commands": common_owner,
        }
        for name, owner in expected_owners.items():
            with self.subTest(name=name):
                self.assertIs(getattr(facade, name), getattr(owner, name))
                self.assertIn(name, facade.__all__)

        for removed in (
            "CommandHandlerActions",
            "ControllerActions",
            "SlashController",
            "register_common_commands",
            "CommandRegistryLimits",
        ):
            with self.subTest(removed=removed):
                self.assertFalse(hasattr(facade, removed))
                self.assertFalse(hasattr(imagent, removed))
                self.assertNotIn(removed, facade.__all__)

    def test_handlers_and_controllers_receive_the_exact_conversation_surface(self) -> None:
        handler_hints = get_type_hints(
            registry_owner.CommandHandler.__call__,
        )
        controller_hints = get_type_hints(
            contract_owner.InboundController.handle,
        )
        registry_hints = get_type_hints(
            registry_owner.CommandRegistry.handle,
        )
        self.assertIs(handler_hints["actions"], ConversationActions)
        self.assertIs(controller_hints["actions"], ConversationActions)
        self.assertIs(registry_hints["actions"], ConversationActions)

    def test_include_common_commands_is_explicit_local_composition(self) -> None:
        signature = inspect.signature(include_common_commands)
        self.assertEqual(tuple(signature.parameters), ("registry", "presenter", "names", "clock"))
        first = CommandRegistry(CommandLimits(max_commands=2))
        second = CommandRegistry(CommandLimits(max_commands=2))
        include_common_commands(first, names=("help",))
        include_common_commands(second, names=("help",))
        first.freeze()
        second.freeze()
        self.assertIsNot(first, second)
        self.assertTrue(first.frozen and second.frozen)

    def test_root_exports_are_exact_and_clean_process_has_no_compatibility_aliases(self) -> None:
        self.assertIs(imagent.CommandRegistry, CommandRegistry)
        self.assertIs(imagent.CommandLimits, CommandLimits)
        self.assertIs(imagent.include_common_commands, include_common_commands)
        self.assertIsNone(importlib.util.find_spec("imagent.interaction.controllers.slash"))

        script = """
import imagent
from imagent import CommandLimits, CommandRegistry, include_common_commands
from imagent.gateway.actions import ConversationActions
assert imagent.CommandLimits is CommandLimits
assert imagent.CommandRegistry is CommandRegistry
assert imagent.include_common_commands is include_common_commands
for removed in (
    'ControllerActions', 'CommandHandlerActions', 'SlashController',
    'register_common_commands', 'CommandRegistryLimits',
):
    assert not hasattr(imagent, removed)
    assert not hasattr(__import__('imagent.interaction.controllers', fromlist=['x']), removed)
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()

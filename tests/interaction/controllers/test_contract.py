from __future__ import annotations

import unittest
from datetime import datetime
from importlib import import_module
from importlib.util import find_spec
from subprocess import run
from sys import executable
from typing import get_type_hints

import imagent.interaction.controllers as controllers_facade
from imagent.applications.operations import ApplicationOperation, ApplicationOperationResult
from imagent.applications.requests import InteractiveRequest
from imagent.contracts import ConversationBinding as ContractConversationBinding
from imagent.contracts import GatewayOperation, GatewayOperationResult
from imagent.gateway.persistence import ConversationBinding
from imagent.interaction.controllers import (
    CommandHandlerActions,
    ControllerActions,
)
from imagent.interaction.controllers import common as common_owner
from imagent.interaction.controllers import contract as contract_owner
from imagent.interaction.controllers import registry as registry_owner
from imagent.interaction.controllers import request_presentation as request_owner
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
)


class ControllerContractOwnershipTests(unittest.TestCase):
    def test_controller_facade_reexports_every_exact_owner_object(self) -> None:
        expected = {
            **{
                name: contract_owner
                for name in (
                    "CommandHandlerActions",
                    "CommandInvocationFacts",
                    "ControllerActions",
                    "ControllerLifecycle",
                    "InboundController",
                )
            },
            **{
                name: registry_owner
                for name in (
                    "CommandArgumentContract",
                    "CommandDefinition",
                    "CommandExecutionSafety",
                    "CommandHandler",
                    "CommandHandlerTimeout",
                    "CommandInvocation",
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
                    "derive_command_invocation_id",
                )
            },
            **{name: common_owner for name in ("SlashController", "register_common_commands")},
            **{
                name: request_owner
                for name in (
                    "MarkdownRequestPresenter",
                    "RequestPresentation",
                    "RequestPresenter",
                )
            },
        }
        self.assertEqual(set(controllers_facade.__all__), set(expected))
        for name, owner in expected.items():
            with self.subTest(name=name):
                value = getattr(owner, name)
                self.assertIs(getattr(controllers_facade, name), value)
                self.assertEqual(value.__module__, owner.__name__)

    def test_controller_facade_runtime_type_hints_resolve_to_exact_contracts(self) -> None:
        action_hints = get_type_hints(contract_owner.CommandHandlerActions.execute_application)
        self.assertIs(action_hints["operation"], ApplicationOperation)
        self.assertIs(action_hints["return"], ApplicationOperationResult)

        gateway_hints = get_type_hints(contract_owner.CommandHandlerActions.execute_gateway)
        self.assertIs(gateway_hints["operation"], GatewayOperation)
        self.assertIs(gateway_hints["return"], GatewayOperationResult)

        binding_hints = get_type_hints(contract_owner.CommandHandlerActions.get_binding)
        self.assertIs(binding_hints["conversation_ref"], ConversationRef)
        self.assertEqual(binding_hints["return"], ConversationBinding | None)
        self.assertIs(ContractConversationBinding, ConversationBinding)

        controller_hints = get_type_hints(contract_owner.InboundController.handle)
        self.assertIs(controller_hints["message"], InboundMessage)
        self.assertIs(controller_hints["actions"], ControllerActions)
        self.assertEqual(controller_hints["return"], tuple[OutboundMessage, ...] | None)

        handler_hints = get_type_hints(registry_owner.CommandHandler.__call__)
        self.assertIs(handler_hints["invocation"], registry_owner.CommandInvocation)
        self.assertIs(handler_hints["actions"], CommandHandlerActions)
        self.assertIs(handler_hints["return"], registry_owner.CommandResult)

        invocation_hints = get_type_hints(registry_owner.CommandInvocation)
        self.assertIs(invocation_hints["conversation_ref"], ConversationRef)
        self.assertEqual(invocation_hints["arguments"], tuple[str, ...])
        self.assertIs(invocation_hints["created_at"], datetime)

        result_hints = get_type_hints(registry_owner.CommandResult)
        self.assertEqual(result_hints["content"], tuple[TextContent, ...])
        self.assertIs(result_hints["status"], registry_owner.CommandResultStatus)

        definition_hints = get_type_hints(registry_owner.CommandDefinition)
        self.assertIs(definition_hints["handler"], registry_owner.CommandHandler)
        self.assertIs(definition_hints["arguments"], registry_owner.CommandArgumentContract)
        self.assertIs(definition_hints["safety"], registry_owner.CommandExecutionSafety)

        common_hints = get_type_hints(common_owner.register_common_commands)
        self.assertIs(common_hints["registry"], registry_owner.CommandRegistry)
        self.assertIs(common_hints["return"], type(None))

        presentation_hints = get_type_hints(request_owner.RequestPresentation)
        self.assertIs(presentation_hints["message"], OutboundMessage)
        self.assertIs(presentation_hints["response_supported"], bool)
        presenter_hints = get_type_hints(request_owner.RequestPresenter.present_request)
        self.assertIs(presenter_hints["request"], InteractiveRequest)
        self.assertIs(presenter_hints["conversation_ref"], ConversationRef)
        self.assertIs(presenter_hints["return"], request_owner.RequestPresentation)

    def test_historical_controller_package_is_absent_and_unimportable(self) -> None:
        self.assertIsNone(find_spec("imagent.controllers"))
        with self.assertRaises(ModuleNotFoundError):
            import_module("imagent.controllers")

    def test_historical_controller_import_order_fails_in_clean_process(self) -> None:
        snippets = (
            "import imagent.interaction.controllers; import imagent.controllers",
            "import imagent.controllers",
        )
        for snippet in snippets:
            with self.subTest(snippet=snippet):
                result = run(
                    [executable, "-c", snippet],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ModuleNotFoundError", result.stderr)
                self.assertIn("imagent.controllers", result.stderr)

    def test_formal_facade_and_hints_hold_in_clean_process(self) -> None:
        result = run(
            [
                executable,
                "-c",
                "import typing; "
                "import sys; "
                "import imagent.interaction.controllers as facade; "
                "import imagent.interaction.controllers.contract as owner; "
                "import imagent.contracts as contracts_facade; "
                "from imagent.applications.operations import ApplicationOperation; "
                "from imagent.gateway.persistence.state_contracts import ConversationBinding; "
                "assert facade.ControllerActions is owner.ControllerActions; "
                "assert 'imagent.interaction.controllers.common' not in sys.modules; "
                "first_common = facade.SlashController; "
                "second_common = facade.SlashController; "
                "from imagent.interaction.controllers.common import SlashController; "
                "assert first_common is second_common is SlashController; "
                "assert facade.__dict__['SlashController'] is SlashController; "
                "assert typing.get_type_hints("
                "owner.CommandHandlerActions.execute_application"
                ')["operation"] '
                "is ApplicationOperation; "
                "assert contracts_facade.ConversationBinding is ConversationBinding; "
                "assert typing.get_type_hints("
                "owner.CommandHandlerActions.get_binding"
                ')["return"] == ConversationBinding | None',
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

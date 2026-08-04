from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import imagent.contracts as contracts_facade
import imagent.contracts.operations as historical_operations
import imagent.contracts.validators as historical_validators
import imagent.gateway as gateway_facade
import imagent.gateway.routing as routing_facade
from imagent.contracts import (
    ApplicationRef,
    ApplicationsListed,
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationThread,
    ContractViolation,
    ConversationBound,
    ConversationRef,
    GatewayOperation,
    GatewayOperationType,
    ProjectRef,
    ThreadRef,
    validate_gateway_operation,
    validate_gateway_operation_result,
)
from imagent.gateway.persistence import ConversationBinding
from imagent.gateway.routing import bindings as binding_owner

_IMPORT_ORDER_ASSERTIONS = textwrap.dedent(
    """
    import inspect
    import typing

    import imagent.contracts as contracts_facade
    import imagent.contracts.operations as historical_operations
    import imagent.contracts.validators as validators_owner
    import imagent.gateway as gateway_facade
    import imagent.gateway.routing as routing_facade
    from imagent.gateway.persistence import ConversationBinding
    from imagent.gateway.routing import bindings as binding_owner

    binding_names = (
        "BindConversationToProject",
        "BindConversationToThread",
        "ClearConversationThread",
        "ConversationBound",
    )
    for name in binding_names:
        owner = getattr(binding_owner, name)
        assert getattr(routing_facade, name) is owner
        assert getattr(contracts_facade, name) is owner
        assert getattr(gateway_facade, name) is owner
        assert inspect.signature(getattr(contracts_facade, name)) == inspect.signature(owner)
        assert owner.__module__ == "imagent.gateway.routing.bindings"
        assert not hasattr(historical_operations, name)
        assert not hasattr(validators_owner, name)

    binding_hints = typing.get_type_hints(binding_owner.ConversationBound)
    facade_hints = typing.get_type_hints(contracts_facade.ConversationBound)
    assert binding_hints["binding"] is ConversationBinding
    assert facade_hints["binding"] is ConversationBinding
    assert binding_hints["type"] is historical_operations.GatewayOperationType
    assert facade_hints["type"] is historical_operations.GatewayOperationType
    assert typing.get_type_hints(contracts_facade.__getattr__)["return"] is object
    assert typing.get_args(contracts_facade.GatewayOperation)
    assert inspect.signature(contracts_facade.validate_gateway_operation) == inspect.signature(
        validators_owner.validate_gateway_operation
    )
    assert (
        inspect.signature(contracts_facade.validate_gateway_operation_result)
        == inspect.signature(validators_owner.validate_gateway_operation_result)
    )
    assert (
        typing.get_type_hints(contracts_facade.validate_gateway_operation)
        == typing.get_type_hints(validators_owner.validate_gateway_operation)
    )
    assert (
        typing.get_type_hints(contracts_facade.validate_gateway_operation_result)
        == typing.get_type_hints(validators_owner.validate_gateway_operation_result)
    )
    """
).strip()

_IMPORT_ORDERS = {
    "canonical owner first": "import imagent.gateway.routing.bindings\n",
    "historical operations first": "import imagent.contracts.operations\n",
    "validators first": "import imagent.contracts.validators\n",
    "contracts facade first": "import imagent.contracts\n",
    "routing facade first": "import imagent.gateway.routing\n",
}
ROOT = Path(__file__).resolve().parents[3]


class BindingOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conversation = ConversationRef("qq-primary", "c2c:user-1")
        self.application_id = "zen-local"
        self.application = ApplicationRef("zen-local")
        self.thread = ThreadRef(self.application_id, "thread-1")
        self.other_thread = ThreadRef(self.application_id, "thread-2")

    def _bind_thread(self) -> BindConversationToThread:
        return BindConversationToThread(
            operation_id="op-bind",
            conversation_ref=self.conversation,
            actor="user-1",
            thread_ref=self.thread,
            created_at=datetime.now(UTC),
        )

    def test_public_facades_are_exact_owner_objects(self) -> None:
        names = (
            "BindConversationToProject",
            "BindConversationToThread",
            "ClearConversationThread",
            "ConversationBound",
        )
        for name in names:
            with self.subTest(name=name):
                owner = getattr(binding_owner, name)
                self.assertIs(getattr(routing_facade, name), owner)
                self.assertIs(getattr(contracts_facade, name), owner)
                self.assertIs(getattr(gateway_facade, name), owner)
                self.assertEqual(owner.__module__, "imagent.gateway.routing.bindings")
                self.assertFalse(hasattr(historical_operations, name))
                self.assertFalse(hasattr(historical_validators, name))

    def test_clean_process_import_orders_preserve_identity_signatures_and_hints(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        for label, first_import in _IMPORT_ORDERS.items():
            with self.subTest(import_order=label):
                completed = subprocess.run(
                    [sys.executable, "-c", f"{first_import}{_IMPORT_ORDER_ASSERTIONS}"],
                    capture_output=True,
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{label} failed\nstdout={completed.stdout}\nstderr={completed.stderr}",
                )

    def test_historical_gateway_union_contains_owner_types(self) -> None:
        union_types = set(get_args(GatewayOperation))
        self.assertTrue(
            {
                BindConversationToProject,
                BindConversationToThread,
                ClearConversationThread,
            }.issubset(union_types)
        )

    def test_owner_dataclasses_preserve_public_field_shape(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(BindConversationToProject)),
            (
                "operation_id",
                "conversation_ref",
                "actor",
                "created_at",
                "project_ref",
                "expected_revision",
                "type",
            ),
        )
        self.assertEqual(
            tuple(item.name for item in fields(BindConversationToThread)),
            (
                "operation_id",
                "conversation_ref",
                "actor",
                "created_at",
                "thread_ref",
                "expected_revision",
                "type",
            ),
        )

    def test_operation_validation_preserves_binding_error(self) -> None:
        operation = BindConversationToProject(
            operation_id="op-project",
            conversation_ref=self.conversation,
            actor="user-1",
            project_ref=ProjectRef("", "project-1"),
            created_at=datetime.now(UTC),
        )
        with self.assertRaisesRegex(ContractViolation, "application_instance_id"):
            validate_gateway_operation(operation)

    def test_result_validation_preserves_binding_postcondition_errors(self) -> None:
        operation = self._bind_thread()
        result = ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=datetime.now(UTC),
            binding=ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=self.application,
                thread_ref=self.other_thread,
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "different thread"):
            validate_gateway_operation_result(operation, result)

    def test_result_validation_delegates_wrong_variant_to_binding_owner(self) -> None:
        operation = self._bind_thread()
        result = ApplicationsListed(
            operation_id=operation.operation_id,
            completed_at=datetime.now(UTC),
            applications=(),
        )
        object.__setattr__(result, "type", GatewayOperationType.CONVERSATION_BIND_THREAD)
        with patch.object(
            historical_validators,
            "_validate_binding_operation_result",
            wraps=binding_owner._validate_binding_operation_result,
        ) as owner_validator:
            with self.assertRaises(ContractViolation) as context:
                validate_gateway_operation_result(operation, result)
        self.assertEqual(
            str(context.exception),
            "conversation.bind_thread must return ConversationBound",
        )
        owner_validator.assert_called_once_with(operation, result)

    def test_clear_result_validation_preserves_thread_clear_error(self) -> None:
        operation = ClearConversationThread(
            operation_id="op-clear",
            conversation_ref=self.conversation,
            actor="user-1",
            created_at=datetime.now(UTC),
        )
        result = ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=datetime.now(UTC),
            binding=ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=self.application,
                thread_ref=self.thread,
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "did not clear the thread"):
            validate_gateway_operation_result(operation, result)


if __name__ == "__main__":
    unittest.main()

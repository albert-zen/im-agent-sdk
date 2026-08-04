from __future__ import annotations

import unittest
from datetime import UTC, datetime
from importlib import import_module
from typing import get_type_hints

from imagent import contracts
from imagent.applications import operations
from imagent.interaction.operations import ContractError, ContractViolation


class ApplicationOperationTests(unittest.TestCase):
    def test_facade_exports_preserve_exact_owner_identities(self) -> None:
        for name in (
            "ActivateNativeThread",
            "ApplicationOperation",
            "ApplicationOperationFailed",
            "ApplicationOperationResult",
            "ApplicationOperationType",
            "CreateThread",
            "DeleteThread",
            "GetProject",
            "GetThread",
            "GetThreadHistory",
            "GetThreadStatus",
            "GetTurnCatchup",
            "InterruptTurn",
            "ListProjects",
            "ListThreads",
            "NativeThreadActivated",
            "ProjectRead",
            "ProjectsListed",
            "RespondRequest",
            "RequestResponded",
            "ThreadCreated",
            "ThreadDeleted",
            "ThreadDeletionMode",
            "ThreadHistoryRead",
            "ThreadRead",
            "ThreadsListed",
            "ThreadStatusRead",
            "TurnCatchupRead",
            "TurnInterrupted",
            "validate_application_operation",
            "validate_application_operation_result",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(contracts, name), getattr(operations, name))

    def test_historical_contract_modules_no_longer_define_application_operations(self) -> None:
        historical_operations = import_module("imagent.contracts.operations")
        historical_validators = import_module("imagent.contracts.validators")
        for name in (
            "ApplicationOperation",
            "ApplicationOperationFailed",
            "ApplicationOperationResult",
            "ApplicationOperationType",
            "ThreadDeletionMode",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(historical_operations, name))
        self.assertFalse(hasattr(historical_validators, "validate_application_operation"))
        self.assertFalse(hasattr(historical_validators, "validate_application_operation_result"))

    def test_operation_annotations_resolve_to_existing_contract_values(self) -> None:
        hints = get_type_hints(operations.RespondRequest)
        self.assertIs(hints["application_ref"], contracts.ApplicationRef)
        self.assertIs(hints["request_ref"], contracts.RequestRef)
        self.assertEqual(hints["response"], contracts.RequestResponse)

    def test_operation_validation_preserves_scope_and_bounds(self) -> None:
        application = operations.ApplicationRef("zen-local")
        thread = operations.ThreadRef("zen-local", "thread-1")
        operations.validate_application_operation(
            operations.GetTurnCatchup(
                operation_id="op-catchup",
                application_ref=application,
                thread_ref=thread,
                limit=5,
                created_at=datetime.now(UTC),
            )
        )

        with self.assertRaisesRegex(ContractViolation, "between 1 and 20"):
            operations.validate_application_operation(
                operations.GetThreadHistory(
                    operation_id="op-history",
                    application_ref=application,
                    thread_ref=thread,
                    limit=21,
                    created_at=datetime.now(UTC),
                )
            )
        with self.assertRaisesRegex(ContractViolation, "different application"):
            operations.validate_application_operation(
                operations.GetTurnCatchup(
                    operation_id="op-cross-app",
                    application_ref=application,
                    thread_ref=operations.ThreadRef("t3-remote", "thread-1"),
                    created_at=datetime.now(UTC),
                )
            )

    def test_result_validation_preserves_variant_and_error_checks(self) -> None:
        operation = operations.ListThreads(
            operation_id="op-list",
            application_ref=operations.ApplicationRef("zen-local"),
            created_at=datetime.now(UTC),
        )
        result = operations.ThreadsListed(
            operation_id=operation.operation_id,
            completed_at=datetime.now(UTC),
            threads=operations.Page(()),
        )
        operations.validate_application_operation_result(operation, result)

        with self.assertRaisesRegex(ContractViolation, "type does not match"):
            operations.validate_application_operation_result(
                operation,
                operations.ApplicationOperationFailed(
                    operation_id=operation.operation_id,
                    type=operations.ApplicationOperationType.THREAD_STATUS,
                    completed_at=datetime.now(UTC),
                    error=ContractError(code="thread_not_found", message="missing"),
                ),
            )

        with self.assertRaisesRegex(ContractViolation, "message cannot be empty"):
            operations.validate_application_operation_result(
                operation,
                operations.ApplicationOperationFailed(
                    operation_id=operation.operation_id,
                    type=operation.type,
                    completed_at=datetime.now(UTC),
                    error=ContractError(code="thread_not_found", message=""),
                ),
            )

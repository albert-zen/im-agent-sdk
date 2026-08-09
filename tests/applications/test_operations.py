from __future__ import annotations

import unittest
from datetime import UTC, datetime
from importlib import import_module
from typing import get_type_hints

import imagent.applications as applications
from imagent import contracts
from imagent.applications import contract, operations, requests
from imagent.interaction.messages import MessageRole, TextContent
from imagent.interaction.operations import ContractError, ContractViolation


class ApplicationOperationTests(unittest.TestCase):
    def test_owner_has_exact_finite_exports_and_facades_are_negative(self) -> None:
        for name in (
            "ActivateNativeThread",
            "ApplicationOperation",
            "ApplicationOperationFailed",
            "ApplicationOperationResult",
            "ApplicationOperationType",
            "CreateProject",
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
            "MAX_PROJECT_CWD_LENGTH",
            "MAX_PROJECT_DISPLAY_NAME_LENGTH",
            "NativeThreadActivated",
            "ProjectRead",
            "ProjectCreated",
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
                self.assertTrue(hasattr(operations, name))
                self.assertIn(name, operations.__all__)
                self.assertFalse(hasattr(contracts, name))
                self.assertNotIn(name, contracts.__all__)
                self.assertNotIn(name, applications.__all__)

    def test_historical_contract_modules_no_longer_define_application_operations(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            import_module("imagent.contracts.operations")
        with self.assertRaises(ModuleNotFoundError):
            import_module("imagent.contracts.validators")

    def test_operation_annotations_resolve_to_existing_contract_values(self) -> None:
        hints = get_type_hints(operations.RespondRequest)
        self.assertIs(hints["application_ref"], contract.ApplicationRef)
        self.assertIs(hints["request_ref"], requests.RequestRef)
        self.assertEqual(hints["response"], requests.RequestResponse)

    def test_project_creation_is_bounded_and_result_scope_is_application_owned(self) -> None:
        application = contract.ApplicationRef("managed-app")
        operation = operations.CreateProject(
            operation_id="op-project-create",
            application_ref=application,
            cwd="/repo",
            display_name="Repository",
            created_at=datetime.now(UTC),
        )
        operations.validate_application_operation(operation)
        operations.validate_application_operation_result(
            operation,
            operations.ProjectCreated(
                operation_id=operation.operation_id,
                completed_at=datetime.now(UTC),
                project=contract.ProjectSummary(
                    ref=contract.ProjectRef("managed-app", "project-1"),
                    display_name="Repository",
                ),
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "cwd"):
            operations.validate_application_operation(
                operations.CreateProject(
                    operation_id="op-project-create-invalid",
                    application_ref=application,
                    cwd="x" * 4097,
                    created_at=datetime.now(UTC),
                )
            )

    def test_operation_validation_preserves_scope_and_bounds(self) -> None:
        application = contract.ApplicationRef("zen-local")
        thread = contract.ThreadRef(contract.ProjectRef("zen-local", "workspace"), "thread-1")
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
                    thread_ref=contract.ThreadRef(
                        contract.ProjectRef("t3-remote", "workspace"), "thread-1"
                    ),
                    created_at=datetime.now(UTC),
                )
            )

    def test_result_validation_preserves_variant_and_error_checks(self) -> None:
        operation = operations.ListThreads(
            operation_id="op-list",
            application_ref=contract.ApplicationRef("zen-local"),
            project_ref=contract.ProjectRef(
                contract.ApplicationRef("zen-local").application_instance_id, "workspace"
            ),
            created_at=datetime.now(UTC),
        )
        result = operations.ThreadsListed(
            operation_id=operation.operation_id,
            completed_at=datetime.now(UTC),
            threads=contract.Page(()),
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

    def test_history_result_rejects_foreign_nested_turns_and_messages(self) -> None:
        outer_thread = contract.ThreadRef(
            contract.ProjectRef("managed-app", "project-a"), "thread-a"
        )
        foreign_thread = contract.ThreadRef(
            contract.ProjectRef("managed-app", "project-b"), "thread-b"
        )
        operation = operations.GetThreadHistory(
            operation_id="op-history-ancestry",
            application_ref=contract.ApplicationRef("managed-app"),
            thread_ref=outer_thread,
            created_at=datetime.now(UTC),
        )
        foreign_message = contract.AgentMessage(
            agent_item_id="item-foreign",
            thread_ref=foreign_thread,
            role=MessageRole.ASSISTANT,
            content=(TextContent("foreign"),),
            created_at=datetime.now(UTC),
        )

        for entry, expected in (
            (
                contract.TurnHistoryEntry(
                    turn_ref=contract.TurnRef(foreign_thread, "turn-1"),
                    status=contract.TurnStatus.COMPLETED,
                ),
                "history Turn belongs to a different Thread",
            ),
            (
                contract.TurnHistoryEntry(
                    turn_ref=contract.TurnRef(outer_thread, "turn-1"),
                    status=contract.TurnStatus.COMPLETED,
                    agent_messages=(foreign_message,),
                ),
                "history message belongs to a different Thread",
            ),
        ):
            with (
                self.subTest(expected=expected),
                self.assertRaisesRegex(ContractViolation, expected),
            ):
                operations.validate_application_operation_result(
                    operation,
                    operations.ThreadHistoryRead(
                        operation_id=operation.operation_id,
                        completed_at=datetime.now(UTC),
                        history=contract.ThreadHistory(
                            thread_ref=outer_thread,
                            turns=(entry,),
                        ),
                    ),
                )

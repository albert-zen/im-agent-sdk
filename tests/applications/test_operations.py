from __future__ import annotations

import unittest
from datetime import UTC, datetime
from importlib import import_module
from typing import get_args, get_type_hints

import imagent.applications as applications
from imagent import contracts
from imagent.applications import contract, operations, requests
from imagent.interaction.media import AttachmentContent, AttachmentHandle
from imagent.interaction.messages import Content, MessageRole, TextContent, TextFormat
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
            "DeleteProject",
            "DeleteThread",
            "GetProject",
            "GetThread",
            "GetThreadHistory",
            "GetThreadStatus",
            "GetTurnCatchup",
            "InterruptTurn",
            "ListProjects",
            "ListThreads",
            "MAX_LIST_CURSOR_LENGTH",
            "MAX_LIST_PAGE_ITEMS",
            "MAX_LIST_QUERY_LENGTH",
            "MAX_PROJECT_CWD_LENGTH",
            "MAX_PROJECT_DISPLAY_NAME_LENGTH",
            "MAX_THREAD_INITIAL_CONTEXT_ATTACHMENT_FIELD_LENGTH",
            "MAX_THREAD_INITIAL_CONTEXT_HANDLE_LENGTH",
            "MAX_THREAD_INITIAL_CONTEXT_ITEMS",
            "MAX_THREAD_INITIAL_CONTEXT_METADATA_BYTES",
            "MAX_THREAD_INITIAL_CONTEXT_METADATA_ITEMS",
            "MAX_THREAD_INITIAL_CONTEXT_TEXT_LENGTH",
            "MAX_THREAD_TITLE_LENGTH",
            "NativeThreadActivated",
            "ProjectRead",
            "ProjectCreated",
            "ProjectDeleted",
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
        self.assertNotIn(
            operations.DeleteProject,
            get_args(operations._LegacyApplicationOperation),
        )

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

    def test_managed_project_deletion_is_primitive_and_exactly_scoped(self) -> None:
        application = contract.ApplicationRef("managed-app")
        project = contract.ProjectRef("managed-app", "project-1")
        operation = operations.DeleteProject(
            operation_id="op-project-delete",
            application_ref=application,
            project_ref=project,
            created_at=datetime.now(UTC),
        )
        operations.validate_application_operation(operation)
        operations.validate_application_operation_result(
            operation,
            operations.ProjectDeleted(
                operation_id=operation.operation_id,
                completed_at=datetime.now(UTC),
                project_ref=project,
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "different application"):
            operations.validate_application_operation(
                operations.DeleteProject(
                    operation_id="op-project-delete-foreign",
                    application_ref=application,
                    project_ref=contract.ProjectRef("other-app", "project-1"),
                    created_at=datetime.now(UTC),
                )
            )
        with self.assertRaisesRegex(ContractViolation, "different project"):
            operations.validate_application_operation_result(
                operation,
                operations.ProjectDeleted(
                    operation_id=operation.operation_id,
                    completed_at=datetime.now(UTC),
                    project_ref=contract.ProjectRef("managed-app", "project-2"),
                ),
            )

    def test_thread_creation_payload_is_bounded_before_native_execution(self) -> None:
        application = contract.ApplicationRef("managed-app")
        project = contract.ProjectRef("managed-app", "project-1")

        def operation(
            *,
            title: str | None = None,
            initial_context: tuple[Content, ...] = (),
        ) -> operations.CreateThread:
            return operations.CreateThread(
                operation_id="op-thread-create",
                application_ref=application,
                project_ref=project,
                title=title,
                initial_context=initial_context,
                created_at=datetime.now(UTC),
            )

        operations.validate_application_operation(
            operation(title="Thread", initial_context=(TextContent("context"),))
        )
        for invalid in (
            operation(title="x" * (operations.MAX_THREAD_TITLE_LENGTH + 1)),
            operation(
                initial_context=(TextContent("context"),)
                * (operations.MAX_THREAD_INITIAL_CONTEXT_ITEMS + 1)
            ),
            operation(
                initial_context=(
                    TextContent("x" * (operations.MAX_THREAD_INITIAL_CONTEXT_TEXT_LENGTH + 1)),
                )
            ),
            operation(
                initial_context=(
                    AttachmentContent(
                        attachment_id="attachment-1",
                        media_type="application/octet-stream",
                        source=AttachmentHandle(
                            "h" * (operations.MAX_THREAD_INITIAL_CONTEXT_HANDLE_LENGTH + 1)
                        ),
                    ),
                )
            ),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContractViolation):
                    operations.validate_application_operation(invalid)

        for invalid_size in (True, 1.5, "1"):
            with self.subTest(invalid_size=invalid_size):
                invalid = operation(
                    initial_context=(
                        AttachmentContent(
                            attachment_id="attachment-1",
                            media_type="application/octet-stream",
                            source=AttachmentHandle("handle-1"),
                            size_bytes=invalid_size,  # type: ignore[arg-type]
                        ),
                    )
                )
                with self.assertRaisesRegex(ContractViolation, "size_bytes"):
                    operations.validate_application_operation(invalid)

        for invalid_text in (
            TextContent([], TextFormat.PLAIN),  # type: ignore[arg-type]
            TextContent("context", "bogus"),  # type: ignore[arg-type]
        ):
            with self.subTest(invalid_text=invalid_text):
                with self.assertRaisesRegex(ContractViolation, "initial_context text"):
                    operations.validate_application_operation(
                        operation(initial_context=(invalid_text,))
                    )

    def test_operation_validation_preserves_scope_and_bounds(self) -> None:
        application = contract.ApplicationRef("zen-local")
        thread = contract.ThreadRef(contract.ProjectRef("zen-local", "workspace"), "thread-1")
        with self.assertRaisesRegex(ContractViolation, "query"):
            operations.validate_application_operation(
                operations.ListProjects(
                    operation_id="op-list-oversized",
                    application_ref=application,
                    query="x" * (operations.MAX_LIST_QUERY_LENGTH + 1),
                    created_at=datetime.now(UTC),
                )
            )
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

    def test_request_response_operation_uses_canonical_admission_bounds(self) -> None:
        application = contract.ApplicationRef("managed-app")
        turn_ref = contract.TurnRef(
            contract.ThreadRef(contract.ProjectRef("managed-app", "project-1"), "thread-1"),
            "turn-1",
        )
        request_ref = requests.RequestRef(application, "request-1")

        def operation(response: requests.RequestResponse) -> operations.RespondRequest:
            return operations.RespondRequest(
                operation_id="op-request-respond",
                application_ref=application,
                request_ref=request_ref,
                turn_ref=turn_ref,
                response=response,
                created_at=datetime.now(UTC),
            )

        boundary_responses = (
            requests.UserInputResponse(
                {
                    f"question-{index}": ("answer",)
                    for index in range(requests.MAX_INTERACTIVE_REQUEST_QUESTIONS)
                }
            ),
            requests.UserInputResponse(
                {
                    "question": tuple(
                        f"answer-{index}"
                        for index in range(requests.MAX_INTERACTIVE_REQUEST_CHOICES)
                    )
                }
            ),
            requests.UserInputResponse(
                {"question": ("x" * requests.MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH,)}
            ),
        )
        for response in boundary_responses:
            with self.subTest(response=response):
                operations.validate_application_operation(operation(response))

        invalid_responses = (
            requests.UserInputResponse(
                {
                    f"question-{index}": ("answer",)
                    for index in range(requests.MAX_INTERACTIVE_REQUEST_QUESTIONS + 1)
                }
            ),
            requests.UserInputResponse(
                {
                    "question": tuple(
                        f"answer-{index}"
                        for index in range(requests.MAX_INTERACTIVE_REQUEST_CHOICES + 1)
                    )
                }
            ),
            requests.UserInputResponse(
                {"question": ("x" * (requests.MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH + 1),)}
            ),
        )
        for response in invalid_responses:
            with self.subTest(response=response):
                with self.assertRaises(ContractViolation):
                    operations.validate_application_operation(operation(response))

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

    def test_list_results_require_immutable_bounded_page_items(self) -> None:
        operation = operations.ListProjects(
            operation_id="op-list-page-contract",
            application_ref=contract.ApplicationRef("managed-app"),
            created_at=datetime.now(UTC),
        )
        mutable_items = [
            contract.ProjectSummary(
                ref=contract.ProjectRef("managed-app", "project-1"),
                display_name="Project 1",
            )
        ]
        mutable_result = operations.ProjectsListed(
            operation_id=operation.operation_id,
            completed_at=datetime.now(UTC),
            projects=contract.Page(mutable_items),  # type: ignore[arg-type]
        )
        with self.assertRaisesRegex(ContractViolation, "items must be a tuple"):
            operations.validate_application_operation_result(operation, mutable_result)

        mutable_items.extend(
            contract.ProjectSummary(
                ref=contract.ProjectRef("managed-app", f"project-{index}"),
                display_name=f"Project {index}",
            )
            for index in range(2, operations.MAX_LIST_PAGE_ITEMS + 2)
        )
        with self.assertRaisesRegex(ContractViolation, "items must be a tuple"):
            operations.validate_application_operation_result(operation, mutable_result)

        oversized_result = operations.ProjectsListed(
            operation_id=operation.operation_id,
            completed_at=datetime.now(UTC),
            projects=contract.Page(
                tuple(
                    contract.ProjectSummary(
                        ref=contract.ProjectRef("managed-app", f"project-{index}"),
                        display_name=f"Project {index}",
                    )
                    for index in range(operations.MAX_LIST_PAGE_ITEMS + 1)
                )
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "at most 1000 items"):
            operations.validate_application_operation_result(operation, oversized_result)

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

    def test_history_and_catchup_reject_adapters_that_ignore_requested_limits(self) -> None:
        thread = contract.ThreadRef(
            contract.ProjectRef("managed-app", "project-a"),
            "thread-a",
        )
        now = datetime.now(UTC)
        history_operation = operations.GetThreadHistory(
            operation_id="op-history-limit",
            application_ref=contract.ApplicationRef("managed-app"),
            thread_ref=thread,
            limit=1,
            created_at=now,
        )
        turns = tuple(
            contract.TurnHistoryEntry(
                turn_ref=contract.TurnRef(thread, f"turn-{index}"),
                status=contract.TurnStatus.COMPLETED,
            )
            for index in range(2)
        )
        with self.assertRaisesRegex(ContractViolation, "more Turns than requested"):
            operations.validate_application_operation_result(
                history_operation,
                operations.ThreadHistoryRead(
                    operation_id=history_operation.operation_id,
                    completed_at=now,
                    history=contract.ThreadHistory(thread_ref=thread, turns=turns),
                ),
            )

        catchup_operation = operations.GetTurnCatchup(
            operation_id="op-catchup-limit",
            application_ref=contract.ApplicationRef("managed-app"),
            thread_ref=thread,
            limit=1,
            created_at=now,
        )
        messages = tuple(
            contract.AgentMessage(
                agent_item_id=f"message-{index}",
                thread_ref=thread,
                role=MessageRole.ASSISTANT,
                content=(TextContent("bounded"),),
                created_at=now,
            )
            for index in range(2)
        )
        with self.assertRaisesRegex(ContractViolation, "more messages than requested"):
            operations.validate_application_operation_result(
                catchup_operation,
                operations.TurnCatchupRead(
                    operation_id=catchup_operation.operation_id,
                    completed_at=now,
                    catchup=contract.TurnCatchup(
                        thread_ref=thread,
                        turn_ref=contract.TurnRef(thread, "turn-live"),
                        status=contract.TurnStatus.RUNNING,
                        messages=messages,
                    ),
                ),
            )

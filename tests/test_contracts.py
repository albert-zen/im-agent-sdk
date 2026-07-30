from __future__ import annotations

import unittest
from datetime import UTC, datetime

from imagent.contracts import (
    ApplicationCapabilities,
    ApplicationRef,
    ContractError,
    ContractViolation,
    ConversationBinding,
    ConversationRef,
    Operation,
    OperationResult,
    OperationResultStatus,
    OperationTarget,
    OperationType,
    ProjectCapabilities,
    ProjectMode,
    ProjectRef,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
    ThreadDeletionCapability,
    ThreadRef,
    derive_client_message_id,
    validate_application_capabilities,
    validate_binding,
    validate_operation,
    validate_operation_result,
)


def capabilities(mode: ProjectMode) -> ApplicationCapabilities:
    project_support = (
        SupportLevel.NATIVE if mode is ProjectMode.MANAGED else SupportLevel.UNSUPPORTED
    )
    return ApplicationCapabilities(
        projects=ProjectCapabilities(
            mode=mode,
            discovery=project_support,
            selection=project_support,
        ),
        threads=ThreadCapabilities(
            listing=SupportLevel.NATIVE,
            creation=SupportLevel.NATIVE,
            switching=SupportLevel.NATIVE,
            deletion=ThreadDeletionCapability.ARCHIVE,
        ),
        runtime=RuntimeCapabilities(
            history=SupportLevel.NATIVE,
            streaming=SupportLevel.NATIVE,
            replay_from_cursor=SupportLevel.UNSUPPORTED,
            interruption=SupportLevel.NATIVE,
            interactive_requests=SupportLevel.NATIVE,
        ),
    )


class CapabilityTests(unittest.TestCase):
    def test_accepts_managed_flat_and_fixed_project_modes(self) -> None:
        for mode in ProjectMode:
            with self.subTest(mode=mode):
                validate_application_capabilities(capabilities(mode))

    def test_rejects_project_operations_in_flat_mode(self) -> None:
        invalid = capabilities(ProjectMode.FLAT)
        invalid = ApplicationCapabilities(
            projects=ProjectCapabilities(
                mode=ProjectMode.FLAT,
                discovery=SupportLevel.NATIVE,
                selection=SupportLevel.UNSUPPORTED,
            ),
            threads=invalid.threads,
            runtime=invalid.runtime,
        )
        with self.assertRaisesRegex(ContractViolation, "flat project mode"):
            validate_application_capabilities(invalid)


class ReferenceAndBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conversation = ConversationRef("qq-primary", "c2c:user-1")
        self.application = ApplicationRef("zen-local")
        self.project = ProjectRef("zen-local", "repo-1")
        self.thread = ThreadRef("zen-local", "thread-1", self.project)

    def test_accepts_managed_binding(self) -> None:
        validate_binding(
            ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=self.application,
                project_ref=self.project,
                thread_ref=self.thread,
            ),
            capabilities(ProjectMode.MANAGED),
        )

    def test_accepts_projectless_flat_and_fixed_bindings(self) -> None:
        thread = ThreadRef("zen-local", "thread-1")
        for mode in (ProjectMode.FLAT, ProjectMode.FIXED):
            with self.subTest(mode=mode):
                validate_binding(
                    ConversationBinding(
                        conversation_ref=self.conversation,
                        application_ref=self.application,
                        thread_ref=thread,
                    ),
                    capabilities(mode),
                )

    def test_rejects_project_binding_in_fixed_mode(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "fixed project mode"):
            validate_binding(
                ConversationBinding(
                    conversation_ref=self.conversation,
                    application_ref=self.application,
                    project_ref=self.project,
                ),
                capabilities(ProjectMode.FIXED),
            )

    def test_rejects_cross_application_thread(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "different application"):
            validate_binding(
                ConversationBinding(
                    conversation_ref=self.conversation,
                    application_ref=self.application,
                    thread_ref=ThreadRef("t3-remote", "thread-1"),
                )
            )


class MessageIdentityTests(unittest.TestCase):
    def test_client_message_id_is_stable_and_scoped(self) -> None:
        conversation = ConversationRef("qq-primary", "c2c:user-1")
        first = derive_client_message_id(conversation, "message-7")
        second = derive_client_message_id(conversation, "message-7")
        other = derive_client_message_id(
            ConversationRef("qq-primary", "c2c:user-2"),
            "message-7",
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)


class OperationTests(unittest.TestCase):
    def test_history_operations_require_thread_reference(self) -> None:
        for operation_type in (
            OperationType.TURN_CATCHUP,
            OperationType.THREAD_HISTORY,
        ):
            with self.subTest(operation_type=operation_type):
                operation = Operation(
                    operation_id=f"op-{operation_type.value}",
                    conversation_ref=ConversationRef(
                        "qq-primary",
                        "c2c:user-1",
                    ),
                    actor="user-1",
                    type=operation_type,
                    target=OperationTarget(),
                    arguments={},
                    created_at=datetime.now(UTC),
                )
                with self.assertRaisesRegex(
                    ContractViolation,
                    "requires thread_ref",
                ):
                    validate_operation(operation)

    def test_thread_switch_requires_thread_reference(self) -> None:
        operation = Operation(
            operation_id="op-1",
            conversation_ref=ConversationRef("qq-primary", "c2c:user-1"),
            actor="user-1",
            type=OperationType.THREAD_SWITCH,
            target=OperationTarget(),
            arguments={},
            created_at=datetime.now(UTC),
        )
        with self.assertRaisesRegex(ContractViolation, "requires thread_ref"):
            validate_operation(operation)

    def test_thread_switch_accepts_scoped_thread(self) -> None:
        validate_operation(
            Operation(
                operation_id="op-1",
                conversation_ref=ConversationRef("qq-primary", "c2c:user-1"),
                actor="user-1",
                type=OperationType.THREAD_SWITCH,
                target=OperationTarget(thread_ref=ThreadRef("zen-local", "thread-1")),
                arguments={},
                created_at=datetime.now(UTC),
            )
        )

    def test_operation_result_error_invariants(self) -> None:
        validate_operation_result(
            OperationResult(
                operation_id="op-1",
                status=OperationResultStatus.FAILED,
                completed_at=datetime.now(UTC),
                error=ContractError(code="thread_not_found", message="missing"),
            )
        )
        with self.assertRaisesRegex(ContractViolation, "must contain an error"):
            validate_operation_result(
                OperationResult(
                    operation_id="op-2",
                    status=OperationResultStatus.FAILED,
                    completed_at=datetime.now(UTC),
                )
            )


if __name__ == "__main__":
    unittest.main()

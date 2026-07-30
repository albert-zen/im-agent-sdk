from __future__ import annotations

import unittest
from datetime import UTC, datetime

from imagent.contracts import (
    AgentEvent,
    AgentEventType,
    ApplicationCapabilities,
    ApplicationOperationFailed,
    ApplicationOperationType,
    ApplicationRef,
    BindConversationToThread,
    ContractError,
    ContractViolation,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    EventSequenceScope,
    GatewayOperationType,
    GetThreadHistory,
    GetTurnCatchup,
    ListThreads,
    OperationErrorCode,
    Page,
    ProjectCapabilities,
    ProjectMode,
    ProjectRef,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
    ThreadDeletionCapability,
    ThreadRef,
    ThreadsListed,
    derive_client_message_id,
    operation_error,
    validate_agent_event,
    validate_application_capabilities,
    validate_application_operation,
    validate_application_operation_result,
    validate_binding,
    validate_gateway_operation,
    validate_gateway_operation_result,
)


def capabilities(mode: ProjectMode) -> ApplicationCapabilities:
    project_support = (
        SupportLevel.NATIVE if mode is ProjectMode.MANAGED else SupportLevel.UNSUPPORTED
    )
    return ApplicationCapabilities(
        projects=ProjectCapabilities(
            mode=mode,
            discovery=project_support,
            reading=project_support,
        ),
        threads=ThreadCapabilities(
            listing=SupportLevel.NATIVE,
            creation=SupportLevel.NATIVE,
            reading=SupportLevel.NATIVE,
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

    def test_operation_errors_use_stable_codes(self) -> None:
        cases = (
            (ValueError("bad input"), OperationErrorCode.INVALID_OPERATION),
            (NotImplementedError("missing"), OperationErrorCode.UNSUPPORTED),
            (KeyError("gone"), OperationErrorCode.NOT_FOUND),
            (RuntimeError("boom"), OperationErrorCode.ADAPTER_FAILURE),
        )
        for error, expected in cases:
            with self.subTest(error=error):
                projected = operation_error(error)
                self.assertEqual(projected.code, expected.value)
                self.assertEqual(projected.metadata["native_exception"], type(error).__name__)

    def test_rejects_project_operations_in_flat_mode(self) -> None:
        invalid = capabilities(ProjectMode.FLAT)
        invalid = ApplicationCapabilities(
            projects=ProjectCapabilities(
                mode=ProjectMode.FLAT,
                discovery=SupportLevel.NATIVE,
                reading=SupportLevel.UNSUPPORTED,
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

    def test_event_ordering_fields_require_declared_guarantees(self) -> None:
        created_at = datetime.now(UTC)
        honest = AgentEvent(
            event_id="event-1",
            application_instance_id="app-1",
            type=AgentEventType.TURN_COMPLETED,
            data={},
            created_at=created_at,
        )
        validate_agent_event(honest, capabilities(ProjectMode.FLAT))

        with self.assertRaisesRegex(ContractViolation, "cursor"):
            validate_agent_event(
                AgentEvent(
                    event_id="event-2",
                    application_instance_id="app-1",
                    type=AgentEventType.TURN_COMPLETED,
                    data={},
                    created_at=created_at,
                    cursor="unsupported",
                ),
                capabilities(ProjectMode.FLAT),
            )

        replay_capabilities = ApplicationCapabilities(
            projects=ProjectCapabilities(
                mode=ProjectMode.FLAT,
                discovery=SupportLevel.UNSUPPORTED,
                reading=SupportLevel.UNSUPPORTED,
            ),
            threads=ThreadCapabilities(
                listing=SupportLevel.NATIVE,
                creation=SupportLevel.NATIVE,
                reading=SupportLevel.NATIVE,
            ),
            runtime=RuntimeCapabilities(
                history=SupportLevel.NATIVE,
                streaming=SupportLevel.NATIVE,
                replay_from_cursor=SupportLevel.NATIVE,
                interruption=SupportLevel.NATIVE,
                interactive_requests=SupportLevel.NATIVE,
                gap_detection=SupportLevel.NATIVE,
                event_sequence_scope=EventSequenceScope.THREAD,
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "sequence_epoch"):
            validate_agent_event(
                AgentEvent(
                    event_id="event-3",
                    application_instance_id="app-1",
                    type=AgentEventType.TURN_COMPLETED,
                    data={},
                    created_at=created_at,
                    sequence=1,
                    cursor="cursor-1",
                ),
                replay_capabilities,
            )
        validate_agent_event(
            AgentEvent(
                event_id="event-4",
                application_instance_id="app-1",
                type=AgentEventType.TURN_COMPLETED,
                data={},
                created_at=created_at,
                sequence=1,
                sequence_epoch="epoch-1",
                cursor="cursor-1",
            ),
            replay_capabilities,
        )


class OperationTests(unittest.TestCase):
    def test_typed_history_operations_validate_limits_and_scope(self) -> None:
        application = ApplicationRef("zen-local")
        thread = ThreadRef("zen-local", "thread-1")
        validate_application_operation(
            GetTurnCatchup(
                operation_id="op-catchup",
                application_ref=application,
                thread_ref=thread,
                limit=5,
                created_at=datetime.now(UTC),
            )
        )
        with self.assertRaisesRegex(ContractViolation, "between 1 and 20"):
            validate_application_operation(
                GetThreadHistory(
                    operation_id="op-history",
                    application_ref=application,
                    thread_ref=thread,
                    limit=21,
                    created_at=datetime.now(UTC),
                )
            )
        with self.assertRaisesRegex(ContractViolation, "different application"):
            validate_application_operation(
                GetTurnCatchup(
                    operation_id="op-cross-app",
                    application_ref=application,
                    thread_ref=ThreadRef("t3-remote", "thread-1"),
                    created_at=datetime.now(UTC),
                )
            )

    def test_gateway_thread_binding_is_a_distinct_typed_operation(self) -> None:
        operation = BindConversationToThread(
            operation_id="op-1",
            conversation_ref=ConversationRef("qq-primary", "c2c:user-1"),
            actor="user-1",
            thread_ref=ThreadRef("zen-local", "thread-1"),
            created_at=datetime.now(UTC),
        )
        validate_gateway_operation(operation)
        self.assertEqual(
            operation.type,
            GatewayOperationType.CONVERSATION_BIND_THREAD,
        )

    def test_result_variant_must_match_operation(self) -> None:
        operation = ListThreads(
            operation_id="op-list",
            application_ref=ApplicationRef("zen-local"),
            created_at=datetime.now(UTC),
        )
        result = ThreadsListed(
            operation_id=operation.operation_id,
            completed_at=datetime.now(UTC),
            threads=Page(()),
        )
        validate_application_operation_result(operation, result)
        with self.assertRaisesRegex(ContractViolation, "type does not match"):
            validate_application_operation_result(
                operation,
                ApplicationOperationFailed(
                    operation_id=operation.operation_id,
                    type=ApplicationOperationType.THREAD_STATUS,
                    completed_at=datetime.now(UTC),
                    error=ContractError(code="thread_not_found", message="missing"),
                ),
            )

    def test_gateway_result_must_preserve_conversation(self) -> None:
        operation = BindConversationToThread(
            operation_id="op-bind",
            conversation_ref=ConversationRef("qq-primary", "c2c:user-1"),
            actor="user-1",
            thread_ref=ThreadRef("zen-local", "thread-1"),
            created_at=datetime.now(UTC),
        )
        with self.assertRaisesRegex(ContractViolation, "different Conversation"):
            validate_gateway_operation_result(
                operation,
                ConversationBound(
                    operation_id=operation.operation_id,
                    type=operation.type,
                    completed_at=datetime.now(UTC),
                    binding=ConversationBinding(
                        conversation_ref=ConversationRef("qq-primary", "c2c:user-2"),
                        application_ref=ApplicationRef("zen-local"),
                        thread_ref=ThreadRef("zen-local", "thread-1"),
                    ),
                ),
            )

    def test_failed_result_requires_valid_error(self) -> None:
        operation = ListThreads(
            operation_id="op-1",
            application_ref=ApplicationRef("zen-local"),
            created_at=datetime.now(UTC),
        )
        with self.assertRaisesRegex(ContractViolation, "message cannot be empty"):
            validate_application_operation_result(
                operation,
                ApplicationOperationFailed(
                    operation_id="op-1",
                    type=operation.type,
                    completed_at=datetime.now(UTC),
                    error=ContractError(code="thread_not_found", message=""),
                ),
            )


if __name__ == "__main__":
    unittest.main()

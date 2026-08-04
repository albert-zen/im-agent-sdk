# pyright: reportMissingModuleSource=false
from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import ValidationError
from jsonschema.validators import validator_for
from referencing import Registry, Resource

from imagent.applications.capabilities import (
    ApplicationCapabilities,
    EventSequenceScope,
    ProjectCapabilities,
    ProjectMode,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
    ThreadDeletionCapability,
)
from imagent.applications.contract import (
    ApplicationRef,
    Page,
    ProjectRef,
    ThreadRef,
)
from imagent.applications.events import AgentEvent, AgentEventType, validate_agent_event
from imagent.applications.operations import (
    ApplicationOperationFailed,
    ApplicationOperationType,
    GetThreadHistory,
    GetTurnCatchup,
    ListThreads,
    ThreadsListed,
    validate_application_operation,
    validate_application_operation_result,
)
from imagent.applications.requests import (
    MAX_INTERACTIVE_REQUEST_CHOICES,
    MAX_INTERACTIVE_REQUEST_QUESTIONS,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalResponseShape,
    RequestChoice,
    RequestRef,
    UserInputQuestion,
    UserInputQuestionShape,
    UserInputRequest,
    UserInputResponse,
    UserInputResponseShape,
    validate_interactive_request,
    validate_request_response,
    validate_request_response_shape,
)
from imagent.contracts import (
    BindConversationToThread,
    ConversationBound,
    GatewayOperationType,
    validate_gateway_operation,
    validate_gateway_operation_result,
)
from imagent.gateway.input import derive_client_message_id
from imagent.gateway.persistence import (
    ConversationBinding,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
    validate_binding,
    validate_projection_route,
    validate_turn_reply_correlation,
)
from imagent.interaction.channels import (
    ChannelCapabilities,
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySegmentReceipt,
    DeliverySegmentStatus,
    DeliverySupportLevel,
    validate_delivery_receipt,
)
from imagent.interaction.media import AttachmentSourceKind
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractError, ContractViolation


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


class InteractiveRequestContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application = ApplicationRef("codex-local")
        self.request_ref = RequestRef(self.application, "epoch-4:request-7")
        self.thread = ThreadRef("codex-local", "thread-1")

    def test_approval_preserves_native_choice_ids_without_binary_policy(self) -> None:
        choices = tuple(
            RequestChoice(choice_id, label)
            for choice_id, label in (
                ("accept", "Approve once"),
                ("accept_for_session", "Approve for session"),
                ("decline", "Deny"),
                ("cancel", "Cancel"),
            )
        )
        request = ApprovalRequest(
            request_ref=self.request_ref,
            thread_ref=self.thread,
            turn_id="turn-1",
            prompt="Run the command?",
            choices=choices,
        )
        validate_interactive_request(request)
        shape = ApprovalResponseShape(tuple(choice.choice_id for choice in choices))
        validate_request_response(ApprovalResponse("accept_for_session"), shape)
        with self.assertRaisesRegex(ContractViolation, "not offered"):
            validate_request_response(ApprovalResponse("approve"), shape)

    def test_user_input_cardinality_is_explicit_and_enforced(self) -> None:
        single = UserInputQuestion(
            question_id="environment",
            prompt="Choose an environment",
            choices=(
                RequestChoice("staging", "Staging"),
                RequestChoice("production", "Production"),
            ),
            min_answers=1,
            max_answers=1,
        )
        validate_interactive_request(
            UserInputRequest(
                request_ref=self.request_ref,
                thread_ref=self.thread,
                turn_id="turn-1",
                questions=(single,),
            )
        )
        shape = UserInputResponseShape(
            (
                UserInputQuestionShape(
                    question_id=single.question_id,
                    choice_ids=tuple(choice.choice_id for choice in single.choices),
                    allows_other=False,
                    min_answers=1,
                    max_answers=1,
                ),
            )
        )
        validate_request_response(
            UserInputResponse({"environment": ("staging",)}),
            shape,
        )
        with self.assertRaisesRegex(ContractViolation, "too many"):
            validate_request_response(
                UserInputResponse({"environment": ("staging", "production")}),
                shape,
            )

    def test_request_collection_limits_accept_the_boundary_and_reject_one_more(self) -> None:
        approval_choices = tuple(
            RequestChoice(f"choice-{index}", f"Choice {index}")
            for index in range(MAX_INTERACTIVE_REQUEST_CHOICES)
        )
        validate_interactive_request(
            ApprovalRequest(
                request_ref=self.request_ref,
                thread_ref=self.thread,
                turn_id="turn-1",
                prompt="Approve?",
                choices=approval_choices,
            )
        )
        with self.assertRaisesRegex(ContractViolation, "choices exceed"):
            validate_interactive_request(
                ApprovalRequest(
                    request_ref=self.request_ref,
                    thread_ref=self.thread,
                    turn_id="turn-1",
                    prompt="Approve?",
                    choices=approval_choices + (RequestChoice("overflow", "Overflow"),),
                )
            )
        validate_interactive_request(
            UserInputRequest(
                request_ref=self.request_ref,
                thread_ref=self.thread,
                turn_id="turn-1",
                questions=(
                    UserInputQuestion(
                        question_id="bounded-choices",
                        prompt="Choose one",
                        choices=approval_choices,
                        min_answers=1,
                        max_answers=1,
                    ),
                ),
            )
        )
        with self.assertRaisesRegex(ContractViolation, "choices exceed"):
            validate_interactive_request(
                UserInputRequest(
                    request_ref=self.request_ref,
                    thread_ref=self.thread,
                    turn_id="turn-1",
                    questions=(
                        UserInputQuestion(
                            question_id="too-many-choices",
                            prompt="Choose one",
                            choices=approval_choices + (RequestChoice("overflow", "Overflow"),),
                            min_answers=1,
                            max_answers=1,
                        ),
                    ),
                )
            )

        questions = tuple(
            UserInputQuestion(
                question_id=f"question-{index}",
                prompt=f"Question {index}",
                allows_other=True,
            )
            for index in range(MAX_INTERACTIVE_REQUEST_QUESTIONS)
        )
        validate_interactive_request(
            UserInputRequest(
                request_ref=self.request_ref,
                thread_ref=self.thread,
                turn_id="turn-1",
                questions=questions,
            )
        )
        with self.assertRaisesRegex(ContractViolation, "questions exceed"):
            validate_interactive_request(
                UserInputRequest(
                    request_ref=self.request_ref,
                    thread_ref=self.thread,
                    turn_id="turn-1",
                    questions=questions
                    + (
                        UserInputQuestion(
                            question_id="overflow",
                            prompt="Overflow",
                            allows_other=True,
                        ),
                    ),
                )
            )

    def test_persisted_response_shape_collection_limits_reject_before_identity_walks(self) -> None:
        choice_ids = tuple(f"choice-{index}" for index in range(MAX_INTERACTIVE_REQUEST_CHOICES))
        validate_request_response_shape(ApprovalResponseShape(choice_ids))
        with self.assertRaisesRegex(ContractViolation, "choices exceed"):
            validate_request_response_shape(ApprovalResponseShape(choice_ids + ("overflow",)))
        validate_request_response_shape(
            UserInputResponseShape(
                (
                    UserInputQuestionShape(
                        question_id="bounded-choices",
                        choice_ids=choice_ids,
                        allows_other=False,
                        min_answers=1,
                        max_answers=1,
                    ),
                )
            )
        )
        with self.assertRaisesRegex(ContractViolation, "choices exceed"):
            validate_request_response_shape(
                UserInputResponseShape(
                    (
                        UserInputQuestionShape(
                            question_id="too-many-choices",
                            choice_ids=choice_ids + ("overflow",),
                            allows_other=False,
                            min_answers=1,
                            max_answers=1,
                        ),
                    )
                )
            )

        questions = tuple(
            UserInputQuestionShape(
                question_id=f"question-{index}",
                choice_ids=(),
                allows_other=True,
                min_answers=0,
                max_answers=1,
            )
            for index in range(MAX_INTERACTIVE_REQUEST_QUESTIONS)
        )
        validate_request_response_shape(UserInputResponseShape(questions))
        with self.assertRaisesRegex(ContractViolation, "questions exceed"):
            validate_request_response_shape(
                UserInputResponseShape(
                    questions
                    + (
                        UserInputQuestionShape(
                            question_id="overflow",
                            choice_ids=(),
                            allows_other=True,
                            min_answers=0,
                            max_answers=1,
                        ),
                    )
                )
            )

    def test_request_ref_must_match_event_application(self) -> None:
        request = ApprovalRequest(
            request_ref=RequestRef(
                ApplicationRef("other-application"),
                self.request_ref.native_request_id,
            ),
            thread_ref=self.thread,
            turn_id="turn-1",
            prompt="Run the command?",
            choices=(RequestChoice("accept", "Approve"),),
        )
        event = AgentEvent(
            event_id="request-event-1",
            application_instance_id="codex-local",
            type=AgentEventType.REQUEST_OPENED,
            data={},
            created_at=datetime.now(UTC),
            thread_ref=self.thread,
            turn_id="turn-1",
            request=request,
        )
        with self.assertRaisesRegex(ContractViolation, "different application"):
            validate_agent_event(event, capabilities(ProjectMode.FLAT))


class ProjectionContractTests(unittest.TestCase):
    def test_checkpoint_fields_are_a_nullable_pair(self) -> None:
        route = ThreadProjectionRoute(
            route_id="route-1",
            thread_ref=ThreadRef("agent-1", "thread-1"),
            conversation_ref=ConversationRef("channel-1", "conversation-1"),
        )
        validate_projection_route(route)
        with self.assertRaisesRegex(ContractViolation, "present together"):
            validate_projection_route(
                ThreadProjectionRoute(
                    route_id=route.route_id,
                    thread_ref=route.thread_ref,
                    conversation_ref=route.conversation_ref,
                    checkpoint_agent_item_id="agent-item-1",
                )
            )

    def test_turn_reply_correlation_requires_explicit_identity(self) -> None:
        validate_turn_reply_correlation(
            TurnReplyCorrelation(
                correlation_id="correlation-1",
                thread_ref=ThreadRef("agent-1", "thread-1"),
                turn_id="turn-1",
                client_message_id="client-message-1",
                conversation_ref=ConversationRef(
                    "channel-1",
                    "conversation-1",
                ),
                reply_to_message_id="native-message-1",
                created_at=datetime.now(UTC),
            )
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


class VersionOneSchemaCompatibilityTests(unittest.TestCase):
    def test_input_dispatch_and_acceptance_require_matching_correlation_policy(
        self,
    ) -> None:
        thread_ref = {
            "applicationInstanceId": "codex-main",
            "nativeThreadId": "thread-1",
        }
        self._validate_definition(
            "messages.schema.json",
            "ApplicationInputDispatch",
            {
                "threadRef": thread_ref,
                "clientMessageId": "client-1",
                "disposition": "steered",
                "correlationPolicy": "preserve_existing",
                "expectedTurnId": "turn-active",
            },
        )
        self._validate_definition(
            "messages.schema.json",
            "AcceptedTurn",
            {
                "threadRef": thread_ref,
                "turnId": "turn-active",
                "clientMessageId": "client-1",
                "disposition": "started",
                "correlationPolicy": "create_new",
            },
        )
        self._assert_invalid_definition(
            "messages.schema.json",
            "ApplicationInputDispatch",
            {
                "threadRef": thread_ref,
                "clientMessageId": "client-1",
                "disposition": "steered",
                "correlationPolicy": "create_new",
                "expectedTurnId": "turn-active",
            },
        )
        self._assert_invalid_definition(
            "messages.schema.json",
            "AcceptedTurn",
            {
                "threadRef": thread_ref,
                "turnId": "turn-active",
                "clientMessageId": "client-1",
                "disposition": "started",
                "correlationPolicy": "preserve_existing",
            },
        )

    def test_retryable_receipts_cannot_carry_native_acceptance_identity(self) -> None:
        receipts = (
            DeliveryReceipt(
                status=DeliveryReceiptStatus.RETRYABLE_FAILURE,
                items=(
                    DeliveryItemReceipt(
                        content_index=0,
                        status=DeliveryItemStatus.RETRYABLE_FAILURE,
                        native_message_id="native-item",
                    ),
                ),
            ),
            DeliveryReceipt(
                status=DeliveryReceiptStatus.RETRYABLE_FAILURE,
                segments=(
                    DeliverySegmentReceipt(
                        segment_index=0,
                        delivery_id="segment-0",
                        source_content_indexes=(0,),
                        status=DeliverySegmentStatus.RETRYABLE_FAILURE,
                        native_message_id="native-segment",
                    ),
                ),
            ),
        )
        for receipt in receipts:
            with self.subTest(receipt=receipt):
                with self.assertRaisesRegex(
                    ContractViolation,
                    "native acceptance identity",
                ):
                    validate_delivery_receipt(receipt)

        self._assert_invalid_definition(
            "deliveries.schema.json",
            "DeliveryReceipt",
            {
                "status": "retryable_failure",
                "nativeMessageId": "native-top",
                "items": [],
            },
        )
        self._assert_invalid_definition(
            "deliveries.schema.json",
            "DeliveryReceipt",
            {
                "status": "retryable_failure",
                "items": [
                    {
                        "contentIndex": 0,
                        "status": "retryable_failure",
                        "nativeMessageId": "native-item",
                    }
                ],
            },
        )
        self._assert_invalid_definition(
            "deliveries.schema.json",
            "DeliverySegmentReceipt",
            {
                "segmentIndex": 0,
                "deliveryId": "segment-0",
                "sourceContentIndexes": [0],
                "status": "retryable_failure",
                "nativeMessageId": "native-segment",
            },
        )

    def test_channel_capabilities_keep_the_v1_positional_constructor_order(self) -> None:
        capabilities = ChannelCapabilities(
            DeliverySupportLevel.FALLBACK,
            DeliverySupportLevel.NATIVE,
            DeliverySupportLevel.FALLBACK,
            DeliverySupportLevel.NATIVE,
            DeliverySupportLevel.FALLBACK,
            DeliverySupportLevel.NATIVE,
            DeliverySupportLevel.FALLBACK,
            DeliverySupportLevel.NATIVE,
            DeliverySupportLevel.FALLBACK,
            (AttachmentSourceKind.REMOTE_URL,),
            101,
            202,
            3,
        )

        self.assertIs(
            capabilities.reply_references,
            DeliverySupportLevel.NATIVE,
        )
        self.assertIs(
            capabilities.native_threads_or_topics,
            DeliverySupportLevel.FALLBACK,
        )
        self.assertEqual(
            capabilities.attachment_sources,
            (AttachmentSourceKind.REMOTE_URL,),
        )
        self.assertEqual(capabilities.max_text_length, 101)
        self.assertEqual(capabilities.max_attachment_size, 202)
        self.assertEqual(capabilities.max_attachment_count, 3)

    def test_channel_capabilities_keep_the_flat_v1_surface(self) -> None:
        capabilities = ChannelCapabilities(
            markdown=DeliverySupportLevel.NATIVE,
            max_text_length=4_000,
        )
        self.assertIs(capabilities.markdown, DeliverySupportLevel.NATIVE)
        self.assertIs(
            capabilities.delivery.markdown,
            DeliverySupportLevel.NATIVE,
        )
        self.assertEqual(capabilities.delivery.max_text_length, 4_000)

        self._validate_definition(
            "capabilities.schema.json",
            "ChannelCapabilities",
            {
                "plainText": "native",
                "markdown": "native",
                "messageEdits": "unsupported",
                "attachments": "unsupported",
                "interactiveActions": "unsupported",
            },
        )

    def test_channel_and_application_support_are_nominally_distinct_in_schema(
        self,
    ) -> None:
        schema_path = (
            Path(__file__).resolve().parents[2] / "schemas" / "v1" / "capabilities.schema.json"
        )
        definitions = json.loads(schema_path.read_text(encoding="utf-8"))["$defs"]

        self.assertEqual(
            definitions["DeliverySupportLevel"]["enum"],
            definitions["SupportLevel"]["enum"],
        )
        self.assertEqual(
            definitions["ChannelCapabilities"]["properties"]["plainText"]["$ref"],
            "#/$defs/DeliverySupportLevel",
        )
        self.assertEqual(
            definitions["RuntimeCapabilities"]["properties"]["history"]["$ref"],
            "#/$defs/SupportLevel",
        )

    def test_delivery_receipt_segments_remain_an_optional_v1_extension(self) -> None:
        self._validate_definition(
            "deliveries.schema.json",
            "DeliveryReceipt",
            {
                "status": "accepted_by_platform",
                "items": [],
            },
        )

    @staticmethod
    def _validate_definition(filename: str, definition: str, instance: Any) -> None:
        _definition_validator(filename, definition).validate(instance)

    def _assert_invalid_definition(
        self,
        filename: str,
        definition: str,
        instance: Any,
    ) -> None:
        with self.assertRaises(ValidationError):
            _definition_validator(filename, definition).validate(instance)


def _definition_validator(filename: str, definition: str) -> Any:
    schema_directory = Path(__file__).resolve().parents[2] / "schemas" / "v1"
    resources: list[tuple[str, Resource[Any]]] = []
    documents: dict[str, Any] = {}
    for path in schema_directory.glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        documents[path.name] = document
        resources.append((document["$id"], Resource.from_contents(document)))
    document = documents[filename]
    schema = {
        "$schema": document["$schema"],
        "$id": document["$id"],
        "$defs": document["$defs"],
        "$ref": f"#/$defs/{definition}",
    }
    return validator_for(schema)(
        schema,
        registry=Registry().with_resources(resources),
    )


if __name__ == "__main__":
    unittest.main()

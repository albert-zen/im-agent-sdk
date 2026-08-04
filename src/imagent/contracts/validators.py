from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from ..interaction.messages import ConversationRef
from ..interaction.operations import ContractViolation, require_identifier
from ._validation import validate_thread_ref
from .model import ConversationBinding, ThreadProjectionRoute, TurnReplyCorrelation
from .operations import (
    ApplicationsListed,
    ApprovalResponse,
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationThread,
    ConversationBound,
    GatewayOperation,
    GatewayOperationFailed,
    GatewayOperationResult,
    ListApplications,
    ObserveThread,
    RequestResponseRouted,
    RespondToRequest,
    SelectApplication,
    ThreadObserved,
    UserInputResponse,
)
from .request_validation import validate_request_ref

if TYPE_CHECKING:
    from ..applications.capabilities import ApplicationCapabilities


def validate_binding(
    binding: ConversationBinding,
    capabilities: ApplicationCapabilities | None = None,
) -> None:
    from ..applications.capabilities import ProjectMode

    require_identifier(binding.conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(binding.conversation_ref.native_conversation_id, "native_conversation_id")
    if binding.revision < 0:
        raise ContractViolation("binding revision cannot be negative")

    application_id = (
        binding.application_ref.application_instance_id if binding.application_ref else None
    )
    if binding.project_ref is not None:
        if application_id != binding.project_ref.application_instance_id:
            raise ContractViolation("binding project belongs to a different application")
    if binding.thread_ref is not None:
        validate_thread_ref(binding.thread_ref)
        if application_id != binding.thread_ref.application_instance_id:
            raise ContractViolation("binding thread belongs to a different application")
    if (
        binding.project_ref is not None
        and binding.thread_ref is not None
        and binding.thread_ref.project_ref is not None
        and binding.project_ref != binding.thread_ref.project_ref
    ):
        raise ContractViolation("binding thread belongs to a different project")
    if (
        capabilities is not None
        and capabilities.projects.mode in (ProjectMode.FLAT, ProjectMode.FIXED)
        and binding.project_ref is not None
    ):
        raise ContractViolation(
            f"{capabilities.projects.mode.value} project mode cannot bind a project"
        )


def validate_projection_route(route: ThreadProjectionRoute) -> None:
    require_identifier(route.route_id, "route_id")
    validate_thread_ref(route.thread_ref)
    require_identifier(
        route.conversation_ref.channel_instance_id,
        "channel_instance_id",
    )
    require_identifier(
        route.conversation_ref.native_conversation_id,
        "native_conversation_id",
    )
    if route.reply_to_message_id is not None:
        require_identifier(route.reply_to_message_id, "reply_to_message_id")
    if route.checkpoint_agent_item_id is not None:
        require_identifier(
            route.checkpoint_agent_item_id,
            "checkpoint_agent_item_id",
        )
    if (route.checkpoint_agent_item_id is None) != (route.checkpointed_at is None):
        raise ContractViolation(
            "checkpoint_agent_item_id and checkpointed_at must be present together"
        )


def validate_turn_reply_correlation(correlation: TurnReplyCorrelation) -> None:
    require_identifier(correlation.correlation_id, "correlation_id")
    validate_thread_ref(correlation.thread_ref)
    require_identifier(correlation.turn_id, "turn_id")
    require_identifier(correlation.client_message_id, "client_message_id")
    require_identifier(
        correlation.conversation_ref.channel_instance_id,
        "channel_instance_id",
    )
    require_identifier(
        correlation.conversation_ref.native_conversation_id,
        "native_conversation_id",
    )
    require_identifier(correlation.reply_to_message_id, "reply_to_message_id")


def validate_gateway_operation(operation: GatewayOperation) -> None:
    require_identifier(operation.operation_id, "operation_id")
    require_identifier(operation.actor, "actor")
    require_identifier(operation.conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(operation.conversation_ref.native_conversation_id, "native_conversation_id")
    expected_revision = getattr(operation, "expected_revision", None)
    if expected_revision is not None and expected_revision < 0:
        raise ContractViolation("expected_revision cannot be negative")
    if isinstance(operation, SelectApplication):
        require_identifier(
            operation.application_ref.application_instance_id,
            "application_instance_id",
        )
    if isinstance(operation, BindConversationToProject):
        require_identifier(operation.project_ref.application_instance_id, "application_instance_id")
        require_identifier(operation.project_ref.native_project_id, "native_project_id")
    if isinstance(operation, BindConversationToThread):
        validate_thread_ref(operation.thread_ref)
    if isinstance(operation, ObserveThread):
        validate_thread_ref(operation.thread_ref)
        if operation.reply_to_message_id is not None:
            require_identifier(operation.reply_to_message_id, "reply_to_message_id")
    if isinstance(operation, RespondToRequest):
        validate_request_ref(operation.request_ref)
        if isinstance(operation.response, ApprovalResponse):
            require_identifier(operation.response.choice_id, "choice_id")
        if isinstance(operation.response, UserInputResponse):
            if not operation.response.answers:
                raise ContractViolation("user input response requires answers")
            for question_id, answers in operation.response.answers.items():
                require_identifier(question_id, "question_id")
                if not answers or any(not answer for answer in answers):
                    raise ContractViolation("each user input question requires non-empty answers")


def validate_gateway_operation_result(
    operation: GatewayOperation,
    result: GatewayOperationResult,
) -> None:
    require_identifier(result.operation_id, "operation_id")
    if result.operation_id != operation.operation_id:
        raise ContractViolation("Gateway result ID does not match the request")
    if result.type is not operation.type:
        raise ContractViolation("Gateway result type does not match the request")
    if isinstance(result, GatewayOperationFailed):
        _validate_error(result.error)
        return
    if isinstance(operation, ListApplications):
        if not isinstance(result, ApplicationsListed):
            raise ContractViolation("application.list must return ApplicationsListed")
        return
    if isinstance(operation, ObserveThread):
        if not isinstance(result, ThreadObserved):
            raise ContractViolation("thread.observe must return ThreadObserved")
        validate_projection_route(result.route)
        if result.route.thread_ref != operation.thread_ref:
            raise ContractViolation("thread.observe returned a different Thread")
        if result.route.conversation_ref != operation.conversation_ref:
            raise ContractViolation("thread.observe returned a different Conversation")
        if result.route.reply_to_message_id != operation.reply_to_message_id:
            raise ContractViolation("thread.observe returned different reply correlation")
        return
    if isinstance(operation, RespondToRequest):
        if not isinstance(result, RequestResponseRouted):
            raise ContractViolation(
                "conversation.respond_request must return RequestResponseRouted"
            )
        if result.request_ref != operation.request_ref:
            raise ContractViolation("Gateway response routed a different request")
        return
    if not isinstance(
        operation,
        (
            SelectApplication,
            BindConversationToProject,
            BindConversationToThread,
            ClearConversationThread,
        ),
    ) or not isinstance(result, ConversationBound):
        raise ContractViolation(f"{operation.type.value} must return ConversationBound")
    if result.binding.conversation_ref != operation.conversation_ref:
        raise ContractViolation("Gateway result belongs to a different Conversation")
    validate_binding(result.binding)
    if isinstance(operation, SelectApplication):
        if (
            result.binding.application_ref != operation.application_ref
            or result.binding.project_ref is not None
            or result.binding.thread_ref is not None
        ):
            raise ContractViolation("application.select returned an incompatible binding")
    elif isinstance(operation, BindConversationToProject):
        if (
            result.binding.project_ref != operation.project_ref
            or result.binding.thread_ref is not None
        ):
            raise ContractViolation("conversation.bind_project returned an incompatible binding")
    elif isinstance(operation, BindConversationToThread):
        if result.binding.thread_ref != operation.thread_ref:
            raise ContractViolation("conversation.bind_thread returned a different thread")
    elif isinstance(operation, ClearConversationThread):
        if result.binding.thread_ref is not None:
            raise ContractViolation("conversation.clear_thread did not clear the thread")


def _validate_error(error) -> None:
    require_identifier(error.code, "error.code")
    if not error.message:
        raise ContractViolation("operation error message cannot be empty")


def derive_client_message_id(
    conversation: ConversationRef,
    channel_message_id: str,
) -> str:
    require_identifier(channel_message_id, "channel_message_id")
    identity = json.dumps(
        [
            conversation.channel_instance_id,
            conversation.native_conversation_id,
            channel_message_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:client-message:sha256:{digest}"

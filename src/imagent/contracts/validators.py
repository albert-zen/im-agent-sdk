"""Pending owner-specific Gateway validation delegates.

The aggregate Gateway validator lives in
``imagent.gateway.routing.operations``. These helpers remain with the pending
projection-route and request-correlation contract values until those later
slices move their concrete owners. ``derive_client_message_id`` remains an
input-dispatch contract helper.
"""

from __future__ import annotations

import hashlib
import json

from ..applications.contract import validate_thread_ref
from ..applications.requests import (
    ApprovalResponse,
    UserInputResponse,
    validate_request_ref,
)
from ..gateway.persistence.state_contracts import validate_projection_route
from ..interaction.messages import ConversationRef
from ..interaction.operations import ContractViolation, require_identifier
from .operations import (
    ObserveThread as _ObserveThread,
)
from .operations import (
    RequestResponseRouted as _RequestResponseRouted,
)
from .operations import (
    RespondToRequest as _RespondToRequest,
)
from .operations import (
    ThreadObserved as _ThreadObserved,
)


def _validate_observe_operation(operation: _ObserveThread) -> None:
    validate_thread_ref(operation.thread_ref)
    if operation.reply_to_message_id is not None:
        require_identifier(operation.reply_to_message_id, "reply_to_message_id")


def _validate_observe_operation_result(
    operation: _ObserveThread,
    result: object,
) -> None:
    if not isinstance(result, _ThreadObserved):
        raise ContractViolation("thread.observe must return ThreadObserved")
    validate_projection_route(result.route)
    if result.route.thread_ref != operation.thread_ref:
        raise ContractViolation("thread.observe returned a different Thread")
    if result.route.conversation_ref != operation.conversation_ref:
        raise ContractViolation("thread.observe returned a different Conversation")
    if result.route.reply_to_message_id != operation.reply_to_message_id:
        raise ContractViolation("thread.observe returned different reply correlation")


def _validate_respond_operation(operation: _RespondToRequest) -> None:
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


def _validate_respond_operation_result(
    operation: _RespondToRequest,
    result: object,
) -> None:
    if not isinstance(result, _RequestResponseRouted):
        raise ContractViolation("conversation.respond_request must return RequestResponseRouted")
    if result.request_ref != operation.request_ref:
        raise ContractViolation("Gateway response routed a different request")


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

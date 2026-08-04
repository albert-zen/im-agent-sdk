"""Pending request-correlation and input-dispatch validation delegates.

The aggregate Gateway validator lives in
``imagent.gateway.routing.operations``. Request-correlation helpers remain
with their pending contract values until that focused slice moves them.
``derive_client_message_id`` remains an input-dispatch contract helper.
"""

from __future__ import annotations

import hashlib
import json

from ..applications.requests import (
    ApprovalResponse,
    UserInputResponse,
    validate_request_ref,
)
from ..interaction.messages import ConversationRef
from ..interaction.operations import ContractViolation, require_identifier
from .operations import (
    RequestResponseRouted as _RequestResponseRouted,
)
from .operations import (
    RespondToRequest as _RespondToRequest,
)


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

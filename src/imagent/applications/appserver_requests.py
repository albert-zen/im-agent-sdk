from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..contracts import (
    MAX_INTERACTIVE_REQUEST_CHOICES,
    MAX_INTERACTIVE_REQUEST_QUESTIONS,
    ApplicationRef,
    ApprovalRequest,
    ApprovalResponse,
    InteractiveRequest,
    RequestChoice,
    RequestRef,
    RequestResponse,
    ThreadRef,
    UserInputQuestion,
    UserInputRequest,
    UserInputResponse,
    derive_request_response_shape,
    validate_request_response,
)

COMMAND_APPROVAL = "item/commandExecution/requestApproval"
FILE_APPROVAL = "item/fileChange/requestApproval"
USER_INPUT = "item/tool/requestUserInput"
PERMISSIONS_APPROVAL = "item/permissions/requestApproval"
_DISPLAY_VALUE_LIMIT = 2_000
_PERMISSION_SUMMARY_LIMIT = 4_000


class UnsupportedAppServerRequest(NotImplementedError):
    pass


@dataclass(frozen=True, slots=True)
class PendingAppServerRequest:
    request: InteractiveRequest
    method: str
    transport_request_id: str | int
    connection_epoch: int
    approval_payloads: Mapping[str, object] = field(default_factory=dict)
    answer_payloads: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


def map_appserver_request(
    application_ref: ApplicationRef,
    message: Mapping[str, object],
) -> PendingAppServerRequest:
    method = str(message.get("method") or "")
    params = message.get("params")
    if not isinstance(params, Mapping):
        raise ValueError("App Server request params must be an object")
    transport_request_id = params.get("_transport_request_id")
    if isinstance(transport_request_id, bool) or not isinstance(
        transport_request_id,
        (str, int),
    ):
        raise ValueError("App Server request is missing its transport request ID")
    epoch = params.get("_connection_epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ValueError("App Server request is missing its connection epoch")
    thread_id = _required_string(params, "threadId")
    turn_id = _required_string(params, "turnId")
    request_ref = derive_appserver_request_ref(
        application_ref,
        connection_epoch=epoch,
        transport_request_id=transport_request_id,
    )
    thread_ref = ThreadRef(
        application_instance_id=application_ref.application_instance_id,
        native_thread_id=thread_id,
    )
    if method == COMMAND_APPROVAL:
        return _command_approval(
            request_ref,
            thread_ref,
            turn_id,
            params,
            transport_request_id,
            epoch,
        )
    if method == FILE_APPROVAL:
        return _file_approval(
            request_ref,
            thread_ref,
            turn_id,
            params,
            transport_request_id,
            epoch,
        )
    if method == USER_INPUT:
        return _user_input(
            request_ref,
            thread_ref,
            turn_id,
            params,
            transport_request_id,
            epoch,
        )
    if method == PERMISSIONS_APPROVAL:
        return _permissions_approval(
            request_ref,
            thread_ref,
            turn_id,
            params,
            transport_request_id,
            epoch,
        )
    raise UnsupportedAppServerRequest(
        f"unsupported App Server request method: {method or '<missing>'}"
    )


def map_zen_appserver_request(
    application_ref: ApplicationRef,
    message: Mapping[str, object],
) -> PendingAppServerRequest:
    """Map only the interactive request surface evidenced by native Zen."""

    method = str(message.get("method") or "")
    if method != COMMAND_APPROVAL:
        raise UnsupportedAppServerRequest(
            f"unsupported Zen App Server request method: {method or '<missing>'}"
        )
    return map_appserver_request(application_ref, message)


def build_appserver_response(
    pending: PendingAppServerRequest,
    response: RequestResponse,
) -> dict[str, object]:
    validate_request_response(
        response,
        derive_request_response_shape(pending.request),
    )
    if isinstance(response, ApprovalResponse):
        payload = pending.approval_payloads[response.choice_id]
        if not isinstance(payload, Mapping):
            raise ValueError("App Server approval response payload is invalid")
        return dict(payload)
    assert isinstance(response, UserInputResponse)
    if not isinstance(pending.request, UserInputRequest):
        raise ValueError("user input response belongs to an approval request")
    answers: dict[str, object] = {}
    questions = {question.question_id: question for question in pending.request.questions}
    for question_id, values in response.answers.items():
        question = questions[question_id]
        native_options = pending.answer_payloads.get(question_id, {})
        native_values = [
            native_options.get(value, value) if question.allows_other else native_options[value]
            for value in values
        ]
        answers[question_id] = {"answers": native_values}
    return {"answers": answers}


def derive_appserver_request_ref(
    application_ref: ApplicationRef,
    *,
    connection_epoch: int,
    transport_request_id: str | int,
) -> RequestRef:
    identity = json.dumps(
        [
            application_ref.application_instance_id,
            connection_epoch,
            transport_request_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return RequestRef(
        application_ref=application_ref,
        native_request_id=f"appserver:sha256:{digest}",
    )


def _command_approval(
    request_ref: RequestRef,
    thread_ref: ThreadRef,
    turn_id: str,
    params: Mapping[str, object],
    transport_request_id: str | int,
    epoch: int,
) -> PendingAppServerRequest:
    decisions = params.get("availableDecisions")
    if not isinstance(decisions, list) or not decisions:
        decisions = ["accept", "decline"]
    choices, decisions_by_choice = _approval_choices(decisions)
    reason = _optional_string(params.get("reason"))
    command = _optional_string(params.get("command"))
    cwd = _optional_string(params.get("cwd"))
    prompt = _approval_prompt(
        "Approve command execution?",
        (
            ("Reason", reason),
            ("Command", command),
            ("Working directory", cwd),
        ),
    )
    return PendingAppServerRequest(
        request=ApprovalRequest(
            request_ref=request_ref,
            thread_ref=thread_ref,
            turn_id=turn_id,
            prompt=prompt,
            choices=choices,
            metadata={"native_method": COMMAND_APPROVAL},
        ),
        method=COMMAND_APPROVAL,
        transport_request_id=transport_request_id,
        connection_epoch=epoch,
        approval_payloads={
            choice_id: {"decision": decision} for choice_id, decision in decisions_by_choice.items()
        },
    )


def _file_approval(
    request_ref: RequestRef,
    thread_ref: ThreadRef,
    turn_id: str,
    params: Mapping[str, object],
    transport_request_id: str | int,
    epoch: int,
) -> PendingAppServerRequest:
    decisions = ["accept", "acceptForSession", "decline", "cancel"]
    choices, decisions_by_choice = _approval_choices(decisions)
    reason = _optional_string(params.get("reason"))
    grant_root = _optional_string(params.get("grantRoot"))
    prompt = _approval_prompt(
        "Approve the proposed file changes?",
        (
            ("Reason", reason),
            ("Requested write root", grant_root),
        ),
    )
    return PendingAppServerRequest(
        request=ApprovalRequest(
            request_ref=request_ref,
            thread_ref=thread_ref,
            turn_id=turn_id,
            prompt=prompt,
            choices=choices,
            metadata={"native_method": FILE_APPROVAL},
        ),
        method=FILE_APPROVAL,
        transport_request_id=transport_request_id,
        connection_epoch=epoch,
        approval_payloads={
            choice_id: {"decision": decision} for choice_id, decision in decisions_by_choice.items()
        },
    )


def _permissions_approval(
    request_ref: RequestRef,
    thread_ref: ThreadRef,
    turn_id: str,
    params: Mapping[str, object],
    transport_request_id: str | int,
    epoch: int,
) -> PendingAppServerRequest:
    permissions = params.get("permissions")
    if not isinstance(permissions, Mapping):
        raise ValueError("App Server permission approval has invalid permissions")
    reason = _optional_string(params.get("reason"))
    cwd = _optional_string(params.get("cwd"))
    permission_summary = _bounded_json(
        permissions,
        limit=_PERMISSION_SUMMARY_LIMIT,
    )
    prompt = _approval_prompt(
        "Approve the requested additional permissions?",
        (
            ("Reason", reason),
            ("Working directory", cwd),
            ("Requested permissions", permission_summary),
        ),
    )
    return PendingAppServerRequest(
        request=ApprovalRequest(
            request_ref=request_ref,
            thread_ref=thread_ref,
            turn_id=turn_id,
            prompt=prompt,
            choices=(
                RequestChoice(
                    "grant_requested_permissions",
                    "Grant requested permissions",
                ),
                RequestChoice(
                    "decline_requested_permissions",
                    "Deny requested permissions",
                ),
            ),
            metadata={"native_method": PERMISSIONS_APPROVAL},
        ),
        method=PERMISSIONS_APPROVAL,
        transport_request_id=transport_request_id,
        connection_epoch=epoch,
        approval_payloads={
            "grant_requested_permissions": {"permissions": dict(permissions)},
            "decline_requested_permissions": {"permissions": {}},
        },
    )


def _user_input(
    request_ref: RequestRef,
    thread_ref: ThreadRef,
    turn_id: str,
    params: Mapping[str, object],
    transport_request_id: str | int,
    epoch: int,
) -> PendingAppServerRequest:
    native_questions = params.get("questions")
    if not isinstance(native_questions, list) or not native_questions:
        raise ValueError("App Server user input request has no questions")
    _require_collection_limit(
        native_questions,
        limit=MAX_INTERACTIVE_REQUEST_QUESTIONS,
        field="App Server user input questions",
    )
    questions: list[UserInputQuestion] = []
    answer_payloads: dict[str, Mapping[str, str]] = {}
    for native_question in native_questions:
        if not isinstance(native_question, Mapping):
            raise ValueError("App Server user input question must be an object")
        question_id = _required_string(native_question, "id")
        native_options = native_question.get("options")
        options = native_options if isinstance(native_options, list) else []
        _require_collection_limit(
            options,
            limit=MAX_INTERACTIVE_REQUEST_CHOICES,
            field="App Server user input options",
        )
        choices: list[RequestChoice] = []
        option_payloads: dict[str, str] = {}
        for index, native_option in enumerate(options, start=1):
            if not isinstance(native_option, Mapping):
                raise ValueError("App Server user input option must be an object")
            label = _required_string(native_option, "label")
            choice_id = f"option:{index}"
            choices.append(
                RequestChoice(
                    choice_id=choice_id,
                    label=label,
                    description=_optional_string(native_option.get("description")),
                )
            )
            option_payloads[choice_id] = label
        allows_other = native_question.get("isOther") is True or not choices
        questions.append(
            UserInputQuestion(
                question_id=question_id,
                prompt=_required_string(native_question, "question"),
                header=_optional_string(native_question.get("header")),
                choices=tuple(choices),
                allows_other=allows_other,
                secret=native_question.get("isSecret") is True,
                min_answers=1,
                max_answers=1,
            )
        )
        answer_payloads[question_id] = option_payloads
    return PendingAppServerRequest(
        request=UserInputRequest(
            request_ref=request_ref,
            thread_ref=thread_ref,
            turn_id=turn_id,
            questions=tuple(questions),
            metadata={"native_method": USER_INPUT},
        ),
        method=USER_INPUT,
        transport_request_id=transport_request_id,
        connection_epoch=epoch,
        answer_payloads=answer_payloads,
    )


def _approval_choices(
    decisions: Sequence[object],
) -> tuple[tuple[RequestChoice, ...], Mapping[str, object]]:
    _require_collection_limit(
        decisions,
        limit=MAX_INTERACTIVE_REQUEST_CHOICES,
        field="App Server approval decisions",
    )
    choices: list[RequestChoice] = []
    payloads: dict[str, object] = {}
    for decision in decisions:
        canonical = json.dumps(
            decision,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        choice_id = (
            decision
            if isinstance(decision, str)
            else f"native:sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
        )
        if choice_id in payloads:
            continue
        choices.append(
            RequestChoice(
                choice_id=choice_id,
                label=_decision_label(decision),
            )
        )
        payloads[choice_id] = decision
    if not choices:
        raise ValueError("App Server approval has no supported decisions")
    return tuple(choices), payloads


def _decision_label(decision: object) -> str:
    if decision == "accept":
        return "Approve once"
    if decision == "acceptForSession":
        return "Approve for this session"
    if decision == "decline":
        return "Deny and continue"
    if decision == "cancel":
        return "Deny and stop the Turn"
    if isinstance(decision, Mapping):
        if "acceptWithExecpolicyAmendment" in decision:
            return "Approve and remember command policy"
        if "applyNetworkPolicyAmendment" in decision:
            return "Apply the proposed network policy"
    return "Native approval option"


def _required_string(value: Mapping[str, object], key: str) -> str:
    result = _optional_string(value.get(key))
    if result is None:
        raise ValueError(f"App Server request is missing {key}")
    return result


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    result = str(value)
    return result if result else None


def _approval_prompt(
    summary: str,
    fields: Sequence[tuple[str, str | None]],
) -> str:
    lines = [summary]
    for label, value in fields:
        if value is None:
            continue
        lines.extend(
            (
                "",
                f"{label}:",
                _bounded_text(value, limit=_DISPLAY_VALUE_LIMIT),
            )
        )
    return "\n".join(lines)


def _bounded_json(value: object, *, limit: int) -> str:
    return _bounded_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        limit=limit,
    )


def _bounded_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(0, limit - 16)].rstrip()}\n… [truncated]"


def _require_collection_limit(
    values: Sequence[object],
    *,
    limit: int,
    field: str,
) -> None:
    if len(values) > limit:
        raise ValueError(f"{field} exceed the maximum of {limit}")

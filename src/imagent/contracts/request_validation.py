from __future__ import annotations

from ..interaction.operations import ContractViolation, require_identifier
from ._validation import validate_thread_ref
from .model import (
    ApprovalRequest,
    ApprovalResponseShape,
    RequestRef,
    RequestResolution,
    RequestResponseShape,
    RequestRouteCorrelation,
    RequestRouteState,
    UserInputQuestionShape,
    UserInputRequest,
    UserInputResponseShape,
)
from .operations import ApprovalResponse, UserInputResponse

MAX_INTERACTIVE_REQUEST_QUESTIONS = 32
MAX_INTERACTIVE_REQUEST_CHOICES = 64


def validate_interactive_request(
    request: ApprovalRequest | UserInputRequest,
) -> None:
    validate_request_ref(request.request_ref)
    validate_thread_ref(request.thread_ref)
    if (
        request.request_ref.application_ref.application_instance_id
        != request.thread_ref.application_instance_id
    ):
        raise ContractViolation("request belongs to a different application")
    require_identifier(request.turn_id, "turn_id")
    if request.expires_at is not None and request.expires_at.tzinfo is None:
        raise ContractViolation("request expiry must include a timezone")
    if isinstance(request, ApprovalRequest):
        if not request.prompt.strip():
            raise ContractViolation("approval prompt cannot be empty")
        if not request.choices:
            raise ContractViolation("approval request requires choices")
        _validate_choice_count(len(request.choices))
        _validate_choices(request.choices)
        return
    if not request.questions:
        raise ContractViolation("user input request requires questions")
    _validate_question_count(len(request.questions))
    question_ids = tuple(question.question_id for question in request.questions)
    if len(set(question_ids)) != len(question_ids):
        raise ContractViolation("user input question IDs must be unique")
    for question in request.questions:
        require_identifier(question.question_id, "question_id")
        if not question.prompt.strip():
            raise ContractViolation("user input question cannot be empty")
        _validate_choice_count(len(question.choices))
        _validate_choices(question.choices)
        if question.min_answers < 0:
            raise ContractViolation("minimum answers cannot be negative")
        if question.max_answers < question.min_answers:
            raise ContractViolation("maximum answers is below minimum answers")
        if question.max_answers < 1:
            raise ContractViolation("maximum answers must be positive")
        if (
            question.choices
            and not question.allows_other
            and question.min_answers > len(question.choices)
        ):
            raise ContractViolation("minimum answers exceeds available choices")
        if not question.allows_other and question.max_answers > len(question.choices):
            raise ContractViolation("maximum answers exceeds available choices")


def validate_request_resolution(resolution: RequestResolution) -> None:
    validate_request_ref(resolution.request_ref)
    if resolution.resolved_at.tzinfo is None:
        raise ContractViolation("request resolution time must include a timezone")


def validate_request_route_correlation(
    correlation: RequestRouteCorrelation,
) -> None:
    require_identifier(correlation.correlation_id, "correlation_id")
    validate_request_ref(correlation.request_ref)
    validate_thread_ref(correlation.thread_ref)
    if (
        correlation.thread_ref.application_instance_id
        != correlation.request_ref.application_ref.application_instance_id
    ):
        raise ContractViolation("request correlation belongs to a different application")
    require_identifier(correlation.turn_id, "turn_id")
    require_identifier(
        correlation.conversation_ref.channel_instance_id,
        "channel_instance_id",
    )
    require_identifier(
        correlation.conversation_ref.native_conversation_id,
        "native_conversation_id",
    )
    require_identifier(correlation.delivery_id, "delivery_id")
    validate_request_response_shape(correlation.response_shape)
    if correlation.created_at.tzinfo is None or correlation.updated_at.tzinfo is None:
        raise ContractViolation("request correlation times must include a timezone")
    if correlation.updated_at < correlation.created_at:
        raise ContractViolation("request correlation update precedes creation")
    if correlation.expires_at is not None and correlation.expires_at.tzinfo is None:
        raise ContractViolation("request correlation expiry must include a timezone")
    if correlation.state not in RequestRouteState:
        raise ContractViolation("invalid request correlation state")


def validate_request_ref(request_ref: RequestRef) -> None:
    require_identifier(
        request_ref.application_ref.application_instance_id,
        "application_instance_id",
    )
    require_identifier(request_ref.native_request_id, "native_request_id")


def validate_request_response_shape(shape: RequestResponseShape) -> None:
    if isinstance(shape, ApprovalResponseShape):
        if not shape.choice_ids:
            raise ContractViolation("approval response shape requires choices")
        _validate_choice_count(len(shape.choice_ids))
        if len(set(shape.choice_ids)) != len(shape.choice_ids):
            raise ContractViolation("approval response choices must be unique")
        for choice_id in shape.choice_ids:
            require_identifier(choice_id, "choice_id")
        return
    if not shape.questions:
        raise ContractViolation("user input response shape requires questions")
    _validate_question_count(len(shape.questions))
    question_ids = tuple(question.question_id for question in shape.questions)
    if len(set(question_ids)) != len(question_ids):
        raise ContractViolation("user input response questions must be unique")
    for question in shape.questions:
        require_identifier(question.question_id, "question_id")
        _validate_choice_count(len(question.choice_ids))
        if len(set(question.choice_ids)) != len(question.choice_ids):
            raise ContractViolation("user input response choices must be unique")
        for choice_id in question.choice_ids:
            require_identifier(choice_id, "choice_id")
        if question.min_answers < 0 or question.max_answers < question.min_answers:
            raise ContractViolation("invalid user input response cardinality")
        if question.max_answers < 1:
            raise ContractViolation("maximum answers must be positive")
        if not question.allows_other and question.max_answers > len(question.choice_ids):
            raise ContractViolation("maximum answers exceeds available choices")


def derive_request_response_shape(
    request: ApprovalRequest | UserInputRequest,
) -> RequestResponseShape:
    validate_interactive_request(request)
    if isinstance(request, ApprovalRequest):
        return ApprovalResponseShape(tuple(choice.choice_id for choice in request.choices))
    return UserInputResponseShape(
        tuple(
            UserInputQuestionShape(
                question_id=question.question_id,
                choice_ids=tuple(choice.choice_id for choice in question.choices),
                allows_other=question.allows_other,
                min_answers=question.min_answers,
                max_answers=question.max_answers,
            )
            for question in request.questions
        )
    )


def validate_request_response(
    response: ApprovalResponse | UserInputResponse,
    shape: RequestResponseShape,
) -> None:
    """Validate a response against the shape delivered to its destination."""

    validate_request_response_shape(shape)
    if isinstance(shape, ApprovalResponseShape):
        if not isinstance(response, ApprovalResponse):
            raise ContractViolation("approval request requires an approval response")
        require_identifier(response.choice_id, "choice_id")
        if response.choice_id not in shape.choice_ids:
            raise ContractViolation("approval choice was not offered")
        return
    if not isinstance(response, UserInputResponse):
        raise ContractViolation("user input request requires a user input response")

    question_shapes = {question.question_id: question for question in shape.questions}
    unknown = set(response.answers) - set(question_shapes)
    if unknown:
        raise ContractViolation("user input response contains an unknown question")
    for question_id, question in question_shapes.items():
        answers = tuple(response.answers.get(question_id, ()))
        if len(answers) < question.min_answers:
            raise ContractViolation("user input response has too few answers")
        if len(answers) > question.max_answers:
            raise ContractViolation("user input response has too many answers")
        if len(set(answers)) != len(answers):
            raise ContractViolation("user input response contains duplicate answers")
        for answer in answers:
            if not answer:
                raise ContractViolation("user input answer cannot be empty")
            if answer not in question.choice_ids and not question.allows_other:
                raise ContractViolation("user input answer was not offered")


def _validate_choices(choices) -> None:
    choice_ids = tuple(choice.choice_id for choice in choices)
    if len(set(choice_ids)) != len(choice_ids):
        raise ContractViolation("request choice IDs must be unique")
    for choice in choices:
        require_identifier(choice.choice_id, "choice_id")
        if not choice.label.strip():
            raise ContractViolation("request choice label cannot be empty")


def _validate_question_count(count: int) -> None:
    if count > MAX_INTERACTIVE_REQUEST_QUESTIONS:
        raise ContractViolation(
            "interactive request questions exceed the maximum of "
            f"{MAX_INTERACTIVE_REQUEST_QUESTIONS}"
        )


def _validate_choice_count(count: int) -> None:
    if count > MAX_INTERACTIVE_REQUEST_CHOICES:
        raise ContractViolation(
            f"interactive request choices exceed the maximum of {MAX_INTERACTIVE_REQUEST_CHOICES}"
        )

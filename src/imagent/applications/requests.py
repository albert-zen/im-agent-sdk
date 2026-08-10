from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from ..interaction.messages import Metadata
from ..interaction.operations import (
    ContractViolation,
    _MappedOperationError,
    require_identifier,
)
from ..interaction.operations import (
    OperationErrorCode as _OperationErrorCode,
)


class InteractiveRequestKind(StrEnum):
    APPROVAL = "approval"
    USER_INPUT = "user_input"


class RequestResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    STALE = "stale"


class RequestDuplicateError(_MappedOperationError):
    operation_error_code = _OperationErrorCode.REQUEST_DUPLICATE


class RequestResolvedError(_MappedOperationError):
    operation_error_code = _OperationErrorCode.REQUEST_RESOLVED


class RequestStaleError(_MappedOperationError):
    operation_error_code = _OperationErrorCode.REQUEST_STALE


@dataclass(frozen=True, slots=True)
class RequestRef:
    """Application-scoped opaque identity for an interactive request."""

    application_ref: ApplicationRef
    native_request_id: str


@dataclass(frozen=True, slots=True)
class RequestChoice:
    choice_id: str
    label: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    request_ref: RequestRef
    turn_ref: TurnRef
    prompt: str
    choices: tuple[RequestChoice, ...]
    expires_at: datetime | None = None
    metadata: Metadata = field(default_factory=dict)
    kind: InteractiveRequestKind = field(
        init=False,
        default=InteractiveRequestKind.APPROVAL,
    )


@dataclass(frozen=True, slots=True)
class UserInputQuestion:
    question_id: str
    prompt: str
    header: str | None = None
    choices: tuple[RequestChoice, ...] = ()
    allows_other: bool = False
    secret: bool = False
    min_answers: int = 1
    max_answers: int = 1


@dataclass(frozen=True, slots=True)
class UserInputRequest:
    request_ref: RequestRef
    turn_ref: TurnRef
    questions: tuple[UserInputQuestion, ...]
    prompt: str | None = None
    expires_at: datetime | None = None
    metadata: Metadata = field(default_factory=dict)
    kind: InteractiveRequestKind = field(
        init=False,
        default=InteractiveRequestKind.USER_INPUT,
    )


InteractiveRequest: TypeAlias = ApprovalRequest | UserInputRequest


@dataclass(frozen=True, slots=True)
class ApprovalResponseShape:
    choice_ids: tuple[str, ...]
    kind: InteractiveRequestKind = field(
        init=False,
        default=InteractiveRequestKind.APPROVAL,
    )


@dataclass(frozen=True, slots=True)
class UserInputQuestionShape:
    question_id: str
    choice_ids: tuple[str, ...]
    allows_other: bool
    min_answers: int
    max_answers: int


@dataclass(frozen=True, slots=True)
class UserInputResponseShape:
    questions: tuple[UserInputQuestionShape, ...]
    kind: InteractiveRequestKind = field(
        init=False,
        default=InteractiveRequestKind.USER_INPUT,
    )


RequestResponseShape: TypeAlias = ApprovalResponseShape | UserInputResponseShape


@dataclass(frozen=True, slots=True)
class RequestResolution:
    request_ref: RequestRef
    turn_ref: TurnRef
    status: RequestResolutionStatus
    resolved_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalResponse:
    choice_id: str
    kind: str = field(init=False, default="approval")


@dataclass(frozen=True, slots=True)
class UserInputResponse:
    answers: Mapping[str, tuple[str, ...]]
    kind: str = field(init=False, default="user_input")


RequestResponse: TypeAlias = ApprovalResponse | UserInputResponse


MAX_INTERACTIVE_REQUEST_QUESTIONS = 32
MAX_INTERACTIVE_REQUEST_CHOICES = 64
MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH = 4_096


def validate_interactive_request(
    request: ApprovalRequest | UserInputRequest,
) -> None:
    validate_request_ref(request.request_ref)
    validate_turn_ref(request.turn_ref)
    if (
        request.request_ref.application_ref.application_instance_id
        != request.turn_ref.thread_ref.project_ref.application_instance_id
    ):
        raise ContractViolation("request belongs to a different application")
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
        _validate_answer_cardinality(question.min_answers, question.max_answers)
        if question.min_answers < 0:
            raise ContractViolation("minimum answers cannot be negative")
        if question.max_answers < question.min_answers:
            raise ContractViolation("maximum answers is below minimum answers")
        if question.max_answers < 1:
            raise ContractViolation("maximum answers must be positive")
        if question.max_answers > MAX_INTERACTIVE_REQUEST_CHOICES:
            raise ContractViolation("maximum answers exceeds the interactive response limit")
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
    validate_turn_ref(resolution.turn_ref)
    if (
        resolution.turn_ref.thread_ref.project_ref.application_instance_id
        != resolution.request_ref.application_ref.application_instance_id
    ):
        raise ContractViolation("request resolution belongs to a different application")
    if resolution.resolved_at.tzinfo is None:
        raise ContractViolation("request resolution time must include a timezone")


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
        _validate_answer_cardinality(question.min_answers, question.max_answers)
        if question.min_answers < 0 or question.max_answers < question.min_answers:
            raise ContractViolation("invalid user input response cardinality")
        if question.max_answers < 1:
            raise ContractViolation("maximum answers must be positive")
        if question.max_answers > MAX_INTERACTIVE_REQUEST_CHOICES:
            raise ContractViolation("maximum answers exceeds the interactive response limit")
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
    validate_request_response_admission(response)
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


def validate_request_response_admission(
    response: ApprovalResponse | UserInputResponse,
) -> None:
    """Bound one response before copying, fingerprinting, or native admission."""

    if isinstance(response, ApprovalResponse):
        require_identifier(response.choice_id, "choice_id")
        return
    if not isinstance(response, UserInputResponse):
        raise ContractViolation("request response has an unsupported kind")
    if not isinstance(response.answers, Mapping):
        raise ContractViolation("user input answers must be a mapping")
    if not response.answers:
        raise ContractViolation("user input response requires answers")
    if len(response.answers) > MAX_INTERACTIVE_REQUEST_QUESTIONS:
        raise ContractViolation(
            "user input response questions exceed the maximum of "
            f"{MAX_INTERACTIVE_REQUEST_QUESTIONS}"
        )
    for question_id, answers in response.answers.items():
        require_identifier(question_id, "question_id")
        if not isinstance(answers, tuple):
            raise ContractViolation("user input answers must be tuples")
        if not answers:
            raise ContractViolation("each user input question requires non-empty answers")
        if len(answers) > MAX_INTERACTIVE_REQUEST_CHOICES:
            raise ContractViolation(
                "user input answers exceed the maximum of "
                f"{MAX_INTERACTIVE_REQUEST_CHOICES} per question"
            )
        for answer in answers:
            if not isinstance(answer, str) or not answer:
                raise ContractViolation("user input answer must be a non-empty string")
            if len(answer) > MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH:
                raise ContractViolation(
                    "user input answer exceeds the maximum length of "
                    f"{MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH}"
                )


def _validate_choices(choices) -> None:
    choice_ids = tuple(choice.choice_id for choice in choices)
    if len(set(choice_ids)) != len(choice_ids):
        raise ContractViolation("request choice IDs must be unique")
    for choice in choices:
        require_identifier(choice.choice_id, "choice_id")
        if not choice.label.strip():
            raise ContractViolation("request choice label cannot be empty")


def _validate_answer_cardinality(min_answers: int, max_answers: int) -> None:
    if not isinstance(min_answers, int) or isinstance(min_answers, bool):
        raise ContractViolation("minimum answers must be a non-negative integer")
    if not isinstance(max_answers, int) or isinstance(max_answers, bool):
        raise ContractViolation("maximum answers must be a positive integer")


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


# Bind shared Application resource identities only after this leaf is fully
# defined, preserving runtime annotations without a function-local reverse
# import or a second request contract.
from .contract import (  # noqa: E402
    ApplicationRef,
    TurnRef,
    validate_turn_ref,
)

__all__ = [
    "ApprovalRequest",
    "UserInputRequest",
    "RequestResolution",
    "RequestResponse",
    "ApprovalResponse",
    "ApprovalResponseShape",
    "InteractiveRequest",
    "InteractiveRequestKind",
    "MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH",
    "MAX_INTERACTIVE_REQUEST_CHOICES",
    "MAX_INTERACTIVE_REQUEST_QUESTIONS",
    "RequestChoice",
    "RequestDuplicateError",
    "RequestRef",
    "RequestResolutionStatus",
    "RequestResolvedError",
    "RequestResponseShape",
    "RequestStaleError",
    "UserInputQuestion",
    "UserInputQuestionShape",
    "UserInputResponse",
    "UserInputResponseShape",
    "derive_request_response_shape",
    "validate_interactive_request",
    "validate_request_ref",
    "validate_request_resolution",
    "validate_request_response",
    "validate_request_response_admission",
    "validate_request_response_shape",
]

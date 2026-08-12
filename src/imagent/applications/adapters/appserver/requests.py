from __future__ import annotations

import hashlib
import inspect
import json
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ...contract import ApplicationRef, ProjectRef, ThreadRef, TurnRef
from ...events import AgentEvent, AgentEventType
from ...operations import RequestResponded, RespondRequest
from ...requests import (
    MAX_INTERACTIVE_REQUEST_CHOICES,
    MAX_INTERACTIVE_REQUEST_QUESTIONS,
    ApprovalRequest,
    ApprovalResponse,
    InteractiveRequest,
    RequestChoice,
    RequestDuplicateError,
    RequestRef,
    RequestResolution,
    RequestResolutionStatus,
    RequestResolvedError,
    RequestResponse,
    RequestStaleError,
    UserInputQuestion,
    UserInputRequest,
    UserInputResponse,
    derive_request_response_shape,
    validate_request_response,
)
from .mapping import (
    APP_SERVER_MAPPING_ERROR_MESSAGE,
    MAX_NATIVE_ID_CHARACTERS,
    SUPPORTED_SERVER_REQUEST_METHODS,
    AppServerEvent,
    AppServerMappingError,
    derive_appserver_event_id,
    normalize_appserver_message,
)
from .mapping import (
    optional_string as _native_optional_string,
)

COMMAND_APPROVAL = "item/commandExecution/requestApproval"
FILE_APPROVAL = "item/fileChange/requestApproval"
USER_INPUT = "item/tool/requestUserInput"
PERMISSIONS_APPROVAL = "item/permissions/requestApproval"
_DISPLAY_VALUE_LIMIT = 2_000
_PERMISSION_SUMMARY_LIMIT = 4_000
_TERMINAL_REQUEST_CACHE_LIMIT = 256

logger = logging.getLogger(__name__)


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
    project_ref: ProjectRef,
    message: AppServerEvent | Mapping[str, object],
) -> PendingAppServerRequest:
    application_ref = ApplicationRef(project_ref.application_instance_id)
    event = _as_appserver_event(message)
    method = event.method
    if method not in SUPPORTED_SERVER_REQUEST_METHODS:
        raise UnsupportedAppServerRequest(
            f"unsupported App Server request method: {method or '<missing>'}"
        )
    params = event.payload
    transport_request_id = event.transport_request_id
    epoch = event.connection_epoch
    thread_id = event.thread_id
    turn_id = event.turn_id
    if transport_request_id is None or epoch is None or thread_id is None or turn_id is None:
        raise AppServerMappingError
    request_ref = derive_appserver_request_ref(
        application_ref,
        connection_epoch=epoch,
        transport_request_id=transport_request_id,
    )
    thread_ref = ThreadRef(
        project_ref=project_ref,
        thread_id=thread_id,
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
    project_ref: ProjectRef,
    message: AppServerEvent | Mapping[str, object],
) -> PendingAppServerRequest:
    """Map only the interactive request surface evidenced by native Zen."""

    event = _as_appserver_event(message)
    if event.method != COMMAND_APPROVAL:
        raise UnsupportedAppServerRequest(
            f"unsupported Zen App Server request method: {event.method or '<missing>'}"
        )
    return map_appserver_request(project_ref, event)


def _as_appserver_event(
    message: AppServerEvent | Mapping[str, object],
) -> AppServerEvent:
    if isinstance(message, AppServerEvent):
        return message
    return normalize_appserver_message(message)


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
            turn_ref=TurnRef(thread_ref, turn_id),
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
            turn_ref=TurnRef(thread_ref, turn_id),
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
            turn_ref=TurnRef(thread_ref, turn_id),
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
        question_id = _required_request_identity(native_question.get("id"))
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
            turn_ref=TurnRef(thread_ref, turn_id),
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
            _required_request_identity(decision)
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


def _required_request_identity(value: object) -> str:
    result = value
    if not isinstance(result, str) or not result.strip() or len(result) > MAX_NATIVE_ID_CHARACTERS:
        raise AppServerMappingError
    return result


def _optional_string(value: object) -> str | None:
    return _native_optional_string(value)


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


ServerRequestMapper = Callable[
    [ProjectRef, AppServerEvent],
    PendingAppServerRequest,
]
PublishEvent = Callable[[str, AgentEvent], None]
RequireThreadScope = Callable[[ThreadRef], Awaitable[object]]
FailObservation = Callable[[], None]
ObserveTurnEvidence = Callable[[ThreadRef], None]


@dataclass(frozen=True, slots=True)
class _RecentRequestOutcome:
    state: str
    pending: PendingAppServerRequest


class AppServerRequestRuntime:
    """Codex request handles and bounded terminal diagnostics for one client."""

    def __init__(
        self,
        *,
        project_ref: ProjectRef,
        client: object,
        mapper: ServerRequestMapper | None,
        publish_event: PublishEvent,
        require_thread_scope: RequireThreadScope,
        fail_observation: FailObservation,
        observe_turn_evidence: ObserveTurnEvidence,
    ) -> None:
        self._project_ref = project_ref
        self._application_ref = ApplicationRef(project_ref.application_instance_id)
        self._client = client
        self._mapper = mapper
        self._publish_event = publish_event
        self._require_thread_scope = require_thread_scope
        self._fail_observation = fail_observation
        self._observe_turn_evidence = observe_turn_evidence
        self._pending: dict[RequestRef, PendingAppServerRequest] = {}
        self._outcomes: OrderedDict[
            RequestRef,
            _RecentRequestOutcome,
        ] = OrderedDict()
        add_request_handler = getattr(
            self._client,
            "add_server_request_handler",
            None,
        )
        add_reset_handler = getattr(
            self._client,
            "add_connection_reset_handler",
            None,
        )
        self.enabled = mapper is not None and all(
            callable(candidate)
            for candidate in (
                add_request_handler,
                add_reset_handler,
                getattr(
                    self._client,
                    "reply_to_transport_request",
                    None,
                ),
                getattr(
                    self._client,
                    "reply_error_to_transport_request",
                    None,
                ),
            )
        )
        if self.enabled:
            assert callable(add_request_handler)
            assert callable(add_reset_handler)
            add_request_handler(self.handle_server_request)
            add_reset_handler(self.handle_connection_reset)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def terminal_count(self) -> int:
        return len(self._outcomes)

    async def respond(
        self,
        operation: RespondRequest,
        completed_at: datetime,
    ) -> RequestResponded:
        if not self.enabled:
            raise NotImplementedError("this App Server client cannot answer native requests")
        request_ref = operation.request_ref
        outcome = self._get_outcome(request_ref)
        if outcome is not None and outcome.state == "responded":
            raise RequestDuplicateError("App Server request already has a response")
        if outcome is not None and outcome.state == "resolved":
            raise RequestResolvedError("App Server request is already resolved")
        if outcome is not None and outcome.state == "stale":
            raise RequestStaleError("App Server request belongs to an expired connection")
        pending = self._pending.get(request_ref)
        if pending is None:
            raise RequestStaleError("App Server request is not pending")
        if operation.turn_ref != pending.request.turn_ref:
            raise ValueError("request.respond belongs to a different Turn")
        await self._require_thread_scope(pending.request.turn_ref.thread_ref)
        current_epoch = getattr(self._client, "connection_epoch", None)
        if (
            isinstance(current_epoch, int)
            and current_epoch > 0
            and current_epoch != pending.connection_epoch
        ):
            await self._mark_stale(pending)
            raise RequestStaleError("App Server request belongs to an expired connection")
        response = build_appserver_response(pending, operation.response)
        reply = getattr(self._client, "reply_to_transport_request")
        try:
            result = reply(
                pending.transport_request_id,
                response,
                expected_connection_epoch=pending.connection_epoch,
            )
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            current_epoch = getattr(self._client, "connection_epoch", None)
            if isinstance(current_epoch, int) and current_epoch != pending.connection_epoch:
                await self._mark_stale(pending)
                raise RequestStaleError(
                    "App Server request became stale while responding"
                ) from error
            raise
        outcome = self._get_outcome(request_ref)
        if outcome is None:
            await self._remember_outcome(pending, "responded")
        return RequestResponded(
            operation_id=operation.operation_id,
            completed_at=completed_at,
            request_ref=request_ref,
        )

    async def handle_server_request(self, message: dict) -> None:
        mapper = self._mapper
        if mapper is None:
            raise RuntimeError("native interactive requests are not configured")
        try:
            event = normalize_appserver_message(message)
            if (
                event.method in SUPPORTED_SERVER_REQUEST_METHODS
                and event.thread_id is not None
                and event.turn_id is not None
            ):
                self._observe_turn_evidence(
                    ThreadRef(
                        project_ref=self._project_ref,
                        thread_id=event.thread_id,
                    )
                )
            pending = mapper(self._project_ref, event)
        except UnsupportedAppServerRequest as error:
            await self._reject(message, error, code=-32601)
            return
        except (AppServerMappingError, ValueError):
            await self._reject(
                message,
                AppServerMappingError(),
                code=-32602,
            )
            return
        try:
            await self._require_thread_scope(pending.request.turn_ref.thread_ref)
        except Exception:
            self._fail_observation()
            await self._reject(
                message,
                AppServerMappingError(),
                code=-32602,
            )
            return
        request_ref = pending.request.request_ref
        self._pending[request_ref] = pending
        self._outcomes.pop(request_ref, None)
        await self._publish(
            pending.request.turn_ref.thread_ref,
            pending.request.turn_ref.turn_id,
            AgentEventType.REQUEST_OPENED,
            event_id=self._request_event_id(
                event_identity="request_opened",
                thread_ref=pending.request.turn_ref.thread_ref,
                turn_id=pending.request.turn_ref.turn_id,
                transport_request_id=pending.transport_request_id,
                connection_epoch=pending.connection_epoch,
            ),
            request=pending.request,
        )

    async def handle_connection_reset(self, connection_epoch: int) -> None:
        active = tuple(
            request
            for request in self._pending.values()
            if request.connection_epoch == connection_epoch
        )
        responded = tuple(
            outcome.pending
            for outcome in self._outcomes.values()
            if outcome.state == "responded" and outcome.pending.connection_epoch == connection_epoch
        )
        for request in (*active, *responded):
            await self._mark_stale(request)

    async def handle_resolution_notification(
        self,
        event: AppServerEvent,
    ) -> None:
        transport_request_id = event.request_id
        if transport_request_id is None:
            return
        notification_epoch = event.connection_epoch
        candidates = tuple(
            pending
            for pending in (
                *self._pending.values(),
                *(
                    outcome.pending
                    for outcome in self._outcomes.values()
                    if outcome.state == "responded"
                ),
            )
            if pending.transport_request_id == transport_request_id
            and (notification_epoch is None or pending.connection_epoch == notification_epoch)
        )
        pending = candidates[0] if len(candidates) == 1 else None
        if pending is not None:
            request_ref = pending.request.request_ref
            thread_ref = pending.request.turn_ref.thread_ref
            turn_id = pending.request.turn_ref.turn_id
            resolution_epoch = pending.connection_epoch
        else:
            if notification_epoch is None or notification_epoch < 1:
                logger.warning("Ignoring unscoped App Server request resolution")
                return
            resolution_epoch = notification_epoch
            request_ref = derive_appserver_request_ref(
                self._application_ref,
                connection_epoch=notification_epoch,
                transport_request_id=transport_request_id,
            )
            if event.thread_id is None:
                logger.warning(
                    "Ignoring App Server request resolution without pending or Thread scope"
                )
                return
            thread_ref = ThreadRef(
                project_ref=self._project_ref,
                thread_id=event.thread_id,
            )
            turn_id = event.turn_id
            if turn_id is None:
                logger.warning(
                    "Ignoring App Server request resolution without pending or Turn scope"
                )
                return
        try:
            await self._require_thread_scope(thread_ref)
        except Exception:
            self._fail_observation()
            return
        if pending is not None:
            await self._remember_outcome(pending, "resolved")
        resolution = RequestResolution(
            request_ref=request_ref,
            turn_ref=TurnRef(thread_ref, turn_id),
            status=RequestResolutionStatus.RESOLVED,
            resolved_at=datetime.now(UTC),
        )
        await self._publish(
            thread_ref,
            turn_id,
            AgentEventType.REQUEST_RESOLVED,
            event_id=self._request_event_id(
                event_identity="request_resolved",
                thread_ref=thread_ref,
                turn_id=turn_id,
                transport_request_id=transport_request_id,
                connection_epoch=resolution_epoch,
            ),
            resolution=resolution,
        )

    async def _reject(
        self,
        message: Mapping[str, object],
        error: Exception,
        *,
        code: int,
    ) -> None:
        request_id = message.get("id")
        epoch = message.get("_connection_epoch")
        if isinstance(request_id, bool) or not isinstance(
            request_id,
            (str, int),
        ):
            raise error
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise error
        reply_error = getattr(
            self._client,
            "reply_error_to_transport_request",
        )
        result = reply_error(
            request_id,
            code=code,
            message=(APP_SERVER_MAPPING_ERROR_MESSAGE if code == -32602 else str(error)),
            expected_connection_epoch=epoch,
        )
        if inspect.isawaitable(result):
            await result

    async def _mark_stale(
        self,
        pending: PendingAppServerRequest,
    ) -> None:
        request_ref = pending.request.request_ref
        await self._remember_outcome(pending, "stale")
        resolution = RequestResolution(
            request_ref=request_ref,
            turn_ref=pending.request.turn_ref,
            status=RequestResolutionStatus.STALE,
            resolved_at=datetime.now(UTC),
        )
        await self._publish(
            pending.request.turn_ref.thread_ref,
            pending.request.turn_ref.turn_id,
            AgentEventType.REQUEST_RESOLVED,
            event_id=self._request_event_id(
                event_identity="request_stale",
                thread_ref=pending.request.turn_ref.thread_ref,
                turn_id=pending.request.turn_ref.turn_id,
                transport_request_id=pending.transport_request_id,
                connection_epoch=pending.connection_epoch,
            ),
            resolution=resolution,
        )

    def _get_outcome(
        self,
        request_ref: RequestRef,
    ) -> _RecentRequestOutcome | None:
        outcome = self._outcomes.get(request_ref)
        if outcome is not None:
            self._outcomes.move_to_end(request_ref)
        return outcome

    async def _remember_outcome(
        self,
        pending: PendingAppServerRequest,
        state: str,
    ) -> None:
        request_ref = pending.request.request_ref
        self._pending.pop(request_ref, None)
        self._outcomes.pop(request_ref, None)
        self._outcomes[request_ref] = _RecentRequestOutcome(
            state=state,
            pending=pending,
        )
        while len(self._outcomes) > _TERMINAL_REQUEST_CACHE_LIMIT:
            _evicted_ref, evicted = self._outcomes.popitem(last=False)
            if evicted.state == "responded":
                await self._publish_retention_stale(evicted.pending)

    async def _publish_retention_stale(
        self,
        pending: PendingAppServerRequest,
    ) -> None:
        request_ref = pending.request.request_ref
        resolution = RequestResolution(
            request_ref=request_ref,
            turn_ref=pending.request.turn_ref,
            status=RequestResolutionStatus.STALE,
            resolved_at=datetime.now(UTC),
        )
        await self._publish(
            pending.request.turn_ref.thread_ref,
            pending.request.turn_ref.turn_id,
            AgentEventType.REQUEST_RESOLVED,
            event_id=self._request_event_id(
                event_identity="request_retention_stale",
                thread_ref=pending.request.turn_ref.thread_ref,
                turn_id=pending.request.turn_ref.turn_id,
                transport_request_id=pending.transport_request_id,
                connection_epoch=pending.connection_epoch,
            ),
            resolution=resolution,
        )

    def _request_event_id(
        self,
        *,
        event_identity: str,
        thread_ref: ThreadRef,
        turn_id: str,
        transport_request_id: str | int,
        connection_epoch: int,
    ) -> str:
        return derive_appserver_event_id(
            self._application_ref.application_instance_id,
            project_id=thread_ref.project_ref.project_id,
            event_type=event_identity,
            thread_id=thread_ref.thread_id,
            turn_id=turn_id,
            request_id=transport_request_id,
            connection_epoch=connection_epoch,
        )

    async def _publish(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
        event_type: AgentEventType,
        *,
        event_id: str,
        request=None,
        resolution: RequestResolution | None = None,
    ) -> None:
        event = AgentEvent(
            event_id=event_id,
            application_instance_id=(self._application_ref.application_instance_id),
            project_ref=thread_ref.project_ref,
            type=event_type,
            data={},
            created_at=datetime.now(UTC),
            thread_ref=thread_ref,
            turn_ref=TurnRef(thread_ref, turn_id),
            request=request,
            request_resolution=resolution,
        )
        self._publish_event(thread_ref.thread_id, event)

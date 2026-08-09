"""Passive v1 action fingerprints, receipt state, and mutation plans."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from ...applications.contract import (
    ApplicationRef,
    ProjectRef,
    ThreadRef,
    TurnRef,
    validate_project_ref,
    validate_thread_ref,
    validate_turn_ref,
)
from ...applications.requests import RequestRef, validate_request_ref
from ...interaction.messages import ConversationRef
from ...interaction.operations import (
    ContractViolation,
    OperationErrorCode,
    require_identifier,
)
from ..outcomes import Failed, Outcome, OutcomeUnknown, Partial, Succeeded
from .state_contracts import ThreadProjectionRoute, validate_projection_route

MAX_ACTION_KIND_LENGTH = 128
MAX_ACTION_FINGERPRINT_FIELDS = 128
MAX_ACTION_FINGERPRINT_VALUES = 1024
MAX_ACTION_FINGERPRINT_PAYLOAD_BYTES = 1_048_576
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

FingerprintValue: TypeAlias = str | int | bool | None | tuple["FingerprintValue", ...]
FingerprintPayload: TypeAlias = tuple[tuple[str, FingerprintValue], ...]


class EffectCategory(StrEnum):
    GATEWAY = "gateway"
    NATIVE = "native"
    REQUEST_RESPONSE = "request_response"
    WORKFLOW = "workflow"


class EffectPhase(StrEnum):
    RESERVED = "reserved"
    NATIVE_SIDE_EFFECT_STARTED = "native_side_effect_started"
    NATIVE_RESULT_KNOWN = "native_result_known"
    TERMINAL = "terminal"


class ActionErrorCode(StrEnum):
    CONFLICT = "conflict"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    STALE_RUNTIME = "stale_runtime"
    STALE_BINDING = "stale_binding"
    UNSUPPORTED = "unsupported"
    NATIVE_REJECTED = "native_rejected"
    NATIVE_OUTCOME_UNKNOWN = "native_outcome_unknown"
    STORE_FAILURE = "store_failure"


@dataclass(frozen=True, slots=True)
class ActionError:
    code: ActionErrorCode
    operation_error_code: OperationErrorCode | None = None


class StableReferenceKind(StrEnum):
    APPLICATION = "application"
    PROJECT = "project"
    THREAD = "thread"
    TURN = "turn"
    REQUEST = "request"


@dataclass(frozen=True, slots=True)
class StableReference:
    kind: StableReferenceKind
    application_instance_id: str
    project_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    native_request_id: str | None = None

    @classmethod
    def from_value(
        cls,
        value: ApplicationRef | ProjectRef | ThreadRef | TurnRef | RequestRef,
    ) -> StableReference:
        if isinstance(value, ApplicationRef):
            return cls(StableReferenceKind.APPLICATION, value.application_instance_id)
        if isinstance(value, ProjectRef):
            return cls(
                StableReferenceKind.PROJECT,
                value.application_instance_id,
                project_id=value.project_id,
            )
        if isinstance(value, ThreadRef):
            return cls(
                StableReferenceKind.THREAD,
                value.project_ref.application_instance_id,
                project_id=value.project_ref.project_id,
                thread_id=value.thread_id,
            )
        if isinstance(value, TurnRef):
            return cls(
                StableReferenceKind.TURN,
                value.thread_ref.project_ref.application_instance_id,
                project_id=value.thread_ref.project_ref.project_id,
                thread_id=value.thread_ref.thread_id,
                turn_id=value.turn_id,
            )
        if isinstance(value, RequestRef):
            return cls(
                StableReferenceKind.REQUEST,
                value.application_ref.application_instance_id,
                native_request_id=value.native_request_id,
            )
        raise TypeError(f"unsupported stable reference value: {type(value).__name__}")

    def to_value(self) -> ApplicationRef | ProjectRef | ThreadRef | TurnRef | RequestRef:
        validate_stable_reference(self)
        return self._to_value_unchecked()

    def _to_value_unchecked(
        self,
    ) -> ApplicationRef | ProjectRef | ThreadRef | TurnRef | RequestRef:
        application_ref = ApplicationRef(self.application_instance_id)
        if self.kind is StableReferenceKind.APPLICATION:
            return application_ref
        if self.kind is StableReferenceKind.REQUEST:
            return RequestRef(application_ref, self.native_request_id or "")
        project_ref = ProjectRef(self.application_instance_id, self.project_id or "")
        if self.kind is StableReferenceKind.PROJECT:
            return project_ref
        thread_ref = ThreadRef(project_ref, self.thread_id or "")
        if self.kind is StableReferenceKind.THREAD:
            return thread_ref
        return TurnRef(thread_ref, self.turn_id or "")


@dataclass(frozen=True, slots=True)
class EffectValue:
    reference: StableReference | None = None
    conversation_ref: ConversationRef | None = None
    binding_generation: int | None = None
    route_id: str | None = None


ActionOutcome: TypeAlias = Outcome[EffectValue, ActionError]
KnownNativeOutcome: TypeAlias = Succeeded[EffectValue] | Failed[ActionError]


@dataclass(frozen=True, slots=True)
class ActionIdentity:
    gateway_id: str
    principal_id: str
    action_kind: str
    action_id: str
    conversation_ref: ConversationRef | None = None


@dataclass(frozen=True, slots=True)
class ActionFingerprint:
    action_kind: str
    action_key: str
    payload_fingerprint: str

    def phase_id(self, phase: str) -> str:
        require_identifier(phase, "phase")
        digest = _domain_hash("imagent:v1:action-phase", (self.action_key, phase))
        return f"imagent:v1:phase:{digest}"


@dataclass(frozen=True, slots=True)
class BindingTarget:
    conversation_ref: ConversationRef
    application_ref: ApplicationRef | None = None
    project_ref: ProjectRef | None = None
    thread_ref: ThreadRef | None = None


class BindingClearScope(StrEnum):
    THREAD = "thread"
    PROJECT = "project"
    APPLICATION = "application"


@dataclass(frozen=True, slots=True)
class StoreMutationPlan:
    conversation_ref: ConversationRef
    binding_target: BindingTarget | None = None
    binding_clear: BindingClearScope | None = None
    route_upsert: ThreadProjectionRoute | None = None
    route_delete_id: str | None = None
    expected_generation: int | None = None


@dataclass(frozen=True, slots=True)
class StoreMutationRequest:
    fingerprint: ActionFingerprint
    plan: StoreMutationPlan


@dataclass(frozen=True, slots=True)
class NativeMutationRequest:
    fingerprint: ActionFingerprint
    category: EffectCategory = EffectCategory.NATIVE


class CreateBindingWorkflowKind(StrEnum):
    CREATE_AND_SELECT_PROJECT = "create_and_select_project"
    CREATE_AND_BIND_THREAD = "create_and_bind_thread"


@dataclass(frozen=True, slots=True)
class CreateBindingWorkflowRequest:
    fingerprint: ActionFingerprint
    conversation_ref: ConversationRef
    kind: CreateBindingWorkflowKind
    application_ref: ApplicationRef
    project_ref: ProjectRef | None = None
    foreground_route: bool = True


@dataclass(frozen=True, slots=True)
class EffectReceipt:
    gateway_id: str
    action_kind: str
    action_key: str
    payload_fingerprint: str
    category: EffectCategory
    phase: EffectPhase
    native_phase_id: str | None
    binding_generation: int | None
    outcome: ActionOutcome | None
    created_at: datetime
    updated_at: datetime


def derive_action_fingerprint(
    identity: ActionIdentity,
    payload: FingerprintPayload,
) -> ActionFingerprint:
    validate_action_identity(identity)
    canonical_payload = _canonical_payload(payload)
    namespace_values = (
        identity.gateway_id,
        identity.principal_id,
        identity.conversation_ref.channel_instance_id
        if identity.conversation_ref is not None
        else "",
        identity.conversation_ref.native_conversation_id
        if identity.conversation_ref is not None
        else "",
        identity.action_kind,
        identity.action_id,
    )
    return ActionFingerprint(
        action_kind=identity.action_kind,
        action_key=_domain_hash("imagent:v1:action-identity", namespace_values),
        payload_fingerprint=_domain_hash(
            "imagent:v1:action-payload",
            (identity.action_kind, canonical_payload),
        ),
    )


def validate_action_identity(identity: ActionIdentity) -> None:
    require_identifier(identity.gateway_id, "gateway_id")
    require_identifier(identity.principal_id, "principal_id")
    require_identifier(identity.action_id, "action_id")
    require_identifier(identity.action_kind, "action_kind")
    if len(identity.action_kind) > MAX_ACTION_KIND_LENGTH:
        raise ContractViolation("action_kind is too long")
    if identity.conversation_ref is not None:
        require_identifier(
            identity.conversation_ref.channel_instance_id,
            "channel_instance_id",
        )
        require_identifier(
            identity.conversation_ref.native_conversation_id,
            "native_conversation_id",
        )


def validate_action_fingerprint(fingerprint: ActionFingerprint) -> None:
    require_identifier(fingerprint.action_kind, "action_kind")
    if len(fingerprint.action_kind) > MAX_ACTION_KIND_LENGTH:
        raise ContractViolation("action_kind is too long")
    for name, value in (
        ("action_key", fingerprint.action_key),
        ("payload_fingerprint", fingerprint.payload_fingerprint),
    ):
        if _SHA256_PATTERN.fullmatch(value) is None:
            raise ContractViolation(f"{name} must be a lowercase SHA-256 digest")


def validate_action_error(error: ActionError) -> None:
    if not isinstance(error.code, ActionErrorCode):
        raise ContractViolation("action error code is invalid")
    if error.operation_error_code is not None and not isinstance(
        error.operation_error_code,
        OperationErrorCode,
    ):
        raise ContractViolation("operation error code is invalid")


def validate_stable_reference(reference: StableReference) -> None:
    require_identifier(reference.application_instance_id, "application_instance_id")
    present = {
        "project": reference.project_id is not None,
        "thread": reference.thread_id is not None,
        "turn": reference.turn_id is not None,
        "request": reference.native_request_id is not None,
    }
    required_by_kind = {
        StableReferenceKind.APPLICATION: set(),
        StableReferenceKind.PROJECT: {"project"},
        StableReferenceKind.THREAD: {"project", "thread"},
        StableReferenceKind.TURN: {"project", "thread", "turn"},
        StableReferenceKind.REQUEST: {"request"},
    }[reference.kind]
    if {name for name, is_present in present.items() if is_present} != required_by_kind:
        raise ContractViolation("stable reference fields do not match its kind")
    value = reference._to_value_unchecked()
    if isinstance(value, ProjectRef):
        validate_project_ref(value)
    elif isinstance(value, ThreadRef):
        validate_thread_ref(value)
    elif isinstance(value, TurnRef):
        validate_turn_ref(value)
    elif isinstance(value, RequestRef):
        validate_request_ref(value)


def validate_effect_value(value: EffectValue) -> None:
    if value.reference is not None:
        validate_stable_reference(value.reference)
    if value.conversation_ref is not None:
        require_identifier(value.conversation_ref.channel_instance_id, "channel_instance_id")
        require_identifier(
            value.conversation_ref.native_conversation_id,
            "native_conversation_id",
        )
    if value.binding_generation is not None and value.binding_generation < 0:
        raise ContractViolation("binding_generation cannot be negative")
    if value.route_id is not None:
        require_identifier(value.route_id, "route_id")


def validate_binding_target(target: BindingTarget) -> None:
    require_identifier(target.conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(
        target.conversation_ref.native_conversation_id,
        "native_conversation_id",
    )
    if target.project_ref is not None:
        validate_project_ref(target.project_ref)
        if (
            target.application_ref is None
            or target.project_ref.application_instance_id
            != target.application_ref.application_instance_id
        ):
            raise ContractViolation("binding target project belongs to another application")
    if target.thread_ref is not None:
        validate_thread_ref(target.thread_ref)
        if target.project_ref != target.thread_ref.project_ref:
            raise ContractViolation("binding target Thread belongs to another Project")


def validate_store_mutation_request(request: StoreMutationRequest) -> None:
    validate_action_fingerprint(request.fingerprint)
    plan = request.plan
    require_identifier(plan.conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(
        plan.conversation_ref.native_conversation_id,
        "native_conversation_id",
    )
    if (
        plan.binding_target is None
        and plan.binding_clear is None
        and plan.route_upsert is None
        and plan.route_delete_id is None
    ):
        raise ContractViolation("store mutation plan has no mutation")
    if plan.binding_target is not None and plan.binding_clear is not None:
        raise ContractViolation("binding replacement and hierarchical clear are mutually exclusive")
    if plan.binding_target is not None:
        validate_binding_target(plan.binding_target)
        if plan.binding_target.conversation_ref != plan.conversation_ref:
            raise ContractViolation("binding target belongs to another Conversation")
    if plan.route_upsert is not None:
        validate_projection_route(plan.route_upsert)
        if plan.route_upsert.conversation_ref != plan.conversation_ref:
            raise ContractViolation("projection route belongs to another Conversation")
        if (
            plan.binding_target is not None
            and plan.binding_target.thread_ref is not None
            and plan.route_upsert.thread_ref != plan.binding_target.thread_ref
        ):
            raise ContractViolation("projection route belongs to another bound Thread")
    if plan.route_upsert is not None and plan.route_delete_id is not None:
        raise ContractViolation("route upsert and delete are mutually exclusive")
    if plan.route_delete_id is not None:
        require_identifier(plan.route_delete_id, "route_delete_id")
    if plan.binding_clear is not None and not isinstance(plan.binding_clear, BindingClearScope):
        raise ContractViolation("binding clear scope is invalid")
    if plan.expected_generation is not None and plan.expected_generation < 0:
        raise ContractViolation("expected_generation cannot be negative")


def validate_native_mutation_request(request: NativeMutationRequest) -> None:
    validate_action_fingerprint(request.fingerprint)
    if request.category not in {EffectCategory.NATIVE, EffectCategory.REQUEST_RESPONSE}:
        raise ContractViolation("native mutation category is invalid")


def validate_create_binding_workflow_request(
    request: CreateBindingWorkflowRequest,
) -> None:
    validate_action_fingerprint(request.fingerprint)
    require_identifier(request.conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(
        request.conversation_ref.native_conversation_id,
        "native_conversation_id",
    )
    require_identifier(request.application_ref.application_instance_id, "application_instance_id")
    if request.kind is CreateBindingWorkflowKind.CREATE_AND_SELECT_PROJECT:
        if request.project_ref is not None:
            raise ContractViolation("create-and-select Project has no parent Project")
    elif request.kind is CreateBindingWorkflowKind.CREATE_AND_BIND_THREAD:
        if request.project_ref is None:
            raise ContractViolation("create-and-bind Thread requires a Project")
        validate_project_ref(request.project_ref)
        if (
            request.project_ref.application_instance_id
            != request.application_ref.application_instance_id
        ):
            raise ContractViolation("workflow Project belongs to another Application")
    else:
        raise ContractViolation("create binding workflow kind is invalid")


def validate_effect_receipt(receipt: EffectReceipt) -> None:
    require_identifier(receipt.gateway_id, "gateway_id")
    validate_action_fingerprint(
        ActionFingerprint(
            receipt.action_kind,
            receipt.action_key,
            receipt.payload_fingerprint,
        )
    )
    if receipt.native_phase_id is not None:
        require_identifier(receipt.native_phase_id, "native_phase_id")
    if receipt.binding_generation is not None and receipt.binding_generation < 0:
        raise ContractViolation("receipt binding generation cannot be negative")
    if receipt.created_at.tzinfo is None or receipt.updated_at.tzinfo is None:
        raise ContractViolation("receipt timestamps must include a timezone")
    if receipt.updated_at < receipt.created_at:
        raise ContractViolation("receipt update precedes creation")
    if not isinstance(receipt.category, EffectCategory):
        raise ContractViolation("receipt effect category is invalid")
    if not isinstance(receipt.phase, EffectPhase):
        raise ContractViolation("receipt effect phase is invalid")
    if receipt.category is EffectCategory.GATEWAY:
        if (
            receipt.phase is not EffectPhase.TERMINAL
            or receipt.native_phase_id is not None
            or receipt.outcome is None
            or receipt.binding_generation is not None
        ):
            raise ContractViolation("Gateway receipt fields are inconsistent")
    elif receipt.native_phase_id is None:
        raise ContractViolation("native/workflow receipt requires a stable phase ID")
    if receipt.category is not EffectCategory.WORKFLOW and receipt.binding_generation is not None:
        raise ContractViolation("only a workflow receipt may retain a binding generation")
    if receipt.phase is EffectPhase.RESERVED and receipt.outcome is not None:
        raise ContractViolation("reserved receipt cannot have an outcome")
    if receipt.phase is EffectPhase.NATIVE_SIDE_EFFECT_STARTED and receipt.outcome is not None:
        if not isinstance(receipt.outcome, OutcomeUnknown):
            raise ContractViolation("started native receipt may retain only unknown outcome")
    if receipt.phase is EffectPhase.NATIVE_RESULT_KNOWN:
        if receipt.category is not EffectCategory.WORKFLOW or not isinstance(
            receipt.outcome,
            Succeeded,
        ):
            raise ContractViolation("known native result is valid only for a workflow success")
    if receipt.phase is EffectPhase.TERMINAL and receipt.outcome is None:
        raise ContractViolation("terminal receipt requires an outcome")
    if receipt.outcome is not None:
        if isinstance(receipt.outcome, (Succeeded, Partial)):
            validate_effect_value(receipt.outcome.value)
        if isinstance(receipt.outcome, (Failed, Partial, OutcomeUnknown)):
            validate_action_error(receipt.outcome.error)
    if receipt.category is EffectCategory.GATEWAY and not isinstance(
        receipt.outcome,
        (Succeeded, Failed),
    ):
        raise ContractViolation("Gateway receipt outcome is invalid")
    if receipt.category in {EffectCategory.NATIVE, EffectCategory.REQUEST_RESPONSE}:
        if receipt.phase is EffectPhase.TERMINAL and not isinstance(
            receipt.outcome,
            (Succeeded, Failed),
        ):
            raise ContractViolation("primitive native terminal outcome is invalid")
    if receipt.category is EffectCategory.WORKFLOW:
        if receipt.phase is EffectPhase.TERMINAL and not isinstance(
            receipt.outcome,
            (Succeeded, Failed, Partial),
        ):
            raise ContractViolation("workflow terminal outcome is invalid")


def encode_action_outcome(outcome: ActionOutcome | None) -> str | None:
    if outcome is None:
        return None
    if isinstance(outcome, Succeeded):
        status = "succeeded"
        validate_effect_value(outcome.value)
    elif isinstance(outcome, Failed):
        status = "failed"
        validate_action_error(outcome.error)
    elif isinstance(outcome, Partial):
        status = "partial"
        validate_effect_value(outcome.value)
        validate_action_error(outcome.error)
    elif isinstance(outcome, OutcomeUnknown):
        status = "outcome_unknown"
        validate_action_error(outcome.error)
    else:
        raise TypeError("action outcome uses an unknown variant")
    payload: dict[str, object] = {"status": status}
    if isinstance(outcome, (Succeeded, Partial)):
        payload["value"] = _effect_value_to_json(outcome.value)
    if isinstance(outcome, (Failed, Partial, OutcomeUnknown)):
        payload["error"] = {
            "code": outcome.error.code.value,
            "operation_error_code": (
                outcome.error.operation_error_code.value
                if outcome.error.operation_error_code is not None
                else None
            ),
        }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def decode_action_outcome(value: str | None) -> ActionOutcome | None:
    if value is None:
        return None
    try:
        raw = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise ContractViolation("stored action outcome is invalid") from error
    if not isinstance(raw, dict):
        raise ContractViolation("stored action outcome is invalid")
    status = raw.get("status")
    if not isinstance(status, str):
        raise ContractViolation("stored action outcome status is invalid")
    expected_fields = {
        "succeeded": {"status", "value"},
        "failed": {"status", "error"},
        "partial": {"status", "value", "error"},
        "outcome_unknown": {"status", "error"},
    }.get(status)
    if expected_fields is None or set(raw) != expected_fields:
        raise ContractViolation("stored action outcome fields are inconsistent")
    effect_value = _effect_value_from_json(raw.get("value")) if "value" in raw else None
    error = _action_error_from_json(raw.get("error")) if "error" in raw else None
    if status == "succeeded" and effect_value is not None and error is None:
        outcome: ActionOutcome = Succeeded(effect_value)
    elif status == "failed" and effect_value is None and error is not None:
        outcome = Failed(error)
    elif status == "partial" and effect_value is not None and error is not None:
        outcome = Partial(effect_value, error)
    elif status == "outcome_unknown" and effect_value is None and error is not None:
        outcome = OutcomeUnknown(error)
    else:
        raise ContractViolation("stored action outcome fields are inconsistent")
    return outcome


def _canonical_payload(payload: FingerprintPayload) -> str:
    if len(payload) > MAX_ACTION_FINGERPRINT_FIELDS:
        raise ContractViolation("action fingerprint has too many fields")
    names: set[str] = set()
    normalized: list[tuple[str, FingerprintValue]] = []
    value_budget = [MAX_ACTION_FINGERPRINT_VALUES]
    for name, value in payload:
        require_identifier(name, "fingerprint field name")
        if name in names:
            raise ContractViolation("action fingerprint field names must be unique")
        names.add(name)
        _validate_fingerprint_value(value, depth=0, remaining=value_budget)
        normalized.append((name, value))
    encoded = json.dumps(sorted(normalized), ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_ACTION_FINGERPRINT_PAYLOAD_BYTES:
        raise ContractViolation("action fingerprint payload is too large")
    return encoded


def _validate_fingerprint_value(
    value: FingerprintValue,
    *,
    depth: int,
    remaining: list[int],
) -> None:
    if depth > 16:
        raise ContractViolation("action fingerprint payload is too deeply nested")
    remaining[0] -= 1
    if remaining[0] < 0:
        raise ContractViolation("action fingerprint payload has too many values")
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, str):
        if len(value) > MAX_ACTION_FINGERPRINT_PAYLOAD_BYTES:
            raise ContractViolation("action fingerprint string is too large")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ContractViolation("action fingerprint string must be valid UTF-8") from error
        if len(encoded) > MAX_ACTION_FINGERPRINT_PAYLOAD_BYTES:
            raise ContractViolation("action fingerprint string is too large")
        return
    if isinstance(value, int):
        if value < -(2**63) or value > 2**63 - 1:
            raise ContractViolation("action fingerprint integer must be signed 64-bit")
        return
    if isinstance(value, tuple):
        for item in value:
            _validate_fingerprint_value(
                item,
                depth=depth + 1,
                remaining=remaining,
            )
        return
    raise ContractViolation("action fingerprint values use a closed scalar/tuple union")


def _domain_hash(domain: str, values: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(domain.encode("ascii"))
    digest.update(b"\0")
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _effect_value_to_json(value: EffectValue) -> dict[str, object]:
    validate_effect_value(value)
    reference = None
    if value.reference is not None:
        reference = {
            "kind": value.reference.kind.value,
            "application_instance_id": value.reference.application_instance_id,
        }
        if value.reference.project_id is not None:
            reference["project_id"] = value.reference.project_id
        if value.reference.thread_id is not None:
            reference["thread_id"] = value.reference.thread_id
        if value.reference.turn_id is not None:
            reference["turn_id"] = value.reference.turn_id
        if value.reference.native_request_id is not None:
            reference["native_request_id"] = value.reference.native_request_id
    conversation = None
    if value.conversation_ref is not None:
        conversation = {
            "channel_instance_id": value.conversation_ref.channel_instance_id,
            "native_conversation_id": value.conversation_ref.native_conversation_id,
        }
    return {
        "reference": reference,
        "conversation": conversation,
        "binding_generation": value.binding_generation,
        "route_id": value.route_id,
    }


def _effect_value_from_json(raw: object) -> EffectValue:
    if not isinstance(raw, dict):
        raise ContractViolation("stored effect value is invalid")
    if not set(raw).issubset({"reference", "conversation", "binding_generation", "route_id"}):
        raise ContractViolation("stored effect value has unknown fields")
    reference_raw = raw.get("reference")
    reference = None
    if reference_raw is not None:
        if not isinstance(reference_raw, dict):
            raise ContractViolation("stored stable reference is invalid")
        try:
            kind = StableReferenceKind(str(reference_raw.get("kind")))
        except ValueError as error:
            raise ContractViolation("stored stable reference kind is invalid") from error
        required_fields = {
            StableReferenceKind.APPLICATION: {"kind", "application_instance_id"},
            StableReferenceKind.PROJECT: {
                "kind",
                "application_instance_id",
                "project_id",
            },
            StableReferenceKind.THREAD: {
                "kind",
                "application_instance_id",
                "project_id",
                "thread_id",
            },
            StableReferenceKind.TURN: {
                "kind",
                "application_instance_id",
                "project_id",
                "thread_id",
                "turn_id",
            },
            StableReferenceKind.REQUEST: {
                "kind",
                "application_instance_id",
                "native_request_id",
            },
        }[kind]
        if set(reference_raw) != required_fields:
            raise ContractViolation("stored stable reference fields are inconsistent")
        reference = StableReference(
            kind=kind,
            application_instance_id=str(reference_raw.get("application_instance_id", "")),
            project_id=_optional_string(reference_raw.get("project_id")),
            thread_id=_optional_string(reference_raw.get("thread_id")),
            turn_id=_optional_string(reference_raw.get("turn_id")),
            native_request_id=_optional_string(reference_raw.get("native_request_id")),
        )
    conversation_raw = raw.get("conversation")
    conversation = None
    if conversation_raw is not None:
        if not isinstance(conversation_raw, dict):
            raise ContractViolation("stored Conversation reference is invalid")
        if set(conversation_raw) != {
            "channel_instance_id",
            "native_conversation_id",
        }:
            raise ContractViolation("stored Conversation reference fields are inconsistent")
        conversation = ConversationRef(
            str(conversation_raw.get("channel_instance_id", "")),
            str(conversation_raw.get("native_conversation_id", "")),
        )
    generation = raw.get("binding_generation")
    if generation is not None and (not isinstance(generation, int) or isinstance(generation, bool)):
        raise ContractViolation("stored binding generation is invalid")
    route_id = _optional_string(raw.get("route_id"))
    result = EffectValue(reference, conversation, generation, route_id)
    validate_effect_value(result)
    return result


def _action_error_from_json(raw: object) -> ActionError:
    if not isinstance(raw, dict):
        raise ContractViolation("stored action error is invalid")
    if set(raw) != {"code", "operation_error_code"}:
        raise ContractViolation("stored action error fields are inconsistent")
    operation_error_raw = _optional_string(raw.get("operation_error_code"))
    try:
        operation_error_code = (
            OperationErrorCode(operation_error_raw) if operation_error_raw is not None else None
        )
        code = ActionErrorCode(str(raw.get("code")))
    except ValueError as conversion_error:
        raise ContractViolation("stored action error code is invalid") from conversion_error
    error = ActionError(code, operation_error_code)
    validate_action_error(error)
    return error


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractViolation("stored optional identifier is invalid")
    return value


__all__ = [
    "ActionError",
    "ActionErrorCode",
    "ActionFingerprint",
    "ActionIdentity",
    "ActionOutcome",
    "BindingClearScope",
    "BindingTarget",
    "CreateBindingWorkflowKind",
    "CreateBindingWorkflowRequest",
    "EffectCategory",
    "EffectPhase",
    "EffectReceipt",
    "EffectValue",
    "FingerprintPayload",
    "FingerprintValue",
    "KnownNativeOutcome",
    "MAX_ACTION_FINGERPRINT_FIELDS",
    "MAX_ACTION_FINGERPRINT_PAYLOAD_BYTES",
    "MAX_ACTION_FINGERPRINT_VALUES",
    "NativeMutationRequest",
    "StableReference",
    "StableReferenceKind",
    "StoreMutationPlan",
    "StoreMutationRequest",
    "decode_action_outcome",
    "derive_action_fingerprint",
    "encode_action_outcome",
    "validate_action_fingerprint",
    "validate_action_identity",
    "validate_binding_target",
    "validate_create_binding_workflow_request",
    "validate_effect_receipt",
    "validate_native_mutation_request",
    "validate_store_mutation_request",
]

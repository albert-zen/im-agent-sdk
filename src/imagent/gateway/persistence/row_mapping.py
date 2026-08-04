"""Pure conversion between current SQLite rows and typed bridge state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime

from ...applications.contract import ApplicationRef, ProjectRef, ThreadRef
from ...applications.requests import (
    ApprovalResponseShape,
    RequestRef,
    RequestResponseShape,
    UserInputQuestionShape,
    UserInputResponseShape,
)
from ...interaction.channels.contract import (
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySegmentReceipt,
    DeliverySegmentStatus,
)
from ...interaction.messages import ConversationRef
from .state_contracts import (
    ConversationBinding,
    DeliveryRouteSnapshot,
    DeliverySubmissionOrigin,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
    validate_binding,
    validate_delivery_submission_record,
    validate_projection_route,
    validate_request_route_correlation,
    validate_turn_reply_correlation,
)


def binding_from_row(row: sqlite3.Row) -> ConversationBinding:
    application_id = row["application_instance_id"]
    project_id = row["project_id"]
    thread_id = row["thread_id"]
    if application_id is None and (project_id is not None or thread_id is not None):
        raise ValueError("binding scope requires an application")
    application_ref = None
    if application_id is not None:
        application_ref = ApplicationRef(required_text(application_id, "application_instance_id"))
    project_ref = (
        ProjectRef(
            application_ref.application_instance_id,
            required_text(project_id, "project_id"),
        )
        if application_ref is not None and project_id is not None
        else None
    )
    thread_ref = (
        ThreadRef(
            application_instance_id=application_ref.application_instance_id,
            native_thread_id=required_text(thread_id, "thread_id"),
            project_ref=project_ref,
        )
        if application_ref is not None and thread_id is not None
        else None
    )
    binding = ConversationBinding(
        conversation_ref=ConversationRef(
            required_text(row["channel_instance_id"], "channel_instance_id"),
            required_text(row["native_conversation_id"], "native_conversation_id"),
        ),
        application_ref=application_ref,
        project_ref=project_ref,
        thread_ref=thread_ref,
        revision=required_integer(row["revision"], "revision"),
        updated_at=decode_datetime(row["updated_at"], "updated_at"),
    )
    validate_binding(binding)
    return binding


def binding_to_row(binding: ConversationBinding) -> tuple[object, ...]:
    return (
        binding.conversation_ref.channel_instance_id,
        binding.conversation_ref.native_conversation_id,
        (
            binding.application_ref.application_instance_id
            if binding.application_ref is not None
            else None
        ),
        binding.project_ref.native_project_id if binding.project_ref is not None else None,
        binding.thread_ref.native_thread_id if binding.thread_ref is not None else None,
        binding.revision,
        binding.updated_at.isoformat() if binding.updated_at is not None else None,
    )


def thread_storage_key(thread_ref: ThreadRef | None) -> tuple[str, str, str]:
    if thread_ref is None:
        return "", "", ""
    return (
        thread_ref.application_instance_id,
        (thread_ref.project_ref.native_project_id if thread_ref.project_ref is not None else ""),
        thread_ref.native_thread_id,
    )


def projection_route_from_row(row: sqlite3.Row) -> ThreadProjectionRoute:
    application_id = required_text(row["application_instance_id"], "application_instance_id")
    project_id = empty_storage_text(row["project_id"], "project_id")
    project_ref = ProjectRef(application_id, project_id) if project_id else None
    route = ThreadProjectionRoute(
        route_id=required_text(row["route_id"], "route_id"),
        thread_ref=ThreadRef(
            application_instance_id=application_id,
            native_thread_id=required_text(row["thread_id"], "thread_id"),
            project_ref=project_ref,
        ),
        conversation_ref=ConversationRef(
            channel_instance_id=required_text(row["channel_instance_id"], "channel_instance_id"),
            native_conversation_id=required_text(
                row["native_conversation_id"], "native_conversation_id"
            ),
        ),
        reply_to_message_id=optional_text(row["reply_to_message_id"], "reply_to_message_id"),
        checkpoint_agent_item_id=optional_text(
            row["checkpoint_agent_item_id"], "checkpoint_agent_item_id"
        ),
        checkpointed_at=(
            decode_datetime(row["checkpointed_at"], "checkpointed_at")
            if row["checkpointed_at"] is not None
            else None
        ),
        updated_at=decode_datetime(row["updated_at"], "updated_at"),
    )
    validate_projection_route(route)
    return route


def projection_route_to_row(
    route: ThreadProjectionRoute,
    *,
    updated_at: datetime | None = None,
) -> tuple[object, ...]:
    effective_updated_at = updated_at if updated_at is not None else route.updated_at
    return (
        route.route_id,
        *thread_storage_key(route.thread_ref),
        route.conversation_ref.channel_instance_id,
        route.conversation_ref.native_conversation_id,
        route.reply_to_message_id,
        route.checkpoint_agent_item_id,
        route.checkpointed_at.isoformat() if route.checkpointed_at is not None else None,
        effective_updated_at.isoformat() if effective_updated_at is not None else None,
    )


def turn_reply_correlation_from_row(row: sqlite3.Row) -> TurnReplyCorrelation:
    application_id = required_text(row["application_instance_id"], "application_instance_id")
    project_id = empty_storage_text(row["project_id"], "project_id")
    project_ref = ProjectRef(application_id, project_id) if project_id else None
    correlation = TurnReplyCorrelation(
        correlation_id=required_text(row["correlation_id"], "correlation_id"),
        thread_ref=ThreadRef(
            application_instance_id=application_id,
            native_thread_id=required_text(row["thread_id"], "thread_id"),
            project_ref=project_ref,
        ),
        turn_id=required_text(row["turn_id"], "turn_id"),
        client_message_id=required_text(row["client_message_id"], "client_message_id"),
        conversation_ref=ConversationRef(
            channel_instance_id=required_text(row["channel_instance_id"], "channel_instance_id"),
            native_conversation_id=required_text(
                row["native_conversation_id"], "native_conversation_id"
            ),
        ),
        reply_to_message_id=required_text(row["reply_to_message_id"], "reply_to_message_id"),
        created_at=decode_datetime(row["created_at"], "created_at"),
    )
    validate_turn_reply_correlation(correlation)
    return correlation


def turn_reply_correlation_to_row(correlation: TurnReplyCorrelation) -> tuple[object, ...]:
    return (
        correlation.correlation_id,
        *thread_storage_key(correlation.thread_ref),
        correlation.turn_id,
        correlation.client_message_id,
        correlation.conversation_ref.channel_instance_id,
        correlation.conversation_ref.native_conversation_id,
        correlation.reply_to_message_id,
        correlation.created_at.isoformat(),
    )


def required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty SQLite text value")
    return value


def optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return required_text(value, label)


def empty_storage_text(value: object, label: str) -> str | None:
    """Decode the current NOT NULL empty-string sentinel for an absent Project."""

    if value == "":
        return None
    return required_text(value, label)


def required_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an SQLite integer")
    return value


def decode_datetime(value: object, label: str) -> datetime:
    try:
        decoded = datetime.fromisoformat(required_text(value, label))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO timestamp") from error
    if decoded.tzinfo is None or decoded.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return decoded


def request_correlation_from_row(row: sqlite3.Row) -> RequestRouteCorrelation:
    application_id = required_text(row["application_instance_id"], "application_instance_id")
    project_id = empty_storage_text(row["project_id"], "project_id")
    project_ref = ProjectRef(application_id, project_id) if project_id else None
    correlation = RequestRouteCorrelation(
        correlation_id=required_text(row["correlation_id"], "correlation_id"),
        request_ref=RequestRef(
            application_ref=ApplicationRef(application_id),
            native_request_id=required_text(row["native_request_id"], "native_request_id"),
        ),
        thread_ref=ThreadRef(
            application_instance_id=application_id,
            native_thread_id=required_text(row["thread_id"], "thread_id"),
            project_ref=project_ref,
        ),
        turn_id=required_text(row["turn_id"], "turn_id"),
        conversation_ref=ConversationRef(
            channel_instance_id=required_text(row["channel_instance_id"], "channel_instance_id"),
            native_conversation_id=required_text(
                row["native_conversation_id"], "native_conversation_id"
            ),
        ),
        delivery_id=required_text(row["delivery_id"], "delivery_id"),
        response_shape=decode_response_shape(row["response_shape_json"]),
        state=RequestRouteState(required_text(row["state"], "state")),
        created_at=decode_datetime(row["created_at"], "created_at"),
        updated_at=decode_datetime(row["updated_at"], "updated_at"),
        expires_at=(
            decode_datetime(row["expires_at"], "expires_at")
            if row["expires_at"] is not None
            else None
        ),
    )
    validate_request_route_correlation(correlation)
    return correlation


def request_correlation_to_row(correlation: RequestRouteCorrelation) -> tuple[object, ...]:
    return (
        correlation.correlation_id,
        correlation.request_ref.application_ref.application_instance_id,
        correlation.request_ref.native_request_id,
        *thread_storage_key(correlation.thread_ref)[1:],
        correlation.turn_id,
        correlation.conversation_ref.channel_instance_id,
        correlation.conversation_ref.native_conversation_id,
        correlation.delivery_id,
        encode_response_shape(correlation.response_shape),
        correlation.state.value,
        correlation.created_at.isoformat(),
        correlation.updated_at.isoformat(),
        correlation.expires_at.isoformat() if correlation.expires_at is not None else None,
    )


def encode_response_shape(shape: RequestResponseShape) -> str:
    if isinstance(shape, ApprovalResponseShape):
        payload: dict[str, object] = {
            "kind": "approval",
            "choice_ids": list(shape.choice_ids),
        }
    else:
        payload = {
            "kind": "user_input",
            "questions": [
                {
                    "question_id": question.question_id,
                    "choice_ids": list(question.choice_ids),
                    "allows_other": question.allows_other,
                    "min_answers": question.min_answers,
                    "max_answers": question.max_answers,
                }
                for question in shape.questions
            ],
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def decode_response_shape(value: object) -> RequestResponseShape:
    encoded = required_text(value, "response_shape_json")
    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise ValueError("response_shape_json must be valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("response_shape_json must contain an object")
    kind = payload.get("kind")
    if kind == "approval":
        if set(payload) != {"kind", "choice_ids"} or not isinstance(payload["choice_ids"], list):
            raise ValueError("approval response shape is malformed")
        if not all(isinstance(choice_id, str) for choice_id in payload["choice_ids"]):
            raise ValueError("approval response shape choice IDs must be text")
        return ApprovalResponseShape(choice_ids=tuple(payload["choice_ids"]))
    if kind != "user_input" or set(payload) != {"kind", "questions"}:
        raise ValueError("response shape kind is invalid")
    questions = payload["questions"]
    if not isinstance(questions, list):
        raise ValueError("user input response shape questions must be a list")
    decoded_questions: list[UserInputQuestionShape] = []
    expected_keys = {
        "question_id",
        "choice_ids",
        "allows_other",
        "min_answers",
        "max_answers",
    }
    for question in questions:
        if not isinstance(question, dict) or set(question) != expected_keys:
            raise ValueError("user input response shape question is malformed")
        choice_ids = question["choice_ids"]
        if (
            not isinstance(question["question_id"], str)
            or not isinstance(choice_ids, list)
            or not all(isinstance(choice_id, str) for choice_id in choice_ids)
            or not isinstance(question["allows_other"], bool)
            or not isinstance(question["min_answers"], int)
            or isinstance(question["min_answers"], bool)
            or not isinstance(question["max_answers"], int)
            or isinstance(question["max_answers"], bool)
        ):
            raise ValueError("user input response shape question fields are malformed")
        decoded_questions.append(
            UserInputQuestionShape(
                question_id=question["question_id"],
                choice_ids=tuple(choice_ids),
                allows_other=question["allows_other"],
                min_answers=question["min_answers"],
                max_answers=question["max_answers"],
            )
        )
    return UserInputResponseShape(questions=tuple(decoded_questions))


def delivery_submission_from_rows(
    root: sqlite3.Row,
    destination_rows: Iterable[sqlite3.Row],
) -> DeliverySubmissionRecord:
    record = DeliverySubmissionRecord(
        submission_id=required_text(root["submission_id"], "submission_id"),
        delivery_id=required_text(root["delivery_id"], "delivery_id"),
        origin=DeliverySubmissionOrigin(required_text(root["origin"], "origin")),
        principal_id=required_text(root["principal_id"], "principal_id"),
        target_fingerprint=required_text(root["target_fingerprint"], "target_fingerprint"),
        payload_fingerprint=required_text(root["payload_fingerprint"], "payload_fingerprint"),
        destinations=tuple(delivery_destination_from_row(row) for row in destination_rows),
        created_at=decode_datetime(root["created_at"], "created_at"),
        updated_at=decode_datetime(root["updated_at"], "updated_at"),
    )
    validate_delivery_submission_record(record)
    return record


def delivery_submission_to_row(record: DeliverySubmissionRecord) -> tuple[object, ...]:
    return (
        record.submission_id,
        record.delivery_id,
        record.origin.value,
        record.principal_id,
        record.target_fingerprint,
        record.payload_fingerprint,
        record.created_at.isoformat(),
        record.updated_at.isoformat(),
    )


def delivery_destination_to_row(
    root_submission_id: str,
    destination: DestinationDeliveryRecord,
) -> tuple[object, ...]:
    snapshot = destination.snapshot
    application_id, project_id, thread_id = thread_storage_key(snapshot.thread_ref)
    return (
        root_submission_id,
        destination.delivery_id,
        snapshot.conversation_ref.channel_instance_id,
        snapshot.conversation_ref.native_conversation_id,
        application_id,
        project_id,
        thread_id,
        snapshot.route_id or "",
        snapshot.route_updated_at.isoformat() if snapshot.route_updated_at is not None else None,
        snapshot.reply_to_message_id,
        destination.state.value,
        encode_receipt(destination.receipt),
        destination.error,
        destination.updated_at.isoformat(),
    )


def delivery_destination_from_row(row: sqlite3.Row) -> DestinationDeliveryRecord:
    application_id = _optional_route_scope_text(
        row["application_instance_id"], "application_instance_id"
    )
    project_id = _optional_route_scope_text(row["project_id"], "project_id")
    thread_id = _optional_route_scope_text(row["thread_id"], "thread_id")
    route_id = _optional_route_scope_text(row["route_id"], "route_id")
    has_thread_scope = any(
        value is not None for value in (application_id, project_id, thread_id, route_id)
    )
    if has_thread_scope and (application_id is None or thread_id is None or route_id is None):
        raise ValueError("delivery destination Thread route scope is incomplete")
    thread_ref = None
    if has_thread_scope:
        if application_id is None or thread_id is None or route_id is None:
            raise AssertionError("complete Thread route scope was not established")
        thread_ref = ThreadRef(
            application_instance_id=application_id,
            native_thread_id=thread_id,
            project_ref=(ProjectRef(application_id, project_id) if project_id else None),
        )
    return DestinationDeliveryRecord(
        delivery_id=required_text(row["destination_delivery_id"], "destination_delivery_id"),
        snapshot=DeliveryRouteSnapshot(
            conversation_ref=ConversationRef(
                channel_instance_id=required_text(
                    row["channel_instance_id"], "channel_instance_id"
                ),
                native_conversation_id=required_text(
                    row["native_conversation_id"], "native_conversation_id"
                ),
            ),
            thread_ref=thread_ref,
            route_id=route_id,
            route_updated_at=(
                decode_datetime(row["route_updated_at"], "route_updated_at")
                if row["route_updated_at"] is not None
                else None
            ),
            reply_to_message_id=optional_text(row["reply_to_message_id"], "reply_to_message_id"),
        ),
        state=DeliverySubmissionState(required_text(row["state"], "state")),
        receipt=decode_receipt(row["receipt_json"]),
        error=optional_text(row["error"], "error"),
        updated_at=decode_datetime(row["updated_at"], "updated_at"),
    )


def encode_receipt(receipt: DeliveryReceipt | None) -> str | None:
    if receipt is None:
        return None
    return json.dumps(
        {
            "status": receipt.status.value,
            "native_message_id": receipt.native_message_id,
            "detail": receipt.detail,
            "retry_after_seconds": receipt.retry_after_seconds,
            "items": [
                {
                    "content_index": item.content_index,
                    "status": item.status.value,
                    "attachment_id": item.attachment_id,
                    "native_message_id": item.native_message_id,
                    "detail": item.detail,
                }
                for item in receipt.items
            ],
            "segments": [
                {
                    "segment_index": segment.segment_index,
                    "delivery_id": segment.delivery_id,
                    "source_content_indexes": list(segment.source_content_indexes),
                    "status": segment.status.value,
                    "native_message_id": segment.native_message_id,
                    "detail": segment.detail,
                    "retry_after_seconds": segment.retry_after_seconds,
                }
                for segment in receipt.segments
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_receipt(value: object) -> DeliveryReceipt | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("receipt_json must be SQLite text")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("receipt_json must be valid JSON") from error
    expected_keys = {
        "status",
        "native_message_id",
        "detail",
        "retry_after_seconds",
        "items",
        "segments",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("delivery receipt is malformed")
    items = payload["items"]
    segments = payload["segments"]
    if not isinstance(items, list) or not isinstance(segments, list):
        raise ValueError("delivery receipt items and segments must be lists")
    return DeliveryReceipt(
        status=DeliveryReceiptStatus(_json_required_text(payload["status"], "receipt.status")),
        native_message_id=_json_optional_text(
            payload["native_message_id"], "receipt.native_message_id"
        ),
        detail=_json_optional_text(payload["detail"], "receipt.detail"),
        retry_after_seconds=_json_optional_number(
            payload["retry_after_seconds"], "receipt.retry_after_seconds"
        ),
        items=tuple(_decode_item_receipt(item) for item in items),
        segments=tuple(_decode_segment_receipt(segment) for segment in segments),
    )


def _decode_item_receipt(value: object) -> DeliveryItemReceipt:
    expected_keys = {
        "content_index",
        "status",
        "attachment_id",
        "native_message_id",
        "detail",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("delivery item receipt is malformed")
    return DeliveryItemReceipt(
        content_index=_json_integer(value["content_index"], "receipt.item.content_index"),
        status=DeliveryItemStatus(_json_required_text(value["status"], "receipt.item.status")),
        attachment_id=_json_optional_text(value["attachment_id"], "receipt.item.attachment_id"),
        native_message_id=_json_optional_text(
            value["native_message_id"], "receipt.item.native_message_id"
        ),
        detail=_json_optional_text(value["detail"], "receipt.item.detail"),
    )


def _decode_segment_receipt(value: object) -> DeliverySegmentReceipt:
    expected_keys = {
        "segment_index",
        "delivery_id",
        "source_content_indexes",
        "status",
        "native_message_id",
        "detail",
        "retry_after_seconds",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("delivery segment receipt is malformed")
    indexes = value["source_content_indexes"]
    if not isinstance(indexes, list):
        raise ValueError("delivery segment source indexes must be a list")
    return DeliverySegmentReceipt(
        segment_index=_json_integer(value["segment_index"], "receipt.segment.segment_index"),
        delivery_id=_json_required_text(value["delivery_id"], "receipt.segment.delivery_id"),
        source_content_indexes=tuple(
            _json_integer(index, "receipt.segment.source_content_index") for index in indexes
        ),
        status=DeliverySegmentStatus(
            _json_required_text(value["status"], "receipt.segment.status")
        ),
        native_message_id=_json_optional_text(
            value["native_message_id"], "receipt.segment.native_message_id"
        ),
        detail=_json_optional_text(value["detail"], "receipt.segment.detail"),
        retry_after_seconds=_json_optional_number(
            value["retry_after_seconds"], "receipt.segment.retry_after_seconds"
        ),
    )


def _json_required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be non-empty text")
    return value


def _json_optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _json_required_text(value, label)


def _json_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _json_optional_number(value: object, label: str) -> float | int | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    return value


def _optional_route_scope_text(value: object, label: str) -> str | None:
    """Decode the current NOT NULL empty-string sentinel for absent route scope."""

    if value == "":
        return None
    return optional_text(value, label)

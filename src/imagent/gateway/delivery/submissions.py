from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Protocol

from ...applications.contract import ProjectRef, ThreadRef
from ...contracts.delivery import (
    ConversationDeliveryTarget,
    DeliveryIntent,
    DeliveryReservation,
    DeliveryRouteSnapshot,
    DeliverySubmissionOrigin,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DeliveryTarget,
    DestinationDeliveryRecord,
    _canonical_metadata,
    _validate_conversation_ref,
    validate_delivery_intent,
    validate_delivery_submission_record,
)
from ...interaction.channels import (
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySegmentReceipt,
    DeliverySegmentStatus,
)
from ...interaction.media import LocalPath, RemoteUrl
from ...interaction.messages import Content, ConversationRef, TextContent
from ...interaction.operations import ContractViolation, require_identifier
from ...sqlite_rows import decode_datetime, optional_text, required_text
from ..persistence.repository_contracts import DeliverySubmissionConflict
from ..persistence.submission_identity import (
    ensure_same_delivery_submission_reservation,
)


def derive_delivery_target_fingerprint(target: DeliveryTarget) -> str:
    if isinstance(target, ConversationDeliveryTarget):
        identity: object = [
            target.kind.value,
            target.conversation_ref.channel_instance_id,
            target.conversation_ref.native_conversation_id,
        ]
    else:
        identity = [
            target.kind.value,
            _thread_identity(target.thread_ref),
            target.route_id,
        ]
    return _sha256_identity("target", identity)


def derive_delivery_payload_fingerprint(intent: DeliveryIntent) -> str:
    validate_delivery_intent(intent)
    identity = {
        "content": [_content_identity(item) for item in intent.content],
        "reply_to": intent.reply_to,
        "metadata": _canonical_metadata(intent.metadata),
    }
    return _sha256_identity("payload", identity)


def derive_destination_delivery_id(
    root_submission_id: str,
    conversation_ref: ConversationRef,
) -> str:
    require_identifier(root_submission_id, "submission_id")
    _validate_conversation_ref(conversation_ref)
    return _sha256_identity(
        "destination",
        [
            root_submission_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
    )


def derive_delivery_submission_id(
    origin: DeliverySubmissionOrigin,
    principal_id: str,
    delivery_id: str,
) -> str:
    if not isinstance(origin, DeliverySubmissionOrigin):
        raise ContractViolation("delivery submission origin is invalid")
    require_identifier(principal_id, "principal_id")
    require_identifier(delivery_id, "delivery_id")
    return _sha256_identity(
        "submission",
        [origin.value, principal_id, delivery_id],
    )


def _content_identity(content: Content) -> object:
    if isinstance(content, TextContent):
        return {
            "kind": "text",
            "text": content.text,
            "format": content.format.value,
        }
    source = content.source
    if isinstance(source, LocalPath):
        digest = content.metadata.get("sha256")
        source_identity: object = (
            {"kind": source.kind.value, "sha256": digest}
            if isinstance(digest, str) and digest
            else {"kind": source.kind.value, "path": source.path}
        )
    elif isinstance(source, RemoteUrl):
        source_identity = {"kind": source.kind.value, "url": source.url}
    else:
        source_identity = {
            "kind": source.kind.value,
            "handle_id": source.handle_id,
        }
    return {
        "kind": "attachment",
        "attachment_id": content.attachment_id,
        "media_type": content.media_type,
        "source": source_identity,
        "filename": content.filename,
        "size_bytes": content.size_bytes,
        "metadata": _canonical_metadata(content.metadata),
    }


def _sha256_identity(label: str, identity: object) -> str:
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return f"imagent:delivery-{label}:sha256:{digest}"


def _thread_identity(thread_ref: ThreadRef) -> object:
    return [
        thread_ref.application_instance_id,
        (thread_ref.project_ref.native_project_id if thread_ref.project_ref is not None else None),
        thread_ref.native_thread_id,
    ]


class _SQLiteOwner(Protocol):
    _connection: sqlite3.Connection
    _lock: asyncio.Lock


class SQLiteDeliverySubmissionMixin:
    """Delivery outcome methods sharing a SQLiteGatewayState transaction owner."""

    _connection: sqlite3.Connection
    _lock: asyncio.Lock

    async def get_delivery_submission(
        self: _SQLiteOwner,
        submission_id: str,
    ) -> DeliverySubmissionRecord | None:
        async with self._lock:
            return _read_submission(self._connection, submission_id)

    async def reserve_delivery_submission(
        self: _SQLiteOwner,
        record: DeliverySubmissionRecord,
    ) -> DeliveryReservation:
        validate_delivery_submission_record(record)
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = _read_submission(self._connection, record.submission_id)
                if existing is not None:
                    ensure_same_delivery_submission_reservation(existing, record)
                    self._connection.commit()
                    return DeliveryReservation(acquired=False, record=existing)
                _write_submission(self._connection, record)
                self._connection.commit()
                return DeliveryReservation(acquired=True, record=record)
            except BaseException:
                self._connection.rollback()
                raise

    async def update_delivery_destination(
        self: _SQLiteOwner,
        submission_id: str,
        destination_delivery_id: str,
        *,
        expected_state: DeliverySubmissionState,
        destination: DestinationDeliveryRecord,
    ) -> DeliverySubmissionRecord:
        if destination.delivery_id != destination_delivery_id:
            raise DeliverySubmissionConflict("destination delivery identity changed")
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = _read_submission(self._connection, submission_id)
                if current is None:
                    raise KeyError(f"delivery submission does not exist: {submission_id}")
                existing = next(
                    (
                        candidate
                        for candidate in current.destinations
                        if candidate.delivery_id == destination_delivery_id
                    ),
                    None,
                )
                if existing is None:
                    raise KeyError(
                        f"delivery destination does not exist: {destination_delivery_id}"
                    )
                if existing.state is not expected_state:
                    if existing == destination:
                        self._connection.commit()
                        return current
                    raise DeliverySubmissionConflict("delivery destination state changed")
                if existing.snapshot != destination.snapshot:
                    raise DeliverySubmissionConflict("delivery destination snapshot changed")
                _write_destination(
                    self._connection,
                    root_submission_id=submission_id,
                    destination=destination,
                )
                updated_at = max(current.updated_at, destination.updated_at)
                self._connection.execute(
                    """
                    UPDATE delivery_submissions
                    SET updated_at = ?
                    WHERE submission_id = ?
                    """,
                    (updated_at.isoformat(), submission_id),
                )
                updated = replace(
                    current,
                    destinations=tuple(
                        destination
                        if candidate.delivery_id == destination_delivery_id
                        else candidate
                        for candidate in current.destinations
                    ),
                    updated_at=updated_at,
                )
                validate_delivery_submission_record(updated)
                self._connection.commit()
                return updated
            except BaseException:
                self._connection.rollback()
                raise


def initialize_delivery_submission_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS delivery_submissions (
            submission_id TEXT NOT NULL PRIMARY KEY,
            delivery_id TEXT NOT NULL,
            origin TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            target_fingerprint TEXT NOT NULL,
            payload_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS delivery_submission_destinations (
            root_submission_id TEXT NOT NULL,
            destination_delivery_id TEXT NOT NULL PRIMARY KEY,
            channel_instance_id TEXT NOT NULL,
            native_conversation_id TEXT NOT NULL,
            application_instance_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            route_id TEXT NOT NULL,
            route_updated_at TEXT,
            reply_to_message_id TEXT,
            state TEXT NOT NULL,
            receipt_json TEXT,
            error TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (
                root_submission_id,
                channel_instance_id,
                native_conversation_id
            ),
            FOREIGN KEY(root_submission_id)
                REFERENCES delivery_submissions(submission_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS delivery_destinations_root
            ON delivery_submission_destinations(root_submission_id);
        """
    )


def _read_submission(
    connection: sqlite3.Connection,
    submission_id: str,
) -> DeliverySubmissionRecord | None:
    root = connection.execute(
        "SELECT * FROM delivery_submissions WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    if root is None:
        return None
    rows = connection.execute(
        """
        SELECT * FROM delivery_submission_destinations
        WHERE root_submission_id = ?
        ORDER BY channel_instance_id, native_conversation_id, destination_delivery_id
        """,
        (submission_id,),
    ).fetchall()
    record = DeliverySubmissionRecord(
        submission_id=required_text(root["submission_id"], "submission_id"),
        delivery_id=required_text(root["delivery_id"], "delivery_id"),
        origin=DeliverySubmissionOrigin(required_text(root["origin"], "origin")),
        principal_id=required_text(root["principal_id"], "principal_id"),
        target_fingerprint=required_text(root["target_fingerprint"], "target_fingerprint"),
        payload_fingerprint=required_text(root["payload_fingerprint"], "payload_fingerprint"),
        destinations=tuple(_destination_from_row(row) for row in rows),
        created_at=decode_datetime(root["created_at"], "created_at"),
        updated_at=decode_datetime(root["updated_at"], "updated_at"),
    )
    validate_delivery_submission_record(record)
    return record


def _write_submission(
    connection: sqlite3.Connection,
    record: DeliverySubmissionRecord,
) -> None:
    connection.execute(
        """
        INSERT INTO delivery_submissions (
            submission_id,
            delivery_id,
            origin,
            principal_id,
            target_fingerprint,
            payload_fingerprint,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.submission_id,
            record.delivery_id,
            record.origin.value,
            record.principal_id,
            record.target_fingerprint,
            record.payload_fingerprint,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
        ),
    )
    for destination in record.destinations:
        _write_destination(
            connection,
            root_submission_id=record.submission_id,
            destination=destination,
        )


def _write_destination(
    connection: sqlite3.Connection,
    *,
    root_submission_id: str,
    destination: DestinationDeliveryRecord,
) -> None:
    snapshot = destination.snapshot
    application_id, project_id, thread_id = _thread_storage_key(snapshot.thread_ref)
    connection.execute(
        """
        INSERT INTO delivery_submission_destinations (
            root_submission_id,
            destination_delivery_id,
            channel_instance_id,
            native_conversation_id,
            application_instance_id,
            project_id,
            thread_id,
            route_id,
            route_updated_at,
            reply_to_message_id,
            state,
            receipt_json,
            error,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(destination_delivery_id)
        DO UPDATE SET
            state = excluded.state,
            receipt_json = excluded.receipt_json,
            error = excluded.error,
            updated_at = excluded.updated_at
        """,
        (
            root_submission_id,
            destination.delivery_id,
            snapshot.conversation_ref.channel_instance_id,
            snapshot.conversation_ref.native_conversation_id,
            application_id,
            project_id,
            thread_id,
            snapshot.route_id or "",
            (
                snapshot.route_updated_at.isoformat()
                if snapshot.route_updated_at is not None
                else None
            ),
            snapshot.reply_to_message_id,
            destination.state.value,
            _encode_receipt(destination.receipt),
            destination.error,
            destination.updated_at.isoformat(),
        ),
    )


def _destination_from_row(row: sqlite3.Row) -> DestinationDeliveryRecord:
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
        receipt=_decode_receipt(row["receipt_json"]),
        error=optional_text(row["error"], "error"),
        updated_at=decode_datetime(row["updated_at"], "updated_at"),
    )


def _encode_receipt(receipt: DeliveryReceipt | None) -> str | None:
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


def _decode_receipt(value: object) -> DeliveryReceipt | None:
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
    """Decode the current NOT NULL empty-string sentinel for an absent route scope."""

    if value == "":
        return None
    return optional_text(value, label)


def _thread_storage_key(
    thread_ref: ThreadRef | None,
) -> tuple[str, str, str]:
    if thread_ref is None:
        return "", "", ""
    return (
        thread_ref.application_instance_id,
        (thread_ref.project_ref.native_project_id if thread_ref.project_ref is not None else ""),
        thread_ref.native_thread_id,
    )

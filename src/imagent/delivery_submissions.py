from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from .adapters import DeliverySubmissionConflict
from .contracts import (
    ConversationRef,
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliveryReservation,
    DeliveryRouteSnapshot,
    DeliverySubmissionOrigin,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    ProjectRef,
    ThreadRef,
    validate_delivery_submission_record,
)


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
                    _ensure_same_identity(existing, record)
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
        submission_id=str(root["submission_id"]),
        delivery_id=str(root["delivery_id"]),
        origin=DeliverySubmissionOrigin(str(root["origin"])),
        principal_id=str(root["principal_id"]),
        target_fingerprint=str(root["target_fingerprint"]),
        payload_fingerprint=str(root["payload_fingerprint"]),
        destinations=tuple(_destination_from_row(row) for row in rows),
        created_at=datetime.fromisoformat(str(root["created_at"])),
        updated_at=datetime.fromisoformat(str(root["updated_at"])),
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
    application_id = str(row["application_instance_id"])
    project_id = str(row["project_id"])
    thread_id = str(row["thread_id"])
    thread_ref = (
        ThreadRef(
            application_instance_id=application_id,
            native_thread_id=thread_id,
            project_ref=(ProjectRef(application_id, project_id) if project_id else None),
        )
        if application_id and thread_id
        else None
    )
    return DestinationDeliveryRecord(
        delivery_id=str(row["destination_delivery_id"]),
        snapshot=DeliveryRouteSnapshot(
            conversation_ref=ConversationRef(
                channel_instance_id=str(row["channel_instance_id"]),
                native_conversation_id=str(row["native_conversation_id"]),
            ),
            thread_ref=thread_ref,
            route_id=(str(row["route_id"]) if row["route_id"] else None),
            route_updated_at=(
                datetime.fromisoformat(str(row["route_updated_at"]))
                if row["route_updated_at"] is not None
                else None
            ),
            reply_to_message_id=(
                str(row["reply_to_message_id"]) if row["reply_to_message_id"] is not None else None
            ),
        ),
        state=DeliverySubmissionState(str(row["state"])),
        receipt=_decode_receipt(row["receipt_json"]),
        error=(str(row["error"]) if row["error"] is not None else None),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _encode_receipt(receipt: DeliveryReceipt | None) -> str | None:
    if receipt is None:
        return None
    return json.dumps(
        {
            "status": receipt.status.value,
            "native_message_id": receipt.native_message_id,
            "detail": receipt.detail,
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
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_receipt(value: object) -> DeliveryReceipt | None:
    if value is None:
        return None
    payload = json.loads(str(value))
    return DeliveryReceipt(
        status=DeliveryReceiptStatus(str(payload["status"])),
        native_message_id=payload.get("native_message_id"),
        detail=payload.get("detail"),
        items=tuple(
            DeliveryItemReceipt(
                content_index=int(item["content_index"]),
                status=DeliveryItemStatus(str(item["status"])),
                attachment_id=item.get("attachment_id"),
                native_message_id=item.get("native_message_id"),
                detail=item.get("detail"),
            )
            for item in payload.get("items", [])
        ),
    )


def _ensure_same_identity(
    existing: DeliverySubmissionRecord,
    replacement: DeliverySubmissionRecord,
) -> None:
    if (
        existing.delivery_id != replacement.delivery_id
        or existing.origin is not replacement.origin
        or existing.principal_id != replacement.principal_id
        or existing.target_fingerprint != replacement.target_fingerprint
        or existing.payload_fingerprint != replacement.payload_fingerprint
    ):
        raise DeliverySubmissionConflict(
            f"delivery ID belongs to a different submission: {existing.delivery_id}"
        )


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

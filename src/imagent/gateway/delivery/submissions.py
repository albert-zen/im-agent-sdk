from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Protocol

from ...applications.contract import ThreadRef
from ...interaction.media import LocalPath, RemoteUrl
from ...interaction.messages import Content, ConversationRef, Metadata, TextContent
from ...interaction.operations import ContractViolation, require_identifier
from ..persistence import row_mapping
from ..persistence.repository_contracts import DeliverySubmissionConflict
from ..persistence.state_contracts import (
    DeliveryReservation,
    DeliverySubmissionOrigin,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    _validate_conversation_ref,
    validate_delivery_submission_record,
)
from ..persistence.submission_identity import (
    ensure_same_delivery_submission_reservation,
)
from .proactive import (
    ConversationDeliveryTarget,
    DeliveryIntent,
    DeliveryTarget,
    validate_delivery_intent,
)


def _canonical_metadata(metadata: Metadata) -> object:
    try:
        return json.loads(
            json.dumps(
                dict(metadata),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as error:
        raise ContractViolation("delivery metadata must be JSON-compatible") from error


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
    return row_mapping.delivery_submission_from_rows(root, rows)


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
        row_mapping.delivery_submission_to_row(record),
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
        row_mapping.delivery_destination_to_row(root_submission_id, destination),
    )

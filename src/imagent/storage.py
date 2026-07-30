from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .bindings import BindingConflict
from .contracts import (
    ApplicationRef,
    ConversationBinding,
    ConversationRef,
    ProjectRef,
    ThreadProjectionRoute,
    ThreadRef,
    validate_binding,
    validate_projection_route,
)


class InMemoryIdempotencyRepository:
    """Process-local exact-once admission for tests and ephemeral deployments."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def claim(self, scope: str, key: str) -> bool:
        async with self._lock:
            record = (scope, key)
            if record in self._records:
                return False
            self._records[record] = "in_flight"
            return True

    async def complete(self, scope: str, key: str) -> None:
        async with self._lock:
            self._records[(scope, key)] = "completed"

    async def release(self, scope: str, key: str) -> None:
        async with self._lock:
            record = (scope, key)
            if self._records.get(record) == "in_flight":
                self._records.pop(record, None)


class SQLiteGatewayState:
    """Durable bindings, projection routes, and idempotency without Agent truth."""

    def __init__(
        self,
        path: str | Path,
        *,
        stale_claim_after_seconds: float = 300.0,
    ) -> None:
        if stale_claim_after_seconds < 0:
            raise ValueError("stale_claim_after_seconds must be non-negative")
        self._path = Path(path)
        self._stale_claim_after_seconds = stale_claim_after_seconds
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS conversation_bindings (
                channel_instance_id TEXT NOT NULL,
                native_conversation_id TEXT NOT NULL,
                application_instance_id TEXT,
                project_id TEXT,
                thread_id TEXT,
                revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (channel_instance_id, native_conversation_id)
            );
            CREATE TABLE IF NOT EXISTS idempotency_records (
                scope TEXT NOT NULL,
                record_key TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scope, record_key)
            );
            CREATE TABLE IF NOT EXISTS thread_projection_routes (
                route_id TEXT NOT NULL PRIMARY KEY,
                application_instance_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                channel_instance_id TEXT NOT NULL,
                native_conversation_id TEXT NOT NULL,
                reply_to_message_id TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE (
                    application_instance_id,
                    project_id,
                    thread_id,
                    channel_instance_id,
                    native_conversation_id
                )
            );
            """
        )

    async def close(self) -> None:
        async with self._lock:
            self._connection.close()

    async def get(
        self,
        conversation: ConversationRef,
    ) -> ConversationBinding | None:
        async with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM conversation_bindings
                WHERE channel_instance_id = ? AND native_conversation_id = ?
                """,
                (
                    conversation.channel_instance_id,
                    conversation.native_conversation_id,
                ),
            ).fetchone()
        return _binding_from_row(row) if row is not None else None

    async def put(
        self,
        binding: ConversationBinding,
        expected_revision: int | None = None,
    ) -> ConversationBinding:
        validate_binding(binding)
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT revision FROM conversation_bindings
                    WHERE channel_instance_id = ? AND native_conversation_id = ?
                    """,
                    (
                        binding.conversation_ref.channel_instance_id,
                        binding.conversation_ref.native_conversation_id,
                    ),
                ).fetchone()
                current_revision = int(row["revision"]) if row is not None else 0
                if expected_revision is not None and expected_revision != current_revision:
                    raise BindingConflict(
                        f"expected revision {expected_revision}, "
                        f"current revision is {current_revision}"
                    )
                updated_at = datetime.now(UTC)
                stored = ConversationBinding(
                    conversation_ref=binding.conversation_ref,
                    application_ref=binding.application_ref,
                    project_ref=binding.project_ref,
                    thread_ref=binding.thread_ref,
                    revision=current_revision + 1,
                    updated_at=updated_at,
                )
                self._connection.execute(
                    """
                    INSERT INTO conversation_bindings (
                        channel_instance_id,
                        native_conversation_id,
                        application_instance_id,
                        project_id,
                        thread_id,
                        revision,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(channel_instance_id, native_conversation_id)
                    DO UPDATE SET
                        application_instance_id = excluded.application_instance_id,
                        project_id = excluded.project_id,
                        thread_id = excluded.thread_id,
                        revision = excluded.revision,
                        updated_at = excluded.updated_at
                    """,
                    (
                        stored.conversation_ref.channel_instance_id,
                        stored.conversation_ref.native_conversation_id,
                        (
                            stored.application_ref.application_instance_id
                            if stored.application_ref is not None
                            else None
                        ),
                        (
                            stored.project_ref.native_project_id
                            if stored.project_ref is not None
                            else None
                        ),
                        (
                            stored.thread_ref.native_thread_id
                            if stored.thread_ref is not None
                            else None
                        ),
                        stored.revision,
                        updated_at.isoformat(),
                    ),
                )
                self._connection.commit()
                return stored
            except BaseException:
                self._connection.rollback()
                raise

    async def delete(
        self,
        conversation: ConversationRef,
        expected_revision: int | None = None,
    ) -> None:
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT revision FROM conversation_bindings
                    WHERE channel_instance_id = ? AND native_conversation_id = ?
                    """,
                    (
                        conversation.channel_instance_id,
                        conversation.native_conversation_id,
                    ),
                ).fetchone()
                current_revision = int(row["revision"]) if row is not None else 0
                if expected_revision is not None and expected_revision != current_revision:
                    raise BindingConflict(
                        f"expected revision {expected_revision}, "
                        f"current revision is {current_revision}"
                    )
                self._connection.execute(
                    """
                    DELETE FROM conversation_bindings
                    WHERE channel_instance_id = ? AND native_conversation_id = ?
                    """,
                    (
                        conversation.channel_instance_id,
                        conversation.native_conversation_id,
                    ),
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    async def list_projection_routes(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[ThreadProjectionRoute, ...]:
        async with self._lock:
            if thread_ref is None:
                rows = self._connection.execute(
                    "SELECT * FROM thread_projection_routes ORDER BY updated_at"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM thread_projection_routes
                    WHERE application_instance_id = ?
                      AND project_id = ?
                      AND thread_id = ?
                    ORDER BY updated_at
                    """,
                    _thread_storage_key(thread_ref),
                ).fetchall()
        return tuple(_projection_route_from_row(row) for row in rows)

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._write_projection_route(route)
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        return route

    async def replace_thread_projection_routes(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    DELETE FROM thread_projection_routes
                    WHERE application_instance_id = ?
                      AND project_id = ?
                      AND thread_id = ?
                    """,
                    _thread_storage_key(route.thread_ref),
                )
                self._write_projection_route(route)
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        return route

    def _write_projection_route(self, route: ThreadProjectionRoute) -> None:
        updated_at = route.updated_at or datetime.now(UTC)
        self._connection.execute(
            """
            INSERT INTO thread_projection_routes (
                route_id,
                application_instance_id,
                project_id,
                thread_id,
                channel_instance_id,
                native_conversation_id,
                reply_to_message_id,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(route_id)
            DO UPDATE SET
                reply_to_message_id = excluded.reply_to_message_id,
                updated_at = excluded.updated_at
            """,
            (
                route.route_id,
                *_thread_storage_key(route.thread_ref),
                route.conversation_ref.channel_instance_id,
                route.conversation_ref.native_conversation_id,
                route.reply_to_message_id,
                updated_at.isoformat(),
            ),
        )

    async def claim(self, scope: str, key: str) -> bool:
        async with self._lock:
            now = datetime.now(UTC)
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO idempotency_records (
                        scope, record_key, status, updated_at
                    ) VALUES (?, ?, 'in_flight', ?)
                    """,
                    (scope, key, now.isoformat()),
                )
                self._connection.commit()
                return True
            except sqlite3.IntegrityError:
                row = self._connection.execute(
                    """
                    SELECT status, updated_at FROM idempotency_records
                    WHERE scope = ? AND record_key = ?
                    """,
                    (scope, key),
                ).fetchone()
                if row is None or str(row["status"]) == "completed":
                    self._connection.rollback()
                    return False
                updated_at = datetime.fromisoformat(str(row["updated_at"]))
                age = (now - updated_at).total_seconds()
                if age < self._stale_claim_after_seconds:
                    self._connection.rollback()
                    return False
                self._connection.execute(
                    """
                    UPDATE idempotency_records SET updated_at = ?
                    WHERE scope = ? AND record_key = ?
                    """,
                    (now.isoformat(), scope, key),
                )
                self._connection.commit()
                return True

    async def complete(self, scope: str, key: str) -> None:
        async with self._lock:
            self._connection.execute(
                """
                UPDATE idempotency_records
                SET status = 'completed', updated_at = ?
                WHERE scope = ? AND record_key = ?
                """,
                (datetime.now(UTC).isoformat(), scope, key),
            )
            self._connection.commit()

    async def release(self, scope: str, key: str) -> None:
        async with self._lock:
            self._connection.execute(
                """
                DELETE FROM idempotency_records
                WHERE scope = ? AND record_key = ? AND status = 'in_flight'
                """,
                (scope, key),
            )
            self._connection.commit()


def _binding_from_row(row: sqlite3.Row) -> ConversationBinding:
    application_id = row["application_instance_id"]
    project_id = row["project_id"]
    thread_id = row["thread_id"]
    application_ref = ApplicationRef(str(application_id)) if application_id is not None else None
    project_ref = (
        ProjectRef(str(application_id), str(project_id))
        if application_id is not None and project_id is not None
        else None
    )
    thread_ref = (
        ThreadRef(
            application_instance_id=str(application_id),
            native_thread_id=str(thread_id),
            project_ref=project_ref,
        )
        if application_id is not None and thread_id is not None
        else None
    )
    return ConversationBinding(
        conversation_ref=ConversationRef(
            str(row["channel_instance_id"]),
            str(row["native_conversation_id"]),
        ),
        application_ref=application_ref,
        project_ref=project_ref,
        thread_ref=thread_ref,
        revision=int(row["revision"]),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _thread_storage_key(thread_ref: ThreadRef) -> tuple[str, str, str]:
    return (
        thread_ref.application_instance_id,
        (thread_ref.project_ref.native_project_id if thread_ref.project_ref is not None else ""),
        thread_ref.native_thread_id,
    )


def _projection_route_from_row(row: sqlite3.Row) -> ThreadProjectionRoute:
    application_id = str(row["application_instance_id"])
    project_id = str(row["project_id"])
    project_ref = ProjectRef(application_id, project_id) if project_id else None
    return ThreadProjectionRoute(
        route_id=str(row["route_id"]),
        thread_ref=ThreadRef(
            application_instance_id=application_id,
            native_thread_id=str(row["thread_id"]),
            project_ref=project_ref,
        ),
        conversation_ref=ConversationRef(
            channel_instance_id=str(row["channel_instance_id"]),
            native_conversation_id=str(row["native_conversation_id"]),
        ),
        reply_to_message_id=(
            str(row["reply_to_message_id"]) if row["reply_to_message_id"] is not None else None
        ),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )

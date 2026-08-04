from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from . import sqlite_rows
from .adapters import (
    IdempotencyClaimStatus,
    ProjectionCheckpointConflict,
    TurnReplyCorrelationConflict,
)
from .contracts import (
    ConversationBinding,
    ThreadProjectionRoute,
    ThreadRef,
    TurnReplyCorrelation,
    validate_binding,
    validate_projection_route,
    validate_turn_reply_correlation,
)
from .gateway.delivery.submissions import (
    SQLiteDeliverySubmissionMixin,
    initialize_delivery_submission_schema,
)
from .gateway.persistence import BindingConflict
from .interaction.messages import ConversationRef
from .request_correlations import (
    SQLiteRequestCorrelationMixin,
    initialize_request_correlation_schema,
)


def _same_turn_reply_correlation(
    left: TurnReplyCorrelation,
    right: TurnReplyCorrelation,
) -> bool:
    return (
        left.correlation_id == right.correlation_id
        and left.thread_ref == right.thread_ref
        and left.turn_id == right.turn_id
        and left.client_message_id == right.client_message_id
        and left.conversation_ref == right.conversation_ref
        and left.reply_to_message_id == right.reply_to_message_id
    )


class InMemoryIdempotencyRepository:
    """Process-local stable claim state for tests and ephemeral deployments."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], tuple[str, str | None]] = {}
        self._lock = asyncio.Lock()

    async def claim(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> IdempotencyClaimStatus:
        async with self._lock:
            record = (scope, key)
            current = self._records.get(record)
            status = current[0] if current is not None else None
            if status == "completed":
                return IdempotencyClaimStatus.ALREADY_COMPLETED
            if status in {"in_flight", "side_effect_started"}:
                return IdempotencyClaimStatus.IN_FLIGHT
            self._records[record] = ("in_flight", owner_token)
            return IdempotencyClaimStatus.ACQUIRED

    async def complete(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            record = (scope, key)
            current = self._records.get(record)
            if current is None or current[1] != owner_token:
                raise RuntimeError("idempotency claim is not owned by caller")
            self._records[record] = ("completed", owner_token)

    async def mark_side_effect_started(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            record = (scope, key)
            if self._records.get(record) != ("in_flight", owner_token):
                raise RuntimeError("idempotency claim is not owned by caller")
            self._records[record] = ("side_effect_started", owner_token)

    async def refresh(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            if self._records.get((scope, key)) != ("in_flight", owner_token):
                raise RuntimeError("idempotency claim is not owned by caller")

    async def release(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            record = (scope, key)
            current = self._records.get(record)
            if current in {
                ("in_flight", owner_token),
                ("side_effect_started", owner_token),
            }:
                self._records.pop(record, None)


class SQLiteGatewayState(
    SQLiteDeliverySubmissionMixin,
    SQLiteRequestCorrelationMixin,
):
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
                owner_token TEXT,
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
                checkpoint_agent_item_id TEXT,
                checkpointed_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE (
                    application_instance_id,
                    project_id,
                    thread_id,
                    channel_instance_id,
                    native_conversation_id
                )
            );
            CREATE TABLE IF NOT EXISTS turn_reply_correlations (
                correlation_id TEXT NOT NULL PRIMARY KEY,
                application_instance_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                client_message_id TEXT NOT NULL,
                channel_instance_id TEXT NOT NULL,
                native_conversation_id TEXT NOT NULL,
                reply_to_message_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (
                    application_instance_id,
                    project_id,
                    thread_id,
                    turn_id
                )
            );
            """
        )
        idempotency_columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(idempotency_records)").fetchall()
        }
        if "owner_token" not in idempotency_columns:
            self._connection.execute("ALTER TABLE idempotency_records ADD COLUMN owner_token TEXT")
        route_columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(thread_projection_routes)"
            ).fetchall()
        }
        legacy_route_schema = (
            "checkpoint_agent_item_id" not in route_columns
            or "checkpointed_at" not in route_columns
        )
        if "checkpoint_agent_item_id" not in route_columns:
            self._connection.execute(
                "ALTER TABLE thread_projection_routes ADD COLUMN checkpoint_agent_item_id TEXT"
            )
        if "checkpointed_at" not in route_columns:
            self._connection.execute(
                "ALTER TABLE thread_projection_routes ADD COLUMN checkpointed_at TEXT"
            )
        if legacy_route_schema:
            # The pre-checkpoint Gateway stored the latest inbound message ID
            # in this column.  That value cannot be distinguished from an
            # explicit topic default, so preserving it would mis-correlate
            # external/recovered Turns after upgrade.
            self._connection.execute(
                "UPDATE thread_projection_routes SET reply_to_message_id = NULL"
            )
        initialize_request_correlation_schema(self._connection)
        initialize_delivery_submission_schema(self._connection)
        self._connection.commit()

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
        return sqlite_rows.binding_from_row(row) if row is not None else None

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
                    sqlite_rows.thread_storage_key(thread_ref),
                ).fetchall()
        return tuple(sqlite_rows.projection_route_from_row(row) for row in rows)

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._read_projection_route(
                    route.route_id
                ) or self._read_projection_route_for_endpoints(route)
                stored = sqlite_rows.merge_projection_route(existing, route)
                self._write_projection_route(stored)
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        return stored

    async def replace_thread_projection_routes(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                stored = sqlite_rows.merge_projection_route(
                    self._read_projection_route(route.route_id)
                    or self._read_projection_route_for_endpoints(route),
                    route,
                )
                self._connection.execute(
                    """
                    DELETE FROM thread_projection_routes
                    WHERE application_instance_id = ?
                      AND project_id = ?
                      AND thread_id = ?
                    """,
                    sqlite_rows.thread_storage_key(stored.thread_ref),
                )
                self._write_projection_route(stored)
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        return stored

    async def advance_projection_checkpoint(
        self,
        route_id: str,
        *,
        expected_agent_item_id: str | None,
        agent_item_id: str,
        checkpointed_at: datetime,
    ) -> ThreadProjectionRoute:
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._read_projection_route(route_id)
                if existing is None:
                    raise KeyError(f"projection route does not exist: {route_id}")
                if existing.checkpoint_agent_item_id != expected_agent_item_id:
                    raise ProjectionCheckpointConflict(
                        f"projection checkpoint changed for route {route_id}"
                    )
                candidate = replace(
                    existing,
                    checkpoint_agent_item_id=agent_item_id,
                    checkpointed_at=checkpointed_at,
                )
                validate_projection_route(candidate)
                cursor = self._connection.execute(
                    """
                    UPDATE thread_projection_routes
                    SET checkpoint_agent_item_id = ?, checkpointed_at = ?
                    WHERE route_id = ?
                      AND (
                        checkpoint_agent_item_id = ?
                        OR (
                            checkpoint_agent_item_id IS NULL
                            AND ? IS NULL
                        )
                      )
                    """,
                    (
                        agent_item_id,
                        checkpointed_at.isoformat(),
                        route_id,
                        expected_agent_item_id,
                        expected_agent_item_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ProjectionCheckpointConflict(
                        f"projection checkpoint changed for route {route_id}"
                    )
                advanced = self._read_projection_route(route_id)
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        if advanced is None:
            raise KeyError(f"projection route does not exist: {route_id}")
        return advanced

    async def delete_projection_routes(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef | None = None,
    ) -> int:
        where = """
            application_instance_id = ? AND project_id = ? AND thread_id = ?
            """
        parameters: tuple[object, ...] = sqlite_rows.thread_storage_key(thread_ref)
        if conversation_ref is not None:
            where += " AND channel_instance_id = ? AND native_conversation_id = ?"
            parameters += (
                conversation_ref.channel_instance_id,
                conversation_ref.native_conversation_id,
            )
        async with self._lock:
            cursor = self._connection.execute(
                f"DELETE FROM thread_projection_routes WHERE {where}",
                parameters,
            )
            self._connection.commit()
            return cursor.rowcount

    async def get_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> TurnReplyCorrelation | None:
        async with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM turn_reply_correlations
                WHERE application_instance_id = ?
                  AND project_id = ?
                  AND thread_id = ?
                  AND turn_id = ?
                """,
                (*sqlite_rows.thread_storage_key(thread_ref), turn_id),
            ).fetchone()
        return sqlite_rows.turn_reply_correlation_from_row(row) if row is not None else None

    async def list_turn_reply_correlations(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[TurnReplyCorrelation, ...]:
        async with self._lock:
            if thread_ref is None:
                rows = self._connection.execute(
                    "SELECT * FROM turn_reply_correlations ORDER BY created_at"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM turn_reply_correlations
                    WHERE application_instance_id = ?
                      AND project_id = ?
                      AND thread_id = ?
                    ORDER BY created_at
                    """,
                    sqlite_rows.thread_storage_key(thread_ref),
                ).fetchall()
        return tuple(sqlite_rows.turn_reply_correlation_from_row(row) for row in rows)

    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation:
        validate_turn_reply_correlation(correlation)
        async with self._lock:
            self._connection.execute(
                """
                INSERT INTO turn_reply_correlations (
                    correlation_id,
                    application_instance_id,
                    project_id,
                    thread_id,
                    turn_id,
                    client_message_id,
                    channel_instance_id,
                    native_conversation_id,
                    reply_to_message_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    application_instance_id,
                    project_id,
                    thread_id,
                    turn_id
                )
                DO NOTHING
                """,
                (
                    correlation.correlation_id,
                    *sqlite_rows.thread_storage_key(correlation.thread_ref),
                    correlation.turn_id,
                    correlation.client_message_id,
                    correlation.conversation_ref.channel_instance_id,
                    correlation.conversation_ref.native_conversation_id,
                    correlation.reply_to_message_id,
                    correlation.created_at.isoformat(),
                ),
            )
            self._connection.commit()
            row = self._connection.execute(
                """
                SELECT * FROM turn_reply_correlations
                WHERE application_instance_id = ?
                  AND project_id = ?
                  AND thread_id = ?
                  AND turn_id = ?
                """,
                (*sqlite_rows.thread_storage_key(correlation.thread_ref), correlation.turn_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("Turn reply correlation insert did not persist a row")
            current = sqlite_rows.turn_reply_correlation_from_row(row)
            if not _same_turn_reply_correlation(current, correlation):
                raise TurnReplyCorrelationConflict(
                    "Turn reply correlation already belongs to another IM input"
                )
            return current

    async def delete_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> bool:
        async with self._lock:
            cursor = self._connection.execute(
                """
                DELETE FROM turn_reply_correlations
                WHERE application_instance_id = ?
                  AND project_id = ?
                  AND thread_id = ?
                  AND turn_id = ?
                """,
                (*sqlite_rows.thread_storage_key(thread_ref), turn_id),
            )
            self._connection.commit()
            return cursor.rowcount == 1

    async def delete_turn_reply_correlations(
        self,
        *,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int:
        if thread_ref is None and conversation_ref is None and older_than is None:
            raise ValueError("correlation deletion requires at least one selector")
        clauses: list[str] = []
        parameters: list[object] = []
        if thread_ref is not None:
            clauses.extend(
                (
                    "application_instance_id = ?",
                    "project_id = ?",
                    "thread_id = ?",
                )
            )
            parameters.extend(sqlite_rows.thread_storage_key(thread_ref))
        if conversation_ref is not None:
            clauses.extend(
                (
                    "channel_instance_id = ?",
                    "native_conversation_id = ?",
                )
            )
            parameters.extend(
                (
                    conversation_ref.channel_instance_id,
                    conversation_ref.native_conversation_id,
                )
            )
        if older_than is not None:
            clauses.append("created_at < ?")
            parameters.append(older_than.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._lock:
            cursor = self._connection.execute(
                f"DELETE FROM turn_reply_correlations{where}",
                tuple(parameters),
            )
            self._connection.commit()
            return cursor.rowcount

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
                checkpoint_agent_item_id,
                checkpointed_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(route_id)
            DO UPDATE SET
                reply_to_message_id = excluded.reply_to_message_id,
                checkpoint_agent_item_id = excluded.checkpoint_agent_item_id,
                checkpointed_at = excluded.checkpointed_at,
                updated_at = excluded.updated_at
            """,
            (
                route.route_id,
                *sqlite_rows.thread_storage_key(route.thread_ref),
                route.conversation_ref.channel_instance_id,
                route.conversation_ref.native_conversation_id,
                route.reply_to_message_id,
                route.checkpoint_agent_item_id,
                (route.checkpointed_at.isoformat() if route.checkpointed_at is not None else None),
                updated_at.isoformat(),
            ),
        )

    def _read_projection_route(
        self,
        route_id: str,
    ) -> ThreadProjectionRoute | None:
        row = self._connection.execute(
            "SELECT * FROM thread_projection_routes WHERE route_id = ?",
            (route_id,),
        ).fetchone()
        return sqlite_rows.projection_route_from_row(row) if row is not None else None

    def _read_projection_route_for_endpoints(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute | None:
        row = self._connection.execute(
            """
            SELECT * FROM thread_projection_routes
            WHERE application_instance_id = ?
              AND project_id = ?
              AND thread_id = ?
              AND channel_instance_id = ?
              AND native_conversation_id = ?
            """,
            (
                *sqlite_rows.thread_storage_key(route.thread_ref),
                route.conversation_ref.channel_instance_id,
                route.conversation_ref.native_conversation_id,
            ),
        ).fetchone()
        return sqlite_rows.projection_route_from_row(row) if row is not None else None

    async def claim(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> IdempotencyClaimStatus:
        async with self._lock:
            now = datetime.now(UTC)
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO idempotency_records (
                        scope, record_key, status, owner_token, updated_at
                    ) VALUES (?, ?, 'in_flight', ?, ?)
                    """,
                    (scope, key, owner_token, now.isoformat()),
                )
                self._connection.commit()
                return IdempotencyClaimStatus.ACQUIRED
            except sqlite3.IntegrityError:
                row = self._connection.execute(
                    """
                    SELECT status, owner_token, updated_at FROM idempotency_records
                    WHERE scope = ? AND record_key = ?
                    """,
                    (scope, key),
                ).fetchone()
                if row is None:
                    self._connection.rollback()
                    raise RuntimeError("idempotency record disappeared during claim")
                status = row["status"]
                if not isinstance(status, str):
                    self._connection.rollback()
                    raise ValueError("idempotency status must be SQLite text")
                if status == "completed":
                    self._connection.rollback()
                    return IdempotencyClaimStatus.ALREADY_COMPLETED
                if status == "side_effect_started":
                    self._connection.rollback()
                    return IdempotencyClaimStatus.IN_FLIGHT
                if status != "in_flight":
                    self._connection.rollback()
                    raise ValueError("idempotency status is invalid")
                try:
                    updated_at = sqlite_rows.decode_datetime(row["updated_at"], "updated_at")
                except ValueError:
                    self._connection.rollback()
                    raise
                age = (now - updated_at).total_seconds()
                if age < self._stale_claim_after_seconds:
                    self._connection.rollback()
                    return IdempotencyClaimStatus.IN_FLIGHT
                self._connection.execute(
                    """
                    UPDATE idempotency_records
                    SET owner_token = ?, updated_at = ?
                    WHERE scope = ? AND record_key = ?
                    """,
                    (owner_token, now.isoformat(), scope, key),
                )
                self._connection.commit()
                return IdempotencyClaimStatus.ACQUIRED

    async def mark_side_effect_started(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE idempotency_records
                SET status = 'side_effect_started', updated_at = ?
                WHERE scope = ? AND record_key = ? AND status = 'in_flight'
                  AND owner_token IS ?
                """,
                (datetime.now(UTC).isoformat(), scope, key, owner_token),
            )
            if cursor.rowcount != 1:
                self._connection.rollback()
                raise RuntimeError("idempotency claim is not owned by caller")
            self._connection.commit()

    async def refresh(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE idempotency_records
                SET updated_at = ?
                WHERE scope = ? AND record_key = ? AND status = 'in_flight'
                  AND owner_token IS ?
                """,
                (datetime.now(UTC).isoformat(), scope, key, owner_token),
            )
            if cursor.rowcount != 1:
                self._connection.rollback()
                raise RuntimeError("idempotency claim is not owned by caller")
            self._connection.commit()

    async def complete(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE idempotency_records
                SET status = 'completed', updated_at = ?
                WHERE scope = ? AND record_key = ? AND owner_token IS ?
                """,
                (datetime.now(UTC).isoformat(), scope, key, owner_token),
            )
            if cursor.rowcount != 1:
                self._connection.rollback()
                raise RuntimeError("idempotency claim is not owned by caller")
            self._connection.commit()

    async def release(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            self._connection.execute(
                """
                DELETE FROM idempotency_records
                WHERE scope = ? AND record_key = ?
                  AND status IN ('in_flight', 'side_effect_started')
                  AND owner_token IS ?
                """,
                (scope, key, owner_token),
            )
            self._connection.commit()

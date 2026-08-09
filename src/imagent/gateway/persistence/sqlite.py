from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from ...applications.contract import ThreadRef
from ...applications.requests import RequestRef
from ...interaction.channels.contract import validate_delivery_receipt
from ...interaction.messages import ConversationRef
from ..projection.request_correlation import (
    _merge_correlation,
    _require_delete_selector,
    _transition_correlations,
)
from . import row_mapping
from .repository_contracts import (
    BindingConflict,
    DeliverySubmissionConflict,
    IdempotencyClaimStatus,
    ProjectionCheckpointConflict,
    ProjectionRouteConflict,
    RequestCorrelationConflict,
    TurnReplyCorrelationConflict,
)
from .state_contracts import (
    ConversationBinding,
    DeliveryReservation,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
    _validate_generation,
    validate_binding,
    validate_delivery_submission_record,
    validate_projection_route,
    validate_request_route_correlation,
    validate_turn_reply_correlation,
)
from .submission_identity import ensure_same_delivery_submission_reservation


def _same_turn_reply_correlation(
    left: TurnReplyCorrelation,
    right: TurnReplyCorrelation,
) -> bool:
    return (
        left.correlation_id == right.correlation_id
        and left.turn_ref == right.turn_ref
        and left.client_message_id == right.client_message_id
        and left.conversation_ref == right.conversation_ref
        and left.reply_to_message_id == right.reply_to_message_id
    )


def merge_projection_route(
    existing: ThreadProjectionRoute | None,
    replacement: ThreadProjectionRoute,
) -> ThreadProjectionRoute:
    if existing is None:
        return replacement
    if (
        existing.route_id != replacement.route_id
        or existing.thread_ref != replacement.thread_ref
        or existing.conversation_ref != replacement.conversation_ref
    ):
        raise ProjectionRouteConflict(
            f"route ID belongs to different endpoints: {replacement.route_id}"
        )
    if replacement.checkpoint_agent_item_id is not None:
        if (
            replacement.checkpoint_agent_item_id != existing.checkpoint_agent_item_id
            or replacement.checkpointed_at != existing.checkpointed_at
        ):
            raise ProjectionCheckpointConflict(
                f"route refresh cannot change checkpoint: {replacement.route_id}"
            )
        return replacement
    if existing.checkpoint_agent_item_id is None:
        return replacement
    return replace(
        replacement,
        checkpoint_agent_item_id=existing.checkpoint_agent_item_id,
        checkpointed_at=existing.checkpointed_at,
    )


def _migrate_binding_generation_column(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(conversation_bindings)").fetchall()
    }
    if "generation" not in columns and "revision" in columns:
        connection.execute("ALTER TABLE conversation_bindings RENAME COLUMN revision TO generation")
        return
    if "generation" in columns and "revision" in columns:
        connection.execute(
            "UPDATE conversation_bindings SET generation = MAX(generation, revision)"
        )
        connection.execute("ALTER TABLE conversation_bindings DROP COLUMN revision")
        return
    if "generation" not in columns:
        raise RuntimeError("conversation binding schema has no generation column")


class SQLiteGatewayState:
    """Durable bindings, projection routes, and idempotency without Agent truth."""

    def __init__(
        self,
        path: str | Path,
        *,
        stale_claim_after_seconds: float = 300.0,
        _configure_connection: Callable[[sqlite3.Connection], None] | None = None,
        _mutation_guard: Callable[[sqlite3.Connection], None] | None = None,
    ) -> None:
        if stale_claim_after_seconds < 0:
            raise ValueError("stale_claim_after_seconds must be non-negative")
        self._path = Path(path)
        self._stale_claim_after_seconds = stale_claim_after_seconds
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        if _configure_connection is not None:
            _configure_connection(self._connection)
        self._mutation_guard = _mutation_guard
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
                generation INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (channel_instance_id, native_conversation_id)
            );
            CREATE TABLE IF NOT EXISTS conversation_binding_generations (
                channel_instance_id TEXT NOT NULL,
                native_conversation_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
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
        _migrate_binding_generation_column(self._connection)
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
        self._connection.execute(
            """
            INSERT INTO conversation_binding_generations (
                channel_instance_id,
                native_conversation_id,
                generation
            )
            SELECT channel_instance_id, native_conversation_id, generation
            FROM conversation_bindings
            WHERE 1
            ON CONFLICT(channel_instance_id, native_conversation_id)
            DO UPDATE SET generation = MAX(
                conversation_binding_generations.generation,
                excluded.generation
            )
            """
        )
        self._connection.commit()

    def _begin_mutation(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            if self._mutation_guard is not None:
                self._mutation_guard(self._connection)
        except BaseException:
            self._connection.rollback()
            raise

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
        return row_mapping.binding_from_row(row) if row is not None else None

    async def put(
        self,
        binding: ConversationBinding,
        expected_generation: int | None = None,
    ) -> ConversationBinding:
        validate_binding(binding)
        _validate_generation(expected_generation, "expected_generation")
        async with self._lock:
            self._begin_mutation()
            try:
                row = self._connection.execute(
                    """
                    SELECT MAX(generation) AS generation
                    FROM (
                        SELECT generation
                        FROM conversation_bindings
                        WHERE channel_instance_id = ? AND native_conversation_id = ?
                        UNION ALL
                        SELECT generation
                        FROM conversation_binding_generations
                        WHERE channel_instance_id = ? AND native_conversation_id = ?
                    )
                    """,
                    (
                        binding.conversation_ref.channel_instance_id,
                        binding.conversation_ref.native_conversation_id,
                        binding.conversation_ref.channel_instance_id,
                        binding.conversation_ref.native_conversation_id,
                    ),
                ).fetchone()
                current_generation = (
                    int(row["generation"])
                    if row is not None and row["generation"] is not None
                    else 0
                )
                if expected_generation is not None and expected_generation != current_generation:
                    raise BindingConflict(
                        f"expected generation {expected_generation}, "
                        f"current generation is {current_generation}"
                    )
                updated_at = datetime.now(UTC)
                stored = ConversationBinding(
                    conversation_ref=binding.conversation_ref,
                    application_ref=binding.application_ref,
                    project_ref=binding.project_ref,
                    thread_ref=binding.thread_ref,
                    generation=current_generation + 1,
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
                        generation,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(channel_instance_id, native_conversation_id)
                    DO UPDATE SET
                        application_instance_id = excluded.application_instance_id,
                        project_id = excluded.project_id,
                        thread_id = excluded.thread_id,
                        generation = excluded.generation,
                        updated_at = excluded.updated_at
                    """,
                    row_mapping.binding_to_row(stored),
                )
                self._connection.execute(
                    """
                    INSERT INTO conversation_binding_generations (
                        channel_instance_id,
                        native_conversation_id,
                        generation
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(channel_instance_id, native_conversation_id)
                    DO UPDATE SET generation = excluded.generation
                    """,
                    (
                        binding.conversation_ref.channel_instance_id,
                        binding.conversation_ref.native_conversation_id,
                        stored.generation,
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
        expected_generation: int | None = None,
    ) -> None:
        _validate_generation(expected_generation, "expected_generation")
        async with self._lock:
            self._begin_mutation()
            try:
                row = self._connection.execute(
                    """
                    SELECT MAX(generation) AS generation
                    FROM (
                        SELECT generation
                        FROM conversation_bindings
                        WHERE channel_instance_id = ? AND native_conversation_id = ?
                        UNION ALL
                        SELECT generation
                        FROM conversation_binding_generations
                        WHERE channel_instance_id = ? AND native_conversation_id = ?
                    )
                    """,
                    (
                        conversation.channel_instance_id,
                        conversation.native_conversation_id,
                        conversation.channel_instance_id,
                        conversation.native_conversation_id,
                    ),
                ).fetchone()
                current_generation = (
                    int(row["generation"])
                    if row is not None and row["generation"] is not None
                    else 0
                )
                if expected_generation is not None and expected_generation != current_generation:
                    raise BindingConflict(
                        f"expected generation {expected_generation}, "
                        f"current generation is {current_generation}"
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
                self._connection.execute(
                    """
                    INSERT INTO conversation_binding_generations (
                        channel_instance_id,
                        native_conversation_id,
                        generation
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(channel_instance_id, native_conversation_id)
                    DO UPDATE SET generation = excluded.generation
                    """,
                    (
                        conversation.channel_instance_id,
                        conversation.native_conversation_id,
                        current_generation + 1,
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
                    row_mapping.thread_storage_key(thread_ref),
                ).fetchall()
        return tuple(row_mapping.projection_route_from_row(row) for row in rows)

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            self._begin_mutation()
            try:
                existing = self._read_projection_route(
                    route.route_id
                ) or self._read_projection_route_for_endpoints(route)
                stored = merge_projection_route(existing, route)
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
            self._begin_mutation()
            try:
                stored = merge_projection_route(
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
                    row_mapping.thread_storage_key(stored.thread_ref),
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
            self._begin_mutation()
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
        parameters: tuple[object, ...] = row_mapping.thread_storage_key(thread_ref)
        if conversation_ref is not None:
            where += " AND channel_instance_id = ? AND native_conversation_id = ?"
            parameters += (
                conversation_ref.channel_instance_id,
                conversation_ref.native_conversation_id,
            )
        async with self._lock:
            self._begin_mutation()
            try:
                cursor = self._connection.execute(
                    f"DELETE FROM thread_projection_routes WHERE {where}",
                    parameters,
                )
                self._connection.commit()
                return cursor.rowcount
            except BaseException:
                self._connection.rollback()
                raise

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
                (*row_mapping.thread_storage_key(thread_ref), turn_id),
            ).fetchone()
        return row_mapping.turn_reply_correlation_from_row(row) if row is not None else None

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
                    row_mapping.thread_storage_key(thread_ref),
                ).fetchall()
        return tuple(row_mapping.turn_reply_correlation_from_row(row) for row in rows)

    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation:
        validate_turn_reply_correlation(correlation)
        async with self._lock:
            self._begin_mutation()
            try:
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
                    row_mapping.turn_reply_correlation_to_row(correlation),
                )
                row = self._connection.execute(
                    """
                    SELECT * FROM turn_reply_correlations
                    WHERE application_instance_id = ?
                      AND project_id = ?
                      AND thread_id = ?
                      AND turn_id = ?
                    """,
                    (
                        *row_mapping.thread_storage_key(correlation.turn_ref.thread_ref),
                        correlation.turn_ref.turn_id,
                    ),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Turn reply correlation insert did not persist a row")
                current = row_mapping.turn_reply_correlation_from_row(row)
                if not _same_turn_reply_correlation(current, correlation):
                    raise TurnReplyCorrelationConflict(
                        "Turn reply correlation already belongs to another IM input"
                    )
                self._connection.commit()
                return current
            except BaseException:
                self._connection.rollback()
                raise

    async def delete_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> bool:
        async with self._lock:
            self._begin_mutation()
            try:
                cursor = self._connection.execute(
                    """
                    DELETE FROM turn_reply_correlations
                    WHERE application_instance_id = ?
                      AND project_id = ?
                      AND thread_id = ?
                      AND turn_id = ?
                    """,
                    (*row_mapping.thread_storage_key(thread_ref), turn_id),
                )
                self._connection.commit()
                return cursor.rowcount == 1
            except BaseException:
                self._connection.rollback()
                raise

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
            parameters.extend(row_mapping.thread_storage_key(thread_ref))
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
            self._begin_mutation()
            try:
                cursor = self._connection.execute(
                    f"DELETE FROM turn_reply_correlations{where}",
                    tuple(parameters),
                )
                self._connection.commit()
                return cursor.rowcount
            except BaseException:
                self._connection.rollback()
                raise

    async def list_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
    ) -> tuple[RequestRouteCorrelation, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        _append_request_selectors(
            clauses,
            parameters,
            request_ref=request_ref,
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
        )
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM request_route_correlations{where} "
                "ORDER BY created_at, correlation_id",
                tuple(parameters),
            ).fetchall()
        return tuple(row_mapping.request_correlation_from_row(row) for row in rows)

    async def put_request_correlation(
        self,
        correlation: RequestRouteCorrelation,
    ) -> RequestRouteCorrelation:
        validate_request_route_correlation(correlation)
        async with self._lock:
            self._begin_mutation()
            try:
                rows = self._connection.execute(
                    """
                    SELECT * FROM request_route_correlations
                    WHERE application_instance_id = ?
                      AND native_request_id = ?
                    """,
                    (
                        correlation.request_ref.application_ref.application_instance_id,
                        correlation.request_ref.native_request_id,
                    ),
                ).fetchall()
                request_correlations = tuple(
                    row_mapping.request_correlation_from_row(row) for row in rows
                )
                existing = next(
                    (
                        candidate
                        for candidate in request_correlations
                        if candidate.correlation_id == correlation.correlation_id
                    ),
                    None,
                )
                stored = _merge_correlation(
                    existing,
                    correlation,
                    request_correlations=request_correlations,
                )
                conflicting = self._connection.execute(
                    """
                    SELECT * FROM request_route_correlations
                    WHERE application_instance_id = ?
                      AND native_request_id = ?
                      AND channel_instance_id = ?
                      AND native_conversation_id = ?
                      AND correlation_id != ?
                    """,
                    (
                        stored.request_ref.application_ref.application_instance_id,
                        stored.request_ref.native_request_id,
                        stored.conversation_ref.channel_instance_id,
                        stored.conversation_ref.native_conversation_id,
                        stored.correlation_id,
                    ),
                ).fetchone()
                if conflicting is not None:
                    raise RequestCorrelationConflict(
                        "request destination belongs to a different correlation"
                    )
                _write_correlation(self._connection, stored)
                self._connection.commit()
                return stored
            except BaseException:
                self._connection.rollback()
                raise

    async def transition_request_correlations(
        self,
        request_ref: RequestRef,
        *,
        expected_states: tuple[RequestRouteState, ...],
        state: RequestRouteState,
        updated_at: datetime,
    ) -> tuple[RequestRouteCorrelation, ...]:
        async with self._lock:
            self._begin_mutation()
            try:
                rows = self._connection.execute(
                    """
                    SELECT * FROM request_route_correlations
                    WHERE application_instance_id = ?
                      AND native_request_id = ?
                    """,
                    (
                        request_ref.application_ref.application_instance_id,
                        request_ref.native_request_id,
                    ),
                ).fetchall()
                selected = tuple(row_mapping.request_correlation_from_row(row) for row in rows)
                transitioned = _transition_correlations(
                    selected,
                    expected_states=expected_states,
                    state=state,
                    updated_at=updated_at,
                )
                for correlation in transitioned:
                    _write_correlation(self._connection, correlation)
                self._connection.commit()
                return transitioned
            except BaseException:
                self._connection.rollback()
                raise

    async def delete_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int:
        _require_delete_selector(request_ref, thread_ref, conversation_ref, older_than)
        clauses: list[str] = []
        parameters: list[object] = []
        _append_request_selectors(
            clauses,
            parameters,
            request_ref=request_ref,
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
        )
        if older_than is not None:
            clauses.append("updated_at < ?")
            parameters.append(older_than.isoformat())
        async with self._lock:
            self._begin_mutation()
            try:
                cursor = self._connection.execute(
                    f"DELETE FROM request_route_correlations WHERE {' AND '.join(clauses)}",
                    tuple(parameters),
                )
                self._connection.commit()
                return cursor.rowcount
            except BaseException:
                self._connection.rollback()
                raise

    async def get_delivery_submission(
        self,
        submission_id: str,
    ) -> DeliverySubmissionRecord | None:
        async with self._lock:
            return _read_submission(self._connection, submission_id)

    async def reserve_delivery_submission(
        self,
        record: DeliverySubmissionRecord,
    ) -> DeliveryReservation:
        validate_delivery_submission_record(record)
        async with self._lock:
            self._begin_mutation()
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
        self,
        submission_id: str,
        destination_delivery_id: str,
        *,
        expected_state: DeliverySubmissionState,
        destination: DestinationDeliveryRecord,
    ) -> DeliverySubmissionRecord:
        if destination.delivery_id != destination_delivery_id:
            raise DeliverySubmissionConflict("destination delivery identity changed")
        async with self._lock:
            self._begin_mutation()
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
            row_mapping.projection_route_to_row(route, updated_at=updated_at),
        )

    def _read_projection_route(
        self,
        route_id: str,
    ) -> ThreadProjectionRoute | None:
        row = self._connection.execute(
            "SELECT * FROM thread_projection_routes WHERE route_id = ?",
            (route_id,),
        ).fetchone()
        return row_mapping.projection_route_from_row(row) if row is not None else None

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
                *row_mapping.thread_storage_key(route.thread_ref),
                route.conversation_ref.channel_instance_id,
                route.conversation_ref.native_conversation_id,
            ),
        ).fetchone()
        return row_mapping.projection_route_from_row(row) if row is not None else None

    async def claim(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> IdempotencyClaimStatus:
        async with self._lock:
            now = datetime.now(UTC)
            self._begin_mutation()
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
                    updated_at = row_mapping.decode_datetime(row["updated_at"], "updated_at")
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
            self._begin_mutation()
            try:
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
                    raise RuntimeError("idempotency claim is not owned by caller")
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    async def refresh(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            self._begin_mutation()
            try:
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
                    raise RuntimeError("idempotency claim is not owned by caller")
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    async def complete(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            self._begin_mutation()
            try:
                cursor = self._connection.execute(
                    """
                    UPDATE idempotency_records
                    SET status = 'completed', updated_at = ?
                    WHERE scope = ? AND record_key = ? AND owner_token IS ?
                    """,
                    (datetime.now(UTC).isoformat(), scope, key, owner_token),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("idempotency claim is not owned by caller")
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    async def release(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            self._begin_mutation()
            try:
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
            except BaseException:
                self._connection.rollback()
                raise


def initialize_request_correlation_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS request_route_correlations (
            correlation_id TEXT NOT NULL PRIMARY KEY,
            application_instance_id TEXT NOT NULL,
            native_request_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            channel_instance_id TEXT NOT NULL,
            native_conversation_id TEXT NOT NULL,
            delivery_id TEXT NOT NULL,
            response_shape_json TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT,
            UNIQUE (
                application_instance_id,
                native_request_id,
                channel_instance_id,
                native_conversation_id
            )
        );
        CREATE INDEX IF NOT EXISTS request_route_correlations_request_ref
            ON request_route_correlations (
                application_instance_id,
                native_request_id
            );
        """
    )


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
        CREATE TABLE IF NOT EXISTS gateway_schema_metadata (
            metadata_key TEXT NOT NULL PRIMARY KEY,
            metadata_value TEXT NOT NULL
        );
        """
    )
    _migrate_delivery_receipt_detail_storage(connection)


def _migrate_delivery_receipt_detail_storage(connection: sqlite3.Connection) -> None:
    migration_key = "delivery_receipt_detail_storage"
    completed_value = "redacted-v2-physically-scrubbed"
    marker = connection.execute(
        "SELECT metadata_value FROM gateway_schema_metadata WHERE metadata_key = ?",
        (migration_key,),
    ).fetchone()
    if marker is not None and str(marker[0]) == completed_value:
        return
    connection.execute(
        "DELETE FROM gateway_schema_metadata WHERE metadata_key = ?",
        (migration_key,),
    )
    connection.execute("PRAGMA secure_delete = ON")
    rows = connection.execute(
        "SELECT destination_delivery_id, receipt_json, error FROM delivery_submission_destinations"
    ).fetchall()
    for row in rows:
        receipt_json = _sanitize_legacy_delivery_receipt_json(row["receipt_json"])
        if receipt_json != row["receipt_json"] or row["error"] is not None:
            connection.execute(
                "UPDATE delivery_submission_destinations "
                "SET receipt_json = ?, error = NULL "
                "WHERE destination_delivery_id = ?",
                (receipt_json, row["destination_delivery_id"]),
            )
    connection.commit()
    _require_truncated_wal(connection)
    connection.execute("VACUUM")
    _require_truncated_wal(connection)
    connection.execute(
        """
        INSERT INTO gateway_schema_metadata (metadata_key, metadata_value)
        VALUES (?, ?)
        ON CONFLICT(metadata_key) DO UPDATE SET metadata_value = excluded.metadata_value
        """,
        (migration_key, completed_value),
    )
    connection.commit()


def _require_truncated_wal(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if result is None or len(result) != 3:
        raise RuntimeError("SQLite did not report WAL checkpoint completion")
    busy, log_frames, checkpointed_frames = (int(result[index]) for index in range(3))
    if busy != 0 or (log_frames, checkpointed_frames) not in {(0, 0), (-1, -1)}:
        raise RuntimeError("SQLite WAL remains pinned; legacy detail scrub is incomplete")


def _sanitize_legacy_delivery_receipt_json(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    try:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            return None
        payload["detail"] = None
        for collection_name in ("items", "segments"):
            collection = payload.get(collection_name)
            if not isinstance(collection, list):
                return None
            for member in collection:
                if not isinstance(member, dict):
                    return None
                member["detail"] = None
        receipt = row_mapping.decode_receipt(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        if receipt is None:
            return None
        validate_delivery_receipt(receipt)
        return row_mapping.encode_receipt(receipt)
    except (TypeError, ValueError):
        return None


def _append_request_selectors(
    clauses: list[str],
    parameters: list[object],
    *,
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
) -> None:
    if request_ref is not None:
        clauses.extend(
            (
                "application_instance_id = ?",
                "native_request_id = ?",
            )
        )
        parameters.extend(
            (
                request_ref.application_ref.application_instance_id,
                request_ref.native_request_id,
            )
        )
    if thread_ref is not None:
        clauses.extend(
            (
                "application_instance_id = ?",
                "project_id = ?",
                "thread_id = ?",
            )
        )
        parameters.extend(row_mapping.thread_storage_key(thread_ref))
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


def _write_correlation(
    connection: sqlite3.Connection,
    correlation: RequestRouteCorrelation,
) -> None:
    connection.execute(
        """
        INSERT INTO request_route_correlations (
            correlation_id,
            application_instance_id,
            native_request_id,
            project_id,
            thread_id,
            turn_id,
            channel_instance_id,
            native_conversation_id,
            delivery_id,
            response_shape_json,
            state,
            created_at,
            updated_at,
            expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(correlation_id)
        DO UPDATE SET
            delivery_id = excluded.delivery_id,
            response_shape_json = excluded.response_shape_json,
            state = excluded.state,
            updated_at = excluded.updated_at,
            expires_at = excluded.expires_at
        """,
        row_mapping.request_correlation_to_row(correlation),
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

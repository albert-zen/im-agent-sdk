"""Executable golden path for the neutral reference consumer."""

from __future__ import annotations

import asyncio
import base64
import bz2
import gzip
import hashlib
import json
import lzma
import math
import os
import sqlite3
import stat
import zlib
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar

from imagent import Failed, ProjectionPolicy, SQLiteGatewayStore, Succeeded
from imagent.applications.contract import ProjectRef, ThreadRef
from imagent.applications.requests import ApprovalResponse
from imagent.gateway.delivery import (
    DeliveryIntent,
    DeliveryPrincipal,
    DeliverySubmissionState,
    ScopedDeliveryAuthorizer,
    ThreadRouteDeliveryTarget,
)
from imagent.interaction.channels import DeliveryReceiptStatus
from imagent.interaction.media import AttachmentContent, AttachmentHandle, LocalPath, RemoteUrl
from imagent.interaction.messages import ConversationRef, OutboundMessage, TextContent
from imagent.interaction.operations import OperationErrorCode

from .gateway import ReferenceArtifactLedger, ReferenceConsumer, build_reference_consumer
from .interaction import ReferenceConversation

TRef = TypeVar("TRef", ProjectRef, ThreadRef)

_SQLITE_INSPECTION_MAX_FILES = 8
_SQLITE_INSPECTION_MAX_FILE_BYTES = 8 * 1024 * 1024
_SQLITE_TEXT_VALUE_MAX_BYTES = 16 * 1024
_SQLITE_JSON_MAX_DEPTH = 8
_SQLITE_JSON_MAX_ITEMS = 256

_SQLiteSchemaObjectKey = tuple[str, str, str]

_SQLiteColumn = tuple[str, str, int, int]
_SQLITE_SCHEMA: dict[str, tuple[_SQLiteColumn, ...]] = {
    "conversation_binding_generations": (
        ("channel_instance_id", "TEXT", 1, 1),
        ("native_conversation_id", "TEXT", 1, 2),
        ("generation", "INTEGER", 1, 0),
    ),
    "conversation_bindings": (
        ("channel_instance_id", "TEXT", 1, 1),
        ("native_conversation_id", "TEXT", 1, 2),
        ("application_instance_id", "TEXT", 0, 0),
        ("project_id", "TEXT", 0, 0),
        ("thread_id", "TEXT", 0, 0),
        ("generation", "INTEGER", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "delivery_submission_destinations": (
        ("root_submission_id", "TEXT", 1, 0),
        ("destination_delivery_id", "TEXT", 1, 1),
        ("channel_instance_id", "TEXT", 1, 0),
        ("native_conversation_id", "TEXT", 1, 0),
        ("application_instance_id", "TEXT", 1, 0),
        ("project_id", "TEXT", 1, 0),
        ("thread_id", "TEXT", 1, 0),
        ("route_id", "TEXT", 1, 0),
        ("route_updated_at", "TEXT", 0, 0),
        ("reply_to_message_id", "TEXT", 0, 0),
        ("state", "TEXT", 1, 0),
        ("receipt_json", "TEXT", 0, 0),
        ("error", "TEXT", 0, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "delivery_submissions": (
        ("submission_id", "TEXT", 1, 1),
        ("delivery_id", "TEXT", 1, 0),
        ("origin", "TEXT", 1, 0),
        ("principal_id", "TEXT", 1, 0),
        ("target_fingerprint", "TEXT", 1, 0),
        ("payload_fingerprint", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "gateway_effect_receipts": (
        ("action_key", "TEXT", 1, 1),
        ("gateway_id", "TEXT", 1, 0),
        ("action_kind", "TEXT", 1, 0),
        ("payload_fingerprint", "TEXT", 1, 0),
        ("category", "TEXT", 1, 0),
        ("phase", "TEXT", 1, 0),
        ("native_phase_id", "TEXT", 0, 0),
        ("binding_generation", "INTEGER", 0, 0),
        ("outcome_json", "TEXT", 0, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "gateway_namespace": (
        ("singleton", "INTEGER", 1, 1),
        ("gateway_id", "TEXT", 1, 0),
    ),
    "gateway_runtime_lease": (
        ("singleton", "INTEGER", 1, 1),
        ("owner_token", "TEXT", 1, 0),
        ("epoch", "INTEGER", 1, 0),
        ("expires_at", "REAL", 1, 0),
    ),
    "gateway_schema_metadata": (
        ("metadata_key", "TEXT", 1, 1),
        ("metadata_value", "TEXT", 1, 0),
    ),
    "gateway_workspace_identities": (
        ("application_instance_id", "TEXT", 1, 1),
        ("project_id", "TEXT", 1, 2),
        ("root_fingerprint", "TEXT", 1, 0),
    ),
    "idempotency_records": (
        ("scope", "TEXT", 1, 1),
        ("record_key", "TEXT", 1, 2),
        ("status", "TEXT", 1, 0),
        ("owner_token", "TEXT", 0, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "request_route_correlations": (
        ("correlation_id", "TEXT", 1, 1),
        ("application_instance_id", "TEXT", 1, 0),
        ("native_request_id", "TEXT", 1, 0),
        ("project_id", "TEXT", 1, 0),
        ("thread_id", "TEXT", 1, 0),
        ("turn_id", "TEXT", 1, 0),
        ("channel_instance_id", "TEXT", 1, 0),
        ("native_conversation_id", "TEXT", 1, 0),
        ("delivery_id", "TEXT", 1, 0),
        ("response_shape_json", "TEXT", 1, 0),
        ("state", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
        ("expires_at", "TEXT", 0, 0),
    ),
    "thread_projection_routes": (
        ("route_id", "TEXT", 1, 1),
        ("application_instance_id", "TEXT", 1, 0),
        ("project_id", "TEXT", 1, 0),
        ("thread_id", "TEXT", 1, 0),
        ("channel_instance_id", "TEXT", 1, 0),
        ("native_conversation_id", "TEXT", 1, 0),
        ("reply_to_message_id", "TEXT", 0, 0),
        ("checkpoint_agent_item_id", "TEXT", 0, 0),
        ("checkpointed_at", "TEXT", 0, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "turn_reply_correlations": (
        ("correlation_id", "TEXT", 1, 1),
        ("application_instance_id", "TEXT", 1, 0),
        ("project_id", "TEXT", 1, 0),
        ("thread_id", "TEXT", 1, 0),
        ("turn_id", "TEXT", 1, 0),
        ("client_message_id", "TEXT", 1, 0),
        ("channel_instance_id", "TEXT", 1, 0),
        ("native_conversation_id", "TEXT", 1, 0),
        ("reply_to_message_id", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ),
}
_SQLITE_FINAL_ROW_COUNTS = {
    "conversation_binding_generations": 2,
    "conversation_bindings": 2,
    "delivery_submission_destinations": 39,
    "delivery_submissions": 30,
    "gateway_effect_receipts": 13,
    "gateway_namespace": 1,
    "gateway_runtime_lease": 1,
    "gateway_schema_metadata": 1,
    "gateway_workspace_identities": 0,
    "idempotency_records": 29,
    "request_route_correlations": 4,
    "thread_projection_routes": 4,
    "turn_reply_correlations": 1,
}
_SQLITE_JSON_COLUMNS = frozenset(
    {
        ("delivery_submission_destinations", "receipt_json"),
        ("gateway_effect_receipts", "outcome_json"),
        ("request_route_correlations", "response_shape_json"),
    }
)
_SQLITE_TABLE_SQL_DIGESTS = {
    "conversation_binding_generations": (
        "53236f41d23b16d1790a11a9dcc778cd86e1c2abf52c5ebdf5f3b5db87e1f83e"
    ),
    "conversation_bindings": "b7e0683549f191ca98f5020b204eb3d4cd7817490f6eaf388dd10f8f17ac5f03",
    "delivery_submission_destinations": (
        "9a175788682fb13cee0730654e7ac88be668bf0b1dd455434a65ecf1b9d7bc4c"
    ),
    "delivery_submissions": "978f5ff466ea39bd72c07bd38225c10efb31676337958c46bffc76386a443fc3",
    "gateway_effect_receipts": "f0e610b815eb5cd9fc59acf1bce719aaa683b48bd0f7d545d5781345efeef6aa",
    "gateway_namespace": "ed339c07b144fca178313cbf6234e677e8defd954a54337c225c35c867456f91",
    "gateway_runtime_lease": "173665e5dd6df99b63f4e8061499e311052d6bacb1d736fde1bf4e261f5d3ada",
    "gateway_schema_metadata": "dfd88fedbdc1b320404a3abf93b7d03596532b64563a61f85f594041cce307ea",
    "gateway_workspace_identities": (
        "bd386fa4861cd1723d57f91aa8ba2f23eccca76e88a38d390f7a889ef2c95f5f"
    ),
    "idempotency_records": "ead8a5757ace8d30c65fc77537530d9bacaadd9cc60dfc13fb8ba385ec3ab719",
    "request_route_correlations": (
        "8499cf69cb36f55ca902f5d7ffa1c1ead16790fa5317976d32a8bdb7412e55d2"
    ),
    "thread_projection_routes": "6f7b09af02ed0a0fadde23f7ae21d109c6f51a6b6580ebf3bdb34abefc1c71f0",
    "turn_reply_correlations": "704890148af16441e2cba877e3945bc533b0027e43b3d2488d0ce5a34cd70944",
}
_SQLITE_EXPLICIT_INDEX_SQL_DIGESTS = {
    ("delivery_destinations_root", "delivery_submission_destinations"): (
        "9a3f9b434a29d3210b1edda70c70fe77d98a5bf2f6e46a49f92c825c1eef86f3"
    ),
    ("request_route_correlations_request_ref", "request_route_correlations"): (
        "b9c51440bec5296c822b874ab6d467e83f2f92c85c9e880c8327ea52bb62ce7e"
    ),
}
_SQLITE_AUTOINDEXES = (
    ("sqlite_autoindex_conversation_binding_generations_1", "conversation_binding_generations"),
    ("sqlite_autoindex_conversation_bindings_1", "conversation_bindings"),
    ("sqlite_autoindex_delivery_submission_destinations_1", "delivery_submission_destinations"),
    ("sqlite_autoindex_delivery_submission_destinations_2", "delivery_submission_destinations"),
    ("sqlite_autoindex_delivery_submissions_1", "delivery_submissions"),
    ("sqlite_autoindex_gateway_effect_receipts_1", "gateway_effect_receipts"),
    ("sqlite_autoindex_gateway_schema_metadata_1", "gateway_schema_metadata"),
    ("sqlite_autoindex_gateway_workspace_identities_1", "gateway_workspace_identities"),
    ("sqlite_autoindex_idempotency_records_1", "idempotency_records"),
    ("sqlite_autoindex_request_route_correlations_1", "request_route_correlations"),
    ("sqlite_autoindex_request_route_correlations_2", "request_route_correlations"),
    ("sqlite_autoindex_thread_projection_routes_1", "thread_projection_routes"),
    ("sqlite_autoindex_thread_projection_routes_2", "thread_projection_routes"),
    ("sqlite_autoindex_turn_reply_correlations_1", "turn_reply_correlations"),
    ("sqlite_autoindex_turn_reply_correlations_2", "turn_reply_correlations"),
)
_SQLITE_FENCED_TABLES = (
    "conversation_bindings",
    "conversation_binding_generations",
    "idempotency_records",
    "thread_projection_routes",
    "turn_reply_correlations",
    "request_route_correlations",
    "delivery_submissions",
    "delivery_submission_destinations",
    "gateway_workspace_identities",
    "gateway_effect_receipts",
)


@dataclass(frozen=True, slots=True)
class _SQLiteRecoverySnapshot:
    binding_generations: tuple[tuple[str, str, int], ...]
    active_route_checkpoints: tuple[tuple[str, str, str | None], ...]
    completed_idempotency: frozenset[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class _SQLiteBridgeInspection:
    file_count: int
    table_count: int
    snapshot: _SQLiteRecoverySnapshot
    schema_and_values_allowlisted: bool


@dataclass(frozen=True, slots=True)
class _SQLiteFileIdentity:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class ReferenceReport:
    projection_policy: ProjectionPolicy
    project_ref: ProjectRef
    first_thread_ref: ThreadRef
    second_thread_ref: ThreadRef
    project_count: int
    thread_count: int
    conversation_count: int
    command_count: int
    initial_thread_conversations: tuple[ConversationRef, ...]
    shared_thread_conversations: tuple[ConversationRef, ...]
    switched_old_thread_conversations: tuple[ConversationRef, ...]
    switched_new_thread_conversations: tuple[ConversationRef, ...]
    switched_back_conversations: tuple[ConversationRef, ...]
    worker_max_active: tuple[int, ...]
    worker_subscription_calls: tuple[int, ...]
    recovered_conversations: tuple[ConversationRef, ...]
    recovered_delivery_count: int
    reconstructed_bindings: bool
    reconstructed_checkpoints: bool
    reconstructed_idempotency: bool
    reconstructed_receipts: bool
    duplicate_input_suppressed: bool
    recovery_redispatched_input: bool
    sqlite_files_inspected: int
    sqlite_table_count: int
    sqlite_bridge_state_allowlisted: bool
    diagnostics_schema_version: int
    diagnostics_size: int
    diagnostics_authoritative: bool
    adapters_stopped: bool
    active_workers_after_shutdown: int
    registry_active_after_shutdown: int
    owned_tasks_after_shutdown: int
    request_delivered_destinations: int
    request_nonrecipient_rejected: bool
    request_first_writer_won: bool
    request_duplicate_rejected: bool
    request_replay_idempotent: bool
    request_restart_pending_recovered: bool
    live_only_checkpoint_stable: bool
    recoverable_presentation_parity: bool
    proactive_destination_count: int
    proactive_routes_pinned: bool
    proactive_partial_isolated: bool
    proactive_unknown_sticky: bool
    proactive_restart_replayed: bool
    media_preflight_side_effect_free: bool
    artifact_startup_swept: bool
    artifact_cleanup_complete: bool


def _message_text(message: OutboundMessage) -> str:
    return " ".join(content.text for content in message.content if isinstance(content, TextContent))


def _destinations(messages: tuple[OutboundMessage, ...]) -> tuple[ConversationRef, ...]:
    return tuple(
        sorted(
            (message.conversation_ref for message in messages),
            key=lambda ref: (ref.channel_instance_id, ref.native_conversation_id),
        )
    )


def _required_ref(result: object, expected: type[TRef]) -> TRef:
    if not isinstance(result, Succeeded) or not isinstance(result.value.ref, expected):
        raise RuntimeError("reference workflow returned a non-success outcome")
    return result.value.ref


def _forbidden_byte_patterns(value: str) -> frozenset[bytes]:
    raw = value.encode("utf-8")
    payloads = {
        raw,
        value.encode("utf-16-le"),
        value.encode("utf-16-be"),
        zlib.compress(raw),
        gzip.compress(raw, mtime=0),
        bz2.compress(raw),
        lzma.compress(raw),
    }
    patterns: set[bytes] = set()
    for payload in payloads:
        patterns.update(
            {
                payload,
                base64.b64encode(payload),
                base64.urlsafe_b64encode(payload),
                base64.b85encode(payload),
                payload.hex().encode("ascii"),
                payload.hex().upper().encode("ascii"),
            }
        )
    return frozenset(patterns)


def _file_identity(file_stat: os.stat_result) -> _SQLiteFileIdentity:
    return _SQLiteFileIdentity(
        device=file_stat.st_dev,
        inode=file_stat.st_ino,
        mode=file_stat.st_mode,
        links=file_stat.st_nlink,
        size=file_stat.st_size,
        modified_ns=file_stat.st_mtime_ns,
        changed_ns=file_stat.st_ctime_ns,
    )


def _snapshot_sqlite_files(database_path: Path) -> tuple[tuple[Path, _SQLiteFileIdentity], ...]:
    allowed_names = {
        database_path.name,
        f"{database_path.name}-journal",
        f"{database_path.name}-shm",
        f"{database_path.name}-wal",
    }
    entries: list[tuple[Path, _SQLiteFileIdentity]] = []
    with os.scandir(database_path.parent) as directory:
        for entry in directory:
            if not entry.name.startswith(database_path.name):
                continue
            if entry.name not in allowed_names:
                raise AssertionError("SQLite inspection found an unexpected sidecar name")
            entry_stat = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(entry_stat.st_mode):
                raise AssertionError("SQLite inspection target is not a regular file")
            entries.append((database_path.parent / entry.name, _file_identity(entry_stat)))
    entries.sort(key=lambda item: item[0].name)
    if not entries or entries[0][0] != database_path:
        raise AssertionError("SQLite recovery database was not created")
    if len(entries) > _SQLITE_INSPECTION_MAX_FILES:
        raise AssertionError("SQLite recovery produced too many sidecar files")
    return tuple(entries)


def _semantic_snapshot_identity(
    snapshot: tuple[tuple[Path, _SQLiteFileIdentity], ...],
) -> tuple[tuple[str, int, int, int, int, int, int, int], ...]:
    return tuple(
        (
            path.name,
            identity.device,
            identity.inode,
            identity.mode,
            identity.links,
            identity.size,
            0 if path.name.endswith("-shm") else identity.modified_ns,
            0 if path.name.endswith("-shm") else identity.changed_ns,
        )
        for path, identity in snapshot
    )


def _read_bounded_descriptor(path: Path, *, expected: _SQLiteFileIdentity) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AssertionError("SQLite inspection path changed before bounded read") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AssertionError("SQLite inspection target is not a regular file")
        if _file_identity(before) != expected:
            raise AssertionError("SQLite inspection path identity changed before bounded read")
        if before.st_size > _SQLITE_INSPECTION_MAX_FILE_BYTES:
            raise AssertionError("SQLite bridge persistence exceeded the inspection bound")
        chunks: list[bytes] = []
        remaining = _SQLITE_INSPECTION_MAX_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        persisted = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(persisted) > _SQLITE_INSPECTION_MAX_FILE_BYTES:
            raise AssertionError("SQLite bridge persistence exceeded the inspection bound")
        after_identity = _file_identity(after)
        if expected != after_identity or len(persisted) != after.st_size:
            raise AssertionError("SQLite inspection target changed during bounded read")
        try:
            path_identity = _file_identity(os.stat(path, follow_symlinks=False))
        except OSError as error:
            message = "SQLite inspection path disappeared during bounded read"
            raise AssertionError(message) from error
        if path_identity != after_identity:
            raise AssertionError("SQLite inspection path was replaced during bounded read")
        return persisted
    finally:
        os.close(descriptor)


def _inspect_sqlite_files(
    database_path: Path,
    *,
    forbidden_values: tuple[str, ...],
) -> int:
    """Inspect a stable bounded database and sidecar entry set."""

    before = _snapshot_sqlite_files(database_path)
    persisted_files = tuple(
        _read_bounded_descriptor(path, expected=identity) for path, identity in before
    )
    after = _snapshot_sqlite_files(database_path)
    if before != after:
        raise AssertionError("SQLite sidecar set or identity changed during bounded inspection")
    persisted_combined = b"".join(persisted_files)
    for value in forbidden_values:
        for pattern in _forbidden_byte_patterns(value):
            if any(pattern in persisted for persisted in persisted_files):
                raise AssertionError("SQLite bridge persistence retained authority-owned data")
            if pattern in persisted_combined:
                raise AssertionError("SQLite sidecars fragmented authority-owned data")
    return len(before)


def _validate_json_tree(value: object) -> tuple[str, ...]:
    text_values: list[str] = []
    item_count = 0

    def visit(current: object, depth: int) -> None:
        nonlocal item_count
        item_count += 1
        if item_count > _SQLITE_JSON_MAX_ITEMS or depth > _SQLITE_JSON_MAX_DEPTH:
            raise AssertionError("SQLite JSON value exceeded its structural bound")
        if current is None or isinstance(current, bool):
            return
        if isinstance(current, str):
            if len(current.encode("utf-8")) > _SQLITE_TEXT_VALUE_MAX_BYTES:
                raise AssertionError("SQLite JSON text exceeded its value bound")
            text_values.append(current)
            return
        if isinstance(current, int) and not isinstance(current, bool):
            if not -(2**63) <= current <= 2**63 - 1:
                raise AssertionError("SQLite JSON integer exceeded its value bound")
            return
        if isinstance(current, float):
            if not math.isfinite(current):
                raise AssertionError("SQLite JSON number must be finite")
            return
        if isinstance(current, list):
            for item in current:
                visit(item, depth + 1)
            return
        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str) or not key:
                    raise AssertionError("SQLite JSON keys must be bounded text")
                if len(key.encode("utf-8")) > _SQLITE_TEXT_VALUE_MAX_BYTES:
                    raise AssertionError("SQLite JSON key exceeded its value bound")
                text_values.append(key)
                visit(item, depth + 1)
            return
        raise AssertionError("SQLite JSON contained an unsupported value type")

    visit(value, 0)
    return tuple(text_values)


def _assembled_from_fragments(target: str, values: tuple[str, ...]) -> bool:
    candidate_counts = Counter(
        value for value in values if 0 < len(value) < len(target) and value in target
    )
    candidates = tuple(sorted(candidate_counts))
    initial_counts = tuple(candidate_counts[value] for value in candidates)
    explored_states = 0

    @cache
    def assemble(position: int, remaining: tuple[int, ...], parts: int) -> bool:
        nonlocal explored_states
        explored_states += 1
        if explored_states > 65_536:
            raise AssertionError("SQLite fragment inspection exceeded its search bound")
        if position == len(target):
            return parts >= 2
        for index, value in enumerate(candidates):
            if remaining[index] and target.startswith(value, position):
                next_remaining = list(remaining)
                next_remaining[index] -= 1
                if assemble(
                    position + len(value),
                    tuple(next_remaining),
                    min(parts + 1, 2),
                ):
                    return True
        return False

    return assemble(0, initial_counts, 0)


def _read_sqlite_recovery_snapshot(
    connection: sqlite3.Connection,
) -> _SQLiteRecoverySnapshot:
    binding_generations = tuple(
        (str(row[0]), str(row[1]), int(row[2]))
        for row in connection.execute(
            "SELECT channel_instance_id, native_conversation_id, generation "
            "FROM conversation_bindings ORDER BY channel_instance_id, native_conversation_id"
        )
    )
    active_route_checkpoints = tuple(
        (str(row[0]), str(row[1]), None if row[2] is None else str(row[2]))
        for row in connection.execute(
            "SELECT r.channel_instance_id, r.native_conversation_id, "
            "r.checkpoint_agent_item_id FROM thread_projection_routes AS r "
            "JOIN conversation_bindings AS b "
            "ON b.channel_instance_id = r.channel_instance_id "
            "AND b.native_conversation_id = r.native_conversation_id "
            "AND b.application_instance_id = r.application_instance_id "
            "AND b.project_id = r.project_id AND b.thread_id = r.thread_id "
            "ORDER BY r.channel_instance_id, r.native_conversation_id"
        )
    )
    completed_idempotency = frozenset(
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT scope, record_key FROM idempotency_records "
            "WHERE status = 'completed' ORDER BY scope, record_key"
        )
    )
    return _SQLiteRecoverySnapshot(
        binding_generations=binding_generations,
        active_route_checkpoints=active_route_checkpoints,
        completed_idempotency=completed_idempotency,
    )


def _read_current_sqlite_recovery_snapshot(database_path: Path) -> _SQLiteRecoverySnapshot:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        return _read_sqlite_recovery_snapshot(connection)
    finally:
        connection.close()


def _sql_digest(sql: str) -> str:
    normalized = " ".join(sql.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _expected_sqlite_schema_objects() -> dict[_SQLiteSchemaObjectKey, str | None]:
    expected: dict[_SQLiteSchemaObjectKey, str | None] = {
        ("table", table, table): digest for table, digest in _SQLITE_TABLE_SQL_DIGESTS.items()
    }
    expected.update(
        {
            ("index", index, table): digest
            for (index, table), digest in _SQLITE_EXPLICIT_INDEX_SQL_DIGESTS.items()
        }
    )
    expected.update({("index", index, table): None for index, table in _SQLITE_AUTOINDEXES})
    for table in _SQLITE_FENCED_TABLES:
        for operation in ("INSERT", "UPDATE", "DELETE"):
            trigger = f"imagent_runtime_fence_{table}_{operation.lower()}"
            definition = f"""
                CREATE TRIGGER {trigger}
                BEFORE {operation} ON {table}
                WHEN imagent_store_maintenance() = 0 AND NOT EXISTS (
                    SELECT 1
                    FROM gateway_namespace AS n
                    JOIN gateway_runtime_lease AS l
                      ON l.singleton = n.singleton
                    WHERE n.singleton = 1
                      AND n.gateway_id = imagent_runtime_gateway_id()
                      AND l.owner_token = imagent_runtime_owner_token()
                      AND l.epoch = imagent_runtime_epoch()
                      AND l.expires_at >
                          ((julianday('now') - 2440587.5) * 86400.0)
                )
                BEGIN
                    SELECT RAISE(ABORT, 'imagent stale runtime fence');
                END
            """
            expected[("trigger", trigger, table)] = _sql_digest(definition)
    return expected


_SQLITE_SCHEMA_OBJECTS = _expected_sqlite_schema_objects()


def _validate_sqlite_schema_objects(connection: sqlite3.Connection) -> None:
    actual: dict[_SQLiteSchemaObjectKey, str | None] = {}
    for object_type, name, table, definition in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
    ):
        key = (str(object_type), str(name), str(table))
        if key in actual:
            raise AssertionError("SQLite schema contains a duplicate object identity")
        if definition is not None and not isinstance(definition, str):
            raise AssertionError("SQLite schema definition is not text")
        actual[key] = None if definition is None else _sql_digest(definition)
    if actual != _SQLITE_SCHEMA_OBJECTS:
        raise AssertionError("SQLite schema objects or definitions changed")


def _validate_sqlite_schema_and_values(
    connection: sqlite3.Connection,
    *,
    forbidden_values: tuple[str, ...],
) -> tuple[tuple[str, ...], _SQLiteRecoverySnapshot]:
    _validate_sqlite_schema_objects(connection)
    tables = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )
    if tables != tuple(sorted(_SQLITE_SCHEMA)):
        raise AssertionError("SQLite bridge schema contains unknown or missing tables")

    all_text_values: list[str] = []
    rows_by_table: dict[str, tuple[tuple[object, ...], ...]] = {}
    for table in tables:
        expected_columns = _SQLITE_SCHEMA[table]
        actual_columns = tuple(
            (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        )
        if actual_columns != expected_columns:
            raise AssertionError("SQLite bridge table columns or types changed")
        rows = tuple(
            tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
        )
        rows_by_table[table] = rows
        if len(rows) != _SQLITE_FINAL_ROW_COUNTS[table]:
            raise AssertionError(f"SQLite bridge row cardinality changed for {table}: {len(rows)}")
        for row in rows:
            for value, (column, declared_type, not_null, _primary_key) in zip(
                row,
                expected_columns,
                strict=True,
            ):
                if value is None:
                    if not_null:
                        raise AssertionError("SQLite required bridge value is NULL")
                    continue
                if declared_type == "TEXT":
                    if not isinstance(value, str):
                        raise AssertionError("SQLite TEXT column retained a non-text value")
                    if len(value.encode("utf-8")) > _SQLITE_TEXT_VALUE_MAX_BYTES:
                        raise AssertionError("SQLite text value exceeded its bound")
                    all_text_values.append(value)
                    if column.endswith("_fingerprint"):
                        prefix, separator, digest = value.rpartition(":")
                        if not separator:
                            prefix, digest = "", value
                        if prefix not in {
                            "",
                            "imagent:delivery-payload:sha256",
                            "imagent:delivery-target:sha256",
                        } or (
                            len(digest) != 64
                            or digest != digest.lower()
                            or any(character not in "0123456789abcdef" for character in digest)
                        ):
                            raise AssertionError("SQLite fingerprint value is malformed")
                    if column.endswith("_at"):
                        try:
                            datetime.fromisoformat(value)
                        except ValueError as error:
                            raise AssertionError("SQLite timestamp value is malformed") from error
                    if (table, column) in _SQLITE_JSON_COLUMNS:
                        try:
                            decoded = json.loads(value)
                        except json.JSONDecodeError as error:
                            raise AssertionError("SQLite JSON value is malformed") from error
                        all_text_values.extend(_validate_json_tree(decoded))
                elif declared_type == "INTEGER":
                    if not isinstance(value, int) or isinstance(value, bool):
                        raise AssertionError("SQLite INTEGER column retained another type")
                    if not -(2**63) <= value <= 2**63 - 1:
                        raise AssertionError("SQLite integer value exceeded its bound")
                elif declared_type == "REAL":
                    if not isinstance(value, float) or not math.isfinite(value):
                        raise AssertionError("SQLite REAL column retained another type")
                else:
                    raise AssertionError("SQLite schema declared an unsupported type")

    if rows_by_table["gateway_namespace"] != ((1, "reference"),):
        raise AssertionError("SQLite Gateway namespace changed")
    if rows_by_table["gateway_schema_metadata"] != (
        ("delivery_receipt_detail_storage", "redacted-v2-physically-scrubbed"),
    ):
        raise AssertionError("SQLite schema metadata changed")
    if {row[2] for row in rows_by_table["idempotency_records"]} != {"completed"}:
        raise AssertionError("SQLite idempotency evidence is not terminal")
    if {row[10] for row in rows_by_table["delivery_submission_destinations"]} != {
        "accepted",
        "rejected",
        "unknown",
    }:
        raise AssertionError("SQLite delivery destination state changed")
    if {row[2] for row in rows_by_table["delivery_submissions"]} != {
        "external",
        "gateway_internal",
    }:
        raise AssertionError("SQLite delivery origin changed")
    if {row[5] for row in rows_by_table["gateway_effect_receipts"]} != {"terminal"}:
        raise AssertionError("SQLite effect receipt is not terminal")

    text_values = tuple(all_text_values)
    for forbidden in forbidden_values:
        text_patterns = tuple(
            pattern.decode("ascii")
            for pattern in _forbidden_byte_patterns(forbidden)
            if all(byte < 128 for byte in pattern)
        )
        for pattern in (forbidden, *text_patterns):
            if any(pattern in value for value in text_values):
                raise AssertionError("SQLite bridge values retained authority-owned data")
            if _assembled_from_fragments(pattern, text_values):
                raise AssertionError("SQLite rows fragmented authority-owned data")
    return tables, _read_sqlite_recovery_snapshot(connection)


def _inspect_sqlite_bridge_state(
    database_path: Path,
    *,
    forbidden_values: tuple[str, ...],
) -> _SQLiteBridgeInspection:
    """Inspect one stable WAL-aware bridge snapshot and its bounded files."""

    outer_before = _snapshot_sqlite_files(database_path)
    outer_names = {path.name for path, _identity in outer_before}
    wal_name = f"{database_path.name}-wal"
    shm_name = f"{database_path.name}-shm"
    if wal_name in outer_names and shm_name not in outer_names:
        raise AssertionError("SQLite WAL inspection requires its stable shared-memory sidecar")
    uri = f"file:{database_path}?mode=ro"
    if wal_name not in outer_names:
        uri = f"{uri}&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        connection.execute("SELECT schema_version FROM pragma_schema_version").fetchone()
        if _semantic_snapshot_identity(outer_before) != _semantic_snapshot_identity(
            _snapshot_sqlite_files(database_path)
        ):
            raise AssertionError("SQLite files changed while establishing the read snapshot")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise AssertionError("SQLite recovery database failed its integrity check")
        tables, snapshot = _validate_sqlite_schema_and_values(
            connection,
            forbidden_values=forbidden_values,
        )
        database_file_count = _inspect_sqlite_files(
            database_path,
            forbidden_values=forbidden_values,
        )
        if _semantic_snapshot_identity(outer_before) != _semantic_snapshot_identity(
            _snapshot_sqlite_files(database_path)
        ):
            raise AssertionError("SQLite files changed across the WAL-aware inspection snapshot")
    finally:
        connection.rollback()
        connection.close()
    return _SQLiteBridgeInspection(
        file_count=database_file_count,
        table_count=len(tables),
        snapshot=snapshot,
        schema_and_values_allowlisted=True,
    )


async def _ordinary_round_trip(
    consumer: ReferenceConsumer,
    conversation: ReferenceConversation,
    *,
    message_id: str,
    text: str,
    expected_count: int,
) -> tuple[OutboundMessage, ...]:
    after = len(consumer.channel.sent)
    await conversation.receive_text(message_id=message_id, text=text)
    await consumer.channel.wait_for_text(
        f"Neutral response: {text}",
        after=after,
        count=expected_count,
    )
    await asyncio.sleep(0)
    delivered = tuple(
        message
        for message in consumer.channel.sent[after:]
        if _message_text(message) == f"Neutral response: {text}"
    )
    if len(delivered) != expected_count:
        raise AssertionError("ordinary input produced duplicate or missing deliveries")
    return delivered


async def _wait_for_messages(
    consumer: ReferenceConsumer,
    *,
    after: int,
    count: int,
) -> tuple[OutboundMessage, ...]:
    async with asyncio.timeout(2.0):
        while len(consumer.channel.sent) < after + count:
            await asyncio.sleep(0)
    return consumer.channel.sent[after : after + count]


async def run_reference_consumer(reference_workspace: str) -> ReferenceReport:
    """Run the same bounded public entry point used by source and wheel tests."""

    database_path = Path(reference_workspace) / "reference-gateway.sqlite3"
    artifact_root = Path(reference_workspace) / "consumer-artifacts"
    startup_ledger = ReferenceArtifactLedger(artifact_root, max_leases=8)
    startup_ledger.stage(
        "restart-orphan",
        b"consumer-owned orphan",
        expected_destinations=1,
    )
    artifact_ledger = ReferenceArtifactLedger(artifact_root, max_leases=8)
    artifact_startup_swept = artifact_ledger.sweep() == 1
    cancelled = artifact_ledger.stage(
        "cancelled-before-submit",
        b"cancelled consumer bytes",
        expected_destinations=1,
    )
    artifact_ledger.abandon(cancelled.attachment_id)
    failed = artifact_ledger.stage(
        "failed-before-submit",
        b"failed consumer bytes",
        expected_destinations=1,
    )
    artifact_ledger.abandon(failed.attachment_id)
    authorizer = ScopedDeliveryAuthorizer(max_principals=4)
    consumer = build_reference_consumer(
        store=SQLiteGatewayStore(database_path, max_effect_receipts=64),
        delivery_authorizer=authorizer,
        delivery_outcome_observer=artifact_ledger,
        trusted_attachment_root=artifact_root,
    )
    gateway = consumer.gateway
    application = consumer.application
    conversation_a = consumer.channel.conversation(
        "conversation-a",
        authenticated_actor="reference-user-a",
    )
    conversation_b = consumer.channel.conversation(
        "conversation-b",
        authenticated_actor="reference-user-b",
    )
    tasks_before = set(asyncio.all_tasks())

    command_count = 0
    diagnostics_size = 0
    diagnostics_schema_version = 0
    diagnostics_authoritative = True
    project_count = 0
    thread_count = 0
    initial: tuple[OutboundMessage, ...] = ()
    shared: tuple[OutboundMessage, ...] = ()
    switched_old: tuple[OutboundMessage, ...] = ()
    switched_new: tuple[OutboundMessage, ...] = ()
    switched_back: tuple[OutboundMessage, ...] = ()
    recovered: tuple[OutboundMessage, ...] = ()
    binding_generations_before_restart: tuple[tuple[ConversationRef, int], ...] = ()
    recovery_snapshot_before_restart: _SQLiteRecoverySnapshot | None = None
    reconstructed_bindings = False
    reconstructed_checkpoints = False
    reconstructed_idempotency = False
    reconstructed_receipts = False
    duplicate_input_suppressed = False
    recovery_redispatched_input = True
    sqlite_files_inspected = 0
    sqlite_table_count = 0
    sqlite_bridge_state_allowlisted = False
    request_delivered_destinations = 0
    request_nonrecipient_rejected = False
    request_first_writer_won = False
    request_duplicate_rejected = False
    request_replay_idempotent = False
    request_restart_pending_recovered = False
    live_only_checkpoint_stable = False
    recoverable_presentation_parity = False
    proactive_destination_count = 0
    proactive_routes_pinned = False
    proactive_partial_isolated = False
    proactive_unknown_sticky = False
    proactive_restart_replayed = False
    media_preflight_side_effect_free = False
    artifact_cleanup_complete = False

    async with gateway:
        actions_a = gateway.actions(
            conversation_a.ref,
            actor=conversation_a.authenticated_actor,
        )
        actions_b = gateway.actions(
            conversation_b.ref,
            actor=conversation_b.authenticated_actor,
        )

        discovered = await actions_a.list_applications()
        if not isinstance(discovered, Succeeded) or len(discovered.value) != 1:
            raise RuntimeError("reference Application discovery did not return one result")
        application_ref = discovered.value[0].ref
        application_actions = gateway.application(
            application_ref,
            principal=conversation_a.authenticated_actor,
        )
        application_read = await application_actions.get_application()
        if (
            not isinstance(application_read, Succeeded)
            or application_read.value.ref != application_ref
            or application_ref != application.ref
        ):
            raise RuntimeError("reference Application discovery identity was not stable")

        project_result = await actions_a.create_and_select_project(
            application_ref,
            cwd=reference_workspace,
            display_name="Reference workspace",
            action_id="reference:create-project:1",
        )
        project_ref = _required_ref(project_result, ProjectRef)
        replayed_project = _required_ref(
            await actions_a.create_and_select_project(
                application.ref,
                cwd=reference_workspace,
                display_name="Reference workspace",
                action_id="reference:create-project:1",
            ),
            ProjectRef,
        )
        if replayed_project != project_ref:
            raise AssertionError("stable Project workflow identity did not replay")

        first_thread_result = await actions_a.create_and_bind_thread(
            project_ref,
            title="Shared reference Thread",
            action_id="reference:create-thread:1",
        )
        first_thread_ref = _required_ref(first_thread_result, ThreadRef)
        replayed_thread = _required_ref(
            await actions_a.create_and_bind_thread(
                project_ref,
                title="Shared reference Thread",
                action_id="reference:create-thread:1",
            ),
            ThreadRef,
        )
        if replayed_thread != first_thread_ref:
            raise AssertionError("stable Thread workflow identity did not replay")

        initial = await _ordinary_round_trip(
            consumer,
            conversation_a,
            message_id="reference:message:initial",
            text="initial-thread-turn",
            expected_count=1,
        )
        if _destinations(initial) != (conversation_a.ref,):
            raise AssertionError("the initial Thread output must reach only Conversation A")

        selected_b = await actions_b.select_project(
            project_ref,
            action_id="reference:conversation-b:select-project",
        )
        if not isinstance(selected_b, Succeeded):
            raise RuntimeError("Conversation B could not select the shared Project")
        bound_b = await actions_b.bind_thread(
            first_thread_ref,
            action_id="reference:conversation-b:bind-thread",
            expected_generation=selected_b.value.binding_generation,
        )
        if not isinstance(bound_b, Succeeded):
            raise RuntimeError("Conversation B could not bind the shared Thread")

        for message_id, command in (
            ("reference:command:help", "/help"),
            ("reference:command:about", "/about"),
        ):
            before = len(consumer.channel.sent)
            await conversation_a.receive_text(message_id=message_id, text=command)
            if len(consumer.channel.sent) != before + 1:
                raise AssertionError("one command invocation must produce one bounded output")
            command_output = _message_text(consumer.channel.sent[-1])
            if not command_output or len(command_output) > 1_024:
                raise AssertionError("command output must be non-empty and bounded")
            if command == "/about" and command_output != (
                "Neutral reference consumer: Channel, Gateway, and Application composed "
                "through public SDK contracts."
            ):
                raise AssertionError("the injected read-only status service did not run")
            command_count += 1

        shared = await _ordinary_round_trip(
            consumer,
            conversation_a,
            message_id="reference:message:shared",
            text="shared-thread-turn",
            expected_count=2,
        )
        if _destinations(shared) != (conversation_a.ref, conversation_b.ref):
            raise AssertionError("both Conversations must observe the shared Thread")

        before_new = len(consumer.channel.sent)
        await conversation_a.receive_text(
            message_id="reference:command:new-thread",
            text="/new Switched reference Thread",
        )
        if len(consumer.channel.sent) != before_new + 1:
            raise AssertionError("the effectful common command must produce one output")
        if not _message_text(consumer.channel.sent[-1]).startswith("Created thread"):
            raise AssertionError("the effectful common workflow command did not complete")
        command_count += 1
        binding_a = await actions_a.get_binding()
        if binding_a is None or binding_a.thread_ref is None:
            raise AssertionError("the effectful command did not bind its created Thread")
        second_thread_ref = binding_a.thread_ref
        if second_thread_ref == first_thread_ref:
            raise AssertionError("the effectful command must create a distinct Thread")

        switched_old = await _ordinary_round_trip(
            consumer,
            conversation_b,
            message_id="reference:message:old-after-switch",
            text="old-thread-after-switch",
            expected_count=1,
        )
        if _destinations(switched_old) != (conversation_b.ref,):
            raise AssertionError("Thread-1 output leaked to the switched Conversation")

        switched_new = await _ordinary_round_trip(
            consumer,
            conversation_a,
            message_id="reference:message:new-after-switch",
            text="new-thread-after-switch",
            expected_count=1,
        )
        if _destinations(switched_new) != (conversation_a.ref,):
            raise AssertionError("Thread-2 output leaked to the other Conversation")

        rebound_a = await actions_a.bind_thread(
            first_thread_ref,
            action_id="reference:conversation-a:switch-back",
            expected_generation=binding_a.generation,
        )
        if not isinstance(rebound_a, Succeeded):
            raise RuntimeError("Conversation A could not switch back to Thread 1")
        switched_back = await _ordinary_round_trip(
            consumer,
            conversation_a,
            message_id="reference:message:after-switch-back",
            text="thread-one-after-switch-back",
            expected_count=2,
        )
        if _destinations(switched_back) != (conversation_a.ref, conversation_b.ref):
            raise AssertionError("switch-back did not restore the shared destinations")

        conversation_c = consumer.channel.conversation(
            "conversation-c",
            authenticated_actor="reference-user-c",
        )
        actions_c = gateway.actions(
            conversation_c.ref,
            actor=conversation_c.authenticated_actor,
        )
        before_request = len(consumer.channel.sent)
        request = await application.receive_native_approval_request(
            first_thread_ref,
            prompt="Approve the bounded reference operation?",
        )
        request_messages = await _wait_for_messages(
            consumer,
            after=before_request,
            count=2,
        )
        request_delivered_destinations = len(set(_destinations(request_messages)))
        if _destinations(request_messages) != (conversation_a.ref, conversation_b.ref):
            raise AssertionError("interactive request did not reach both active Conversations")
        for _ in range(8):
            await asyncio.sleep(0)
        calls_before_nonrecipient = application.request_response_calls
        rejected_nonrecipient = await actions_c.respond_request(
            request.request_ref,
            ApprovalResponse("approve"),
            action_id="reference:request:nonrecipient",
        )
        request_nonrecipient_rejected = bool(
            isinstance(rejected_nonrecipient, Failed)
            and rejected_nonrecipient.error.operation_error_code
            is OperationErrorCode.UNAUTHORIZED_DESTINATION
            and application.request_response_calls == calls_before_nonrecipient
        )
        if not request_nonrecipient_rejected:
            raise AssertionError("a non-recipient reached native request response work")
        accepted_response = await actions_a.respond_request(
            request.request_ref,
            ApprovalResponse("approve"),
            action_id="reference:request:accepted",
        )
        request_first_writer_won = bool(
            isinstance(accepted_response, Succeeded)
            and accepted_response.value.ref == request.request_ref
            and application.request_response_calls == calls_before_nonrecipient + 1
        )
        if not request_first_writer_won:
            raise AssertionError("the Application did not retain first-writer request truth")
        duplicate_response = await actions_b.respond_request(
            request.request_ref,
            ApprovalResponse("approve"),
            action_id="reference:request:duplicate",
        )
        request_duplicate_rejected = bool(
            isinstance(duplicate_response, Failed)
            and duplicate_response.error.operation_error_code
            in {OperationErrorCode.REQUEST_DUPLICATE, OperationErrorCode.REQUEST_RESOLVED}
            and application.request_response_calls == calls_before_nonrecipient + 1
        )
        if not request_duplicate_rejected:
            raise AssertionError("a duplicate request response reached native work")
        replayed_response = await actions_a.respond_request(
            request.request_ref,
            ApprovalResponse("approve"),
            action_id="reference:request:accepted",
        )
        request_replay_idempotent = bool(
            isinstance(replayed_response, Succeeded)
            and replayed_response.value.ref == request.request_ref
            and application.request_response_calls == calls_before_nonrecipient + 1
        )
        if not request_replay_idempotent:
            raise AssertionError("request response replay repeated native work")

        checkpoint_before_live_only = _read_current_sqlite_recovery_snapshot(database_path)
        live_only_after = len(consumer.channel.sent)
        await application.receive_native_live_activity(
            first_thread_ref,
            text="reference live-only activity",
        )
        await _wait_for_messages(
            consumer,
            after=live_only_after,
            count=2,
        )
        await asyncio.sleep(0)
        checkpoint_after_live_only = _read_current_sqlite_recovery_snapshot(database_path)
        live_only_checkpoint_stable = (
            checkpoint_after_live_only.active_route_checkpoints
            == checkpoint_before_live_only.active_route_checkpoints
        )
        if not live_only_checkpoint_stable:
            raise AssertionError("live-only presentation advanced a completion checkpoint")

        proactive_credential = await authorizer.issue(
            DeliveryPrincipal(
                principal_id="reference-proactive-principal",
                allowed_threads=(first_thread_ref,),
            ),
            credential="reference-proactive-credential",
        )
        artifact = artifact_ledger.stage(
            "reference-artifact",
            b"consumer-owned artifact bytes",
            expected_destinations=2,
        )
        proactive_intent = DeliveryIntent(
            delivery_id="reference:proactive:artifact",
            target=ThreadRouteDeliveryTarget(first_thread_ref),
            content=(TextContent("reference proactive artifact"), artifact),
            created_at=datetime.now(UTC),
        )
        proactive_result = await gateway.deliver_proactively(
            proactive_intent,
            credential=proactive_credential,
        )
        proactive_destination_count = len(proactive_result.destinations)
        proactive_route_ids = tuple(
            destination.route_id for destination in proactive_result.destinations
        )
        if (
            proactive_result.state is not DeliverySubmissionState.ACCEPTED
            or proactive_destination_count != 2
        ):
            destination_states = tuple(
                destination.state.value for destination in proactive_result.destinations
            )
            destination_errors = tuple(
                destination.error for destination in proactive_result.destinations
            )
            raise AssertionError(
                "proactive artifact result mismatch: "
                f"aggregate={proactive_result.state.value}, "
                f"destinations={destination_states}, errors={destination_errors}"
            )
        async with asyncio.timeout(2.0):
            while artifact_ledger.active_leases:
                await asyncio.sleep(0)
        artifact_cleanup_complete = artifact_ledger.active_leases == 0

        binding_b_before_move = await actions_b.get_binding()
        if binding_b_before_move is None:
            raise AssertionError("Conversation B lost its binding before route pinning")
        moved_b = await actions_b.bind_thread(
            second_thread_ref,
            action_id="reference:proactive:move-route",
            expected_generation=binding_b_before_move.generation,
        )
        if not isinstance(moved_b, Succeeded):
            raise AssertionError("Conversation B route could not move for pinning evidence")
        sends_before_replay = consumer.channel.native_send_calls
        replayed_proactive = await gateway.deliver_proactively(
            proactive_intent,
            credential=proactive_credential,
        )
        proactive_routes_pinned = bool(
            replayed_proactive.state is DeliverySubmissionState.ACCEPTED
            and len(replayed_proactive.destinations) == 2
            and tuple(destination.route_id for destination in replayed_proactive.destinations)
            == proactive_route_ids
            and all(destination.replayed for destination in replayed_proactive.destinations)
            and consumer.channel.native_send_calls == sends_before_replay
        )
        if not proactive_routes_pinned:
            raise AssertionError(
                "proactive replay followed a moved route or resent: "
                f"state={replayed_proactive.state.value}, "
                f"destinations={len(replayed_proactive.destinations)}, "
                "replayed="
                f"{tuple(item.replayed for item in replayed_proactive.destinations)}, "
                f"send_calls={consumer.channel.native_send_calls}, before={sends_before_replay}"
            )
        rebound_b = await actions_b.bind_thread(
            first_thread_ref,
            action_id="reference:proactive:restore-route",
            expected_generation=moved_b.value.binding_generation,
        )
        if not isinstance(rebound_b, Succeeded):
            raise AssertionError("Conversation B route could not be restored")

        consumer.channel.set_next_delivery_status(
            conversation_b.ref,
            DeliveryReceiptStatus.UNKNOWN,
        )
        unknown_intent = DeliveryIntent(
            delivery_id="reference:proactive:partial-unknown",
            target=ThreadRouteDeliveryTarget(first_thread_ref),
            content=(TextContent("reference partial outcome"),),
            created_at=datetime.now(UTC),
        )
        unknown_result = await gateway.deliver_proactively(
            unknown_intent,
            credential=proactive_credential,
        )
        destination_states = {item.state for item in unknown_result.destinations}
        proactive_partial_isolated = destination_states == {
            DeliverySubmissionState.ACCEPTED,
            DeliverySubmissionState.UNKNOWN,
        }
        sends_before_unknown_replay = consumer.channel.native_send_calls
        unknown_replay = await gateway.deliver_proactively(
            unknown_intent,
            credential=proactive_credential,
        )
        proactive_unknown_sticky = bool(
            {item.state for item in unknown_replay.destinations} == destination_states
            and consumer.channel.native_send_calls == sends_before_unknown_replay
        )
        if not proactive_partial_isolated or not proactive_unknown_sticky:
            raise AssertionError("proactive partial/unknown outcome lost isolation or retried")

        side_effects_before_hostile = consumer.channel.native_send_calls
        digest_attachment = artifact_ledger.stage(
            "digest-mismatch",
            b"digest mismatch bytes",
            expected_destinations=2,
        )
        hostile_content = (
            AttachmentContent(
                "remote-url",
                "text/plain",
                RemoteUrl("https://example.invalid/private"),
                filename="remote.txt",
                size_bytes=1,
            ),
            AttachmentContent(
                "handle",
                "text/plain",
                AttachmentHandle("untrusted-handle"),
                filename="handle.txt",
                size_bytes=1,
            ),
            AttachmentContent(
                "outside-root",
                "text/plain",
                LocalPath(str(database_path)),
                filename="outside.txt",
                size_bytes=database_path.stat().st_size,
                metadata={"sha256": "0" * 64},
            ),
            replace(digest_attachment, metadata={"sha256": "0" * 64}),
            AttachmentContent(
                "unsupported-media",
                "application/pdf",
                LocalPath(str(database_path)),
                filename="unsupported.pdf",
                size_bytes=1,
                metadata={"sha256": "0" * 64},
            ),
            AttachmentContent(
                "group-limit",
                "text/plain",
                LocalPath(str(database_path)),
                filename="group-limit.txt",
                size_bytes=1_500,
                metadata={"sha256": "0" * 64},
            ),
        )
        hostile_results = []
        for index, content in enumerate(hostile_content):
            hostile_results.append(
                await gateway.deliver_proactively(
                    DeliveryIntent(
                        delivery_id=f"reference:media:hostile:{index}",
                        target=ThreadRouteDeliveryTarget(first_thread_ref),
                        content=(content,),
                        created_at=datetime.now(UTC),
                    ),
                    credential=proactive_credential,
                )
            )
        oversized_count = tuple(
            AttachmentContent(
                f"count-{index}",
                "text/plain",
                LocalPath(str(database_path)),
                filename=f"count-{index}.txt",
                size_bytes=1,
                metadata={"sha256": "0" * 64},
            )
            for index in range(3)
        )
        count_result = await gateway.deliver_proactively(
            DeliveryIntent(
                delivery_id="reference:media:count-limit",
                target=ThreadRouteDeliveryTarget(first_thread_ref),
                content=oversized_count,
                created_at=datetime.now(UTC),
            ),
            credential=proactive_credential,
        )
        media_preflight_side_effect_free = (
            consumer.channel.native_send_calls == side_effects_before_hostile
            and all(
                result.state is DeliverySubmissionState.REJECTED
                for result in (*hostile_results, count_result)
            )
        )
        if not media_preflight_side_effect_free:
            raise AssertionError("hostile media crossed the native side-effect boundary")

        projects = await actions_a.list_projects(application.ref)
        threads = await actions_a.list_threads(project_ref)
        if not isinstance(projects, Succeeded) or not isinstance(threads, Succeeded):
            raise RuntimeError("bounded resource reads did not succeed")
        project_count = len(projects.value.items)
        thread_count = len(threads.value.items)

        diagnostics = gateway.diagnostics()
        rendered_diagnostics = repr(diagnostics)
        diagnostics_size = len(rendered_diagnostics)
        diagnostics_schema_version = diagnostics.schema_version
        diagnostics_authoritative = diagnostics.authoritative
        if diagnostics_size > 4_096:
            raise AssertionError("Gateway diagnostics exceeded the reference bound")
        for secret in (
            reference_workspace,
            conversation_a.ref.native_conversation_id,
            conversation_b.ref.native_conversation_id,
            first_thread_ref.thread_id,
            second_thread_ref.thread_id,
            "initial-thread-turn",
            "shared-thread-turn",
        ):
            if secret in rendered_diagnostics:
                raise AssertionError("Gateway diagnostics exposed scoped or content data")

        binding_a_before_restart = await actions_a.get_binding()
        binding_b_before_restart = await actions_b.get_binding()
        if (
            binding_a_before_restart is None
            or binding_b_before_restart is None
            or binding_a_before_restart.thread_ref != first_thread_ref
            or binding_b_before_restart.thread_ref != first_thread_ref
        ):
            raise AssertionError("pre-restart public bindings are incomplete")
        binding_generations_before_restart = (
            (conversation_a.ref, binding_a_before_restart.generation),
            (conversation_b.ref, binding_b_before_restart.generation),
        )
        before_restart_request = len(consumer.channel.sent)
        restart_request = await application.receive_native_approval_request(
            first_thread_ref,
            prompt="Approve after authoritative pending-request recovery?",
        )
        await _wait_for_messages(
            consumer,
            after=before_restart_request,
            count=2,
        )
        recovery_snapshot_before_restart = _read_current_sqlite_recovery_snapshot(database_path)
        if len(recovery_snapshot_before_restart.active_route_checkpoints) != 2 or any(
            checkpoint is None
            for _channel, _conversation, checkpoint in (
                recovery_snapshot_before_restart.active_route_checkpoints
            )
        ):
            raise AssertionError("pre-restart destination checkpoints are incomplete")

    persistence_markers = {
        "native_payload": "native-payload-must-not-persist",
        "request_snapshot": "request-body-must-not-persist",
        "media": "media-bytes-must-not-persist",
        "artifact": "artifact-bytes-must-not-persist",
        "credential": "credential-must-not-persist",
    }
    forbidden_persistence_values = (
        reference_workspace,
        "Reference workspace",
        "initial-thread-turn",
        "shared-thread-turn",
        "old-thread-after-switch",
        "new-thread-after-switch",
        "thread-one-after-switch-back",
        "Approve the bounded reference operation?",
        "Approve after authoritative pending-request recovery?",
        "consumer-owned artifact bytes",
        "reference-proactive-credential",
        "reference proactive artifact",
        "reference partial outcome",
        "https://example.invalid/private",
        str(artifact_root),
        *persistence_markers.values(),
    )
    missed_turn = await application.receive_native_text(
        first_thread_ref,
        client_message_id="reference:native:while-gateway-stopped",
        text="shared-thread-turn",
        metadata=persistence_markers,
    )
    dispatched_before_restart = application.input_dispatch_calls
    creation_calls_before_restart = (
        application.project_creation_calls,
        application.thread_creation_calls,
    )

    restarted = build_reference_consumer(
        application=application,
        store=SQLiteGatewayStore(database_path, max_effect_receipts=64),
        delivery_authorizer=authorizer,
        delivery_outcome_observer=artifact_ledger,
        trusted_attachment_root=artifact_root,
    )
    restarted_conversation_a = restarted.channel.conversation(
        "conversation-a",
        authenticated_actor="reference-user-a",
    )
    restarted_conversation_b = restarted.channel.conversation(
        "conversation-b",
        authenticated_actor="reference-user-b",
    )
    async with restarted.gateway:
        restarted_actions_a = restarted.gateway.actions(
            restarted_conversation_a.ref,
            actor=restarted_conversation_a.authenticated_actor,
        )
        restarted_actions_b = restarted.gateway.actions(
            restarted_conversation_b.ref,
            actor=restarted_conversation_b.authenticated_actor,
        )
        await restarted.channel.wait_for_text(
            "Neutral response: shared-thread-turn",
            count=2,
        )
        await asyncio.sleep(0)
        recovered = tuple(restarted.channel.sent)
        if len(recovered) != 2 or _destinations(recovered) != (
            restarted_conversation_a.ref,
            restarted_conversation_b.ref,
        ):
            raise AssertionError("SQLite recovery duplicated or omitted authoritative output")
        recoverable_presentation_parity = bool(
            shared
            and recovered
            and all(message.content == shared[0].content for message in recovered)
        )
        if not recoverable_presentation_parity:
            raise AssertionError("live and history recovery presentation diverged")

        restored_a = await restarted_actions_a.get_binding()
        restored_b = await restarted_actions_b.get_binding()
        expected_generations = dict(binding_generations_before_restart)
        reconstructed_bindings = bool(
            restored_a is not None
            and restored_b is not None
            and restored_a.thread_ref == first_thread_ref
            and restored_b.thread_ref == first_thread_ref
            and restored_a.generation == expected_generations[restarted_conversation_a.ref]
            and restored_b.generation == expected_generations[restarted_conversation_b.ref]
        )
        if not reconstructed_bindings:
            raise AssertionError("SQLite restart did not reconstruct both bindings")

        replayed_project_after_restart = _required_ref(
            await restarted_actions_a.create_and_select_project(
                application.ref,
                cwd=reference_workspace,
                display_name="Reference workspace",
                action_id="reference:create-project:1",
            ),
            ProjectRef,
        )
        replayed_thread_after_restart = _required_ref(
            await restarted_actions_a.create_and_bind_thread(
                project_ref,
                title="Shared reference Thread",
                action_id="reference:create-thread:1",
            ),
            ThreadRef,
        )
        reconstructed_receipts = (
            replayed_project_after_restart == project_ref
            and replayed_thread_after_restart == first_thread_ref
            and creation_calls_before_restart
            == (
                application.project_creation_calls,
                application.thread_creation_calls,
            )
        )
        if not reconstructed_receipts:
            raise AssertionError("SQLite restart did not replay terminal workflow receipts")
        request_calls_before_restart_response = application.request_response_calls
        restart_response = await restarted_actions_a.respond_request(
            restart_request.request_ref,
            ApprovalResponse("approve"),
            action_id="reference:request:restart-pending",
        )
        request_restart_pending_recovered = bool(
            isinstance(restart_response, Succeeded)
            and application.request_response_calls == request_calls_before_restart_response + 1
        )
        if not request_restart_pending_recovered:
            raise AssertionError("authoritative pending request was not recoverable after restart")
        recovery_redispatched_input = application.input_dispatch_calls != dispatched_before_restart
        if recovery_redispatched_input:
            raise AssertionError("projection recovery redispatched authoritative native input")

        restarted_sends_before_proactive = restarted.channel.native_send_calls
        restarted_proactive = await restarted.gateway.deliver_proactively(
            proactive_intent,
            credential=proactive_credential,
        )
        restarted_unknown = await restarted.gateway.deliver_proactively(
            unknown_intent,
            credential=proactive_credential,
        )
        proactive_restart_replayed = bool(
            restarted_proactive.state is DeliverySubmissionState.ACCEPTED
            and len(restarted_proactive.destinations) == 2
            and all(item.replayed for item in restarted_proactive.destinations)
            and {item.state for item in restarted_unknown.destinations}
            == {
                DeliverySubmissionState.ACCEPTED,
                DeliverySubmissionState.UNKNOWN,
            }
            and restarted.channel.native_send_calls == restarted_sends_before_proactive
        )
        if not proactive_restart_replayed:
            raise AssertionError("SQLite restart did not replay pinned proactive outcomes")

        if recovery_snapshot_before_restart is None:
            raise AssertionError("pre-restart recovery snapshot was not captured")
        recovery_snapshot_after_restart = _read_current_sqlite_recovery_snapshot(database_path)
        checkpoints_before = {
            (channel, conversation): checkpoint
            for channel, conversation, checkpoint in (
                recovery_snapshot_before_restart.active_route_checkpoints
            )
        }
        checkpoints_after = {
            (channel, conversation): checkpoint
            for channel, conversation, checkpoint in (
                recovery_snapshot_after_restart.active_route_checkpoints
            )
        }
        expected_checkpoint = f"{missed_turn.turn_ref.turn_id}:assistant"
        reconstructed_checkpoints = bool(
            checkpoints_after.keys() == checkpoints_before.keys()
            and all(checkpoint is not None for checkpoint in checkpoints_before.values())
            and all(
                checkpoints_after[key] == expected_checkpoint
                and checkpoints_after[key] != checkpoints_before[key]
                for key in checkpoints_before
            )
        )
        if not reconstructed_checkpoints:
            raise AssertionError("SQLite restart did not reconstruct destination checkpoints")

        duplicate_idempotency_key = (
            "inbound:reference-channel",
            "conversation-a:reference:message:after-switch-back",
        )
        reconstructed_idempotency = bool(
            duplicate_idempotency_key in recovery_snapshot_before_restart.completed_idempotency
            and any(
                scope.startswith("outbound:")
                for scope, _key in recovery_snapshot_before_restart.completed_idempotency
            )
            and recovery_snapshot_before_restart.completed_idempotency
            <= recovery_snapshot_after_restart.completed_idempotency
        )
        if not reconstructed_idempotency:
            raise AssertionError("SQLite restart did not reconstruct completed idempotency")
        sent_before_duplicate = len(restarted.channel.sent)
        calls_before_duplicate = application.input_dispatch_calls
        await restarted_conversation_a.receive_text(
            message_id="reference:message:after-switch-back",
            text="thread-one-after-switch-back",
        )
        await asyncio.sleep(0)
        duplicate_input_suppressed = bool(
            len(restarted.channel.sent) == sent_before_duplicate
            and application.input_dispatch_calls == calls_before_duplicate
        )
        if not duplicate_input_suppressed:
            raise AssertionError("reconstructed idempotency did not suppress duplicate input")

        sqlite_files_inspected = _inspect_sqlite_files(
            database_path,
            forbidden_values=forbidden_persistence_values,
        )
        if sqlite_files_inspected < 3:
            raise AssertionError("SQLite WAL and shared-memory sidecars were not inspected")

    await asyncio.sleep(0)
    active_workers = sum(
        application.active_observation_workers(thread_ref)
        for thread_ref in (first_thread_ref, second_thread_ref)
    )
    registry_active = sum(
        registry.diagnostic_facts().active_handler_count
        for registry in (consumer.registry, restarted.registry)
    )
    bridge_inspection = _inspect_sqlite_bridge_state(
        database_path,
        forbidden_values=forbidden_persistence_values,
    )
    sqlite_files_inspected = max(sqlite_files_inspected, bridge_inspection.file_count)
    sqlite_table_count = bridge_inspection.table_count
    sqlite_bridge_state_allowlisted = bridge_inspection.schema_and_values_allowlisted
    if (
        recovery_snapshot_before_restart is None
        or recovery_snapshot_before_restart.binding_generations
        != bridge_inspection.snapshot.binding_generations
        or not recovery_snapshot_before_restart.completed_idempotency
        <= bridge_inspection.snapshot.completed_idempotency
    ):
        raise AssertionError("final SQLite recovery snapshot lost durable evidence")
    current = asyncio.current_task()
    owned_tasks = tuple(
        task
        for task in asyncio.all_tasks()
        if task is not current and task not in tasks_before and not task.done()
    )
    return ReferenceReport(
        projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        project_ref=project_ref,
        first_thread_ref=first_thread_ref,
        second_thread_ref=second_thread_ref,
        project_count=project_count,
        thread_count=thread_count,
        conversation_count=2,
        command_count=command_count,
        initial_thread_conversations=_destinations(initial),
        shared_thread_conversations=_destinations(shared),
        switched_old_thread_conversations=_destinations(switched_old),
        switched_new_thread_conversations=_destinations(switched_new),
        switched_back_conversations=_destinations(switched_back),
        worker_max_active=(
            application.max_active_observation_workers(first_thread_ref),
            application.max_active_observation_workers(second_thread_ref),
        ),
        worker_subscription_calls=(
            application.subscription_calls(first_thread_ref),
            application.subscription_calls(second_thread_ref),
        ),
        recovered_conversations=_destinations(recovered),
        recovered_delivery_count=len(recovered),
        reconstructed_bindings=reconstructed_bindings,
        reconstructed_checkpoints=reconstructed_checkpoints,
        reconstructed_idempotency=reconstructed_idempotency,
        reconstructed_receipts=reconstructed_receipts,
        duplicate_input_suppressed=duplicate_input_suppressed,
        recovery_redispatched_input=recovery_redispatched_input,
        sqlite_files_inspected=sqlite_files_inspected,
        sqlite_table_count=sqlite_table_count,
        sqlite_bridge_state_allowlisted=sqlite_bridge_state_allowlisted,
        diagnostics_schema_version=diagnostics_schema_version,
        diagnostics_size=diagnostics_size,
        diagnostics_authoritative=diagnostics_authoritative,
        adapters_stopped=(
            not gateway.running
            and not consumer.channel.started
            and not restarted.gateway.running
            and not restarted.channel.started
            and not application.started
        ),
        active_workers_after_shutdown=active_workers,
        registry_active_after_shutdown=registry_active,
        owned_tasks_after_shutdown=len(owned_tasks),
        request_delivered_destinations=request_delivered_destinations,
        request_nonrecipient_rejected=request_nonrecipient_rejected,
        request_first_writer_won=request_first_writer_won,
        request_duplicate_rejected=request_duplicate_rejected,
        request_replay_idempotent=request_replay_idempotent,
        request_restart_pending_recovered=request_restart_pending_recovered,
        live_only_checkpoint_stable=live_only_checkpoint_stable,
        recoverable_presentation_parity=recoverable_presentation_parity,
        proactive_destination_count=proactive_destination_count,
        proactive_routes_pinned=proactive_routes_pinned,
        proactive_partial_isolated=proactive_partial_isolated,
        proactive_unknown_sticky=proactive_unknown_sticky,
        proactive_restart_replayed=proactive_restart_replayed,
        media_preflight_side_effect_free=media_preflight_side_effect_free,
        artifact_startup_swept=artifact_startup_swept,
        artifact_cleanup_complete=artifact_cleanup_complete,
    )


def main() -> None:
    with TemporaryDirectory(prefix="imagent-reference-") as reference_workspace:
        report = asyncio.run(run_reference_consumer(reference_workspace))
    if not (
        report.adapters_stopped
        and report.active_workers_after_shutdown == 0
        and report.registry_active_after_shutdown == 0
        and report.owned_tasks_after_shutdown == 0
    ):
        raise RuntimeError("reference consumer shutdown did not drain owned resources")
    print(
        "reference consumer OK: "
        f"projects={report.project_count} "
        f"threads={report.thread_count} "
        f"conversations={report.conversation_count} "
        f"max_workers={max(report.worker_max_active)} "
        "diagnostics=bounded sqlite_recovery=true shutdown=true"
    )


if __name__ == "__main__":
    main()


__all__ = ["ReferenceReport", "main", "run_reference_consumer"]

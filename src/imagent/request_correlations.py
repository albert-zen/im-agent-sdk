from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from .adapters import RequestCorrelationConflict
from .contracts import (
    ApplicationRef,
    ProjectRef,
    RequestRef,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadRef,
    validate_request_route_correlation,
)
from .interaction.messages import ConversationRef


class InMemoryRequestCorrelationRepository:
    """Process-local destination-safe request response routing state."""

    def __init__(self) -> None:
        self._correlations: dict[str, RequestRouteCorrelation] = {}
        self._lock = asyncio.Lock()

    async def list_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
    ) -> tuple[RequestRouteCorrelation, ...]:
        async with self._lock:
            correlations = tuple(self._correlations.values())
        return _select_correlations(
            correlations,
            request_ref=request_ref,
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
        )

    async def put_request_correlation(
        self,
        correlation: RequestRouteCorrelation,
    ) -> RequestRouteCorrelation:
        validate_request_route_correlation(correlation)
        async with self._lock:
            existing = self._correlations.get(correlation.correlation_id)
            stored = _merge_correlation(
                existing,
                correlation,
                request_correlations=tuple(
                    candidate
                    for candidate in self._correlations.values()
                    if candidate.request_ref == correlation.request_ref
                ),
            )
            _reject_conflicting_endpoint(self._correlations.values(), stored)
            self._correlations[stored.correlation_id] = stored
            return stored

    async def transition_request_correlations(
        self,
        request_ref: RequestRef,
        *,
        expected_states: tuple[RequestRouteState, ...],
        state: RequestRouteState,
        updated_at: datetime,
    ) -> tuple[RequestRouteCorrelation, ...]:
        async with self._lock:
            selected = tuple(
                correlation
                for correlation in self._correlations.values()
                if correlation.request_ref == request_ref
            )
            transitioned = _transition_correlations(
                selected,
                expected_states=expected_states,
                state=state,
                updated_at=updated_at,
            )
            for correlation in transitioned:
                self._correlations[correlation.correlation_id] = correlation
            return transitioned

    async def delete_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int:
        _require_delete_selector(request_ref, thread_ref, conversation_ref, older_than)
        async with self._lock:
            keys = tuple(
                correlation_id
                for correlation_id, correlation in self._correlations.items()
                if _matches(
                    correlation,
                    request_ref=request_ref,
                    thread_ref=thread_ref,
                    conversation_ref=conversation_ref,
                    older_than=older_than,
                )
            )
            for correlation_id in keys:
                self._correlations.pop(correlation_id)
            return len(keys)


class _SQLiteOwner(Protocol):
    _connection: sqlite3.Connection
    _lock: asyncio.Lock


class SQLiteRequestCorrelationMixin:
    """Request-correlation methods sharing a SQLiteGatewayState transaction owner."""

    _connection: sqlite3.Connection
    _lock: asyncio.Lock

    async def list_request_correlations(
        self: _SQLiteOwner,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
    ) -> tuple[RequestRouteCorrelation, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        _append_selectors(
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
        return tuple(_correlation_from_row(row) for row in rows)

    async def put_request_correlation(
        self: _SQLiteOwner,
        correlation: RequestRouteCorrelation,
    ) -> RequestRouteCorrelation:
        validate_request_route_correlation(correlation)
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
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
                request_correlations = tuple(_correlation_from_row(row) for row in rows)
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
        self: _SQLiteOwner,
        request_ref: RequestRef,
        *,
        expected_states: tuple[RequestRouteState, ...],
        state: RequestRouteState,
        updated_at: datetime,
    ) -> tuple[RequestRouteCorrelation, ...]:
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
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
                selected = tuple(_correlation_from_row(row) for row in rows)
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
        self: _SQLiteOwner,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int:
        _require_delete_selector(request_ref, thread_ref, conversation_ref, older_than)
        clauses: list[str] = []
        parameters: list[object] = []
        _append_selectors(
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
            cursor = self._connection.execute(
                f"DELETE FROM request_route_correlations WHERE {' AND '.join(clauses)}",
                tuple(parameters),
            )
            self._connection.commit()
            return cursor.rowcount


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


def derive_request_correlation_id(
    request_ref: RequestRef,
    conversation_ref: ConversationRef,
) -> str:
    identity = json.dumps(
        [
            request_ref.application_ref.application_instance_id,
            request_ref.native_request_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:request-route:sha256:{digest}"


def derive_request_delivery_id(
    request_ref: RequestRef,
    conversation_ref: ConversationRef,
) -> str:
    identity = json.dumps(
        [
            request_ref.application_ref.application_instance_id,
            request_ref.native_request_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:request-delivery:sha256:{digest}"


def _merge_correlation(
    existing: RequestRouteCorrelation | None,
    replacement: RequestRouteCorrelation,
    *,
    request_correlations: tuple[RequestRouteCorrelation, ...],
) -> RequestRouteCorrelation:
    if existing is None:
        terminal = max(
            (
                correlation
                for correlation in request_correlations
                if correlation.state is not RequestRouteState.OPEN
            ),
            key=lambda correlation: (
                _REQUEST_STATE_PRECEDENCE[correlation.state],
                correlation.updated_at,
            ),
            default=None,
        )
        if terminal is None:
            return replacement
        return replace(
            replacement,
            state=terminal.state,
            updated_at=max(replacement.updated_at, terminal.updated_at),
        )
    if (
        existing.correlation_id != replacement.correlation_id
        or existing.request_ref != replacement.request_ref
        or existing.thread_ref != replacement.thread_ref
        or existing.turn_id != replacement.turn_id
        or existing.conversation_ref != replacement.conversation_ref
        or existing.response_shape != replacement.response_shape
    ):
        raise RequestCorrelationConflict(
            f"request correlation identity changed: {replacement.correlation_id}"
        )
    if existing.state in {
        RequestRouteState.RESPONDED,
        RequestRouteState.RESOLVED,
        RequestRouteState.STALE,
    }:
        return existing
    return replacement


_REQUEST_STATE_PRECEDENCE = {
    RequestRouteState.OPEN: 0,
    RequestRouteState.RESPONDED: 1,
    RequestRouteState.STALE: 2,
    RequestRouteState.RESOLVED: 3,
}


def _transition_correlations(
    correlations: tuple[RequestRouteCorrelation, ...],
    *,
    expected_states: tuple[RequestRouteState, ...],
    state: RequestRouteState,
    updated_at: datetime,
) -> tuple[RequestRouteCorrelation, ...]:
    if not correlations:
        raise KeyError("request correlation does not exist")
    if all(correlation.state is state for correlation in correlations):
        return correlations
    expected = set(expected_states)
    if any(correlation.state not in expected for correlation in correlations):
        raise RequestCorrelationConflict("request correlation state changed")
    target_precedence = _REQUEST_STATE_PRECEDENCE[state]
    if any(
        target_precedence < _REQUEST_STATE_PRECEDENCE[correlation.state]
        for correlation in correlations
    ):
        raise RequestCorrelationConflict("request correlation state cannot regress")
    transitioned = tuple(
        replace(correlation, state=state, updated_at=updated_at) for correlation in correlations
    )
    for correlation in transitioned:
        validate_request_route_correlation(correlation)
    return transitioned


def _reject_conflicting_endpoint(
    correlations,
    replacement: RequestRouteCorrelation,
) -> None:
    for existing in correlations:
        if (
            existing.request_ref == replacement.request_ref
            and existing.conversation_ref == replacement.conversation_ref
            and existing.correlation_id != replacement.correlation_id
        ):
            raise RequestCorrelationConflict(
                "request destination belongs to a different correlation"
            )


def _select_correlations(
    correlations: tuple[RequestRouteCorrelation, ...],
    *,
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
) -> tuple[RequestRouteCorrelation, ...]:
    return tuple(
        correlation
        for correlation in correlations
        if _matches(
            correlation,
            request_ref=request_ref,
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
            older_than=None,
        )
    )


def _matches(
    correlation: RequestRouteCorrelation,
    *,
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
    older_than: datetime | None,
) -> bool:
    return (
        (request_ref is None or correlation.request_ref == request_ref)
        and (thread_ref is None or correlation.thread_ref == thread_ref)
        and (conversation_ref is None or correlation.conversation_ref == conversation_ref)
        and (older_than is None or correlation.updated_at < older_than)
    )


def _require_delete_selector(
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
    older_than: datetime | None,
) -> None:
    if (
        request_ref is None
        and thread_ref is None
        and conversation_ref is None
        and older_than is None
    ):
        raise ValueError("request correlation deletion requires at least one selector")


def _append_selectors(
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
        parameters.extend(_thread_storage_key(thread_ref))
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
        (
            correlation.correlation_id,
            correlation.request_ref.application_ref.application_instance_id,
            correlation.request_ref.native_request_id,
            *_thread_storage_key(correlation.thread_ref)[1:],
            correlation.turn_id,
            correlation.conversation_ref.channel_instance_id,
            correlation.conversation_ref.native_conversation_id,
            correlation.delivery_id,
            _encode_response_shape(correlation),
            correlation.state.value,
            correlation.created_at.isoformat(),
            correlation.updated_at.isoformat(),
            (correlation.expires_at.isoformat() if correlation.expires_at is not None else None),
        ),
    )


def _correlation_from_row(row: sqlite3.Row) -> RequestRouteCorrelation:
    application_id = str(row["application_instance_id"])
    project_id = str(row["project_id"])
    project_ref = ProjectRef(application_id, project_id) if project_id else None
    return RequestRouteCorrelation(
        correlation_id=str(row["correlation_id"]),
        request_ref=RequestRef(
            application_ref=ApplicationRef(application_id),
            native_request_id=str(row["native_request_id"]),
        ),
        thread_ref=ThreadRef(
            application_instance_id=application_id,
            native_thread_id=str(row["thread_id"]),
            project_ref=project_ref,
        ),
        turn_id=str(row["turn_id"]),
        conversation_ref=ConversationRef(
            channel_instance_id=str(row["channel_instance_id"]),
            native_conversation_id=str(row["native_conversation_id"]),
        ),
        delivery_id=str(row["delivery_id"]),
        response_shape=_decode_response_shape(str(row["response_shape_json"])),
        state=RequestRouteState(str(row["state"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        expires_at=(
            datetime.fromisoformat(str(row["expires_at"]))
            if row["expires_at"] is not None
            else None
        ),
    )


def _encode_response_shape(correlation: RequestRouteCorrelation) -> str:
    from .contracts import ApprovalResponseShape

    shape = correlation.response_shape
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


def _decode_response_shape(value: str):
    from .contracts import (
        ApprovalResponseShape,
        UserInputQuestionShape,
        UserInputResponseShape,
    )

    payload = json.loads(value)
    if payload.get("kind") == "approval":
        return ApprovalResponseShape(choice_ids=tuple(payload["choice_ids"]))
    return UserInputResponseShape(
        questions=tuple(
            UserInputQuestionShape(
                question_id=str(question["question_id"]),
                choice_ids=tuple(question["choice_ids"]),
                allows_other=bool(question["allows_other"]),
                min_answers=int(question["min_answers"]),
                max_answers=int(question["max_answers"]),
            )
            for question in payload["questions"]
        )
    )


def _thread_storage_key(thread_ref: ThreadRef) -> tuple[str, str, str]:
    return (
        thread_ref.application_instance_id,
        (thread_ref.project_ref.native_project_id if thread_ref.project_ref is not None else ""),
        thread_ref.native_thread_id,
    )

"""Coherent SQLite GatewayStore with database-time runtime fencing."""

from __future__ import annotations

import contextvars
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar, cast

from ...applications.contract import (
    ApplicationRef,
    ProjectRef,
    ThreadRef,
    WorkspaceIdentity,
    validate_workspace_identity,
)
from ...interaction.messages import ConversationRef
from ...interaction.operations import require_identifier
from ..outcomes import Failed, OutcomeUnknown, Partial, Succeeded
from . import row_mapping
from .effects import (
    ActionError,
    ActionErrorCode,
    ActionFingerprint,
    ActionOutcome,
    BindingClearScope,
    BindingTarget,
    EffectCategory,
    EffectPhase,
    EffectReceipt,
    EffectValue,
    StableReference,
    StoreMutationPlan,
    StoreMutationRequest,
    decode_action_outcome,
    encode_action_outcome,
    validate_action_error,
    validate_action_fingerprint,
    validate_binding_target,
    validate_effect_receipt,
    validate_store_mutation_request,
)
from .repository_contracts import ProjectionCheckpointConflict, ProjectionRouteConflict
from .sqlite import SQLiteGatewayState, merge_projection_route
from .state_contracts import (
    ConversationBinding,
    ThreadProjectionRoute,
    validate_binding,
    validate_projection_route,
)
from .store import (
    EffectReceiptCapacityError,
    EffectReceiptConflict,
    GatewayNamespaceConflict,
    GatewayStoreSession,
    RuntimeLease,
    RuntimeLeaseUnavailable,
    StaleRuntimeFence,
    WorkspaceIdentityConflict,
    validate_lease_duration,
    validate_runtime_lease,
)

R = TypeVar("R")
_STALE_FENCE_MESSAGE = "imagent stale runtime fence"
_FENCED_TABLES = (
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
_MUTATING_REPOSITORY_METHODS = frozenset(
    {
        "put",
        "delete",
        "put_projection_route",
        "replace_thread_projection_routes",
        "advance_projection_checkpoint",
        "delete_projection_routes",
        "put_turn_reply_correlation",
        "delete_turn_reply_correlation",
        "delete_turn_reply_correlations",
        "claim",
        "mark_side_effect_started",
        "refresh",
        "complete",
        "release",
        "put_request_correlation",
        "transition_request_correlations",
        "delete_request_correlations",
        "reserve_delivery_submission",
        "update_delivery_destination",
    }
)
_FOCUSED_REPOSITORY_METHODS = _MUTATING_REPOSITORY_METHODS | frozenset(
    {
        "get",
        "list_projection_routes",
        "get_turn_reply_correlation",
        "list_turn_reply_correlations",
        "list_request_correlations",
        "get_delivery_submission",
    }
)


class SQLiteGatewayStore:
    """One durable SQLite namespace and transaction domain."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_effect_receipts: int = 4096,
        stale_claim_after_seconds: float = 300.0,
    ) -> None:
        if (
            not isinstance(max_effect_receipts, int)
            or isinstance(max_effect_receipts, bool)
            or max_effect_receipts < 1
        ):
            raise ValueError("max_effect_receipts must be a positive integer")
        self._max_effect_receipts = max_effect_receipts
        self._fence_context: contextvars.ContextVar[RuntimeLease | None] = contextvars.ContextVar(
            "imagent_sqlite_runtime_fence", default=None
        )
        self._maintenance_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
            "imagent_sqlite_store_maintenance",
            default=False,
        )

        def configure(connection: sqlite3.Connection) -> None:
            connection.create_function(
                "imagent_runtime_gateway_id",
                0,
                lambda: _lease_field(self._fence_context.get(), "gateway_id"),
            )
            connection.create_function(
                "imagent_runtime_owner_token",
                0,
                lambda: _lease_field(self._fence_context.get(), "owner_token"),
            )
            connection.create_function(
                "imagent_runtime_epoch",
                0,
                lambda: _lease_field(self._fence_context.get(), "epoch"),
            )
            connection.create_function(
                "imagent_store_maintenance",
                0,
                lambda: 1 if self._maintenance_context.get() else 0,
            )

        maintenance_token = self._maintenance_context.set(True)
        try:
            self._state = SQLiteGatewayState(
                path,
                stale_claim_after_seconds=stale_claim_after_seconds,
                _configure_connection=configure,
                _mutation_guard=self._assert_state_mutation_fence,
            )
            self._initialize_store_schema()
        finally:
            self._maintenance_context.reset(maintenance_token)
        self._closed = False

    @property
    def max_effect_receipts(self) -> int:
        return self._max_effect_receipts

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> GatewayStoreSession:
        require_identifier(gateway_id, "gateway_id")
        require_identifier(owner_token, "owner_token")
        validate_lease_duration(lease_duration_seconds)
        async with self._state._lock:
            self._require_open()
            connection = self._state._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                namespace = connection.execute(
                    "SELECT gateway_id FROM gateway_namespace WHERE singleton = 1"
                ).fetchone()
                if namespace is None:
                    connection.execute(
                        "INSERT INTO gateway_namespace (singleton, gateway_id) VALUES (1, ?)",
                        (gateway_id,),
                    )
                elif str(namespace["gateway_id"]) != gateway_id:
                    raise GatewayNamespaceConflict(
                        "SQLiteGatewayStore already belongs to another gateway namespace"
                    )
                now = _database_now(connection)
                row = connection.execute(
                    "SELECT owner_token, epoch, expires_at FROM gateway_runtime_lease "
                    "WHERE singleton = 1"
                ).fetchone()
                if row is not None and float(row["expires_at"]) > now.timestamp():
                    if str(row["owner_token"]) != owner_token:
                        raise RuntimeLeaseUnavailable(
                            "Gateway store namespace already has an active runtime"
                        )
                    epoch = int(row["epoch"])
                else:
                    epoch = (int(row["epoch"]) if row is not None else 0) + 1
                expires_at = datetime.fromtimestamp(
                    now.timestamp() + float(lease_duration_seconds),
                    tz=UTC,
                )
                connection.execute(
                    """
                    INSERT INTO gateway_runtime_lease (
                        singleton, owner_token, epoch, expires_at
                    ) VALUES (1, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        owner_token = excluded.owner_token,
                        epoch = excluded.epoch,
                        expires_at = excluded.expires_at
                    """,
                    (owner_token, epoch, expires_at.timestamp()),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        lease = RuntimeLease(gateway_id, owner_token, epoch, expires_at)
        validate_runtime_lease(lease)
        return cast(GatewayStoreSession, _SQLiteGatewayStoreSession(self, lease))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._state.close()

    async def _renew(
        self,
        fence: RuntimeLease,
        *,
        lease_duration_seconds: float,
    ) -> RuntimeLease:
        validate_lease_duration(lease_duration_seconds)
        async with self._state._lock:
            connection = self._state._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                now = _database_now(connection)
                self._assert_fence_sql(connection, fence, now=now)
                expires_at = datetime.fromtimestamp(
                    now.timestamp() + float(lease_duration_seconds),
                    tz=UTC,
                )
                connection.execute(
                    "UPDATE gateway_runtime_lease SET expires_at = ? WHERE singleton = 1",
                    (expires_at.timestamp(),),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return replace(fence, expires_at=expires_at)

    async def _release(self, fence: RuntimeLease) -> None:
        async with self._state._lock:
            connection = self._state._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                now = _database_now(connection)
                self._assert_fence_sql(connection, fence, now=now)
                connection.execute(
                    "UPDATE gateway_runtime_lease SET expires_at = ? WHERE singleton = 1",
                    (now.timestamp(),),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    async def _call_fenced(
        self,
        fence: RuntimeLease,
        call: Callable[[], Awaitable[R]],
    ) -> R:
        token = self._fence_context.set(fence)
        try:
            return await call()
        except sqlite3.DatabaseError as error:
            try:
                self._state._connection.rollback()
            except sqlite3.DatabaseError:
                pass
            if _STALE_FENCE_MESSAGE in str(error):
                raise StaleRuntimeFence("Gateway runtime lease is stale or expired") from error
            raise
        finally:
            self._fence_context.reset(token)

    async def _check_workspace_identities(
        self,
        fence: RuntimeLease,
        identities: tuple[WorkspaceIdentity, ...],
    ) -> None:
        for identity in identities:
            validate_workspace_identity(identity)
        async with self._state._lock:
            connection = self._state._connection
            token = self._fence_context.set(fence)
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_fence_sql(connection, fence)
                for identity in identities:
                    row = connection.execute(
                        """
                        SELECT root_fingerprint FROM gateway_workspace_identities
                        WHERE application_instance_id = ? AND project_id = ?
                        """,
                        (
                            identity.project_ref.application_instance_id,
                            identity.project_ref.project_id,
                        ),
                    ).fetchone()
                    if (
                        row is not None
                        and str(row["root_fingerprint"]) != identity.root_fingerprint
                    ):
                        raise WorkspaceIdentityConflict(
                            "stable workspace identity changed canonical-root fingerprint"
                        )
                    connection.execute(
                        """
                        INSERT INTO gateway_workspace_identities (
                            application_instance_id, project_id, root_fingerprint
                        ) VALUES (?, ?, ?)
                        ON CONFLICT(application_instance_id, project_id)
                        DO NOTHING
                        """,
                        (
                            identity.project_ref.application_instance_id,
                            identity.project_ref.project_id,
                            identity.root_fingerprint,
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                self._fence_context.reset(token)

    async def _get_binding_generation(
        self,
        conversation_ref: ConversationRef,
    ) -> int:
        async with self._state._lock:
            return _current_generation(self._state._connection, conversation_ref)

    async def _get_effect_receipt(
        self,
        fingerprint: ActionFingerprint,
    ) -> EffectReceipt | None:
        validate_action_fingerprint(fingerprint)
        async with self._state._lock:
            row = self._state._connection.execute(
                "SELECT * FROM gateway_effect_receipts WHERE action_key = ?",
                (fingerprint.action_key,),
            ).fetchone()
        if row is None:
            return None
        receipt = _receipt_from_row(row)
        _check_receipt_fingerprint(receipt, fingerprint)
        return receipt

    async def _commit_store_mutation(
        self,
        fence: RuntimeLease,
        request: StoreMutationRequest,
    ) -> EffectReceipt:
        validate_store_mutation_request(request)
        async with self._state._lock:
            connection = self._state._connection
            token = self._fence_context.set(fence)
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_fence_sql(connection, fence)
                existing = _read_receipt(connection, request.fingerprint)
                if existing is not None:
                    if existing.category is not EffectCategory.GATEWAY:
                        raise EffectReceiptConflict("effect category changed")
                    connection.commit()
                    return existing
                self._assert_receipt_capacity(connection)
                now = _database_now(connection)
                plan = request.plan
                conversation = _mutation_conversation(plan)
                generation = _current_generation(connection, conversation)
                if plan.expected_generation is not None and plan.expected_generation != generation:
                    outcome: ActionOutcome = Failed(ActionError(ActionErrorCode.STALE_BINDING))
                else:
                    try:
                        stored_binding = None
                        stored_route = _prepare_route(self._state, plan, now=now)
                        current_binding = _read_current_binding(connection, conversation)
                        target, delete_binding, binding_changed = _resolve_binding_change(
                            plan,
                            current_binding,
                        )
                        result_generation = generation
                        if binding_changed:
                            result_generation += 1
                        if target is not None and binding_changed:
                            stored_binding = ConversationBinding(
                                conversation_ref=target.conversation_ref,
                                application_ref=target.application_ref,
                                project_ref=target.project_ref,
                                thread_ref=target.thread_ref,
                                generation=result_generation,
                                updated_at=now,
                            )
                            validate_binding(stored_binding)
                            _write_binding(connection, stored_binding)
                        elif delete_binding:
                            _delete_binding(
                                connection,
                                conversation,
                                generation=result_generation,
                            )
                        if stored_route is not None and plan.route_upsert is not None:
                            self._state._write_projection_route(stored_route)
                        elif plan.route_delete_id is not None:
                            connection.execute(
                                "DELETE FROM thread_projection_routes WHERE route_id = ?",
                                (plan.route_delete_id,),
                            )
                    except (ProjectionCheckpointConflict, ProjectionRouteConflict):
                        outcome = Failed(ActionError(ActionErrorCode.CONFLICT))
                    else:
                        outcome = Succeeded(
                            EffectValue(
                                reference=_binding_or_route_reference(
                                    target,
                                    stored_route,
                                ),
                                conversation_ref=conversation,
                                binding_generation=result_generation,
                                route_id=(
                                    stored_route.route_id
                                    if stored_route is not None
                                    else plan.route_delete_id
                                ),
                            )
                        )
                receipt = _new_receipt(
                    fence.gateway_id,
                    request.fingerprint,
                    category=EffectCategory.GATEWAY,
                    phase=EffectPhase.TERMINAL,
                    native_phase_id=None,
                    binding_generation=None,
                    outcome=outcome,
                    now=now,
                )
                _insert_receipt(connection, receipt)
                connection.commit()
                return receipt
            except BaseException:
                connection.rollback()
                raise
            finally:
                self._fence_context.reset(token)

    async def _reserve_effect(
        self,
        fence: RuntimeLease,
        fingerprint: ActionFingerprint,
        *,
        category: EffectCategory,
        native_phase_id: str,
        conversation_ref: ConversationRef | None,
    ) -> EffectReceipt:
        validate_action_fingerprint(fingerprint)
        require_identifier(native_phase_id, "native_phase_id")
        if category not in {
            EffectCategory.NATIVE,
            EffectCategory.REQUEST_RESPONSE,
            EffectCategory.WORKFLOW,
        }:
            raise ValueError("effect reservation category is invalid")
        async with self._state._lock:
            connection = self._state._connection
            token = self._fence_context.set(fence)
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_fence_sql(connection, fence)
                existing = _read_receipt(connection, fingerprint)
                if existing is not None:
                    if existing.category is not category:
                        raise EffectReceiptConflict("effect category changed")
                    if existing.native_phase_id != native_phase_id:
                        raise EffectReceiptConflict("native phase identity changed")
                    connection.commit()
                    return existing
                self._assert_receipt_capacity(connection)
                now = _database_now(connection)
                generation = (
                    _current_generation(connection, conversation_ref)
                    if conversation_ref is not None
                    else None
                )
                receipt = _new_receipt(
                    fence.gateway_id,
                    fingerprint,
                    category=category,
                    phase=EffectPhase.RESERVED,
                    native_phase_id=native_phase_id,
                    binding_generation=generation,
                    outcome=None,
                    now=now,
                )
                _insert_receipt(connection, receipt)
                connection.commit()
                return receipt
            except BaseException:
                connection.rollback()
                raise
            finally:
                self._fence_context.reset(token)

    async def _mark_native_side_effect_started(
        self,
        fence: RuntimeLease,
        fingerprint: ActionFingerprint,
    ) -> tuple[EffectReceipt, bool]:
        advanced = False

        def transition(receipt: EffectReceipt, now: datetime) -> EffectReceipt:
            nonlocal advanced
            if receipt.phase is not EffectPhase.RESERVED:
                return receipt
            advanced = True
            return replace(
                receipt,
                phase=EffectPhase.NATIVE_SIDE_EFFECT_STARTED,
                updated_at=now,
            )

        receipt = await self._transition_receipt(
            fence,
            fingerprint,
            transition=transition,
        )
        return receipt, advanced

    async def _record_effect_outcome(
        self,
        fence: RuntimeLease,
        fingerprint: ActionFingerprint,
        *,
        phase: EffectPhase,
        outcome: ActionOutcome,
    ) -> EffectReceipt:
        if phase not in {
            EffectPhase.NATIVE_SIDE_EFFECT_STARTED,
            EffectPhase.NATIVE_RESULT_KNOWN,
            EffectPhase.TERMINAL,
        }:
            raise ValueError("effect outcome phase is invalid")
        if phase is EffectPhase.NATIVE_SIDE_EFFECT_STARTED and not isinstance(
            outcome,
            OutcomeUnknown,
        ):
            raise ValueError("native-side-effect-started outcome must be unknown")

        def transition(receipt: EffectReceipt, now: datetime) -> EffectReceipt:
            if receipt.phase is EffectPhase.TERMINAL:
                return receipt
            if receipt.phase is EffectPhase.RESERVED:
                raise RuntimeError("native result cannot precede the durable side-effect fence")
            if phase is EffectPhase.NATIVE_RESULT_KNOWN and (
                receipt.category is not EffectCategory.WORKFLOW
                or not isinstance(outcome, Succeeded)
            ):
                raise RuntimeError("only a workflow success may become native-result-known")
            if receipt.phase is EffectPhase.NATIVE_RESULT_KNOWN:
                raise RuntimeError("known workflow result can advance only through binding commit")
            if (
                receipt.category is EffectCategory.WORKFLOW
                and phase is EffectPhase.TERMINAL
                and isinstance(outcome, Succeeded)
            ):
                raise RuntimeError("workflow success requires atomic binding commit")
            return replace(receipt, phase=phase, outcome=outcome, updated_at=now)

        return await self._transition_receipt(
            fence,
            fingerprint,
            transition=transition,
        )

    async def _record_effect_preflight_failure(
        self,
        fence: RuntimeLease,
        fingerprint: ActionFingerprint,
        *,
        error: ActionError,
    ) -> EffectReceipt:
        validate_action_error(error)

        def transition(receipt: EffectReceipt, now: datetime) -> EffectReceipt:
            if receipt.phase is not EffectPhase.RESERVED:
                return receipt
            return replace(
                receipt,
                phase=EffectPhase.TERMINAL,
                outcome=Failed(error),
                updated_at=now,
            )

        return await self._transition_receipt(
            fence,
            fingerprint,
            transition=transition,
        )

    async def _transition_receipt(
        self,
        fence: RuntimeLease,
        fingerprint: ActionFingerprint,
        *,
        transition: Callable[[EffectReceipt, datetime], EffectReceipt],
    ) -> EffectReceipt:
        async with self._state._lock:
            connection = self._state._connection
            token = self._fence_context.set(fence)
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_fence_sql(connection, fence)
                receipt = _read_receipt(connection, fingerprint)
                if receipt is None:
                    raise KeyError("effect receipt does not exist")
                updated = transition(receipt, _database_now(connection))
                validate_effect_receipt(updated)
                _update_receipt(connection, updated)
                connection.commit()
                return updated
            except BaseException:
                connection.rollback()
                raise
            finally:
                self._fence_context.reset(token)

    async def _commit_workflow_binding(
        self,
        fence: RuntimeLease,
        fingerprint: ActionFingerprint,
        *,
        binding_target: BindingTarget,
        route: ThreadProjectionRoute | None,
    ) -> EffectReceipt:
        validate_binding_target(binding_target)
        if route is not None:
            validate_projection_route(route)
            if route.conversation_ref != binding_target.conversation_ref:
                raise ValueError("workflow route belongs to another Conversation")
        async with self._state._lock:
            connection = self._state._connection
            token = self._fence_context.set(fence)
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_fence_sql(connection, fence)
                receipt = _read_receipt(connection, fingerprint)
                if receipt is None:
                    raise KeyError("workflow receipt does not exist")
                if receipt.category is not EffectCategory.WORKFLOW:
                    raise EffectReceiptConflict("effect category changed")
                if receipt.phase is EffectPhase.TERMINAL:
                    connection.commit()
                    return receipt
                if receipt.phase is not EffectPhase.NATIVE_RESULT_KNOWN or not isinstance(
                    receipt.outcome, Succeeded
                ):
                    raise RuntimeError("workflow binding requires a known native result")
                created_value = receipt.outcome.value
                generation = _current_generation(
                    connection,
                    binding_target.conversation_ref,
                )
                now = _database_now(connection)
                if generation != receipt.binding_generation:
                    outcome: ActionOutcome = Partial(
                        created_value,
                        ActionError(ActionErrorCode.STALE_BINDING),
                    )
                else:
                    try:
                        stored_route = None
                        if route is not None:
                            candidate = replace(route, updated_at=now)
                            stored_route = merge_projection_route(
                                self._state._read_projection_route(candidate.route_id)
                                or self._state._read_projection_route_for_endpoints(candidate),
                                candidate,
                            )
                        stored_binding = ConversationBinding(
                            conversation_ref=binding_target.conversation_ref,
                            application_ref=binding_target.application_ref,
                            project_ref=binding_target.project_ref,
                            thread_ref=binding_target.thread_ref,
                            generation=generation + 1,
                            updated_at=now,
                        )
                        validate_binding(stored_binding)
                        _write_binding(connection, stored_binding)
                        if stored_route is not None:
                            self._state._write_projection_route(stored_route)
                    except (ProjectionCheckpointConflict, ProjectionRouteConflict):
                        outcome = Partial(
                            created_value,
                            ActionError(ActionErrorCode.CONFLICT),
                        )
                    else:
                        outcome = Succeeded(
                            replace(
                                created_value,
                                conversation_ref=binding_target.conversation_ref,
                                binding_generation=stored_binding.generation,
                                route_id=(
                                    stored_route.route_id if stored_route is not None else None
                                ),
                            )
                        )
                terminal = replace(
                    receipt,
                    phase=EffectPhase.TERMINAL,
                    outcome=outcome,
                    updated_at=now,
                )
                validate_effect_receipt(terminal)
                _update_receipt(connection, terminal)
                connection.commit()
                return terminal
            except BaseException:
                connection.rollback()
                raise
            finally:
                self._fence_context.reset(token)

    def _assert_receipt_capacity(self, connection: sqlite3.Connection) -> None:
        count = int(
            connection.execute("SELECT COUNT(*) FROM gateway_effect_receipts").fetchone()[0]
        )
        if count >= self._max_effect_receipts:
            raise EffectReceiptCapacityError("effect receipt capacity is exhausted")

    def _assert_fence_sql(
        self,
        connection: sqlite3.Connection,
        fence: RuntimeLease,
        *,
        now: datetime | None = None,
    ) -> None:
        now = now or _database_now(connection)
        row = connection.execute(
            """
            SELECT n.gateway_id, l.owner_token, l.epoch, l.expires_at
            FROM gateway_namespace AS n
            JOIN gateway_runtime_lease AS l ON l.singleton = n.singleton
            WHERE n.singleton = 1
            """
        ).fetchone()
        if (
            row is None
            or str(row["gateway_id"]) != fence.gateway_id
            or str(row["owner_token"]) != fence.owner_token
            or int(row["epoch"]) != fence.epoch
            or float(row["expires_at"]) <= now.timestamp()
        ):
            raise StaleRuntimeFence("Gateway runtime lease is stale or expired")

    def _assert_state_mutation_fence(self, connection: sqlite3.Connection) -> None:
        if self._maintenance_context.get():
            return
        fence = self._fence_context.get()
        if fence is None:
            raise StaleRuntimeFence("Gateway runtime lease is stale or expired")
        self._assert_fence_sql(connection, fence)

    def _initialize_store_schema(self) -> None:
        connection = self._state._connection
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS gateway_namespace (
                singleton INTEGER NOT NULL PRIMARY KEY CHECK (singleton = 1),
                gateway_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gateway_runtime_lease (
                singleton INTEGER NOT NULL PRIMARY KEY CHECK (singleton = 1),
                owner_token TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gateway_workspace_identities (
                application_instance_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                root_fingerprint TEXT NOT NULL,
                PRIMARY KEY (application_instance_id, project_id)
            );
            CREATE TABLE IF NOT EXISTS gateway_effect_receipts (
                action_key TEXT NOT NULL PRIMARY KEY,
                gateway_id TEXT NOT NULL,
                action_kind TEXT NOT NULL,
                payload_fingerprint TEXT NOT NULL,
                category TEXT NOT NULL,
                phase TEXT NOT NULL,
                native_phase_id TEXT,
                binding_generation INTEGER,
                outcome_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        for table in _FENCED_TABLES:
            for operation in ("INSERT", "UPDATE", "DELETE"):
                trigger = f"imagent_runtime_fence_{table}_{operation.lower()}"
                connection.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {trigger}
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
                        SELECT RAISE(ABORT, '{_STALE_FENCE_MESSAGE}');
                    END
                    """
                )
        connection.commit()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Gateway store is closed")


class _SQLiteGatewayStoreSession:
    def __init__(self, store: SQLiteGatewayStore, lease: RuntimeLease) -> None:
        self._store = store
        self._lease = lease
        self._closed = False

    @property
    def lease(self) -> RuntimeLease:
        return self._lease

    async def renew(self, *, lease_duration_seconds: float) -> RuntimeLease:
        self._require_open()
        self._lease = await self._store._renew(
            self._lease,
            lease_duration_seconds=lease_duration_seconds,
        )
        return self._lease

    async def release_runtime(self) -> None:
        if self._closed:
            return
        await self._store._release(self._lease)
        self._closed = True

    async def close(self) -> None:
        await self.release_runtime()

    async def check_workspace_identities(
        self,
        identities: tuple[WorkspaceIdentity, ...],
    ) -> None:
        self._require_open()
        await self._store._check_workspace_identities(self._lease, identities)

    async def get_binding_generation(self, conversation_ref: ConversationRef) -> int:
        return await self._store._get_binding_generation(conversation_ref)

    async def commit_store_mutation(self, request: StoreMutationRequest) -> EffectReceipt:
        self._require_open()
        return await self._store._commit_store_mutation(self._lease, request)

    async def reserve_effect(
        self,
        fingerprint: ActionFingerprint,
        *,
        category: EffectCategory,
        native_phase_id: str,
        conversation_ref: ConversationRef | None = None,
    ) -> EffectReceipt:
        self._require_open()
        return await self._store._reserve_effect(
            self._lease,
            fingerprint,
            category=category,
            native_phase_id=native_phase_id,
            conversation_ref=conversation_ref,
        )

    async def get_effect_receipt(
        self,
        fingerprint: ActionFingerprint,
    ) -> EffectReceipt | None:
        return await self._store._get_effect_receipt(fingerprint)

    async def mark_native_side_effect_started(
        self,
        fingerprint: ActionFingerprint,
    ) -> tuple[EffectReceipt, bool]:
        self._require_open()
        return await self._store._mark_native_side_effect_started(
            self._lease,
            fingerprint,
        )

    async def record_effect_outcome(
        self,
        fingerprint: ActionFingerprint,
        *,
        phase: EffectPhase,
        outcome: ActionOutcome,
    ) -> EffectReceipt:
        self._require_open()
        return await self._store._record_effect_outcome(
            self._lease,
            fingerprint,
            phase=phase,
            outcome=outcome,
        )

    async def record_effect_preflight_failure(
        self,
        fingerprint: ActionFingerprint,
        *,
        error: ActionError,
    ) -> EffectReceipt:
        self._require_open()
        return await self._store._record_effect_preflight_failure(
            self._lease,
            fingerprint,
            error=error,
        )

    async def commit_workflow_binding(
        self,
        fingerprint: ActionFingerprint,
        *,
        binding_target: BindingTarget,
        route: ThreadProjectionRoute | None,
    ) -> EffectReceipt:
        self._require_open()
        return await self._store._commit_workflow_binding(
            self._lease,
            fingerprint,
            binding_target=binding_target,
            route=route,
        )

    def __getattr__(self, name: str) -> object:
        if name not in _FOCUSED_REPOSITORY_METHODS:
            raise AttributeError(
                f"{type(self).__name__!s} has no focused repository member {name!r}"
            )
        attribute = getattr(self._store._state, name)
        if name not in _MUTATING_REPOSITORY_METHODS or not callable(attribute):
            return attribute
        async_attribute = cast(Callable[..., Awaitable[object]], attribute)

        async def fenced(*args: object, **kwargs: object) -> object:
            self._require_open()
            return await self._store._call_fenced(
                self._lease,
                lambda: async_attribute(*args, **kwargs),
            )

        return fenced

    def _require_open(self) -> None:
        if self._closed:
            raise StaleRuntimeFence("Gateway store session is closed")


def _database_now(connection: sqlite3.Connection) -> datetime:
    seconds = float(
        connection.execute("SELECT ((julianday('now') - 2440587.5) * 86400.0)").fetchone()[0]
    )
    return datetime.fromtimestamp(seconds, tz=UTC)


def _lease_field(lease: RuntimeLease | None, field: str) -> str | int | None:
    if lease is None:
        return None
    value = getattr(lease, field)
    if not isinstance(value, (str, int)):
        raise TypeError("runtime lease field is not SQLite-compatible")
    return value


def _current_generation(
    connection: sqlite3.Connection,
    conversation_ref: ConversationRef,
) -> int:
    row = connection.execute(
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
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ),
    ).fetchone()
    return int(row["generation"]) if row is not None and row["generation"] is not None else 0


def _write_binding(connection: sqlite3.Connection, binding: ConversationBinding) -> None:
    connection.execute(
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
        row_mapping.binding_to_row(binding),
    )
    connection.execute(
        """
        INSERT INTO conversation_binding_generations (
            channel_instance_id, native_conversation_id, generation
        ) VALUES (?, ?, ?)
        ON CONFLICT(channel_instance_id, native_conversation_id)
        DO UPDATE SET generation = excluded.generation
        """,
        (
            binding.conversation_ref.channel_instance_id,
            binding.conversation_ref.native_conversation_id,
            binding.generation,
        ),
    )


def _delete_binding(
    connection: sqlite3.Connection,
    conversation_ref: ConversationRef,
    *,
    generation: int,
) -> None:
    connection.execute(
        "DELETE FROM conversation_bindings "
        "WHERE channel_instance_id = ? AND native_conversation_id = ?",
        (
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO conversation_binding_generations (
            channel_instance_id, native_conversation_id, generation
        ) VALUES (?, ?, ?)
        ON CONFLICT(channel_instance_id, native_conversation_id)
        DO UPDATE SET generation = excluded.generation
        """,
        (
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
            generation,
        ),
    )


def _mutation_conversation(plan: StoreMutationPlan) -> ConversationRef:
    return plan.conversation_ref


def _read_current_binding(
    connection: sqlite3.Connection,
    conversation_ref: ConversationRef,
) -> ConversationBinding | None:
    row = connection.execute(
        "SELECT * FROM conversation_bindings "
        "WHERE channel_instance_id = ? AND native_conversation_id = ?",
        (
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ),
    ).fetchone()
    return row_mapping.binding_from_row(row) if row is not None else None


def _resolve_binding_change(
    plan: StoreMutationPlan,
    current: ConversationBinding | None,
) -> tuple[BindingTarget | None, bool, bool]:
    if plan.binding_target is not None:
        return plan.binding_target, False, True
    if plan.binding_clear is None:
        return None, False, False
    if plan.binding_clear is BindingClearScope.APPLICATION:
        return None, True, True
    if current is None:
        return None, True, True
    target = BindingTarget(
        current.conversation_ref,
        application_ref=current.application_ref,
        project_ref=(
            current.project_ref if plan.binding_clear is BindingClearScope.THREAD else None
        ),
    )
    return target, False, True


def _prepare_route(
    state: SQLiteGatewayState,
    plan: StoreMutationPlan,
    *,
    now: datetime,
) -> ThreadProjectionRoute | None:
    if plan.route_upsert is None:
        if plan.route_delete_id is not None:
            existing = state._read_projection_route(plan.route_delete_id)
            if existing is not None and existing.conversation_ref != plan.conversation_ref:
                raise ProjectionRouteConflict("route deletion belongs to a different Conversation")
            return existing
        return None
    candidate = replace(plan.route_upsert, updated_at=now)
    return merge_projection_route(
        state._read_projection_route(candidate.route_id)
        or state._read_projection_route_for_endpoints(candidate),
        candidate,
    )


def _binding_or_route_reference(
    binding_target: BindingTarget | None,
    route: ThreadProjectionRoute | None,
) -> StableReference | None:
    if binding_target is not None:
        value: ApplicationRef | ProjectRef | ThreadRef | None = (
            binding_target.thread_ref
            or binding_target.project_ref
            or binding_target.application_ref
        )
        if value is not None:
            return StableReference.from_value(value)
    if route is not None:
        return StableReference.from_value(route.thread_ref)
    return None


def _new_receipt(
    gateway_id: str,
    fingerprint: ActionFingerprint,
    *,
    category: EffectCategory,
    phase: EffectPhase,
    native_phase_id: str | None,
    binding_generation: int | None,
    outcome: ActionOutcome | None,
    now: datetime,
) -> EffectReceipt:
    receipt = EffectReceipt(
        gateway_id,
        fingerprint.action_kind,
        fingerprint.action_key,
        fingerprint.payload_fingerprint,
        category,
        phase,
        native_phase_id,
        binding_generation,
        outcome,
        now,
        now,
    )
    validate_effect_receipt(receipt)
    return receipt


def _read_receipt(
    connection: sqlite3.Connection,
    fingerprint: ActionFingerprint,
) -> EffectReceipt | None:
    row = connection.execute(
        "SELECT * FROM gateway_effect_receipts WHERE action_key = ?",
        (fingerprint.action_key,),
    ).fetchone()
    if row is None:
        return None
    receipt = _receipt_from_row(row)
    _check_receipt_fingerprint(receipt, fingerprint)
    return receipt


def _check_receipt_fingerprint(
    receipt: EffectReceipt,
    fingerprint: ActionFingerprint,
) -> None:
    if (
        receipt.action_kind != fingerprint.action_kind
        or receipt.payload_fingerprint != fingerprint.payload_fingerprint
    ):
        raise EffectReceiptConflict("effective action identity was reused with a different payload")


def _insert_receipt(connection: sqlite3.Connection, receipt: EffectReceipt) -> None:
    connection.execute(
        """
        INSERT INTO gateway_effect_receipts (
            action_key, gateway_id, action_kind, payload_fingerprint, category, phase,
            native_phase_id, binding_generation, outcome_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _receipt_to_row(receipt),
    )


def _update_receipt(connection: sqlite3.Connection, receipt: EffectReceipt) -> None:
    cursor = connection.execute(
        """
        UPDATE gateway_effect_receipts SET
            phase = ?, outcome_json = ?, updated_at = ?
        WHERE action_key = ?
        """,
        (
            receipt.phase.value,
            encode_action_outcome(receipt.outcome),
            receipt.updated_at.isoformat(),
            receipt.action_key,
        ),
    )
    if cursor.rowcount != 1:
        raise KeyError("effect receipt does not exist")


def _receipt_to_row(receipt: EffectReceipt) -> tuple[object, ...]:
    return (
        receipt.action_key,
        receipt.gateway_id,
        receipt.action_kind,
        receipt.payload_fingerprint,
        receipt.category.value,
        receipt.phase.value,
        receipt.native_phase_id,
        receipt.binding_generation,
        encode_action_outcome(receipt.outcome),
        receipt.created_at.isoformat(),
        receipt.updated_at.isoformat(),
    )


def _receipt_from_row(row: sqlite3.Row) -> EffectReceipt:
    receipt = EffectReceipt(
        gateway_id=str(row["gateway_id"]),
        action_kind=str(row["action_kind"]),
        action_key=str(row["action_key"]),
        payload_fingerprint=str(row["payload_fingerprint"]),
        category=EffectCategory(str(row["category"])),
        phase=EffectPhase(str(row["phase"])),
        native_phase_id=(
            str(row["native_phase_id"]) if row["native_phase_id"] is not None else None
        ),
        binding_generation=(
            row_mapping.required_integer(row["binding_generation"], "binding_generation")
            if row["binding_generation"] is not None
            else None
        ),
        outcome=decode_action_outcome(
            str(row["outcome_json"]) if row["outcome_json"] is not None else None
        ),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )
    validate_effect_receipt(receipt)
    return receipt


__all__ = ["SQLiteGatewayStore"]

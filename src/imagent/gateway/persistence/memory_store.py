"""Coherent process-local GatewayStore implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from ...applications.contract import (
    ApplicationRef,
    ProjectRef,
    ThreadRef,
    WorkspaceIdentity,
    validate_workspace_identity,
)
from ...applications.requests import RequestRef
from ...interaction.messages import ConversationRef
from ...interaction.operations import require_identifier
from ..outcomes import Failed, OutcomeUnknown, Partial, Succeeded
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
    validate_action_error,
    validate_action_fingerprint,
    validate_binding_target,
    validate_effect_receipt,
    validate_store_mutation_request,
)
from .idempotency import InMemoryIdempotencyRepository
from .memory import (
    InMemoryBindingRepository,
    InMemoryDeliverySubmissionRepository,
    InMemoryProjectionRouteRepository,
    InMemoryRequestCorrelationRepository,
    _merge_projection_route,
    _reject_conflicting_route_id,
)
from .repository_contracts import (
    IdempotencyClaimStatus,
    ProjectionCheckpointConflict,
    ProjectionRouteConflict,
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
    validate_binding,
    validate_projection_route,
)
from .store import (
    EffectReceiptCapacityError,
    EffectReceiptConflict,
    GatewayNamespaceConflict,
    RuntimeLease,
    RuntimeLeaseUnavailable,
    StaleRuntimeFence,
    WorkspaceIdentityConflict,
    validate_lease_duration,
    validate_runtime_lease,
)

R = TypeVar("R")


class MemoryGatewayStore:
    """One coherent ephemeral namespace with store-authoritative fencing."""

    def __init__(
        self,
        *,
        max_effect_receipts: int = 4096,
        max_idempotency_records: int = 4096,
        max_delivery_submission_records: int = 4096,
        _clock: Callable[[], datetime] | None = None,
    ) -> None:
        for name, value in (
            ("max_effect_receipts", max_effect_receipts),
            ("max_idempotency_records", max_idempotency_records),
            ("max_delivery_submission_records", max_delivery_submission_records),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self._max_effect_receipts = max_effect_receipts
        self._clock = _clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self._namespace_id: str | None = None
        self._lease: RuntimeLease | None = None
        self._epoch = 0
        self._closed = False
        self._bindings = InMemoryBindingRepository()
        self._projections = InMemoryProjectionRouteRepository()
        self._idempotency = InMemoryIdempotencyRepository(max_records=max_idempotency_records)
        self._request_correlations = InMemoryRequestCorrelationRepository()
        self._delivery_submissions = InMemoryDeliverySubmissionRepository(
            max_records=max_delivery_submission_records
        )
        self._workspace_identities: dict[ProjectRef, str] = {}
        self._effect_receipts: dict[str, EffectReceipt] = {}

    @property
    def max_effect_receipts(self) -> int:
        return self._max_effect_receipts

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> _MemoryGatewayStoreSession:
        require_identifier(gateway_id, "gateway_id")
        require_identifier(owner_token, "owner_token")
        validate_lease_duration(lease_duration_seconds)
        async with self._lock:
            self._require_open_locked()
            if self._namespace_id is None:
                self._namespace_id = gateway_id
            elif self._namespace_id != gateway_id:
                raise GatewayNamespaceConflict(
                    "MemoryGatewayStore already belongs to another gateway namespace"
                )
            now = self._now_locked()
            current = self._lease
            if current is not None and current.expires_at > now:
                if current.owner_token != owner_token:
                    raise RuntimeLeaseUnavailable(
                        "Gateway store namespace already has an active runtime"
                    )
                lease = RuntimeLease(
                    gateway_id,
                    owner_token,
                    current.epoch,
                    now + timedelta(seconds=float(lease_duration_seconds)),
                )
            else:
                self._epoch += 1
                lease = RuntimeLease(
                    gateway_id,
                    owner_token,
                    self._epoch,
                    now + timedelta(seconds=float(lease_duration_seconds)),
                )
            validate_runtime_lease(lease)
            self._lease = lease
            return _MemoryGatewayStoreSession(self, lease)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True

    async def _renew(
        self,
        fence: RuntimeLease,
        *,
        lease_duration_seconds: float,
    ) -> RuntimeLease:
        validate_lease_duration(lease_duration_seconds)
        async with self._lock:
            self._assert_fence_locked(fence)
            now = self._now_locked()
            renewed = replace(
                fence,
                expires_at=now + timedelta(seconds=float(lease_duration_seconds)),
            )
            self._lease = renewed
            return renewed

    async def _release(self, fence: RuntimeLease) -> None:
        async with self._lock:
            self._assert_fence_locked(fence)
            self._lease = replace(fence, expires_at=self._now_locked())

    async def _read(
        self,
        call: Callable[[], Awaitable[R]],
    ) -> R:
        self._require_open_unlocked()
        return await call()

    async def _mutate(
        self,
        fence: RuntimeLease,
        call: Callable[[], Awaitable[R]],
    ) -> R:
        async with self._lock:
            self._assert_fence_locked(fence)
            return await call()

    async def _check_workspace_identities(
        self,
        fence: RuntimeLease,
        identities: tuple[WorkspaceIdentity, ...],
    ) -> None:
        for identity in identities:
            validate_workspace_identity(identity)
        async with self._lock:
            self._assert_fence_locked(fence)
            staged = dict(self._workspace_identities)
            for identity in identities:
                existing = staged.get(identity.project_ref)
                if existing is not None and existing != identity.root_fingerprint:
                    raise WorkspaceIdentityConflict(
                        "stable workspace identity changed canonical-root fingerprint"
                    )
                staged[identity.project_ref] = identity.root_fingerprint
            self._workspace_identities = staged

    async def _get_binding_generation(
        self,
        conversation_ref: ConversationRef,
    ) -> int:
        async with self._lock:
            self._require_open_locked()
            current = self._bindings._bindings.get(conversation_ref)
            return max(
                current.generation if current is not None else 0,
                self._bindings._binding_generation_floors.get(conversation_ref, 0),
            )

    async def _get_effect_receipt(
        self,
        fingerprint: ActionFingerprint,
    ) -> EffectReceipt | None:
        validate_action_fingerprint(fingerprint)
        async with self._lock:
            self._require_open_locked()
            existing = self._effect_receipts.get(fingerprint.action_key)
            if existing is None:
                return None
            self._check_fingerprint_locked(existing, fingerprint)
            return existing

    async def _commit_store_mutation(
        self,
        fence: RuntimeLease,
        request: StoreMutationRequest,
    ) -> EffectReceipt:
        validate_store_mutation_request(request)
        async with self._lock:
            self._assert_fence_locked(fence)
            existing = self._existing_receipt_locked(
                request.fingerprint,
                category=EffectCategory.GATEWAY,
            )
            if existing is not None:
                return existing
            self._reserve_capacity_locked()
            now = self._now_locked()
            bindings = dict(self._bindings._bindings)
            generation_floors = dict(self._bindings._binding_generation_floors)
            routes = dict(self._projections._routes)
            plan = request.plan
            conversation_ref = _mutation_conversation(plan)
            current_generation = _generation_from_maps(
                conversation_ref,
                bindings,
                generation_floors,
            )
            if (
                plan.expected_generation is not None
                and plan.expected_generation != current_generation
            ):
                outcome: ActionOutcome = Failed(ActionError(ActionErrorCode.STALE_BINDING))
            else:
                try:
                    stored_binding: ConversationBinding | None = None
                    current_binding = bindings.get(conversation_ref)
                    target, delete_binding, binding_changed = _resolve_binding_change(
                        plan,
                        current_binding,
                    )
                    result_generation = current_generation
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
                        bindings[target.conversation_ref] = stored_binding
                        generation_floors[target.conversation_ref] = stored_binding.generation
                    elif delete_binding:
                        bindings.pop(conversation_ref, None)
                        generation_floors[conversation_ref] = result_generation
                    stored_route = _apply_route_plan(routes, plan, now=now)
                    reference = _binding_or_route_reference(
                        target,
                        stored_route,
                    )
                    outcome = Succeeded(
                        EffectValue(
                            reference=reference,
                            conversation_ref=conversation_ref,
                            binding_generation=(result_generation),
                            route_id=(
                                stored_route.route_id
                                if stored_route is not None
                                else plan.route_delete_id
                            ),
                        )
                    )
                except (ProjectionCheckpointConflict, ProjectionRouteConflict):
                    outcome = Failed(ActionError(ActionErrorCode.CONFLICT))
                else:
                    self._bindings._bindings = bindings
                    self._bindings._binding_generation_floors = generation_floors
                    self._projections._routes = routes
            receipt = self._new_receipt_locked(
                request.fingerprint,
                category=EffectCategory.GATEWAY,
                phase=EffectPhase.TERMINAL,
                native_phase_id=None,
                binding_generation=None,
                outcome=outcome,
                now=now,
            )
            self._effect_receipts[receipt.action_key] = receipt
            return receipt

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
        async with self._lock:
            self._assert_fence_locked(fence)
            existing = self._existing_receipt_locked(fingerprint, category=category)
            if existing is not None:
                if existing.native_phase_id != native_phase_id:
                    raise EffectReceiptConflict("native phase identity changed")
                return existing
            self._reserve_capacity_locked()
            generation = None
            if conversation_ref is not None:
                generation = _generation_from_maps(
                    conversation_ref,
                    self._bindings._bindings,
                    self._bindings._binding_generation_floors,
                )
            now = self._now_locked()
            receipt = self._new_receipt_locked(
                fingerprint,
                category=category,
                phase=EffectPhase.RESERVED,
                native_phase_id=native_phase_id,
                binding_generation=generation,
                outcome=None,
                now=now,
            )
            self._effect_receipts[receipt.action_key] = receipt
            return receipt

    async def _mark_native_side_effect_started(
        self,
        fence: RuntimeLease,
        fingerprint: ActionFingerprint,
    ) -> tuple[EffectReceipt, bool]:
        async with self._lock:
            self._assert_fence_locked(fence)
            receipt = self._require_receipt_locked(fingerprint)
            if receipt.phase is not EffectPhase.RESERVED:
                return receipt, False
            updated = replace(
                receipt,
                phase=EffectPhase.NATIVE_SIDE_EFFECT_STARTED,
                updated_at=self._now_locked(),
            )
            self._effect_receipts[updated.action_key] = updated
            return updated, True

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
        async with self._lock:
            self._assert_fence_locked(fence)
            receipt = self._require_receipt_locked(fingerprint)
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
            updated = replace(
                receipt,
                phase=phase,
                outcome=outcome,
                updated_at=self._now_locked(),
            )
            validate_effect_receipt(updated)
            self._effect_receipts[updated.action_key] = updated
            return updated

    async def _record_effect_preflight_failure(
        self,
        fence: RuntimeLease,
        fingerprint: ActionFingerprint,
        *,
        error: ActionError,
    ) -> EffectReceipt:
        validate_action_error(error)
        async with self._lock:
            self._assert_fence_locked(fence)
            receipt = self._require_receipt_locked(fingerprint)
            if receipt.phase is not EffectPhase.RESERVED:
                return receipt
            updated = replace(
                receipt,
                phase=EffectPhase.TERMINAL,
                outcome=Failed(error),
                updated_at=self._now_locked(),
            )
            validate_effect_receipt(updated)
            self._effect_receipts[updated.action_key] = updated
            return updated

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
        async with self._lock:
            self._assert_fence_locked(fence)
            receipt = self._require_receipt_locked(fingerprint)
            if receipt.category is not EffectCategory.WORKFLOW:
                raise EffectReceiptConflict("effect category changed")
            if receipt.phase is EffectPhase.TERMINAL:
                return receipt
            if receipt.phase is not EffectPhase.NATIVE_RESULT_KNOWN or not isinstance(
                receipt.outcome, Succeeded
            ):
                raise RuntimeError("workflow binding requires a known native result")
            created_value = receipt.outcome.value
            current_generation = _generation_from_maps(
                binding_target.conversation_ref,
                self._bindings._bindings,
                self._bindings._binding_generation_floors,
            )
            now = self._now_locked()
            if current_generation != receipt.binding_generation:
                outcome: ActionOutcome = Partial(
                    created_value,
                    ActionError(ActionErrorCode.STALE_BINDING),
                )
            else:
                bindings = dict(self._bindings._bindings)
                floors = dict(self._bindings._binding_generation_floors)
                routes = dict(self._projections._routes)
                try:
                    stored_binding = ConversationBinding(
                        conversation_ref=binding_target.conversation_ref,
                        application_ref=binding_target.application_ref,
                        project_ref=binding_target.project_ref,
                        thread_ref=binding_target.thread_ref,
                        generation=current_generation + 1,
                        updated_at=now,
                    )
                    validate_binding(stored_binding)
                    bindings[binding_target.conversation_ref] = stored_binding
                    floors[binding_target.conversation_ref] = stored_binding.generation
                    stored_route = None
                    if route is not None:
                        _reject_conflicting_route_id(routes.values(), route)
                        key = (route.thread_ref, route.conversation_ref)
                        stored_route = _merge_projection_route(
                            routes.get(key),
                            replace(route, updated_at=now),
                        )
                        routes[key] = stored_route
                except (ProjectionCheckpointConflict, ProjectionRouteConflict):
                    outcome = Partial(created_value, ActionError(ActionErrorCode.CONFLICT))
                else:
                    self._bindings._bindings = bindings
                    self._bindings._binding_generation_floors = floors
                    self._projections._routes = routes
                    outcome = Succeeded(
                        replace(
                            created_value,
                            conversation_ref=binding_target.conversation_ref,
                            binding_generation=stored_binding.generation,
                            route_id=(stored_route.route_id if stored_route is not None else None),
                        )
                    )
            updated = replace(
                receipt,
                phase=EffectPhase.TERMINAL,
                outcome=outcome,
                updated_at=now,
            )
            validate_effect_receipt(updated)
            self._effect_receipts[updated.action_key] = updated
            return updated

    def _existing_receipt_locked(
        self,
        fingerprint: ActionFingerprint,
        *,
        category: EffectCategory,
    ) -> EffectReceipt | None:
        existing = self._effect_receipts.get(fingerprint.action_key)
        if existing is None:
            return None
        self._check_fingerprint_locked(existing, fingerprint)
        if existing.category is not category:
            raise EffectReceiptConflict("effect category changed")
        return existing

    def _require_receipt_locked(self, fingerprint: ActionFingerprint) -> EffectReceipt:
        validate_action_fingerprint(fingerprint)
        receipt = self._effect_receipts.get(fingerprint.action_key)
        if receipt is None:
            raise KeyError("effect receipt does not exist")
        self._check_fingerprint_locked(receipt, fingerprint)
        return receipt

    @staticmethod
    def _check_fingerprint_locked(
        receipt: EffectReceipt,
        fingerprint: ActionFingerprint,
    ) -> None:
        if (
            receipt.action_kind != fingerprint.action_kind
            or receipt.payload_fingerprint != fingerprint.payload_fingerprint
        ):
            raise EffectReceiptConflict(
                "effective action identity was reused with a different payload"
            )

    def _reserve_capacity_locked(self) -> None:
        if len(self._effect_receipts) >= self._max_effect_receipts:
            raise EffectReceiptCapacityError("effect receipt capacity is exhausted")

    def _new_receipt_locked(
        self,
        fingerprint: ActionFingerprint,
        *,
        category: EffectCategory,
        phase: EffectPhase,
        native_phase_id: str | None,
        binding_generation: int | None,
        outcome: ActionOutcome | None,
        now: datetime,
    ) -> EffectReceipt:
        gateway_id = self._namespace_id
        if gateway_id is None:
            raise RuntimeError("Gateway store namespace is not initialized")
        receipt = EffectReceipt(
            gateway_id=gateway_id,
            action_kind=fingerprint.action_kind,
            action_key=fingerprint.action_key,
            payload_fingerprint=fingerprint.payload_fingerprint,
            category=category,
            phase=phase,
            native_phase_id=native_phase_id,
            binding_generation=binding_generation,
            outcome=outcome,
            created_at=now,
            updated_at=now,
        )
        validate_effect_receipt(receipt)
        return receipt

    def _assert_fence_locked(self, fence: RuntimeLease) -> None:
        self._require_open_locked()
        current = self._lease
        now = self._now_locked()
        if (
            current is None
            or current.gateway_id != fence.gateway_id
            or current.owner_token != fence.owner_token
            or current.epoch != fence.epoch
            or current.expires_at <= now
        ):
            raise StaleRuntimeFence("Gateway runtime lease is stale or expired")

    def _now_locked(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("MemoryGatewayStore clock must return a timezone-aware datetime")
        return now

    def _require_open_locked(self) -> None:
        if self._closed:
            raise RuntimeError("Gateway store is closed")

    def _require_open_unlocked(self) -> None:
        if self._closed:
            raise RuntimeError("Gateway store is closed")


class _MemoryGatewayStoreSession:
    def __init__(self, store: MemoryGatewayStore, lease: RuntimeLease) -> None:
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

    async def get(self, conversation: ConversationRef) -> ConversationBinding | None:
        return await self._store._read(lambda: self._store._bindings.get(conversation))

    async def put(
        self,
        binding: ConversationBinding,
        expected_generation: int | None = None,
    ) -> ConversationBinding:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._bindings.put(binding, expected_generation),
        )

    async def delete(
        self,
        conversation: ConversationRef,
        expected_generation: int | None = None,
    ) -> None:
        self._require_open()
        await self._store._mutate(
            self._lease,
            lambda: self._store._bindings.delete(conversation, expected_generation),
        )

    async def list_projection_routes(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[ThreadProjectionRoute, ...]:
        return await self._store._read(
            lambda: self._store._projections.list_projection_routes(thread_ref)
        )

    async def put_projection_route(self, route: ThreadProjectionRoute) -> ThreadProjectionRoute:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._projections.put_projection_route(route),
        )

    async def replace_thread_projection_routes(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._projections.replace_thread_projection_routes(route),
        )

    async def advance_projection_checkpoint(
        self,
        route_id: str,
        *,
        expected_agent_item_id: str | None,
        agent_item_id: str,
        checkpointed_at: datetime,
    ) -> ThreadProjectionRoute:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._projections.advance_projection_checkpoint(
                route_id,
                expected_agent_item_id=expected_agent_item_id,
                agent_item_id=agent_item_id,
                checkpointed_at=checkpointed_at,
            ),
        )

    async def delete_projection_routes(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef | None = None,
    ) -> int:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._projections.delete_projection_routes(
                thread_ref,
                conversation_ref,
            ),
        )

    async def get_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> TurnReplyCorrelation | None:
        return await self._store._read(
            lambda: self._store._projections.get_turn_reply_correlation(thread_ref, turn_id)
        )

    async def list_turn_reply_correlations(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[TurnReplyCorrelation, ...]:
        return await self._store._read(
            lambda: self._store._projections.list_turn_reply_correlations(thread_ref)
        )

    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._projections.put_turn_reply_correlation(correlation),
        )

    async def delete_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> bool:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._projections.delete_turn_reply_correlation(thread_ref, turn_id),
        )

    async def delete_turn_reply_correlations(
        self,
        *,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._projections.delete_turn_reply_correlations(
                thread_ref=thread_ref,
                conversation_ref=conversation_ref,
                older_than=older_than,
            ),
        )

    async def claim(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> IdempotencyClaimStatus:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._idempotency.claim(scope, key, owner_token=owner_token),
        )

    async def mark_side_effect_started(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        self._require_open()
        await self._store._mutate(
            self._lease,
            lambda: self._store._idempotency.mark_side_effect_started(
                scope,
                key,
                owner_token=owner_token,
            ),
        )

    async def refresh(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        self._require_open()
        await self._store._mutate(
            self._lease,
            lambda: self._store._idempotency.refresh(scope, key, owner_token=owner_token),
        )

    async def complete(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        self._require_open()
        await self._store._mutate(
            self._lease,
            lambda: self._store._idempotency.complete(scope, key, owner_token=owner_token),
        )

    async def release(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        self._require_open()
        await self._store._mutate(
            self._lease,
            lambda: self._store._idempotency.release(scope, key, owner_token=owner_token),
        )

    async def list_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
    ) -> tuple[RequestRouteCorrelation, ...]:
        return await self._store._read(
            lambda: self._store._request_correlations.list_request_correlations(
                request_ref=request_ref,
                thread_ref=thread_ref,
                conversation_ref=conversation_ref,
            )
        )

    async def put_request_correlation(
        self,
        correlation: RequestRouteCorrelation,
    ) -> RequestRouteCorrelation:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._request_correlations.put_request_correlation(correlation),
        )

    async def transition_request_correlations(
        self,
        request_ref: RequestRef,
        *,
        expected_states: tuple[RequestRouteState, ...],
        state: RequestRouteState,
        updated_at: datetime,
    ) -> tuple[RequestRouteCorrelation, ...]:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._request_correlations.transition_request_correlations(
                request_ref,
                expected_states=expected_states,
                state=state,
                updated_at=updated_at,
            ),
        )

    async def delete_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._request_correlations.delete_request_correlations(
                request_ref=request_ref,
                thread_ref=thread_ref,
                conversation_ref=conversation_ref,
                older_than=older_than,
            ),
        )

    async def get_delivery_submission(
        self,
        submission_id: str,
    ) -> DeliverySubmissionRecord | None:
        return await self._store._read(
            lambda: self._store._delivery_submissions.get_delivery_submission(submission_id)
        )

    async def reserve_delivery_submission(
        self,
        record: DeliverySubmissionRecord,
    ) -> DeliveryReservation:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._delivery_submissions.reserve_delivery_submission(record),
        )

    async def update_delivery_destination(
        self,
        submission_id: str,
        destination_delivery_id: str,
        *,
        expected_state: DeliverySubmissionState,
        destination: DestinationDeliveryRecord,
    ) -> DeliverySubmissionRecord:
        self._require_open()
        return await self._store._mutate(
            self._lease,
            lambda: self._store._delivery_submissions.update_delivery_destination(
                submission_id,
                destination_delivery_id,
                expected_state=expected_state,
                destination=destination,
            ),
        )

    def _require_open(self) -> None:
        if self._closed:
            raise StaleRuntimeFence("Gateway store session is closed")


def _generation_from_maps(
    conversation_ref: ConversationRef,
    bindings: dict[ConversationRef, ConversationBinding],
    floors: dict[ConversationRef, int],
) -> int:
    current = bindings.get(conversation_ref)
    return max(
        current.generation if current is not None else 0,
        floors.get(conversation_ref, 0),
    )


def _mutation_conversation(plan: StoreMutationPlan) -> ConversationRef:
    return plan.conversation_ref


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


def _apply_route_plan(
    routes: dict[tuple[ThreadRef, ConversationRef], ThreadProjectionRoute],
    plan: StoreMutationPlan,
    *,
    now: datetime,
) -> ThreadProjectionRoute | None:
    route_upsert = plan.route_upsert
    route_delete_id = plan.route_delete_id
    if route_upsert is not None:
        candidate = replace(route_upsert, updated_at=now)
        _reject_conflicting_route_id(routes.values(), candidate)
        key = (candidate.thread_ref, candidate.conversation_ref)
        stored = _merge_projection_route(routes.get(key), candidate)
        routes[key] = stored
        return stored
    if route_delete_id is not None:
        for key, route in tuple(routes.items()):
            if route.route_id == route_delete_id:
                if route.conversation_ref != plan.conversation_ref:
                    raise ProjectionRouteConflict("route deletion belongs to another Conversation")
                routes.pop(key)
                return route
    return None


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


__all__ = ["MemoryGatewayStore"]

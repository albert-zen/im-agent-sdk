"""Public coherent GatewayStore port and private fenced session contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ...applications.contract import WorkspaceIdentity
from ...interaction.messages import ConversationRef
from .effects import (
    ActionError,
    ActionFingerprint,
    ActionOutcome,
    BindingTarget,
    EffectCategory,
    EffectPhase,
    EffectReceipt,
    StoreMutationRequest,
)
from .repository_contracts import (
    BindingRepository,
    DeliverySubmissionRepository,
    IdempotencyRepository,
    ProjectionRouteRepository,
    RequestCorrelationRepository,
)
from .state_contracts import ThreadProjectionRoute


class GatewayStoreError(RuntimeError):
    """Base class for explicit coherent-store failures."""


class GatewayNamespaceConflict(GatewayStoreError):
    """One store instance was reused for a different gateway namespace."""


class RuntimeLeaseUnavailable(GatewayStoreError):
    """Another unexpired runtime owns the store namespace."""


class StaleRuntimeFence(GatewayStoreError):
    """A mutation carried a stale owner token, epoch, or expired lease."""


class EffectReceiptConflict(GatewayStoreError):
    """One effective action identity was reused with a changed fingerprint."""


class EffectReceiptCapacityError(GatewayStoreError):
    """A new effect identity exceeded the finite non-evicting receipt bound."""


class WorkspaceIdentityConflict(GatewayStoreError):
    """A stable workspace ID was reused with a changed root fingerprint."""


@dataclass(frozen=True, slots=True)
class RuntimeLease:
    gateway_id: str
    owner_token: str
    epoch: int
    expires_at: datetime


@runtime_checkable
class GatewayStoreSession(
    BindingRepository,
    ProjectionRouteRepository,
    IdempotencyRepository,
    RequestCorrelationRepository,
    DeliverySubmissionRepository,
    Protocol,
):
    """Private lease-bound owner seam used by Gateway runtime components."""

    @property
    def lease(self) -> RuntimeLease: ...

    async def renew(self, *, lease_duration_seconds: float) -> RuntimeLease: ...

    async def release_runtime(self) -> None: ...

    async def check_workspace_identities(
        self,
        identities: tuple[WorkspaceIdentity, ...],
    ) -> None: ...

    async def get_binding_generation(self, conversation_ref: ConversationRef) -> int: ...

    async def get_store_mutation_receipt(
        self,
        fingerprint: ActionFingerprint,
    ) -> EffectReceipt | None: ...

    async def commit_store_preflight_failure(
        self,
        fingerprint: ActionFingerprint,
        *,
        error: ActionError,
    ) -> EffectReceipt: ...

    async def commit_store_mutation(
        self,
        request: StoreMutationRequest,
    ) -> EffectReceipt: ...

    async def reserve_effect(
        self,
        fingerprint: ActionFingerprint,
        *,
        category: EffectCategory,
        native_phase_id: str,
        conversation_ref: ConversationRef | None = None,
    ) -> EffectReceipt: ...

    async def get_effect_receipt(
        self,
        fingerprint: ActionFingerprint,
    ) -> EffectReceipt | None: ...

    async def mark_native_side_effect_started(
        self,
        fingerprint: ActionFingerprint,
    ) -> tuple[EffectReceipt, bool]: ...

    async def record_effect_preflight_failure(
        self,
        fingerprint: ActionFingerprint,
        *,
        error: ActionError,
    ) -> EffectReceipt: ...

    async def record_effect_outcome(
        self,
        fingerprint: ActionFingerprint,
        *,
        phase: EffectPhase,
        outcome: ActionOutcome,
    ) -> EffectReceipt: ...

    async def commit_workflow_binding(
        self,
        fingerprint: ActionFingerprint,
        *,
        binding_target: BindingTarget,
        route: ThreadProjectionRoute | None,
    ) -> EffectReceipt: ...

    async def close(self) -> None: ...


@runtime_checkable
class GatewayStore(Protocol):
    """One public v1 persistence choice for one coherent Gateway namespace."""

    @property
    def max_effect_receipts(self) -> int: ...

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> GatewayStoreSession: ...

    async def close(self) -> None: ...


def validate_runtime_lease(lease: RuntimeLease) -> None:
    from ...interaction.operations import ContractViolation, require_identifier

    require_identifier(lease.gateway_id, "gateway_id")
    require_identifier(lease.owner_token, "owner_token")
    if lease.epoch < 1:
        raise ContractViolation("runtime lease epoch must be positive")
    if lease.expires_at.tzinfo is None:
        raise ContractViolation("runtime lease expiry must include a timezone")


def validate_lease_duration(value: float) -> None:
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("lease_duration_seconds must be a positive finite number")
    if not math.isfinite(float(value)) or value <= 0:
        raise ValueError("lease_duration_seconds must be a positive finite number")


__all__ = [
    "EffectReceiptCapacityError",
    "EffectReceiptConflict",
    "GatewayNamespaceConflict",
    "GatewayStore",
    "GatewayStoreError",
    "RuntimeLease",
    "RuntimeLeaseUnavailable",
    "StaleRuntimeFence",
    "WorkspaceIdentityConflict",
]

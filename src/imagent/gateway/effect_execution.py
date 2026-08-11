"""Typed block-B execution seam for block-C scoped action surfaces."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol, TypeAlias, runtime_checkable

from ..applications.contract import ProjectRef, ThreadRef
from .outcomes import Failed, OutcomeUnknown, Partial, Succeeded
from .persistence.effects import (
    ActionError,
    ActionErrorCode,
    ActionFingerprint,
    ActionOutcome,
    BindingTarget,
    CreateBindingWorkflowKind,
    CreateBindingWorkflowRequest,
    EffectCategory,
    EffectPhase,
    EffectReceipt,
    EffectValue,
    KnownNativeOutcome,
    NativeMutationRequest,
    StableReferenceKind,
    StoreMutationRequest,
    validate_action_error,
    validate_create_binding_workflow_request,
    validate_effect_value,
    validate_native_mutation_request,
    validate_store_mutation_request,
)
from .persistence.state_contracts import ThreadProjectionRoute
from .persistence.store import (
    EffectReceiptCapacityError,
    EffectReceiptConflict,
    GatewayStoreError,
    GatewayStoreSession,
    StaleRuntimeFence,
)
from .routing.projection_routes import derive_projection_route_id

NativeEffectInvoker: TypeAlias = Callable[[str], Awaitable[KnownNativeOutcome]]
NativeEffectPreflight: TypeAlias = Callable[[], Awaitable[ActionError | None]]
NativeEffectReconciler: TypeAlias = Callable[
    [str],
    Awaitable[KnownNativeOutcome | None],
]
StoreEffectPreflight: TypeAlias = Callable[[], Awaitable[ActionError | None]]
StoreEffectCommitFence: TypeAlias = Callable[
    [],
    AbstractAsyncContextManager[ActionError | None],
]
WorkflowEffectCommitFence: TypeAlias = Callable[
    [str],
    AbstractAsyncContextManager[ActionError | None],
]


@runtime_checkable
class GatewayEffectExecutor(Protocol):
    """Operation-agnostic durable execution; no public action or store escape."""

    async def execute_store_mutation(
        self,
        request: StoreMutationRequest,
        *,
        preflight: StoreEffectPreflight | None = None,
        commit_fence: StoreEffectCommitFence | None = None,
    ) -> ActionOutcome: ...

    async def replay_store_mutation(
        self,
        request: StoreMutationRequest,
    ) -> ActionOutcome | None: ...

    async def execute_native_mutation(
        self,
        request: NativeMutationRequest,
        *,
        invoke: NativeEffectInvoker,
        preflight: NativeEffectPreflight | None = None,
        reconcile: NativeEffectReconciler | None = None,
    ) -> ActionOutcome: ...

    async def execute_create_binding_workflow(
        self,
        request: CreateBindingWorkflowRequest,
        *,
        invoke: NativeEffectInvoker,
        preflight: NativeEffectPreflight | None = None,
        reconcile: NativeEffectReconciler | None = None,
        commit_fence: WorkflowEffectCommitFence | None = None,
    ) -> ActionOutcome: ...


class StoreBackedGatewayEffectExecutor:
    """Execute effects through one lease-bound coherent store session."""

    def __init__(self, session: GatewayStoreSession) -> None:
        self._session = session

    async def replay_store_mutation(
        self,
        request: StoreMutationRequest,
    ) -> ActionOutcome | None:
        """Read one terminal receipt before process-local route preparation."""

        validate_store_mutation_request(request)
        return await self._read_store_mutation_receipt(request.fingerprint)

    async def execute_store_mutation(
        self,
        request: StoreMutationRequest,
        *,
        preflight: StoreEffectPreflight | None = None,
        commit_fence: StoreEffectCommitFence | None = None,
    ) -> ActionOutcome:
        validate_store_mutation_request(request)
        preflight_error: ActionError | None = None
        if preflight is not None or commit_fence is not None:
            replay = await self._read_store_mutation_receipt(request.fingerprint)
            if replay is not None:
                return replay
        if preflight is not None:
            preflight_result = await self._run_store_preflight(
                request.fingerprint,
                preflight,
            )
            if preflight_result is not None and not isinstance(
                preflight_result,
                ActionError,
            ):
                return preflight_result
            preflight_error = preflight_result
        try:
            if commit_fence is None:
                receipt = await self._commit_store_result(request, preflight_error)
            else:
                async with commit_fence() as fence_error:
                    if fence_error is not None:
                        validate_action_error(fence_error)
                        return Failed(fence_error)
                    receipt = await self._commit_store_result(request, preflight_error)
        except asyncio.CancelledError:
            recovered = await self._recover_store_mutation_outcome(request.fingerprint)
            if recovered is not None:
                return recovered
            raise
        except GatewayStoreError as error:
            recovered = await self._recover_store_mutation_outcome(request.fingerprint)
            if recovered is not None:
                return recovered
            return Failed(ActionError(_store_error_code(error)))
        except Exception:
            recovered = await self._recover_store_mutation_outcome(request.fingerprint)
            if recovered is not None:
                return recovered
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        if receipt.outcome is None:
            raise RuntimeError("terminal store mutation receipt has no outcome")
        return receipt.outcome

    async def _read_store_mutation_receipt(
        self,
        fingerprint: ActionFingerprint,
    ) -> ActionOutcome | None:
        try:
            receipt = await self._session.get_store_mutation_receipt(fingerprint)
        except EffectReceiptConflict:
            return Failed(ActionError(ActionErrorCode.CONFLICT))
        except EffectReceiptCapacityError:
            return Failed(ActionError(ActionErrorCode.CAPACITY_EXHAUSTED))
        except StaleRuntimeFence:
            return Failed(ActionError(ActionErrorCode.STALE_RUNTIME))
        except GatewayStoreError:
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        except Exception:
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        if receipt is None:
            return None
        if receipt.phase is not EffectPhase.TERMINAL:
            raise RuntimeError("Gateway store receipt has an invalid phase")
        return _required_outcome(receipt)

    async def _recover_store_mutation_outcome(
        self,
        fingerprint: ActionFingerprint,
    ) -> ActionOutcome | None:
        try:
            receipt = await _shielded_store_receipt_read(self._session, fingerprint)
        except EffectReceiptConflict:
            return Failed(ActionError(ActionErrorCode.CONFLICT))
        if receipt is None:
            return None
        if receipt.phase is not EffectPhase.TERMINAL:
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        return _required_outcome(receipt)

    async def _commit_store_result(
        self,
        request: StoreMutationRequest,
        preflight_error: ActionError | None,
    ) -> EffectReceipt:
        if preflight_error is None:
            return await self._session.commit_store_mutation(request)
        return await self._session.commit_store_preflight_failure(
            request.fingerprint,
            error=preflight_error,
        )

    async def _run_store_preflight(
        self,
        fingerprint: ActionFingerprint,
        preflight: StoreEffectPreflight,
    ) -> ActionError | ActionOutcome | None:
        try:
            error = await preflight()
        except asyncio.CancelledError:
            recovered = await self._recover_store_mutation_outcome(fingerprint)
            if recovered is not None:
                return recovered
            raise
        except Exception:
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        if error is None:
            return None
        validate_action_error(error)
        return error

    async def execute_native_mutation(
        self,
        request: NativeMutationRequest,
        *,
        invoke: NativeEffectInvoker,
        preflight: NativeEffectPreflight | None = None,
        reconcile: NativeEffectReconciler | None = None,
    ) -> ActionOutcome:
        validate_native_mutation_request(request)
        phase_id = request.fingerprint.phase_id("native")
        try:
            receipt = await self._session.reserve_effect(
                request.fingerprint,
                category=request.category,
                native_phase_id=phase_id,
            )
        except EffectReceiptConflict:
            return Failed(ActionError(ActionErrorCode.CONFLICT))
        except EffectReceiptCapacityError:
            return Failed(ActionError(ActionErrorCode.CAPACITY_EXHAUSTED))
        except StaleRuntimeFence:
            return Failed(ActionError(ActionErrorCode.STALE_RUNTIME))
        except GatewayStoreError:
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        except Exception:
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        if receipt.phase is EffectPhase.TERMINAL:
            return _required_outcome(receipt)
        if receipt.phase is EffectPhase.NATIVE_SIDE_EFFECT_STARTED:
            return await self._reconcile_native(
                request.fingerprint,
                phase_id,
                reconcile=reconcile,
                terminal=True,
            )
        if receipt.phase is not EffectPhase.RESERVED:
            raise RuntimeError("primitive native receipt has an invalid phase")
        preflight_outcome = await self._run_native_preflight(
            request.fingerprint,
            preflight,
        )
        if preflight_outcome is not None:
            return preflight_outcome
        acquired, fence_outcome = await self._enter_native_fence(request.fingerprint)
        if fence_outcome is not None:
            return fence_outcome
        if not acquired:
            return await self.execute_native_mutation(
                request,
                invoke=invoke,
                preflight=preflight,
                reconcile=reconcile,
            )
        try:
            known = await invoke(phase_id)
            _validate_known_native_outcome(known)
        except asyncio.CancelledError:
            return await self._persist_unknown(request.fingerprint)
        except Exception:
            return await self._persist_unknown(request.fingerprint)
        try:
            receipt = await self._session.record_effect_outcome(
                request.fingerprint,
                phase=EffectPhase.TERMINAL,
                outcome=known,
            )
        except asyncio.CancelledError:
            receipt = await _shielded_receipt_read(self._session, request.fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            return _unknown_outcome()
        except StaleRuntimeFence:
            receipt = await _shielded_receipt_read(self._session, request.fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            return _unknown_outcome(ActionErrorCode.STALE_RUNTIME)
        except GatewayStoreError:
            receipt = await _shielded_receipt_read(self._session, request.fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            return _unknown_outcome(ActionErrorCode.STORE_FAILURE)
        except Exception:
            receipt = await _shielded_receipt_read(self._session, request.fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            return _unknown_outcome(ActionErrorCode.STORE_FAILURE)
        return _required_outcome(receipt)

    async def execute_create_binding_workflow(
        self,
        request: CreateBindingWorkflowRequest,
        *,
        invoke: NativeEffectInvoker,
        preflight: NativeEffectPreflight | None = None,
        reconcile: NativeEffectReconciler | None = None,
        commit_fence: WorkflowEffectCommitFence | None = None,
    ) -> ActionOutcome:
        validate_create_binding_workflow_request(request)
        phase_id = request.fingerprint.phase_id("native_create")
        try:
            receipt = await self._session.reserve_effect(
                request.fingerprint,
                category=EffectCategory.WORKFLOW,
                native_phase_id=phase_id,
                conversation_ref=request.conversation_ref,
            )
        except EffectReceiptConflict:
            return Failed(ActionError(ActionErrorCode.CONFLICT))
        except EffectReceiptCapacityError:
            return Failed(ActionError(ActionErrorCode.CAPACITY_EXHAUSTED))
        except StaleRuntimeFence:
            return Failed(ActionError(ActionErrorCode.STALE_RUNTIME))
        except GatewayStoreError:
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        except Exception:
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        if receipt.phase is EffectPhase.TERMINAL:
            return _required_outcome(receipt)
        if receipt.phase is EffectPhase.NATIVE_RESULT_KNOWN:
            return await self._commit_known_workflow(
                request,
                receipt,
                commit_fence=commit_fence,
            )
        if receipt.phase is EffectPhase.NATIVE_SIDE_EFFECT_STARTED:
            known = await self._reconcile_native(
                request.fingerprint,
                phase_id,
                reconcile=reconcile,
                terminal=False,
                known_validator=lambda known: _validate_workflow_known_outcome(request, known),
            )
            if isinstance(known, OutcomeUnknown):
                return known
            if isinstance(known, (Failed, Partial)):
                return known
            receipt = await self._session.get_effect_receipt(request.fingerprint)
            if receipt is None:
                return _unknown_outcome(ActionErrorCode.STORE_FAILURE)
            if receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            return await self._commit_known_workflow(
                request,
                receipt,
                commit_fence=commit_fence,
            )
        if receipt.phase is not EffectPhase.RESERVED:
            raise RuntimeError("workflow receipt has an invalid phase")
        preflight_outcome = await self._run_native_preflight(
            request.fingerprint,
            preflight,
        )
        if preflight_outcome is not None:
            return preflight_outcome
        acquired, fence_outcome = await self._enter_native_fence(request.fingerprint)
        if fence_outcome is not None:
            return fence_outcome
        if not acquired:
            return await self.execute_create_binding_workflow(
                request,
                invoke=invoke,
                preflight=preflight,
                reconcile=reconcile,
                commit_fence=commit_fence,
            )
        try:
            known = await invoke(phase_id)
            _validate_known_native_outcome(known)
            _validate_workflow_known_outcome(request, known)
        except asyncio.CancelledError:
            return await self._persist_unknown(request.fingerprint)
        except Exception:
            return await self._persist_unknown(request.fingerprint)
        if isinstance(known, Failed):
            try:
                terminal_receipt = await self._session.record_effect_outcome(
                    request.fingerprint,
                    phase=EffectPhase.TERMINAL,
                    outcome=known,
                )
            except asyncio.CancelledError:
                terminal_receipt = await _shielded_receipt_read(
                    self._session,
                    request.fingerprint,
                )
                if terminal_receipt is not None and terminal_receipt.phase is EffectPhase.TERMINAL:
                    return _required_outcome(terminal_receipt)
                return _unknown_outcome()
            except StaleRuntimeFence:
                terminal_receipt = await _shielded_receipt_read(
                    self._session,
                    request.fingerprint,
                )
                if terminal_receipt is not None and terminal_receipt.phase is EffectPhase.TERMINAL:
                    return _required_outcome(terminal_receipt)
                return _unknown_outcome(ActionErrorCode.STALE_RUNTIME)
            except GatewayStoreError:
                terminal_receipt = await _shielded_receipt_read(
                    self._session,
                    request.fingerprint,
                )
                if terminal_receipt is not None and terminal_receipt.phase is EffectPhase.TERMINAL:
                    return _required_outcome(terminal_receipt)
                return _unknown_outcome(ActionErrorCode.STORE_FAILURE)
            except Exception:
                terminal_receipt = await _shielded_receipt_read(
                    self._session,
                    request.fingerprint,
                )
                if terminal_receipt is not None and terminal_receipt.phase is EffectPhase.TERMINAL:
                    return _required_outcome(terminal_receipt)
                return _unknown_outcome(ActionErrorCode.STORE_FAILURE)
            return _required_outcome(terminal_receipt)
        try:
            receipt = await self._session.record_effect_outcome(
                request.fingerprint,
                phase=EffectPhase.NATIVE_RESULT_KNOWN,
                outcome=known,
            )
        except asyncio.CancelledError:
            receipt = await _shielded_receipt_read(self._session, request.fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            if receipt is not None and receipt.phase is EffectPhase.NATIVE_RESULT_KNOWN:
                return Partial(known.value, ActionError(ActionErrorCode.STORE_FAILURE))
            return Partial(known.value, ActionError(ActionErrorCode.STORE_FAILURE))
        except StaleRuntimeFence:
            receipt = await _shielded_receipt_read(self._session, request.fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            return Partial(known.value, ActionError(ActionErrorCode.STALE_RUNTIME))
        except GatewayStoreError:
            receipt = await _shielded_receipt_read(self._session, request.fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            if receipt is not None and receipt.phase is EffectPhase.NATIVE_RESULT_KNOWN:
                return await self._commit_known_workflow(
                    request,
                    receipt,
                    commit_fence=commit_fence,
                )
            return Partial(known.value, ActionError(ActionErrorCode.STORE_FAILURE))
        except Exception:
            receipt = await _shielded_receipt_read(self._session, request.fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            if receipt is not None and receipt.phase is EffectPhase.NATIVE_RESULT_KNOWN:
                return await self._commit_known_workflow(
                    request,
                    receipt,
                    commit_fence=commit_fence,
                )
            return Partial(known.value, ActionError(ActionErrorCode.STORE_FAILURE))
        return await self._commit_known_workflow(
            request,
            receipt,
            commit_fence=commit_fence,
        )

    async def _enter_native_fence(
        self,
        fingerprint: ActionFingerprint,
    ) -> tuple[bool, ActionOutcome | None]:
        try:
            receipt, acquired = await self._session.mark_native_side_effect_started(fingerprint)
            if receipt.phase is EffectPhase.RESERVED:
                raise RuntimeError("native side-effect fence did not advance")
            return acquired, None
        except asyncio.CancelledError:
            receipt = await _shielded_receipt_read(self._session, fingerprint)
            if receipt is None or receipt.phase is EffectPhase.RESERVED:
                raise
            if receipt.phase is EffectPhase.TERMINAL:
                return False, _required_outcome(receipt)
            return False, await self._persist_unknown(fingerprint)
        except StaleRuntimeFence:
            return False, Failed(ActionError(ActionErrorCode.STALE_RUNTIME))
        except GatewayStoreError:
            return False, Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        except Exception:
            return False, Failed(ActionError(ActionErrorCode.STORE_FAILURE))

    async def _reconcile_native(
        self,
        fingerprint: ActionFingerprint,
        phase_id: str,
        *,
        reconcile: NativeEffectReconciler | None,
        terminal: bool,
        known_validator: Callable[[KnownNativeOutcome], None] | None = None,
    ) -> ActionOutcome:
        if reconcile is None:
            return await self._persist_unknown(fingerprint)
        try:
            known = await reconcile(phase_id)
            if known is not None:
                _validate_known_native_outcome(known)
                if known_validator is not None:
                    known_validator(known)
        except asyncio.CancelledError:
            return await self._persist_unknown(fingerprint)
        except Exception:
            return await self._persist_unknown(fingerprint)
        if known is None:
            return await self._persist_unknown(fingerprint)
        phase = (
            EffectPhase.TERMINAL
            if terminal or isinstance(known, Failed)
            else (EffectPhase.NATIVE_RESULT_KNOWN)
        )
        try:
            receipt = await self._session.record_effect_outcome(
                fingerprint,
                phase=phase,
                outcome=known,
            )
        except asyncio.CancelledError:
            receipt = await _shielded_receipt_read(self._session, fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            if (
                isinstance(known, Succeeded)
                and not terminal
                and receipt is not None
                and receipt.phase is EffectPhase.NATIVE_RESULT_KNOWN
            ):
                return known
            if isinstance(known, Succeeded) and not terminal:
                return Partial(known.value, ActionError(ActionErrorCode.STORE_FAILURE))
            return _unknown_outcome()
        except StaleRuntimeFence:
            receipt = await _shielded_receipt_read(self._session, fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            if isinstance(known, Succeeded) and not terminal:
                return Partial(known.value, ActionError(ActionErrorCode.STALE_RUNTIME))
            return _unknown_outcome(ActionErrorCode.STALE_RUNTIME)
        except GatewayStoreError:
            receipt = await _shielded_receipt_read(self._session, fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            if (
                isinstance(known, Succeeded)
                and not terminal
                and receipt is not None
                and receipt.phase is EffectPhase.NATIVE_RESULT_KNOWN
            ):
                return known
            return _unknown_outcome(ActionErrorCode.STORE_FAILURE)
        except Exception:
            receipt = await _shielded_receipt_read(self._session, fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            if (
                isinstance(known, Succeeded)
                and not terminal
                and receipt is not None
                and receipt.phase is EffectPhase.NATIVE_RESULT_KNOWN
            ):
                return known
            return _unknown_outcome(ActionErrorCode.STORE_FAILURE)
        return _required_outcome(receipt)

    async def _persist_unknown(self, fingerprint: ActionFingerprint) -> ActionOutcome:
        unknown = _unknown_outcome()
        task = asyncio.create_task(
            self._session.record_effect_outcome(
                fingerprint,
                phase=EffectPhase.NATIVE_SIDE_EFFECT_STARTED,
                outcome=unknown,
            )
        )
        try:
            receipt = await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                receipt = await task
            except BaseException:
                return unknown
        except Exception:
            return unknown
        return _required_outcome(receipt)

    async def _run_native_preflight(
        self,
        fingerprint: ActionFingerprint,
        preflight: NativeEffectPreflight | None,
    ) -> ActionOutcome | None:
        if preflight is None:
            return None
        try:
            error = await preflight()
        except asyncio.CancelledError:
            receipt = await _shielded_receipt_read(self._session, fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            if receipt is not None and receipt.phase is not EffectPhase.RESERVED:
                return _unknown_outcome()
            raise
        except Exception:
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        if error is None:
            return None
        validate_action_error(error)
        try:
            receipt = await self._session.record_effect_preflight_failure(
                fingerprint,
                error=error,
            )
        except asyncio.CancelledError:
            receipt = await _shielded_receipt_read(self._session, fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            if receipt is not None and receipt.phase is not EffectPhase.RESERVED:
                return _unknown_outcome()
            raise
        except StaleRuntimeFence:
            return Failed(ActionError(ActionErrorCode.STALE_RUNTIME))
        except GatewayStoreError:
            receipt = await _shielded_receipt_read(self._session, fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        except Exception:
            receipt = await _shielded_receipt_read(self._session, fingerprint)
            if receipt is not None and receipt.phase is EffectPhase.TERMINAL:
                return _required_outcome(receipt)
            return Failed(ActionError(ActionErrorCode.STORE_FAILURE))
        if receipt.phase is EffectPhase.TERMINAL:
            return _required_outcome(receipt)
        return _unknown_outcome()

    async def _commit_known_workflow(
        self,
        request: CreateBindingWorkflowRequest,
        receipt: EffectReceipt,
        *,
        commit_fence: WorkflowEffectCommitFence | None,
    ) -> ActionOutcome:
        if not isinstance(receipt.outcome, Succeeded):
            raise RuntimeError("known workflow phase has no created reference")
        try:
            _validate_workflow_created_value(request, receipt.outcome.value)
        except Exception:
            return Partial(
                receipt.outcome.value,
                ActionError(ActionErrorCode.STORE_FAILURE),
            )
        target, route = _workflow_binding_plan(request, receipt.outcome.value)
        try:
            if route is None or commit_fence is None:
                terminal = await self._session.commit_workflow_binding(
                    request.fingerprint,
                    binding_target=target,
                    route=route,
                )
            else:
                async with commit_fence(route.route_id) as fence_error:
                    if fence_error is not None:
                        validate_action_error(fence_error)
                        return Partial(receipt.outcome.value, fence_error)
                    terminal = await self._session.commit_workflow_binding(
                        request.fingerprint,
                        binding_target=target,
                        route=route,
                    )
        except asyncio.CancelledError:
            terminal = await _shielded_receipt_read(self._session, request.fingerprint)
            if terminal is not None and terminal.phase is EffectPhase.TERMINAL:
                return _required_outcome(terminal)
            return Partial(
                receipt.outcome.value,
                ActionError(ActionErrorCode.STORE_FAILURE),
            )
        except StaleRuntimeFence:
            terminal = await _shielded_receipt_read(self._session, request.fingerprint)
            if terminal is not None and terminal.phase is EffectPhase.TERMINAL:
                return _required_outcome(terminal)
            return Partial(
                receipt.outcome.value,
                ActionError(ActionErrorCode.STALE_RUNTIME),
            )
        except GatewayStoreError:
            terminal = await _shielded_receipt_read(self._session, request.fingerprint)
            if terminal is not None and terminal.phase is EffectPhase.TERMINAL:
                return _required_outcome(terminal)
            return Partial(
                receipt.outcome.value,
                ActionError(ActionErrorCode.STORE_FAILURE),
            )
        except Exception:
            terminal = await _shielded_receipt_read(self._session, request.fingerprint)
            if terminal is not None and terminal.phase is EffectPhase.TERMINAL:
                return _required_outcome(terminal)
            return Partial(
                receipt.outcome.value,
                ActionError(ActionErrorCode.STORE_FAILURE),
            )
        return _required_outcome(terminal)


async def _shielded_receipt_read(
    session: GatewayStoreSession,
    fingerprint: ActionFingerprint,
) -> EffectReceipt | None:
    task = asyncio.create_task(session.get_effect_receipt(fingerprint))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            return await task
        except BaseException:
            return None
    except Exception:
        return None


async def _shielded_store_receipt_read(
    session: GatewayStoreSession,
    fingerprint: ActionFingerprint,
) -> EffectReceipt | None:
    task = asyncio.create_task(session.get_store_mutation_receipt(fingerprint))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            return await task
        except EffectReceiptConflict:
            raise
        except BaseException:
            return None
    except EffectReceiptConflict:
        raise
    except Exception:
        return None


def _store_error_code(error: GatewayStoreError) -> ActionErrorCode:
    if isinstance(error, EffectReceiptConflict):
        return ActionErrorCode.CONFLICT
    if isinstance(error, EffectReceiptCapacityError):
        return ActionErrorCode.CAPACITY_EXHAUSTED
    if isinstance(error, StaleRuntimeFence):
        return ActionErrorCode.STALE_RUNTIME
    return ActionErrorCode.STORE_FAILURE


def _required_outcome(receipt: EffectReceipt) -> ActionOutcome:
    if receipt.outcome is None:
        raise RuntimeError("effect receipt has no outcome")
    return receipt.outcome


def _unknown_outcome(
    code: ActionErrorCode = ActionErrorCode.NATIVE_OUTCOME_UNKNOWN,
) -> OutcomeUnknown[ActionError]:
    return OutcomeUnknown(ActionError(code))


def _validate_known_native_outcome(outcome: KnownNativeOutcome) -> None:
    if isinstance(outcome, Succeeded):
        validate_effect_value(outcome.value)
        return
    if isinstance(outcome, Failed):
        validate_action_error(outcome.error)
        return
    raise TypeError("native effect callback must return Succeeded or Failed")


def _validate_workflow_created_value(
    request: CreateBindingWorkflowRequest,
    value: EffectValue,
) -> None:
    reference = value.reference
    if reference is None:
        raise ValueError("create workflow native success requires a resource reference")
    if request.kind is CreateBindingWorkflowKind.CREATE_AND_SELECT_PROJECT:
        if (
            reference.kind is not StableReferenceKind.PROJECT
            or reference.application_instance_id != request.application_ref.application_instance_id
        ):
            raise ValueError("create-and-select returned a different Project scope")
    elif request.kind is CreateBindingWorkflowKind.CREATE_AND_BIND_THREAD:
        project_ref = request.project_ref
        if (
            project_ref is None
            or reference.kind is not StableReferenceKind.THREAD
            or reference.application_instance_id != project_ref.application_instance_id
            or reference.project_id != project_ref.project_id
        ):
            raise ValueError("create-and-bind returned a different Thread scope")


def _validate_workflow_known_outcome(
    request: CreateBindingWorkflowRequest,
    outcome: KnownNativeOutcome,
) -> None:
    if isinstance(outcome, Succeeded):
        _validate_workflow_created_value(request, outcome.value)


def _workflow_binding_plan(
    request: CreateBindingWorkflowRequest,
    value: EffectValue,
) -> tuple[BindingTarget, ThreadProjectionRoute | None]:
    reference = value.reference
    if reference is None:
        raise ValueError("create workflow has no created reference")
    resource = reference.to_value()
    if request.kind is CreateBindingWorkflowKind.CREATE_AND_SELECT_PROJECT:
        if not isinstance(resource, ProjectRef):
            raise ValueError("create-and-select result is not a Project")
        project_ref = resource
        return (
            BindingTarget(
                request.conversation_ref,
                application_ref=request.application_ref,
                project_ref=project_ref,
            ),
            None,
        )
    if not isinstance(resource, ThreadRef):
        raise ValueError("create-and-bind result is not a Thread")
    thread_ref = resource
    target = BindingTarget(
        request.conversation_ref,
        application_ref=request.application_ref,
        project_ref=thread_ref.project_ref,
        thread_ref=thread_ref,
    )
    route = None
    if request.foreground_route:
        route = ThreadProjectionRoute(
            route_id=derive_projection_route_id(thread_ref, request.conversation_ref),
            thread_ref=thread_ref,
            conversation_ref=request.conversation_ref,
        )
    return target, route


__all__ = [
    "GatewayEffectExecutor",
    "NativeEffectInvoker",
    "NativeEffectPreflight",
    "NativeEffectReconciler",
    "StoreEffectPreflight",
    "StoreEffectCommitFence",
    "WorkflowEffectCommitFence",
    "StoreBackedGatewayEffectExecutor",
]

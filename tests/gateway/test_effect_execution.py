from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from imagent.applications.contract import ApplicationRef, ProjectRef, ThreadRef
from imagent.gateway.effect_execution import (
    GatewayEffectExecutor,
    StoreBackedGatewayEffectExecutor,
)
from imagent.gateway.outcomes import (
    Failed,
    OutcomeStatus,
    OutcomeUnknown,
    Partial,
    Succeeded,
)
from imagent.gateway.persistence.effects import (
    ActionError,
    ActionErrorCode,
    ActionIdentity,
    BindingTarget,
    CreateBindingWorkflowKind,
    CreateBindingWorkflowRequest,
    EffectCategory,
    EffectPhase,
    EffectValue,
    NativeMutationRequest,
    StableReference,
    StoreMutationPlan,
    StoreMutationRequest,
    derive_action_fingerprint,
)
from imagent.gateway.persistence.memory_store import MemoryGatewayStore
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.persistence.store import (
    GatewayStore,
    GatewayStoreError,
    GatewayStoreSession,
)
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation


class GatewayEffectExecutionParityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self._temporary.cleanup()

    def _factories(self, *, capacity: int = 16) -> tuple[Callable[[], GatewayStore], ...]:
        path = Path(self._temporary.name) / f"effects-{capacity}.sqlite3"
        return (
            lambda: MemoryGatewayStore(max_effect_receipts=capacity),
            lambda: SQLiteGatewayStore(path, max_effect_receipts=capacity),
        )

    async def _executor(self, store: GatewayStore):
        session = await store.acquire_runtime(
            gateway_id="gateway",
            owner_token="owner",
            lease_duration_seconds=30,
        )
        executor = StoreBackedGatewayEffectExecutor(session)
        self.assertIsInstance(executor, GatewayEffectExecutor)
        return session, executor

    def test_outcome_algebra_and_action_fingerprints_are_closed_and_stable(self) -> None:
        self.assertEqual(
            {status.value for status in OutcomeStatus},
            {"succeeded", "failed", "partial", "outcome_unknown"},
        )
        conversation = ConversationRef("channel", "conversation")
        identity = ActionIdentity(
            "gateway",
            "principal",
            "conversation.select",
            "action",
            conversation,
        )
        first = derive_action_fingerprint(identity, (("project_id", "project"),))
        repeated = derive_action_fingerprint(identity, (("project_id", "project"),))
        changed = derive_action_fingerprint(identity, (("project_id", "other"),))
        other_principal = derive_action_fingerprint(
            ActionIdentity(
                "gateway",
                "other-principal",
                "conversation.select",
                "action",
                conversation,
            ),
            (("project_id", "project"),),
        )
        self.assertEqual(first, repeated)
        self.assertEqual(first.action_key, changed.action_key)
        self.assertNotEqual(first.payload_fingerprint, changed.payload_fingerprint)
        self.assertNotEqual(first.action_key, other_principal.action_key)
        self.assertNotEqual(first.phase_id("native"), first.phase_id("binding"))
        with self.assertRaises(ContractViolation):
            derive_action_fingerprint(identity, (("number", 2**64),))

    async def test_primitive_native_effect_is_fenced_once_and_replayed(self) -> None:
        request = NativeMutationRequest(_fingerprint("native", "application.interrupt"))
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session, executor = await self._executor(store)
                phase_ids: list[str] = []

                async def invoke(phase_id: str):
                    phase_ids.append(phase_id)
                    receipt = await session.get_effect_receipt(request.fingerprint)
                    self.assertIsNotNone(receipt)
                    assert receipt is not None
                    self.assertEqual(receipt.phase, EffectPhase.NATIVE_SIDE_EFFECT_STARTED)
                    return Succeeded(EffectValue())

                first = await executor.execute_native_mutation(request, invoke=invoke)
                second = await executor.execute_native_mutation(request, invoke=invoke)
                self.assertEqual(first, Succeeded(EffectValue()))
                self.assertEqual(second, first)
                self.assertEqual(phase_ids, [request.fingerprint.phase_id("native")])
                await session.release_runtime()
                await store.close()

    async def test_native_preflight_runs_before_first_fence_and_never_on_terminal_replay(
        self,
    ) -> None:
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session, executor = await self._executor(store)
                request = NativeMutationRequest(
                    _fingerprint("authorized", "request.respond"),
                    category=EffectCategory.REQUEST_RESPONSE,
                )
                events: list[str] = []

                async def authorize():
                    receipt = await session.get_effect_receipt(request.fingerprint)
                    assert receipt is not None
                    self.assertIs(receipt.phase, EffectPhase.RESERVED)
                    events.append("preflight")
                    return None

                async def invoke(_phase_id: str):
                    events.append("invoke")
                    return Succeeded(EffectValue())

                first = await executor.execute_native_mutation(
                    request,
                    invoke=invoke,
                    preflight=authorize,
                )

                async def stale_reauthorization():
                    raise AssertionError("terminal replay must precede authorization")

                replay = await executor.execute_native_mutation(
                    request,
                    invoke=invoke,
                    preflight=stale_reauthorization,
                )
                self.assertEqual(first, Succeeded(EffectValue()))
                self.assertEqual(replay, first)
                self.assertEqual(events, ["preflight", "invoke"])

                rejected = NativeMutationRequest(
                    _fingerprint("rejected", "request.respond"),
                    category=EffectCategory.REQUEST_RESPONSE,
                )
                invoked = False

                async def reject():
                    return ActionError(ActionErrorCode.NATIVE_REJECTED)

                async def rejected_invoke(_phase_id: str):
                    nonlocal invoked
                    invoked = True
                    return Succeeded(EffectValue())

                rejection = await executor.execute_native_mutation(
                    rejected,
                    invoke=rejected_invoke,
                    preflight=reject,
                )
                replayed_rejection = await executor.execute_native_mutation(
                    rejected,
                    invoke=rejected_invoke,
                    preflight=stale_reauthorization,
                )
                self.assertEqual(
                    rejection,
                    Failed(ActionError(ActionErrorCode.NATIVE_REJECTED)),
                )
                self.assertEqual(replayed_rejection, rejection)
                self.assertFalse(invoked)
                await session.release_runtime()
                await store.close()

    async def test_invalid_post_fence_callback_values_become_sticky_unknown(self) -> None:
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session, executor = await self._executor(store)

                invalid_native = NativeMutationRequest(
                    _fingerprint("invalid-native", "application.delete")
                )
                native_invocations = 0

                async def invalid_invoke(_phase_id: str):
                    nonlocal native_invocations
                    native_invocations += 1
                    return Succeeded(EffectValue(binding_generation=True))

                native_outcome = await executor.execute_native_mutation(
                    invalid_native,
                    invoke=invalid_invoke,
                )
                self.assertIsInstance(native_outcome, OutcomeUnknown)
                self.assertEqual(
                    await executor.execute_native_mutation(
                        invalid_native,
                        invoke=invalid_invoke,
                    ),
                    native_outcome,
                )
                self.assertEqual(native_invocations, 1)
                native_receipt = await session.get_effect_receipt(invalid_native.fingerprint)
                assert native_receipt is not None
                self.assertIsInstance(native_receipt.outcome, OutcomeUnknown)

                workflow = CreateBindingWorkflowRequest(
                    _fingerprint("invalid-workflow", "conversation.create_and_select"),
                    ConversationRef("channel", "conversation"),
                    CreateBindingWorkflowKind.CREATE_AND_SELECT_PROJECT,
                    ApplicationRef("app"),
                )

                async def invalid_workflow(_phase_id: str):
                    return Succeeded(
                        EffectValue(
                            reference=StableReference.from_value(ProjectRef("other-app", "project"))
                        )
                    )

                workflow_outcome = await executor.execute_create_binding_workflow(
                    workflow,
                    invoke=invalid_workflow,
                )
                self.assertIsInstance(workflow_outcome, OutcomeUnknown)

                reconcile_request = NativeMutationRequest(
                    _fingerprint("invalid-reconcile", "application.delete")
                )
                phase_id = reconcile_request.fingerprint.phase_id("native")
                await session.reserve_effect(
                    reconcile_request.fingerprint,
                    category=EffectCategory.NATIVE,
                    native_phase_id=phase_id,
                )
                await session.mark_native_side_effect_started(reconcile_request.fingerprint)

                async def invalid_reconcile(_phase_id: str):
                    return Succeeded(EffectValue(binding_generation=-1))

                reconciled = await executor.execute_native_mutation(
                    reconcile_request,
                    invoke=invalid_invoke,
                    reconcile=invalid_reconcile,
                )
                self.assertIsInstance(reconciled, OutcomeUnknown)
                await session.release_runtime()
                await store.close()

    async def test_concurrent_same_action_never_invokes_native_twice(self) -> None:
        request = NativeMutationRequest(_fingerprint("concurrent", "application.interrupt"))
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session, executor = await self._executor(store)
                entered = asyncio.Event()
                release = asyncio.Event()
                calls = 0

                async def invoke(_phase_id: str):
                    nonlocal calls
                    calls += 1
                    entered.set()
                    await release.wait()
                    return Succeeded(EffectValue())

                first_task = asyncio.create_task(
                    executor.execute_native_mutation(request, invoke=invoke)
                )
                await entered.wait()
                second = await executor.execute_native_mutation(request, invoke=invoke)
                release.set()
                first = await first_task
                self.assertIsInstance(second, OutcomeUnknown)
                self.assertEqual(first, Succeeded(EffectValue()))
                self.assertEqual(calls, 1)
                await session.release_runtime()
                await store.close()

    async def test_native_exception_and_cancellation_become_sticky_unknown(self) -> None:
        for error in (RuntimeError("lost response"), asyncio.CancelledError()):
            for factory in self._factories():
                store = factory()
                with self.subTest(store=type(store).__name__, error=type(error).__name__):
                    session, executor = await self._executor(store)
                    request = NativeMutationRequest(
                        _fingerprint(type(error).__name__, "application.delete")
                    )
                    calls = 0

                    async def invoke(_phase_id: str):
                        nonlocal calls
                        calls += 1
                        raise error

                    first = await executor.execute_native_mutation(request, invoke=invoke)
                    second = await executor.execute_native_mutation(request, invoke=invoke)
                    self.assertIsInstance(first, OutcomeUnknown)
                    self.assertEqual(second, first)
                    self.assertEqual(calls, 1)
                    await session.release_runtime()
                    await store.close()

    async def test_cancellation_ack_after_native_fence_never_enters_callback(self) -> None:
        store = MemoryGatewayStore()
        session = await store.acquire_runtime(
            gateway_id="gateway",
            owner_token="owner",
            lease_duration_seconds=30,
        )
        faulted = _FaultSession(session, "mark_native_side_effect_started", "cancel_after")
        executor = StoreBackedGatewayEffectExecutor(cast(GatewayStoreSession, faulted))
        request = NativeMutationRequest(_fingerprint("fence-cancel", "application.delete"))
        called = False

        async def invoke(_phase_id: str):
            nonlocal called
            called = True
            return Succeeded(EffectValue())

        outcome = await executor.execute_native_mutation(request, invoke=invoke)
        self.assertIsInstance(outcome, OutcomeUnknown)
        self.assertFalse(called)
        receipt = await session.get_effect_receipt(request.fingerprint)
        self.assertIsNotNone(receipt)
        assert receipt is not None
        self.assertEqual(receipt.phase, EffectPhase.NATIVE_SIDE_EFFECT_STARTED)
        await session.release_runtime()
        await store.close()

    async def test_committed_receipts_win_over_lost_acknowledgements(self) -> None:
        conversation = ConversationRef("channel", "conversation")

        store = MemoryGatewayStore()
        session = await store.acquire_runtime(
            gateway_id="gateway",
            owner_token="owner",
            lease_duration_seconds=30,
        )
        store_executor = StoreBackedGatewayEffectExecutor(
            cast(
                GatewayStoreSession,
                _FaultSession(session, "commit_store_mutation", "error_after"),
            )
        )
        store_outcome = await store_executor.execute_store_mutation(
            StoreMutationRequest(
                _fingerprint("store-ack", "conversation.select", conversation),
                StoreMutationPlan(
                    conversation_ref=conversation,
                    binding_target=BindingTarget(conversation, ApplicationRef("app")),
                    expected_generation=0,
                ),
            )
        )
        self.assertIsInstance(store_outcome, Succeeded)

        native_executor = StoreBackedGatewayEffectExecutor(
            cast(
                GatewayStoreSession,
                _FaultSession(session, "record_effect_outcome", "error_after"),
            )
        )

        async def native(_phase_id: str):
            return Succeeded(EffectValue())

        native_outcome = await native_executor.execute_native_mutation(
            NativeMutationRequest(_fingerprint("native-ack", "application.interrupt")),
            invoke=native,
        )
        self.assertEqual(native_outcome, Succeeded(EffectValue()))

        project = ProjectRef("app", "project")
        thread = ThreadRef(project, "thread")
        workflow_executor = StoreBackedGatewayEffectExecutor(
            cast(
                GatewayStoreSession,
                _FaultSession(session, "commit_workflow_binding", "error_after"),
            )
        )

        async def create(_phase_id: str):
            return Succeeded(EffectValue(reference=StableReference.from_value(thread)))

        workflow_outcome = await workflow_executor.execute_create_binding_workflow(
            CreateBindingWorkflowRequest(
                _fingerprint("workflow-ack", "conversation.create_and_bind", conversation),
                conversation,
                CreateBindingWorkflowKind.CREATE_AND_BIND_THREAD,
                ApplicationRef("app"),
                project,
            ),
            invoke=create,
        )
        self.assertIsInstance(workflow_outcome, Succeeded)
        await session.release_runtime()
        await store.close()

    async def test_cancellation_boundaries_follow_the_durable_phase(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        store = MemoryGatewayStore()
        session = await store.acquire_runtime(
            gateway_id="gateway",
            owner_token="owner",
            lease_duration_seconds=30,
        )
        invoked = 0

        async def native(_phase_id: str):
            nonlocal invoked
            invoked += 1
            return Succeeded(EffectValue())

        before_reservation = NativeMutationRequest(
            _fingerprint("cancel-before-reserve", "application.interrupt")
        )
        executor = StoreBackedGatewayEffectExecutor(
            cast(
                GatewayStoreSession,
                _FaultSession(session, "reserve_effect", "cancel_before"),
            )
        )
        with self.assertRaises(asyncio.CancelledError):
            await executor.execute_native_mutation(before_reservation, invoke=native)
        self.assertIsNone(await session.get_effect_receipt(before_reservation.fingerprint))

        before_fence = NativeMutationRequest(
            _fingerprint("cancel-before-fence", "application.interrupt")
        )
        executor = StoreBackedGatewayEffectExecutor(
            cast(
                GatewayStoreSession,
                _FaultSession(
                    session,
                    "mark_native_side_effect_started",
                    "cancel_before",
                ),
            )
        )
        with self.assertRaises(asyncio.CancelledError):
            await executor.execute_native_mutation(before_fence, invoke=native)
        reserved = await session.get_effect_receipt(before_fence.fingerprint)
        self.assertIsNotNone(reserved)
        assert reserved is not None
        self.assertEqual(reserved.phase, EffectPhase.RESERVED)
        recovered = await StoreBackedGatewayEffectExecutor(session).execute_native_mutation(
            before_fence,
            invoke=native,
        )
        self.assertIsInstance(recovered, Succeeded)

        project = ProjectRef("app", "cancel-project")
        thread = ThreadRef(project, "cancel-thread")
        workflow = CreateBindingWorkflowRequest(
            _fingerprint("cancel-known", "conversation.create_and_bind", conversation),
            conversation,
            CreateBindingWorkflowKind.CREATE_AND_BIND_THREAD,
            ApplicationRef("app"),
            project,
        )

        async def create(_phase_id: str):
            nonlocal invoked
            invoked += 1
            return Succeeded(EffectValue(reference=StableReference.from_value(thread)))

        executor = StoreBackedGatewayEffectExecutor(
            cast(
                GatewayStoreSession,
                _FaultSession(session, "record_effect_outcome", "cancel_after"),
            )
        )
        incomplete = await executor.execute_create_binding_workflow(
            workflow,
            invoke=create,
        )
        self.assertIsInstance(incomplete, Partial)
        known_receipt = await session.get_effect_receipt(workflow.fingerprint)
        self.assertIsNotNone(known_receipt)
        assert known_receipt is not None
        self.assertEqual(known_receipt.phase, EffectPhase.NATIVE_RESULT_KNOWN)
        completed = await StoreBackedGatewayEffectExecutor(
            cast(
                GatewayStoreSession,
                _FaultSession(session, "commit_workflow_binding", "cancel_after"),
            )
        ).execute_create_binding_workflow(workflow, invoke=create)
        self.assertIsInstance(completed, Succeeded)
        self.assertEqual(invoked, 2)
        await session.release_runtime()
        await store.close()

    async def test_negative_reconciliation_never_repeats_native_effect(self) -> None:
        request = NativeMutationRequest(_fingerprint("unknown", "application.activate"))
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session, executor = await self._executor(store)
                await session.reserve_effect(
                    request.fingerprint,
                    category=EffectCategory.NATIVE,
                    native_phase_id=request.fingerprint.phase_id("native"),
                )
                await session.mark_native_side_effect_started(request.fingerprint)
                invoked = False
                reconciled: list[str] = []

                async def invoke(_phase_id: str):
                    nonlocal invoked
                    invoked = True
                    return Succeeded(EffectValue())

                async def reconcile(phase_id: str):
                    reconciled.append(phase_id)
                    return None

                outcome = await executor.execute_native_mutation(
                    request,
                    invoke=invoke,
                    reconcile=reconcile,
                )
                self.assertIsInstance(outcome, OutcomeUnknown)
                self.assertFalse(invoked)
                self.assertEqual(reconciled, [request.fingerprint.phase_id("native")])
                await session.release_runtime()
                await store.close()

    async def test_takeover_after_native_fence_never_treats_negative_lookup_as_absence(
        self,
    ) -> None:
        path = Path(self._temporary.name) / "native-takeover.sqlite3"
        old_store = SQLiteGatewayStore(path)
        old_session = await old_store.acquire_runtime(
            gateway_id="gateway",
            owner_token="old-owner",
            lease_duration_seconds=0.05,
        )
        old_executor = StoreBackedGatewayEffectExecutor(old_session)
        request = NativeMutationRequest(_fingerprint("takeover", "application.delete"))
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def invoke(_phase_id: str):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return Succeeded(EffectValue())

        old_task = asyncio.create_task(old_executor.execute_native_mutation(request, invoke=invoke))
        await entered.wait()
        await asyncio.sleep(0.08)
        successor_store = SQLiteGatewayStore(path)
        successor_session = await successor_store.acquire_runtime(
            gateway_id="gateway",
            owner_token="successor",
            lease_duration_seconds=30,
        )
        release.set()
        old_outcome = await old_task
        self.assertIsInstance(old_outcome, OutcomeUnknown)

        successor_executor = StoreBackedGatewayEffectExecutor(successor_session)

        async def negative_lookup(_phase_id: str):
            return None

        successor_outcome = await successor_executor.execute_native_mutation(
            request,
            invoke=invoke,
            reconcile=negative_lookup,
        )
        self.assertIsInstance(successor_outcome, OutcomeUnknown)
        self.assertEqual(calls, 1)
        await successor_session.release_runtime()
        await successor_store.close()
        await old_store.close()

    async def test_create_and_bind_commits_binding_route_and_terminal_receipt(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        project = ProjectRef("app", "project")
        thread = ThreadRef(project, "thread")
        request = CreateBindingWorkflowRequest(
            _fingerprint("workflow", "conversation.create_and_bind", conversation),
            conversation,
            CreateBindingWorkflowKind.CREATE_AND_BIND_THREAD,
            ApplicationRef("app"),
            project,
        )
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session, executor = await self._executor(store)
                calls: list[str] = []
                preflight_calls = 0

                async def preflight():
                    nonlocal preflight_calls
                    preflight_calls += 1
                    receipt = await session.get_effect_receipt(request.fingerprint)
                    assert receipt is not None
                    self.assertIs(receipt.phase, EffectPhase.RESERVED)
                    return None

                async def invoke(phase_id: str):
                    calls.append(phase_id)
                    return Succeeded(EffectValue(reference=StableReference.from_value(thread)))

                outcome = await executor.execute_create_binding_workflow(
                    request,
                    invoke=invoke,
                    preflight=preflight,
                )
                self.assertIsInstance(outcome, Succeeded)
                assert isinstance(outcome, Succeeded)
                self.assertEqual(outcome.value.binding_generation, 1)
                self.assertIsNotNone(outcome.value.route_id)
                binding = await session.get(conversation)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(binding.thread_ref, thread)
                self.assertEqual(binding.generation, 1)
                routes = await session.list_projection_routes(thread)
                self.assertEqual(len(routes), 1)
                receipt = await session.get_effect_receipt(request.fingerprint)
                self.assertIsNotNone(receipt)
                assert receipt is not None
                self.assertEqual(receipt.phase, EffectPhase.TERMINAL)

                replay = await executor.execute_create_binding_workflow(
                    request,
                    invoke=invoke,
                    preflight=preflight,
                )
                self.assertEqual(replay, outcome)
                self.assertEqual(calls, [request.fingerprint.phase_id("native_create")])
                self.assertEqual(preflight_calls, 1)
                await session.release_runtime()
                await store.close()

    async def test_workflow_binding_cas_returns_partial_without_compensation(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        project = ProjectRef("app", "project")
        created = ThreadRef(project, "created-thread")
        request = CreateBindingWorkflowRequest(
            _fingerprint("stale-workflow", "conversation.create_and_bind", conversation),
            conversation,
            CreateBindingWorkflowKind.CREATE_AND_BIND_THREAD,
            ApplicationRef("app"),
            project,
        )
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session, executor = await self._executor(store)

                async def invoke(_phase_id: str):
                    newer = StoreMutationRequest(
                        _fingerprint("newer", "conversation.select", conversation),
                        StoreMutationPlan(
                            conversation_ref=conversation,
                            binding_target=BindingTarget(
                                conversation,
                                ApplicationRef("app"),
                                ProjectRef("app", "newer-project"),
                            ),
                            expected_generation=0,
                        ),
                    )
                    await session.commit_store_mutation(newer)
                    return Succeeded(EffectValue(reference=StableReference.from_value(created)))

                outcome = await executor.execute_create_binding_workflow(
                    request,
                    invoke=invoke,
                )
                self.assertIsInstance(outcome, Partial)
                assert isinstance(outcome, Partial)
                self.assertEqual(outcome.value.reference, StableReference.from_value(created))
                self.assertEqual(outcome.error.code, ActionErrorCode.STALE_BINDING)
                binding = await session.get(conversation)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(binding.project_ref, ProjectRef("app", "newer-project"))
                self.assertEqual(await session.list_projection_routes(created), ())
                await session.release_runtime()
                await store.close()

    async def test_native_failure_is_terminal_and_never_binds(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        request = CreateBindingWorkflowRequest(
            _fingerprint("rejected", "conversation.create_and_select", conversation),
            conversation,
            CreateBindingWorkflowKind.CREATE_AND_SELECT_PROJECT,
            ApplicationRef("app"),
        )
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session, executor = await self._executor(store)

                async def reject(_phase_id: str):
                    return Failed(ActionError(ActionErrorCode.NATIVE_REJECTED))

                outcome = await executor.execute_create_binding_workflow(
                    request,
                    invoke=reject,
                )
                self.assertEqual(
                    outcome,
                    Failed(ActionError(ActionErrorCode.NATIVE_REJECTED)),
                )
                self.assertIsNone(await session.get(conversation))
                await session.release_runtime()
                await store.close()

    async def test_all_effect_categories_share_capacity_before_callbacks(self) -> None:
        store = MemoryGatewayStore(max_effect_receipts=1)
        session, executor = await self._executor(store)
        conversation = ConversationRef("channel", "conversation")
        await executor.execute_store_mutation(
            StoreMutationRequest(
                _fingerprint("store", "conversation.select", conversation),
                StoreMutationPlan(
                    conversation_ref=conversation,
                    binding_target=BindingTarget(conversation, ApplicationRef("app")),
                    expected_generation=0,
                ),
            )
        )
        called = False

        async def invoke(_phase_id: str):
            nonlocal called
            called = True
            return Succeeded(EffectValue())

        outcome = await executor.execute_native_mutation(
            NativeMutationRequest(
                _fingerprint("native-at-capacity", "request.respond", conversation),
                category=EffectCategory.REQUEST_RESPONSE,
            ),
            invoke=invoke,
        )
        self.assertEqual(outcome, Failed(ActionError(ActionErrorCode.CAPACITY_EXHAUSTED)))
        self.assertFalse(called)
        await session.release_runtime()
        await store.close()


def _fingerprint(
    action_id: str,
    action_kind: str,
    conversation: ConversationRef | None = None,
):
    return derive_action_fingerprint(
        ActionIdentity("gateway", "principal", action_kind, action_id, conversation),
        (("stable_target", action_id),),
    )


class _FaultSession:
    def __init__(self, inner: GatewayStoreSession, method: str, mode: str) -> None:
        self._inner = inner
        self._method = method
        self._mode = mode
        self._raised = False

    def __getattr__(self, name: str) -> Any:
        attribute = cast(Callable[..., Any], getattr(self._inner, name))
        if name != self._method or self._raised:
            return attribute

        async def fault(*args: object, **kwargs: object) -> object:
            if self._mode == "cancel_before":
                self._raised = True
                raise asyncio.CancelledError
            await attribute(*args, **kwargs)
            self._raised = True
            if self._mode == "cancel_after":
                raise asyncio.CancelledError
            raise GatewayStoreError("response acknowledgement was lost")

        return fault


if __name__ == "__main__":
    unittest.main()

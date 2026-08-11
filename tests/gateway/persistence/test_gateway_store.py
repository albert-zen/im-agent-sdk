from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import tempfile
import unittest
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from imagent.applications.contract import (
    ApplicationRef,
    ProjectRef,
    ThreadRef,
    WorkspaceIdentity,
)
from imagent.gateway.outcomes import Failed, Succeeded
from imagent.gateway.persistence.effects import (
    ActionError,
    ActionErrorCode,
    ActionIdentity,
    BindingClearScope,
    BindingTarget,
    EffectCategory,
    EffectValue,
    RouteDeleteCondition,
    StableReference,
    StoreMutationPlan,
    StoreMutationRequest,
    derive_action_fingerprint,
)
from imagent.gateway.persistence.memory_store import MemoryGatewayStore
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.persistence.state_contracts import (
    ConversationBinding,
    ThreadProjectionRoute,
)
from imagent.gateway.persistence.store import (
    EffectReceiptCapacityError,
    EffectReceiptConflict,
    GatewayNamespaceConflict,
    GatewayStore,
    RuntimeLease,
    RuntimeLeaseUnavailable,
    StaleRuntimeFence,
    WorkspaceIdentityConflict,
    validate_runtime_lease,
)
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation


class _MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class GatewayStoreParityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self._temporary.cleanup()

    def _factories(self, *, capacity: int = 8) -> tuple[Callable[[], GatewayStore], ...]:
        sqlite_path = Path(self._temporary.name) / f"store-{capacity}.sqlite3"
        return (
            lambda: MemoryGatewayStore(max_effect_receipts=capacity),
            lambda: SQLiteGatewayStore(sqlite_path, max_effect_receipts=capacity),
        )

    def test_public_store_facade_resolves_exact_owners(self) -> None:
        from imagent.gateway import persistence

        self.assertIs(persistence.GatewayStore, GatewayStore)
        self.assertIs(persistence.MemoryGatewayStore, MemoryGatewayStore)
        self.assertIs(persistence.SQLiteGatewayStore, SQLiteGatewayStore)
        self.assertIs(persistence.StaleRuntimeFence, StaleRuntimeFence)
        self.assertIs(persistence.EffectReceiptConflict, EffectReceiptConflict)
        self.assertIn("GatewayStore", persistence.__all__)
        self.assertIn("MemoryGatewayStore", persistence.__all__)
        self.assertIn("SQLiteGatewayStore", persistence.__all__)

    def test_runtime_lease_requires_exact_fencing_types(self) -> None:
        valid = RuntimeLease(
            gateway_id="gateway",
            owner_token="owner",
            epoch=1,
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        validate_runtime_lease(valid)
        malformed = (
            RuntimeLease("gateway", "owner", cast(Any, True), valid.expires_at),
            RuntimeLease("gateway", "owner", cast(Any, 1.5), valid.expires_at),
            RuntimeLease("gateway", "owner", 0, valid.expires_at),
            RuntimeLease("gateway", "owner", 1, cast(Any, "later")),
            RuntimeLease("gateway", "owner", 1, datetime(2026, 1, 1)),
        )
        for lease in malformed:
            with self.subTest(lease=lease):
                with self.assertRaises(ContractViolation):
                    validate_runtime_lease(lease)

    async def test_public_stores_are_one_protocol_and_one_namespace(self) -> None:
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                self.assertIsInstance(store, GatewayStore)
                owner = await store.acquire_runtime(
                    gateway_id="gateway-a",
                    owner_token="owner-a",
                    lease_duration_seconds=30,
                )
                with self.assertRaises(RuntimeLeaseUnavailable):
                    await store.acquire_runtime(
                        gateway_id="gateway-a",
                        owner_token="owner-b",
                        lease_duration_seconds=30,
                    )
                with self.assertRaises(GatewayNamespaceConflict):
                    await store.acquire_runtime(
                        gateway_id="gateway-b",
                        owner_token="owner-a",
                        lease_duration_seconds=30,
                    )
                renewed = await owner.renew(lease_duration_seconds=60)
                self.assertEqual(renewed.epoch, 1)
                self.assertEqual(renewed.owner_token, "owner-a")
                await owner.release_runtime()
                successor = await store.acquire_runtime(
                    gateway_id="gateway-a",
                    owner_token="owner-b",
                    lease_duration_seconds=30,
                )
                self.assertEqual(successor.lease.epoch, 2)
                await successor.release_runtime()
                await store.close()

    async def test_same_owner_reacquisition_renews_the_shared_lease(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        binding = ConversationBinding(conversation, ApplicationRef("app"))
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                original = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=0.03,
                )
                alias = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=1,
                )
                self.assertEqual(alias.lease.epoch, original.lease.epoch)
                await asyncio.sleep(0.06)

                await original.put(binding)
                stored = await alias.get(conversation)
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertEqual(stored.conversation_ref, binding.conversation_ref)
                self.assertEqual(stored.application_ref, binding.application_ref)
                self.assertEqual(stored.generation, 1)
                await alias.release_runtime()
                await store.close()

    async def test_store_mutation_replays_before_current_state_and_detects_conflict(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        app = ApplicationRef("app")
        first_project = ProjectRef("app", "project-1")
        second_project = ProjectRef("app", "project-2")
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=30,
                )
                first = _store_request(
                    "first",
                    conversation,
                    BindingTarget(conversation, app, first_project),
                    expected_generation=0,
                )
                first_receipt = await session.commit_store_mutation(first)
                self.assertIsInstance(first_receipt.outcome, Succeeded)
                second = _store_request(
                    "second",
                    conversation,
                    BindingTarget(conversation, app, second_project),
                    expected_generation=1,
                )
                await session.commit_store_mutation(second)

                replay = await session.commit_store_mutation(first)
                self.assertEqual(replay, first_receipt)
                current = await session.get(conversation)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.project_ref, second_project)
                self.assertEqual(current.generation, 2)

                changed_payload = StoreMutationRequest(
                    first.fingerprint.__class__(
                        first.fingerprint.action_kind,
                        first.fingerprint.action_key,
                        second.fingerprint.payload_fingerprint,
                    ),
                    first.plan,
                )
                with self.assertRaises(EffectReceiptConflict):
                    await session.commit_store_mutation(changed_payload)
                await session.release_runtime()
                await store.close()

    async def test_store_route_conflict_commits_terminal_failure_without_binding(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        application = ApplicationRef("app")
        project = ProjectRef("app", "project")
        thread = ThreadRef(project, "thread")
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=30,
                )
                original = ThreadProjectionRoute("route-original", thread, conversation)
                await session.put_projection_route(original)
                request = StoreMutationRequest(
                    _fingerprint("conflict", "conversation.bind", conversation),
                    StoreMutationPlan(
                        conversation_ref=conversation,
                        binding_target=BindingTarget(
                            conversation,
                            application,
                            project,
                            thread,
                        ),
                        route_upsert=ThreadProjectionRoute(
                            "route-conflict",
                            thread,
                            conversation,
                        ),
                        expected_generation=0,
                    ),
                )
                receipt = await session.commit_store_mutation(request)
                self.assertIsInstance(receipt.outcome, Failed)
                assert isinstance(receipt.outcome, Failed)
                self.assertEqual(receipt.outcome.error.code, ActionErrorCode.CONFLICT)
                self.assertIsNone(await session.get(conversation))
                self.assertEqual(
                    tuple(route.route_id for route in await session.list_projection_routes()),
                    ("route-original",),
                )
                self.assertEqual(
                    await session.commit_store_mutation(request),
                    receipt,
                )
                await session.release_runtime()
                await store.close()

    async def test_route_only_observe_and_clear_do_not_write_binding(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        thread = ThreadRef(ProjectRef("app", "project"), "thread")
        route = ThreadProjectionRoute("route", thread, conversation)
        clear_outcomes = []
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=30,
                )
                observe = StoreMutationRequest(
                    _fingerprint("observe", "conversation.observe", conversation),
                    StoreMutationPlan(
                        conversation_ref=conversation,
                        route_upsert=route,
                        expected_generation=0,
                    ),
                )
                observed = await session.commit_store_mutation(observe)
                self.assertIsInstance(observed.outcome, Succeeded)
                self.assertIsNone(await session.get(conversation))
                self.assertEqual(await session.get_binding_generation(conversation), 0)
                stored_routes = await session.list_projection_routes(thread)
                self.assertEqual(tuple(item.route_id for item in stored_routes), (route.route_id,))

                clear = StoreMutationRequest(
                    _fingerprint("clear", "conversation.clear_observation", conversation),
                    StoreMutationPlan(
                        conversation_ref=conversation,
                        route_delete_id=route.route_id,
                        route_delete_condition=(RouteDeleteCondition.UNLESS_BOUND_TO_ROUTE_THREAD),
                        expected_generation=0,
                    ),
                )
                cleared = await session.commit_store_mutation(clear)
                self.assertIsInstance(cleared.outcome, Succeeded)
                self.assertEqual(await session.commit_store_mutation(clear), cleared)
                clear_outcomes.append(cleared.outcome)
                self.assertIsNone(await session.get(conversation))
                self.assertEqual(await session.get_binding_generation(conversation), 0)
                self.assertEqual(await session.list_projection_routes(thread), ())
                await session.release_runtime()
                await store.close()
        expected = Succeeded(
            EffectValue(
                reference=StableReference.from_value(thread),
                conversation_ref=conversation,
                binding_generation=0,
                route_id=route.route_id,
            )
        )
        self.assertEqual(clear_outcomes, [expected, expected])

    async def test_guarded_route_delete_preserves_current_bound_thread_atomically(self) -> None:
        conversation = ConversationRef("channel", "conversation-bound")
        application = ApplicationRef("app")
        project = ProjectRef("app", "project")
        thread = ThreadRef(project, "thread")
        route = ThreadProjectionRoute("route-bound", thread, conversation)
        protected_outcomes = []
        deleted_outcomes = []
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=30,
                )
                bound = StoreMutationRequest(
                    _fingerprint("bind-foreground", "conversation.bind_thread", conversation),
                    StoreMutationPlan(
                        conversation_ref=conversation,
                        binding_target=BindingTarget(
                            conversation,
                            application,
                            project,
                            thread,
                        ),
                        route_upsert=route,
                        expected_generation=0,
                    ),
                )
                await session.commit_store_mutation(bound)

                guarded_clear = StoreMutationRequest(
                    _fingerprint(
                        "clear-protected",
                        "conversation.clear_observation",
                        conversation,
                    ),
                    StoreMutationPlan(
                        conversation_ref=conversation,
                        route_delete_id=route.route_id,
                        route_delete_condition=(RouteDeleteCondition.UNLESS_BOUND_TO_ROUTE_THREAD),
                        expected_generation=1,
                    ),
                )
                protected = await session.commit_store_mutation(guarded_clear)
                protected_outcomes.append(protected.outcome)
                stored_routes = await session.list_projection_routes(thread)
                self.assertEqual(tuple(item.route_id for item in stored_routes), (route.route_id,))

                clear_binding = _clear_request(
                    "clear-bound-thread",
                    conversation,
                    BindingClearScope.THREAD,
                    expected_generation=1,
                )
                await session.commit_store_mutation(clear_binding)
                self.assertEqual(await session.commit_store_mutation(guarded_clear), protected)
                stored_routes = await session.list_projection_routes(thread)
                self.assertEqual(tuple(item.route_id for item in stored_routes), (route.route_id,))

                deletable_clear = StoreMutationRequest(
                    _fingerprint(
                        "clear-unbound-route",
                        "conversation.clear_observation",
                        conversation,
                    ),
                    StoreMutationPlan(
                        conversation_ref=conversation,
                        route_delete_id=route.route_id,
                        route_delete_condition=(RouteDeleteCondition.UNLESS_BOUND_TO_ROUTE_THREAD),
                        expected_generation=2,
                    ),
                )
                deleted = await session.commit_store_mutation(deletable_clear)
                deleted_outcomes.append(deleted.outcome)
                self.assertEqual(await session.list_projection_routes(thread), ())
                await session.release_runtime()
                await store.close()

        protected_expected = Succeeded(
            EffectValue(
                reference=StableReference.from_value(thread),
                conversation_ref=conversation,
                binding_generation=1,
                route_id=route.route_id,
            )
        )
        deleted_expected = Succeeded(
            EffectValue(
                reference=StableReference.from_value(thread),
                conversation_ref=conversation,
                binding_generation=2,
                route_id=route.route_id,
            )
        )
        self.assertEqual(protected_outcomes, [protected_expected, protected_expected])
        self.assertEqual(deleted_outcomes, [deleted_expected, deleted_expected])

    async def test_store_mutation_rejects_cross_conversation_route(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        other = ConversationRef("channel", "other")
        thread = ThreadRef(ProjectRef("app", "project"), "thread")
        request = StoreMutationRequest(
            _fingerprint("cross", "conversation.bind", conversation),
            StoreMutationPlan(
                conversation_ref=conversation,
                binding_target=BindingTarget(
                    conversation,
                    ApplicationRef("app"),
                    thread.project_ref,
                    thread,
                ),
                route_upsert=ThreadProjectionRoute("route", thread, other),
            ),
        )
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=30,
                )
                with self.assertRaises(ContractViolation):
                    await session.commit_store_mutation(request)
                await session.release_runtime()
                await store.close()

    async def test_every_hierarchical_clear_advances_binding_generation(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        application = ApplicationRef("app")
        project = ProjectRef("app", "project")
        thread = ThreadRef(project, "thread")
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=30,
                )
                await session.commit_store_mutation(
                    _store_request(
                        "binding-1",
                        conversation,
                        BindingTarget(conversation, application, project, thread),
                        expected_generation=0,
                    )
                )
                clear_thread = _clear_request(
                    "clear-thread",
                    conversation,
                    BindingClearScope.THREAD,
                    expected_generation=None,
                )
                clear_thread_receipt = await session.commit_store_mutation(clear_thread)
                binding = await session.get(conversation)
                assert binding is not None
                self.assertEqual(binding.project_ref, project)
                self.assertIsNone(binding.thread_ref)
                self.assertEqual(binding.generation, 2)

                await session.commit_store_mutation(
                    _store_request(
                        "binding-2",
                        conversation,
                        BindingTarget(conversation, application, project, thread),
                        expected_generation=2,
                    )
                )
                replay = await session.commit_store_mutation(clear_thread)
                self.assertEqual(replay, clear_thread_receipt)
                binding = await session.get(conversation)
                assert binding is not None
                self.assertEqual(binding.thread_ref, thread)
                self.assertEqual(binding.generation, 3)

                await session.commit_store_mutation(
                    _clear_request(
                        "clear-project",
                        conversation,
                        BindingClearScope.PROJECT,
                        expected_generation=3,
                    )
                )
                binding = await session.get(conversation)
                assert binding is not None
                self.assertEqual(binding.application_ref, application)
                self.assertIsNone(binding.project_ref)
                self.assertEqual(binding.generation, 4)

                await session.commit_store_mutation(
                    _clear_request(
                        "clear-application",
                        conversation,
                        BindingClearScope.APPLICATION,
                        expected_generation=4,
                    )
                )
                self.assertIsNone(await session.get(conversation))
                self.assertEqual(await session.get_binding_generation(conversation), 5)
                await session.commit_store_mutation(
                    _clear_request(
                        "clear-application-again",
                        conversation,
                        BindingClearScope.APPLICATION,
                        expected_generation=5,
                    )
                )
                self.assertIsNone(await session.get(conversation))
                self.assertEqual(await session.get_binding_generation(conversation), 6)
                await session.release_runtime()
                await store.close()

    async def test_binding_generation_survives_delete_and_recreation(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        binding = ConversationBinding(
            conversation,
            ApplicationRef("app"),
            ProjectRef("app", "project"),
        )
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=30,
                )
                first = await session.put(binding, expected_generation=0)
                await session.delete(conversation, expected_generation=first.generation)
                self.assertEqual(await session.get_binding_generation(conversation), 2)
                recreated = await session.put(binding, expected_generation=2)
                self.assertEqual(recreated.generation, 3)
                await session.release_runtime()
                await store.close()

    async def test_shared_receipt_capacity_is_finite_and_non_evicting(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        for factory in self._factories(capacity=2):
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=30,
                )
                await session.commit_store_mutation(
                    _store_request(
                        "store",
                        conversation,
                        BindingTarget(conversation, ApplicationRef("app")),
                        expected_generation=0,
                    )
                )
                native = _fingerprint("native", "application.interrupt", conversation)
                await session.reserve_effect(
                    native,
                    category=EffectCategory.NATIVE,
                    native_phase_id=native.phase_id("native"),
                )
                third = _fingerprint("third", "application.delete", conversation)
                with self.assertRaises(EffectReceiptCapacityError):
                    await session.reserve_effect(
                        third,
                        category=EffectCategory.NATIVE,
                        native_phase_id=third.phase_id("native"),
                    )
                self.assertIsNotNone(await session.get_effect_receipt(native))
                await session.release_runtime()
                await store.close()

    async def test_workspace_fingerprint_conflict_retains_original_evidence(self) -> None:
        project = ProjectRef("app", "workspace")
        first = WorkspaceIdentity(project, "1" * 64)
        changed = WorkspaceIdentity(project, "2" * 64)
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway",
                    owner_token="owner",
                    lease_duration_seconds=30,
                )
                await session.check_workspace_identities((first,))
                with self.assertRaises(WorkspaceIdentityConflict):
                    await session.check_workspace_identities((changed,))
                await session.check_workspace_identities((first,))
                await session.release_runtime()
                await store.close()

    async def test_takeover_rejects_every_old_owner_mutation_family(self) -> None:
        clock = _MutableClock()
        store = MemoryGatewayStore(_clock=clock)
        old = await store.acquire_runtime(
            gateway_id="gateway",
            owner_token="old-owner",
            lease_duration_seconds=10,
        )
        clock.advance(11)
        current = await store.acquire_runtime(
            gateway_id="gateway",
            owner_token="new-owner",
            lease_duration_seconds=10,
        )
        conversation = ConversationRef("channel", "conversation")
        thread = ThreadRef(ProjectRef("app", "project"), "thread")
        fingerprint = _fingerprint("effect", "application.create", conversation)
        mutations = (
            lambda: old.put(ConversationBinding(conversation, ApplicationRef("app"))),
            lambda: old.put_projection_route(ThreadProjectionRoute("route", thread, conversation)),
            lambda: old.claim("scope", "key"),
            lambda: old.check_workspace_identities(
                (WorkspaceIdentity(thread.project_ref, "1" * 64),)
            ),
            lambda: old.reserve_effect(
                fingerprint,
                category=EffectCategory.NATIVE,
                native_phase_id=fingerprint.phase_id("native"),
            ),
            lambda: old.get_store_mutation_receipt(fingerprint),
            lambda: old.commit_store_preflight_failure(
                fingerprint,
                error=ActionError(ActionErrorCode.UNSUPPORTED),
            ),
        )
        for mutate in mutations:
            with self.assertRaises(StaleRuntimeFence):
                await mutate()
        await current.release_runtime()
        await store.close()

    async def test_sqlite_crash_expiry_increments_epoch_and_fences_old_session(self) -> None:
        path = Path(self._temporary.name) / "takeover.sqlite3"
        old_store = SQLiteGatewayStore(path)
        old = await old_store.acquire_runtime(
            gateway_id="gateway",
            owner_token="old-owner",
            lease_duration_seconds=0.001,
        )
        old_epoch = old.lease.epoch
        await asyncio.sleep(0.01)

        successor_store = SQLiteGatewayStore(path)
        successor = await successor_store.acquire_runtime(
            gateway_id="gateway",
            owner_token="new-owner",
            lease_duration_seconds=30,
        )
        self.assertGreater(successor.lease.epoch, old_epoch)
        with self.assertRaises(StaleRuntimeFence):
            await old.put(
                ConversationBinding(
                    ConversationRef("channel", "conversation"),
                    ApplicationRef("app"),
                )
            )
        fingerprint = _fingerprint(
            "sqlite-store-preflight",
            "conversation.select",
            ConversationRef("channel", "conversation"),
        )
        with self.assertRaises(StaleRuntimeFence):
            await old.get_store_mutation_receipt(fingerprint)
        with self.assertRaises(StaleRuntimeFence):
            await old.commit_store_preflight_failure(
                fingerprint,
                error=ActionError(ActionErrorCode.UNSUPPORTED),
            )
        await successor.release_runtime()
        await successor_store.close()
        await old_store.close()

    async def test_sqlite_restart_preserves_generation_receipts_and_no_sensitive_value(
        self,
    ) -> None:
        path = Path(self._temporary.name) / "restart.sqlite3"
        conversation = ConversationRef("channel", "conversation")
        secret_path = "/private/credential/token/native-transcript"
        fingerprint_value = hashlib.sha256(secret_path.encode()).hexdigest()
        first_store = SQLiteGatewayStore(path)
        first_session = await first_store.acquire_runtime(
            gateway_id="gateway",
            owner_token="owner-1",
            lease_duration_seconds=30,
        )
        await first_session.check_workspace_identities(
            (WorkspaceIdentity(ProjectRef("app", "workspace"), fingerprint_value),)
        )
        request = _store_request(
            "first",
            conversation,
            BindingTarget(conversation, ApplicationRef("app")),
            expected_generation=0,
        )
        receipt = await first_session.commit_store_mutation(request)
        await first_session.release_runtime()
        await first_store.close()

        second_store = SQLiteGatewayStore(path)
        second_session = await second_store.acquire_runtime(
            gateway_id="gateway",
            owner_token="owner-2",
            lease_duration_seconds=30,
        )
        self.assertEqual(await second_session.commit_store_mutation(request), receipt)
        self.assertEqual(await second_session.get_binding_generation(conversation), 1)
        await second_session.release_runtime()
        await second_store.close()

        self.assertNotIn(secret_path.encode(), path.read_bytes())
        with closing(sqlite3.connect(path)) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            self.assertIn("gateway_effect_receipts", tables)
            columns = {
                row[1]
                for table in tables
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
        forbidden = {"content", "prompt", "response_body", "credential", "path", "cwd"}
        self.assertTrue(forbidden.isdisjoint(columns))


def _fingerprint(
    action_id: str,
    action_kind: str,
    conversation: ConversationRef,
):
    return derive_action_fingerprint(
        ActionIdentity("gateway", "principal", action_kind, action_id, conversation),
        (("target_id", action_id),),
    )


def _store_request(
    action_id: str,
    conversation: ConversationRef,
    target: BindingTarget,
    *,
    expected_generation: int,
) -> StoreMutationRequest:
    return StoreMutationRequest(
        _fingerprint(action_id, "conversation.select", conversation),
        StoreMutationPlan(
            conversation_ref=conversation,
            binding_target=target,
            expected_generation=expected_generation,
        ),
    )


def _clear_request(
    action_id: str,
    conversation: ConversationRef,
    scope: BindingClearScope,
    *,
    expected_generation: int | None,
) -> StoreMutationRequest:
    return StoreMutationRequest(
        _fingerprint(action_id, f"conversation.clear_{scope.value}", conversation),
        StoreMutationPlan(
            conversation_ref=conversation,
            binding_clear=scope,
            expected_generation=expected_generation,
        ),
    )


if __name__ == "__main__":
    unittest.main()

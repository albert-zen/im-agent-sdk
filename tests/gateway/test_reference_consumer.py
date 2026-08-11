from __future__ import annotations

import ast
import asyncio
import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from examples.reference_consumer.application import ReferenceApplication
from examples.reference_consumer.gateway import build_reference_consumer
from examples.reference_consumer.interaction import (
    ReferenceChannel,
    ReferenceStatusService,
    build_command_registry,
)
from examples.reference_consumer.main import run_reference_consumer
from imagent import Gateway, GatewayLimits, MemoryGatewayStore, ProjectionPolicy, Succeeded
from imagent.applications.capabilities import SupportLevel
from imagent.applications.contract import (
    AgentInput,
    ApplicationRef,
    ProjectRef,
    ThreadHistory,
    ThreadRef,
)
from imagent.applications.operations import (
    ApplicationOperationFailed,
    CreateProject,
    CreateThread,
    GetThreadHistory,
    ListProjects,
    ProjectCreated,
    ProjectsListed,
    ThreadCreated,
    ThreadHistoryRead,
)
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.persistence.store import GatewayStoreSession, RuntimeLease
from imagent.gateway.projection.observation import ThreadProjectionRuntime
from imagent.interaction.channels import InboundAdmissionHandler, MessageHandler
from imagent.interaction.controllers import (
    CommandDefinition,
    CommandRegistry,
    CommandRegistryFrozenError,
    CommandResult,
)
from imagent.interaction.messages import ConversationRef, OutboundMessage, TextContent

_UNSAFE_HUGE_CLEANUP_DETAIL = "secret\x1b[2J\nline\u202e" + ("x" * 1_000_000)


class _CountingMemoryGatewayStore(MemoryGatewayStore):
    def __init__(self) -> None:
        super().__init__()
        self.acquire_count = 0
        self.close_count = 0

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ):
        self.acquire_count += 1
        return await super().acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )

    async def close(self) -> None:
        self.close_count += 1
        await super().close()


class _CountingSQLiteGatewayStore(SQLiteGatewayStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.acquire_count = 0
        self.close_count = 0

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ):
        self.acquire_count += 1
        return await super().acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )

    async def close(self) -> None:
        self.close_count += 1
        await super().close()


class _HugeCloseFailureStore(_CountingMemoryGatewayStore):
    async def close(self) -> None:
        await super().close()
        raise RuntimeError(_UNSAFE_HUGE_CLEANUP_DETAIL)


class _FailingRenewSession:
    def __init__(
        self,
        delegate: GatewayStoreSession,
        failed: asyncio.Event,
        allow_failure: asyncio.Event,
    ) -> None:
        self._delegate = delegate
        self._failed = failed
        self._allow_failure = allow_failure

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    async def renew(self, *, lease_duration_seconds: float):
        del lease_duration_seconds
        await self._allow_failure.wait()
        self._failed.set()
        raise RuntimeError("simulated lease renewal loss")


class _FailingRenewMemoryGatewayStore:
    def __init__(self) -> None:
        self._delegate = _CountingMemoryGatewayStore()
        self.renew_failed = asyncio.Event()
        self._allow_failure = asyncio.Event()

    @property
    def max_effect_receipts(self) -> int:
        return self._delegate.max_effect_receipts

    @property
    def close_count(self) -> int:
        return self._delegate.close_count

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ):
        session = await self._delegate.acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        return cast(
            GatewayStoreSession,
            _FailingRenewSession(session, self.renew_failed, self._allow_failure),
        )

    async def close(self) -> None:
        await self._delegate.close()

    def allow_renew_failure(self) -> None:
        self._allow_failure.set()


class _BlockingAcquireMemoryGatewayStore(_CountingMemoryGatewayStore):
    def __init__(self) -> None:
        super().__init__()
        self.first_acquired = asyncio.Event()
        self.allow_first_return = asyncio.Event()

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ):
        session = await super().acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        if self.acquire_count == 1:
            self.first_acquired.set()
            await self.allow_first_return.wait()
        return session


class _MalformedSession:
    def __init__(self) -> None:
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


class _MalformedSessionStore:
    max_effect_receipts = 16

    def __init__(self) -> None:
        self.acquire_count = 0
        self.close_count = 0
        self.session = _MalformedSession()

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> Any:
        del gateway_id, owner_token, lease_duration_seconds
        self.acquire_count += 1
        return self.session

    async def close(self) -> None:
        self.close_count += 1


class _ChangedLeaseSession:
    def __init__(self, delegate: GatewayStoreSession, lease: RuntimeLease) -> None:
        self._delegate = delegate
        self._lease = lease
        self.close_count = 0

    @property
    def lease(self) -> RuntimeLease:
        return self._lease

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    async def close(self) -> None:
        self.close_count += 1
        await self._delegate.close()


class _ChangedLeaseStore:
    def __init__(self, changed_fields: dict[str, Any]) -> None:
        self._delegate = _CountingMemoryGatewayStore()
        self._changed_fields = changed_fields
        self.session: _ChangedLeaseSession | None = None

    @property
    def max_effect_receipts(self) -> int:
        return self._delegate.max_effect_receipts

    @property
    def acquire_count(self) -> int:
        return self._delegate.acquire_count

    @property
    def close_count(self) -> int:
        return self._delegate.close_count

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> GatewayStoreSession:
        delegate = await self._delegate.acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        self.session = _ChangedLeaseSession(
            delegate,
            replace(delegate.lease, **self._changed_fields),
        )
        return cast(GatewayStoreSession, self.session)

    async def close(self) -> None:
        await self._delegate.close()


class _CleanupFailingChannel(ReferenceChannel):
    def __init__(
        self,
        channel_instance_id: str,
        *,
        fail_start: bool = False,
        fail_stop_before_cleanup: bool = False,
        fail_stop_after_cleanup: bool = False,
    ) -> None:
        super().__init__(channel_instance_id)
        self._fail_start = fail_start
        self._fail_stop_before_cleanup = fail_stop_before_cleanup
        self._fail_stop_after_cleanup = fail_stop_after_cleanup
        self.stop_count = 0

    async def start(
        self,
        on_message: MessageHandler,
        on_admission: InboundAdmissionHandler | None = None,
    ) -> None:
        await super().start(on_message, on_admission)
        if self._fail_start:
            raise RuntimeError(f"{self.channel_instance_id} start failed")

    async def stop(self) -> None:
        self.stop_count += 1
        if self._fail_stop_before_cleanup:
            raise RuntimeError(f"{self.channel_instance_id} stop failed")
        await super().stop()
        if self._fail_stop_after_cleanup:
            raise RuntimeError(f"{self.channel_instance_id} stop failed")


class _CleanupFailingApplication(ReferenceApplication):
    def __init__(
        self,
        application_instance_id: str = "reference-agent",
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
    ) -> None:
        super().__init__(application_instance_id=application_instance_id)
        self._fail_start = fail_start
        self._fail_stop = fail_stop
        self.stop_count = 0

    async def start(self) -> None:
        await super().start()
        if self._fail_start:
            raise RuntimeError(f"{self.ref.application_instance_id} start failed")

    async def stop(self) -> None:
        self.stop_count += 1
        await super().stop()
        if self._fail_stop:
            raise RuntimeError("application stop failed")


class _HugeCleanupApplication(ReferenceApplication):
    def __init__(self) -> None:
        super().__init__(application_instance_id="reference-huge-cleanup")
        self.stop_count = 0

    async def stop(self) -> None:
        self.stop_count += 1
        await super().stop()
        raise RuntimeError(_UNSAFE_HUGE_CLEANUP_DETAIL)


class _CleanupFailingRegistry(CommandRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1
        await super().close()
        raise RuntimeError("controller close failed")


class _BlockingStartChannel(ReferenceChannel):
    def __init__(self, on_blocked: Callable[[], None]) -> None:
        super().__init__()
        self._on_blocked = on_blocked
        self.start_blocked = asyncio.Event()
        self._never = asyncio.Event()

    async def start(
        self,
        on_message: MessageHandler,
        on_admission: InboundAdmissionHandler | None = None,
    ) -> None:
        await super().start(on_message, on_admission)
        self.start_blocked.set()
        self._on_blocked()
        await self._never.wait()


class ReferenceConsumerExampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_executable_entry_point_proves_the_v1_golden_path(self) -> None:
        with TemporaryDirectory() as cwd:
            report = await run_reference_consumer(cwd)

        self.assertIs(report.projection_policy, ProjectionPolicy.FOREGROUND_ONLY)
        self.assertEqual(report.project_count, 1)
        self.assertEqual(report.thread_count, 2)
        self.assertEqual(report.conversation_count, 2)
        self.assertEqual(report.command_count, 3)
        self.assertEqual(report.project_ref.project_id, "reference-project-1")
        self.assertEqual(report.first_thread_ref.thread_id, "reference-thread-1")
        self.assertEqual(report.second_thread_ref.thread_id, "reference-thread-2")
        self.assertEqual(report.first_thread_ref.project_ref, report.project_ref)
        self.assertEqual(report.second_thread_ref.project_ref, report.project_ref)

        conversation_a = ConversationRef("reference-channel", "conversation-a")
        conversation_b = ConversationRef("reference-channel", "conversation-b")
        self.assertEqual(report.initial_thread_conversations, (conversation_a,))
        self.assertEqual(
            report.shared_thread_conversations,
            (conversation_a, conversation_b),
        )
        self.assertEqual(report.switched_old_thread_conversations, (conversation_b,))
        self.assertEqual(report.switched_new_thread_conversations, (conversation_a,))
        self.assertEqual(report.switched_back_conversations, (conversation_a, conversation_b))

        self.assertEqual(report.worker_max_active, (1, 1))
        self.assertEqual(report.worker_subscription_calls, (1, 1))

        self.assertEqual(report.diagnostics_schema_version, 8)
        self.assertLess(report.diagnostics_size, 4_096)
        self.assertFalse(report.diagnostics_authoritative)
        self.assertTrue(report.adapters_stopped)
        self.assertEqual(report.active_workers_after_shutdown, 0)
        self.assertEqual(report.registry_active_after_shutdown, 0)
        self.assertEqual(report.owned_tasks_after_shutdown, 0)

    async def test_local_registry_is_frozen_and_rejects_duplicate_registration(self) -> None:
        consumer = build_reference_consumer()

        self.assertIsInstance(consumer.registry, CommandRegistry)
        self.assertTrue(consumer.registry.frozen)

        async def late_command(invocation, actions) -> CommandResult:
            del invocation, actions
            return CommandResult.text("late")

        with self.assertRaises(CommandRegistryFrozenError):
            consumer.registry.register(CommandDefinition(name="late", handler=late_command))

        registry = CommandRegistry()
        registry.register(CommandDefinition(name="duplicate", handler=late_command))
        with self.assertRaises(ValueError):
            registry.register(CommandDefinition(name="duplicate", handler=late_command))

    async def test_unfrozen_registry_fails_before_gateway_accepts_input(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _CountingMemoryGatewayStore()
        registry = CommandRegistry()

        async def retry_command(invocation, actions) -> CommandResult:
            del invocation, actions
            return CommandResult.text("retry")

        registry.register(CommandDefinition(name="retry", handler=retry_command))
        gateway = Gateway(
            gateway_id="reference-unfrozen",
            channels=[channel],
            applications=[application],
            store=store,
            controller=registry,
        )

        with self.assertRaisesRegex(ValueError, "frozen"):
            await gateway.start()
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertFalse(gateway.running)
        self.assertEqual(store.acquire_count, 0)

        registry.freeze()
        await gateway.start()
        self.assertTrue(gateway.running)
        self.assertEqual(store.acquire_count, 1)
        await gateway.stop()
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_gateway_acquires_one_coherent_store_and_closes_it_once(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-coherent-store",
            channels=[channel],
            applications=[application],
            store=store,
        )

        with self.assertRaisesRegex(RuntimeError, "running"):
            gateway.actions(
                channel.conversation("before-start").ref,
                actor="reference-user",
            )
        async with gateway:
            self.assertTrue(gateway.running)
            self.assertEqual(store.acquire_count, 1)
        self.assertFalse(gateway.running)
        self.assertEqual(store.acquire_count, 1)
        self.assertEqual(store.close_count, 1)

    async def test_scoped_create_and_bind_activates_observation_before_input(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-action-route")
        gateway = Gateway(
            gateway_id="reference-action-route",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:action-route:project",
                )
                project_ref = project.value.ref if isinstance(project, Succeeded) else None
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)
                thread = await actions.create_and_bind_thread(
                    project_ref,
                    action_id="reference:action-route:thread",
                )
                thread_ref = thread.value.ref if isinstance(thread, Succeeded) else None
                self.assertIsInstance(thread_ref, ThreadRef)
                assert isinstance(thread_ref, ThreadRef)
                self.assertEqual(application.active_observation_workers(thread_ref), 1)

                await application.send_input(
                    thread_ref,
                    AgentInput(
                        client_message_id="reference:action-route:native-output",
                        content=(TextContent("native-output-without-inbound"),),
                    ),
                )
                delivered = await conversation.wait_for_text(
                    "Neutral response: native-output-without-inbound"
                )
                self.assertEqual(len(delivered), 1)
                self.assertEqual(application.subscription_calls(thread_ref), 1)

    async def test_scoped_workflow_hands_one_worker_slot_to_the_new_thread(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-workflow-handoff")
        gateway = Gateway(
            gateway_id="reference-workflow-handoff",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            limits=GatewayLimits(projection_max_active_threads=1),
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:workflow-handoff:project",
                )
                self.assertIsInstance(project, Succeeded)
                assert isinstance(project, Succeeded)
                project_ref = project.value.ref
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)

                first = await actions.create_and_bind_thread(
                    project_ref,
                    action_id="reference:workflow-handoff:first",
                )
                second = await actions.create_and_bind_thread(
                    project_ref,
                    action_id="reference:workflow-handoff:second",
                )
                self.assertIsInstance(first, Succeeded)
                self.assertIsInstance(second, Succeeded)
                assert isinstance(first, Succeeded)
                assert isinstance(second, Succeeded)
                first_ref = first.value.ref
                second_ref = second.value.ref
                self.assertIsInstance(first_ref, ThreadRef)
                self.assertIsInstance(second_ref, ThreadRef)
                assert isinstance(first_ref, ThreadRef)
                assert isinstance(second_ref, ThreadRef)

                self.assertEqual(application.active_observation_workers(first_ref), 0)
                self.assertEqual(application.active_observation_workers(second_ref), 1)
                binding = await actions.get_binding()
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(binding.thread_ref, second_ref)

    async def test_cancellation_after_bind_commit_finishes_worker_handoff(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-cancel-handoff")
        gateway = Gateway(
            gateway_id="reference-cancel-handoff",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            limits=GatewayLimits(projection_max_active_threads=1),
        )

        reconciliation_started = asyncio.Event()
        allow_reconciliation = asyncio.Event()
        reconciliation_finished = asyncio.Event()
        block_reconciliation = asyncio.Event()
        original_reconcile = ThreadProjectionRuntime.reconcile_action_route

        async def blocking_reconcile(
            runtime: ThreadProjectionRuntime,
            route_id,
            action_lease=None,
        ):
            if block_reconciliation.is_set():
                reconciliation_started.set()
                await allow_reconciliation.wait()
            result = await original_reconcile(runtime, route_id, action_lease)
            if block_reconciliation.is_set():
                reconciliation_finished.set()
            return result

        with (
            TemporaryDirectory() as cwd,
            patch.object(
                ThreadProjectionRuntime,
                "reconcile_action_route",
                blocking_reconcile,
            ),
        ):
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:cancel-handoff:project",
                )
                self.assertIsInstance(project, Succeeded)
                assert isinstance(project, Succeeded)
                project_ref = project.value.ref
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)
                first = await actions.create_and_bind_thread(
                    project_ref,
                    action_id="reference:cancel-handoff:first",
                )
                self.assertIsInstance(first, Succeeded)
                assert isinstance(first, Succeeded)
                first_ref = first.value.ref
                self.assertIsInstance(first_ref, ThreadRef)
                assert isinstance(first_ref, ThreadRef)

                block_reconciliation.set()
                bind = asyncio.create_task(
                    actions.create_and_bind_thread(
                        project_ref,
                        action_id="reference:cancel-handoff:second",
                    )
                )
                await asyncio.wait_for(reconciliation_started.wait(), timeout=1.0)
                bind.cancel()
                await asyncio.sleep(0)
                self.assertFalse(bind.done())
                allow_reconciliation.set()
                result = await asyncio.wait_for(bind, timeout=1.0)
                self.assertIsInstance(result, Succeeded)

                self.assertTrue(reconciliation_finished.is_set())
                binding = await actions.get_binding()
                self.assertIsNotNone(binding)
                assert binding is not None
                second_ref = binding.thread_ref
                self.assertIsNotNone(second_ref)
                assert second_ref is not None
                self.assertNotEqual(second_ref, first_ref)
                self.assertEqual(application.active_observation_workers(first_ref), 0)
                self.assertEqual(application.active_observation_workers(second_ref), 1)

    async def test_scoped_observe_activates_an_unbound_remembered_route(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-observe-route")
        gateway = Gateway(
            gateway_id="reference-observe-route",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:observe-route:project",
                )
                project_ref = project.value.ref if isinstance(project, Succeeded) else None
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)
                thread = await actions.create_thread(
                    project_ref,
                    action_id="reference:observe-route:thread",
                )
                thread_ref = thread.value.ref if isinstance(thread, Succeeded) else None
                self.assertIsInstance(thread_ref, ThreadRef)
                assert isinstance(thread_ref, ThreadRef)
                observed = await actions.observe_thread(
                    thread_ref,
                    action_id="reference:observe-route:observe",
                )
                self.assertIsInstance(observed, Succeeded)
                self.assertEqual(application.active_observation_workers(thread_ref), 1)

                await application.send_input(
                    thread_ref,
                    AgentInput(
                        client_message_id="reference:observe-route:native-output",
                        content=(TextContent("observed-without-binding"),),
                    ),
                )
                delivered = await conversation.wait_for_text(
                    "Neutral response: observed-without-binding"
                )
                self.assertEqual(len(delivered), 1)
                self.assertEqual(application.subscription_calls(thread_ref), 1)

    async def test_terminal_bind_replay_ignores_current_worker_capacity(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-terminal-replay")
        gateway = Gateway(
            gateway_id="reference-terminal-replay",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            limits=GatewayLimits(projection_max_active_threads=1),
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:terminal-replay:project",
                )
                self.assertIsInstance(project, Succeeded)
                assert isinstance(project, Succeeded)
                project_ref = project.value.ref
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)
                first = await actions.create_thread(
                    project_ref,
                    action_id="reference:terminal-replay:first-thread",
                )
                second = await actions.create_thread(
                    project_ref,
                    action_id="reference:terminal-replay:second-thread",
                )
                self.assertIsInstance(first, Succeeded)
                self.assertIsInstance(second, Succeeded)
                assert isinstance(first, Succeeded)
                assert isinstance(second, Succeeded)
                first_ref = first.value.ref
                second_ref = second.value.ref
                self.assertIsInstance(first_ref, ThreadRef)
                self.assertIsInstance(second_ref, ThreadRef)
                assert isinstance(first_ref, ThreadRef)
                assert isinstance(second_ref, ThreadRef)

                first_binding = await actions.bind_thread(
                    first_ref,
                    action_id="reference:terminal-replay:first-bind",
                    expected_generation=project.value.binding_generation,
                )
                self.assertIsInstance(first_binding, Succeeded)
                assert isinstance(first_binding, Succeeded)
                cleared = await actions.clear_thread(
                    action_id="reference:terminal-replay:clear",
                    expected_generation=first_binding.value.binding_generation,
                )
                self.assertIsInstance(cleared, Succeeded)
                assert isinstance(cleared, Succeeded)
                second_binding = await actions.bind_thread(
                    second_ref,
                    action_id="reference:terminal-replay:second-bind",
                    expected_generation=cleared.value.binding_generation,
                )
                self.assertIsInstance(second_binding, Succeeded)
                self.assertEqual(application.active_observation_workers(first_ref), 0)
                self.assertEqual(application.active_observation_workers(second_ref), 1)

                replay = await actions.bind_thread(
                    first_ref,
                    action_id="reference:terminal-replay:first-bind",
                    expected_generation=project.value.binding_generation,
                )
                self.assertEqual(replay, first_binding)
                current = await actions.get_binding()
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.thread_ref, second_ref)
                self.assertEqual(application.active_observation_workers(first_ref), 0)
                self.assertEqual(application.active_observation_workers(second_ref), 1)

    async def test_invalid_limit_fails_before_store_acquisition(self) -> None:
        store = _CountingMemoryGatewayStore()
        with self.assertRaisesRegex(ValueError, "startup_buffer_max_pending"):
            Gateway(
                gateway_id="reference-invalid-limit",
                channels=[ReferenceChannel()],
                applications=[ReferenceApplication()],
                store=store,
                limits=GatewayLimits(startup_buffer_max_pending=0),
            )
        self.assertEqual(store.acquire_count, 0)
        self.assertEqual(store.close_count, 0)

    async def test_post_acquisition_runtime_construction_failure_closes_store(self) -> None:
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-construction-failure",
            channels=[ReferenceChannel()],
            applications=[ReferenceApplication()],
            store=store,
        )

        with patch(
            "imagent.gateway.runtime.ImAgentGateway",
            side_effect=RuntimeError("simulated runtime construction failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "construction failure"):
                await gateway.start()
        self.assertFalse(gateway.running)
        self.assertEqual(store.acquire_count, 1)
        self.assertEqual(store.close_count, 1)
        with self.assertRaisesRegex(RuntimeError, "cannot restart"):
            await gateway.start()

    async def test_partial_start_cleanup_continues_after_channel_stop_failure(self) -> None:
        first = _CleanupFailingChannel(
            "reference-cleanup-first",
            fail_stop_before_cleanup=True,
        )
        second = _CleanupFailingChannel(
            "reference-cleanup-second",
            fail_start=True,
            fail_stop_before_cleanup=True,
        )
        application = _CleanupFailingApplication()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-partial-cleanup",
            channels=[first, second],
            applications=[application],
            store=store,
        )

        with self.assertRaisesRegex(
            RuntimeError, "reference-cleanup-second start failed"
        ) as caught:
            await gateway.start()

        self.assertTrue(first.started)
        self.assertTrue(second.started)
        self.assertFalse(application.started)
        self.assertEqual(first.stop_count, 1)
        self.assertEqual(second.stop_count, 1)
        self.assertEqual(application.stop_count, 1)
        self.assertEqual(store.close_count, 1)
        self.assertTrue(
            any("reference-cleanup-first" in note for note in caught.exception.__notes__)
        )
        self.assertTrue(
            any("reference-cleanup-second" in note for note in caught.exception.__notes__)
        )

    async def test_partial_application_start_failure_cleans_current_and_prior_once(
        self,
    ) -> None:
        first = _CleanupFailingApplication("reference-application-first")
        second = _CleanupFailingApplication(
            "reference-application-second",
            fail_start=True,
            fail_stop=True,
        )
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-partial-application-cleanup",
            channels=[ReferenceChannel()],
            applications=[first, second],
            store=store,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "reference-application-second start failed",
        ) as caught:
            await gateway.start()

        self.assertFalse(first.started)
        self.assertFalse(second.started)
        self.assertEqual(first.stop_count, 1)
        self.assertEqual(second.stop_count, 1)
        self.assertEqual(store.close_count, 1)
        self.assertTrue(
            any("application stop failed" in note for note in caught.exception.__notes__)
        )

    async def test_normal_stop_continues_after_channel_controller_and_application_failures(
        self,
    ) -> None:
        channel = _CleanupFailingChannel(
            "reference-normal-cleanup",
            fail_stop_after_cleanup=True,
        )
        application = _CleanupFailingApplication(fail_stop=True)
        registry = _CleanupFailingRegistry()

        async def status(invocation, actions) -> CommandResult:
            del invocation, actions
            return CommandResult.text("status")

        registry.register(CommandDefinition(name="status", handler=status))
        registry.freeze()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-normal-cleanup",
            channels=[channel],
            applications=[application],
            store=store,
            controller=registry,
        )
        await gateway.start()

        with self.assertRaisesRegex(RuntimeError, "reference-normal-cleanup stop failed") as caught:
            await gateway.stop()

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(channel.stop_count, 1)
        self.assertEqual(registry.close_count, 1)
        self.assertEqual(application.stop_count, 1)
        self.assertEqual(store.close_count, 1)
        notes = "\n".join(caught.exception.__notes__)
        self.assertIn("Gateway cleanup also failed: Controller", notes)
        self.assertIn("Application", notes)

    async def test_cleanup_notes_and_logs_are_bounded_for_large_owner_errors(self) -> None:
        channel = _CleanupFailingChannel(
            "reference-bounded-cleanup",
            fail_stop_after_cleanup=True,
        )
        application = _HugeCleanupApplication()
        store = _HugeCloseFailureStore()
        gateway = Gateway(
            gateway_id="reference-bounded-cleanup",
            channels=[channel],
            applications=[application],
            store=store,
        )
        await gateway.start()

        with self.assertLogs("imagent.gateway", level="ERROR") as logged:
            with self.assertRaisesRegex(
                RuntimeError,
                "reference-bounded-cleanup stop failed",
            ) as caught:
                await gateway.stop()

        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(channel.stop_count, 1)
        self.assertEqual(application.stop_count, 1)
        self.assertEqual(store.close_count, 1)
        self.assertTrue(caught.exception.__notes__)
        self.assertTrue(all(len(note) <= 450 for note in caught.exception.__notes__))
        self.assertTrue(all(len(entry) <= 500 for entry in logged.output))
        evidence = "\n".join((*caught.exception.__notes__, *logged.output))
        self.assertNotIn("x" * 193, evidence)
        self.assertNotIn("\x1b", evidence)
        self.assertNotIn("\u202e", evidence)
        self.assertIn("secret?[2J?line?", evidence)

    async def test_invalid_application_capability_fails_before_memory_or_sqlite_io(
        self,
    ) -> None:
        application = ReferenceApplication()
        summary = application.summary
        malformed_projects = replace(
            summary.capabilities.projects,
            discovery=SupportLevel.FALLBACK,
        )
        application._summary = replace(  # type: ignore[reportPrivateUsage]
            summary,
            capabilities=replace(summary.capabilities, projects=malformed_projects),
        )

        memory = _CountingMemoryGatewayStore()
        with self.assertRaisesRegex(ValueError, "native project discovery"):
            Gateway(
                gateway_id="reference-invalid-capability-memory",
                channels=[ReferenceChannel()],
                applications=[application],
                store=memory,
            )
        self.assertEqual(memory.acquire_count, 0)
        self.assertEqual(memory.close_count, 0)

        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-capability.sqlite3"
            sqlite = _CountingSQLiteGatewayStore(path)
            with self.assertRaisesRegex(ValueError, "native project discovery"):
                Gateway(
                    gateway_id="reference-invalid-capability-sqlite",
                    channels=[ReferenceChannel("reference-sqlite")],
                    applications=[application],
                    store=sqlite,
                )
            self.assertEqual(sqlite.acquire_count, 0)
            self.assertEqual(sqlite.close_count, 0)
            await sqlite.close()

        for owner, malformed_capabilities in (
            (
                "Thread",
                replace(
                    summary.capabilities,
                    threads=replace(
                        summary.capabilities.threads,
                        listing=cast(Any, "bogus"),
                    ),
                ),
            ),
            (
                "runtime",
                replace(
                    summary.capabilities,
                    runtime=replace(
                        summary.capabilities.runtime,
                        history=cast(Any, "bogus"),
                    ),
                ),
            ),
        ):
            with self.subTest(owner=owner):
                malformed = ReferenceApplication(
                    application_instance_id=f"reference-invalid-{owner.lower()}"
                )
                malformed._summary = replace(  # type: ignore[reportPrivateUsage]
                    malformed.summary,
                    capabilities=malformed_capabilities,
                )
                store = _CountingMemoryGatewayStore()
                with self.assertRaisesRegex(ValueError, owner):
                    Gateway(
                        gateway_id=f"reference-invalid-{owner.lower()}",
                        channels=[ReferenceChannel(f"reference-{owner.lower()}")],
                        applications=[malformed],
                        store=store,
                    )
                self.assertEqual(store.acquire_count, 0)
                self.assertEqual(store.close_count, 0)

    async def test_malformed_store_and_acquired_session_fail_explicitly(self) -> None:
        channel_store = _CountingMemoryGatewayStore()
        with self.assertRaisesRegex(TypeError, "instance ID"):
            Gateway(
                gateway_id="reference-malformed-channel",
                channels=[cast(Any, object())],
                applications=[ReferenceApplication()],
                store=channel_store,
            )
        self.assertEqual(channel_store.acquire_count, 0)

        malformed_store = cast(Any, object())
        with self.assertRaisesRegex(TypeError, "max_effect_receipts"):
            Gateway(
                gateway_id="reference-malformed-store",
                channels=[ReferenceChannel()],
                applications=[ReferenceApplication()],
                store=malformed_store,
            )

        store = _MalformedSessionStore()
        gateway = Gateway(
            gateway_id="reference-malformed-session",
            channels=[ReferenceChannel()],
            applications=[ReferenceApplication()],
            store=cast(Any, store),
        )
        with self.assertRaisesRegex(TypeError, "GatewayStoreSession"):
            await gateway.start()
        self.assertEqual(store.acquire_count, 1)
        self.assertEqual(store.session.close_count, 1)
        self.assertEqual(store.close_count, 1)

    async def test_mismatched_or_malformed_acquired_lease_is_closed(self) -> None:
        cases = (
            ({"gateway_id": "another-gateway"}, "another Gateway"),
            ({"owner_token": "another-owner"}, "owner token"),
            ({"epoch": cast(Any, 1.5)}, "positive integer"),
        )
        for index, (changed_fields, message) in enumerate(cases):
            with self.subTest(changed_fields=changed_fields):
                store = _ChangedLeaseStore(changed_fields)
                gateway = Gateway(
                    gateway_id=f"reference-invalid-lease-{index}",
                    channels=[ReferenceChannel(f"reference-invalid-lease-{index}")],
                    applications=[
                        ReferenceApplication(
                            application_instance_id=f"reference-invalid-lease-{index}"
                        )
                    ],
                    store=store,
                )
                with self.assertRaisesRegex(ValueError, message):
                    await gateway.start()
                self.assertEqual(store.acquire_count, 1)
                self.assertIsNotNone(store.session)
                assert store.session is not None
                self.assertEqual(store.session.close_count, 1)
                self.assertEqual(store.close_count, 1)

    async def test_identity_drift_and_collision_fail_before_acquisition(self) -> None:
        first_channel = ReferenceChannel("reference-drift-a")
        second_channel = ReferenceChannel("reference-drift-b")
        first_application = ReferenceApplication(application_instance_id="reference-app-a")
        second_application = ReferenceApplication(application_instance_id="reference-app-b")
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-identity-drift",
            channels=[first_channel, second_channel],
            applications=[first_application, second_application],
            store=store,
        )
        second_channel._channel_instance_id = first_channel.channel_instance_id  # type: ignore[reportPrivateUsage]

        with self.assertRaisesRegex(ValueError, "Channel instance IDs must be unique"):
            await gateway.start()
        self.assertEqual(store.acquire_count, 0)
        self.assertEqual(store.close_count, 0)

        application_store = _CountingMemoryGatewayStore()
        application_gateway = Gateway(
            gateway_id="reference-application-identity-drift",
            channels=[ReferenceChannel("reference-application-drift")],
            applications=[first_application, second_application],
            store=application_store,
        )
        second_application._summary = replace(  # type: ignore[reportPrivateUsage]
            second_application.summary,
            ref=first_application.ref,
        )
        with self.assertRaisesRegex(ValueError, "Application instance IDs must be unique"):
            await application_gateway.start()
        self.assertEqual(application_store.acquire_count, 0)
        self.assertEqual(application_store.close_count, 0)

    async def test_valid_noncolliding_identity_drift_fails_before_acquisition(self) -> None:
        channel = ReferenceChannel("reference-original-channel")
        channel_store = _CountingMemoryGatewayStore()
        channel_gateway = Gateway(
            gateway_id="reference-channel-noncollision-drift",
            channels=[channel],
            applications=[ReferenceApplication()],
            store=channel_store,
        )
        channel._channel_instance_id = "reference-changed-channel"  # type: ignore[reportPrivateUsage]
        with self.assertRaisesRegex(ValueError, "identity changed"):
            await channel_gateway.start()
        self.assertEqual(channel_store.acquire_count, 0)

        application = ReferenceApplication(application_instance_id="reference-original-application")
        application_store = _CountingMemoryGatewayStore()
        application_gateway = Gateway(
            gateway_id="reference-application-noncollision-drift",
            channels=[ReferenceChannel("reference-application-channel")],
            applications=[application],
            store=application_store,
        )
        application._summary = replace(  # type: ignore[reportPrivateUsage]
            application.summary,
            ref=ApplicationRef("reference-changed-application"),
        )
        with self.assertRaisesRegex(ValueError, "identity changed"):
            await application_gateway.start()
        self.assertEqual(application_store.acquire_count, 0)

    async def test_lease_renewal_loss_stops_admission_adapters_and_store(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _FailingRenewMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-lease-loss",
            channels=[channel],
            applications=[application],
            store=store,
        )

        with patch("imagent.gateway.runtime._LEASE_RENEWAL_SECONDS", 0.001):
            await gateway.start()
            store.allow_renew_failure()
            await asyncio.wait_for(store.renew_failed.wait(), timeout=1.0)
            with self.assertRaisesRegex(RuntimeError, "runtime failure"):
                await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_lease_renewal_loss_racing_stop_closes_every_owner_once(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _FailingRenewMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-lease-loss-stop-race",
            channels=[channel],
            applications=[application],
            store=store,
        )

        with patch("imagent.gateway.runtime._LEASE_RENEWAL_SECONDS", 0.001):
            await gateway.start()
            store.allow_renew_failure()
            await asyncio.wait_for(store.renew_failed.wait(), timeout=1.0)
            explicit_stop = asyncio.create_task(gateway.stop())
            with self.assertRaisesRegex(RuntimeError, "runtime failure"):
                await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)
            await asyncio.wait_for(explicit_stop, timeout=1.0)

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_lease_loss_cancels_blocked_startup_and_rolls_back(self) -> None:
        store = _FailingRenewMemoryGatewayStore()
        channel = _BlockingStartChannel(store.allow_renew_failure)
        application = ReferenceApplication()
        gateway = Gateway(
            gateway_id="reference-startup-lease-loss",
            channels=[channel],
            applications=[application],
            store=store,
        )

        with patch("imagent.gateway.runtime._LEASE_RENEWAL_SECONDS", 0.001):
            startup = asyncio.create_task(gateway.start())
            await asyncio.wait_for(channel.start_blocked.wait(), timeout=1.0)
            await asyncio.wait_for(store.renew_failed.wait(), timeout=1.0)
            with self.assertRaisesRegex(RuntimeError, "renewal failed during startup"):
                await asyncio.wait_for(startup, timeout=1.0)
            with self.assertRaisesRegex(RuntimeError, "runtime failure"):
                await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_stop_cancels_and_joins_blocked_startup_rollback(self) -> None:
        channel = _BlockingStartChannel(lambda: None)
        application = ReferenceApplication()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-stop-during-start",
            channels=[channel],
            applications=[application],
            store=store,
        )

        startup = asyncio.create_task(gateway.start())
        await asyncio.wait_for(channel.start_blocked.wait(), timeout=1.0)
        await asyncio.wait_for(gateway.stop(), timeout=1.0)
        with self.assertRaises(asyncio.CancelledError):
            await startup

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.acquire_count, 1)
        self.assertEqual(store.close_count, 1)
        with self.assertRaisesRegex(RuntimeError, "cannot restart"):
            await gateway.start()

    async def test_concurrent_starts_share_one_serialized_transition(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _BlockingAcquireMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-concurrent-start",
            channels=[channel],
            applications=[application],
            store=store,
        )

        first = asyncio.create_task(gateway.start())
        await asyncio.wait_for(store.first_acquired.wait(), timeout=1.0)
        second = asyncio.create_task(gateway.start())
        await asyncio.sleep(0)
        self.assertFalse(second.done())
        store.allow_first_return.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1.0)

        self.assertTrue(gateway.running)
        self.assertTrue(channel.started)
        self.assertTrue(application.started)
        self.assertEqual(store.acquire_count, 1)
        await gateway.stop()
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_cancelled_async_context_start_rolls_back_once(self) -> None:
        channel = _BlockingStartChannel(lambda: None)
        application = ReferenceApplication()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-cancelled-context-start",
            channels=[channel],
            applications=[application],
            store=store,
        )

        async def enter_context() -> None:
            async with gateway:
                self.fail("a blocked Gateway context must not enter")

        context_task = asyncio.create_task(enter_context())
        await asyncio.wait_for(channel.start_blocked.wait(), timeout=1.0)
        context_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await context_task

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)
        await gateway.stop()
        self.assertEqual(store.close_count, 1)

    async def test_retained_scoped_actions_are_invalid_after_shutdown(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        gateway = Gateway(
            gateway_id="reference-retained-actions",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
        )
        with TemporaryDirectory() as cwd:
            await gateway.start()
            actions = gateway.actions(
                channel.conversation("retained").ref,
                actor="reference-user",
            )
            application_actions = gateway.application(
                application.ref,
                principal="reference-user",
            )
            self.assertEqual(application_actions.principal, "reference-user")
            self.assertFalse(hasattr(application_actions, "conversation_ref"))
            application_read = await application_actions.get_application()
            self.assertIsInstance(application_read, Succeeded)
            created = await actions.create_project(
                application.ref,
                cwd=cwd,
                action_id="reference:retained:project",
            )
            self.assertIsInstance(created, Succeeded)
            await gateway.stop()

            with self.assertRaisesRegex(RuntimeError, "not active"):
                await actions.list_applications()
            with self.assertRaisesRegex(RuntimeError, "not active"):
                await application_actions.get_application()
            with self.assertRaisesRegex(RuntimeError, "not active"):
                await actions.create_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:retained:project",
                )
        self.assertFalse(application.started)

    async def test_no_controller_treats_slash_text_as_ordinary_agent_input(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-no-controller")
        gateway = Gateway(
            gateway_id="reference-no-controller",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            limits=GatewayLimits(projection_max_active_threads=2),
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:no-controller:project",
                )
                self.assertIsInstance(project, Succeeded)
                assert isinstance(project, Succeeded)
                self.assertIsInstance(project.value.ref, ProjectRef)
                assert isinstance(project.value.ref, ProjectRef)
                thread = await actions.create_and_bind_thread(
                    project.value.ref,
                    action_id="reference:no-controller:thread",
                )
                self.assertIsInstance(thread, Succeeded)
                await conversation.receive_text(
                    message_id="reference:no-controller:message",
                    text="/about",
                )
                delivered = await conversation.wait_for_text("Neutral response: /about")
                self.assertEqual(len(delivered), 1)

        self.assertFalse(channel.started)
        self.assertFalse(application.started)

    def test_example_imports_only_clean_installed_public_sdk_modules(self) -> None:
        root = Path(__file__).resolve().parents[2]
        example = root / "examples" / "reference_consumer"
        forbidden = (
            "imagent.gateway.composition",
            "imagent.gateway.effect_execution",
            "imagent.gateway.persistence",
            "imagent.gateway.runtime",
        )
        for path in sorted(example.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = tuple(
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            )
            with self.subTest(path=path.name):
                self.assertFalse(any(name.startswith(forbidden) for name in imports))
                self.assertNotIn("ImAgentGateway", path.read_text(encoding="utf-8"))
                self.assertNotIn("GatewayRepositories", path.read_text(encoding="utf-8"))

        self.assertTrue(build_command_registry(ReferenceStatusService()).frozen)

    async def test_channel_capacity_fails_before_append(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceChannel(max_outbound_records=0)
        with self.assertRaises(ValueError):
            ReferenceChannel(max_outbound_records=True)

        channel = ReferenceChannel(max_outbound_records=1)

        async def on_message(message) -> None:
            del message

        await channel.start(on_message)
        message = OutboundMessage(
            delivery_id="delivery-1",
            conversation_ref=ConversationRef("reference-channel", "conversation-a"),
            content=(TextContent("one"),),
            created_at=datetime.now(UTC),
        )
        await channel.send(message)
        with self.assertRaises(RuntimeError):
            await channel.send(message)
        self.assertEqual(channel.sent, (message,))

    async def test_channel_conversation_uses_native_ingress_and_bounded_wait(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceChannel("")
        channel = ReferenceChannel()
        with self.assertRaises(ValueError):
            channel.conversation("")
        with self.assertRaises(ValueError):
            channel.conversation("conversation-a", authenticated_actor="")
        conversation = channel.conversation(
            "conversation-a",
            authenticated_actor="actor-a",
        )
        received = []

        async def on_message(message) -> None:
            received.append(message)

        await channel.start(on_message)
        try:
            with self.assertRaises(ValueError):
                conversation.text_message(message_id="", text="ordinary input")
            await conversation.receive_text(
                message_id="reference:message:ingress",
                text="ordinary input",
            )
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].conversation_ref, conversation.ref)
            self.assertEqual(received[0].sender, conversation.authenticated_actor)

            outbound = OutboundMessage(
                delivery_id="reference:delivery:one",
                conversation_ref=conversation.ref,
                content=(TextContent("ordinary output"),),
                created_at=datetime.now(UTC),
            )
            await channel.send(outbound)
            self.assertEqual(
                await conversation.wait_for_text("ordinary output"),
                (outbound,),
            )
            with self.assertRaises(ValueError):
                await channel.wait_for_text("never", count=True)
        finally:
            await channel.stop()

        self.assertFalse(channel.started)

    async def test_managed_resources_are_stable_through_the_application_port(self) -> None:
        application = ReferenceApplication()
        with TemporaryDirectory() as cwd:
            foreign = await application.execute(
                ListProjects(
                    operation_id="reference:project:list-foreign",
                    application_ref=ApplicationRef("different-application"),
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(foreign, ApplicationOperationFailed)

            empty = await application.execute(
                ListProjects(
                    operation_id="reference:project:list-empty",
                    application_ref=application.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(empty, ProjectsListed)
            assert isinstance(empty, ProjectsListed)
            self.assertEqual(empty.projects.items, ())

            create_project = CreateProject(
                operation_id="reference:project:stable",
                application_ref=application.ref,
                cwd=cwd,
                created_at=datetime.now(UTC),
            )
            first_project = await application.execute(create_project)
            repeated_project = await application.execute(create_project)
            self.assertIsInstance(first_project, ProjectCreated)
            self.assertIsInstance(repeated_project, ProjectCreated)
            assert isinstance(first_project, ProjectCreated)
            assert isinstance(repeated_project, ProjectCreated)
            self.assertEqual(repeated_project, first_project)

            conflicting_project = await application.execute(
                CreateProject(
                    operation_id=create_project.operation_id,
                    application_ref=application.ref,
                    cwd=f"{cwd}/different",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(conflicting_project, ApplicationOperationFailed)
            assert isinstance(conflicting_project, ApplicationOperationFailed)
            self.assertEqual(conflicting_project.error.code, "conflict")

            listed = await application.execute(
                ListProjects(
                    operation_id="reference:project:list-created",
                    application_ref=application.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(listed, ProjectsListed)
            assert isinstance(listed, ProjectsListed)
            self.assertEqual(
                tuple(project.ref for project in listed.projects.items),
                (first_project.project.ref,),
            )

            create_thread = CreateThread(
                operation_id="reference:thread:stable",
                application_ref=application.ref,
                project_ref=first_project.project.ref,
                created_at=datetime.now(UTC),
            )
            first_thread = await application.execute(create_thread)
            repeated_thread = await application.execute(create_thread)
            self.assertIsInstance(first_thread, ThreadCreated)
            self.assertIsInstance(repeated_thread, ThreadCreated)
            assert isinstance(first_thread, ThreadCreated)
            assert isinstance(repeated_thread, ThreadCreated)
            self.assertEqual(repeated_thread, first_thread)

            conflicting_thread = await application.execute(
                CreateThread(
                    operation_id=create_thread.operation_id,
                    application_ref=application.ref,
                    project_ref=first_project.project.ref,
                    title="different intent",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(conflicting_thread, ApplicationOperationFailed)
            assert isinstance(conflicting_thread, ApplicationOperationFailed)
            self.assertEqual(conflicting_thread.error.code, "conflict")

    async def test_adapter_diagnostics_are_bounded_redacted_and_cleanup_is_explicit(self) -> None:
        application = ReferenceApplication()
        channel = ReferenceChannel()
        secret_content = "content-that-must-not-enter-diagnostics"
        secret_conversation = "conversation-that-must-not-enter-diagnostics"
        secret_message = "message-that-must-not-enter-diagnostics"

        with TemporaryDirectory(prefix="path-that-must-not-enter-diagnostics-") as cwd:
            project = await application.create_project(
                cwd,
                operation_id="reference:diagnostics:project",
            )
            thread = await application.create_thread(
                project.ref,
                operation_id="reference:diagnostics:thread",
            )
            conversation = channel.conversation(secret_conversation)
            message = conversation.text_message(
                message_id=secret_message,
                text=secret_content,
            )

            async def discard(received) -> None:
                del received

            await application.start()
            await channel.start(discard)
            subscription = application.subscribe_thread(thread.ref)
            self.assertEqual(application.active_observation_workers(thread.ref), 1)
            try:
                facts = repr(
                    (
                        application.diagnostic_facts(),
                        channel.diagnostic_facts(),
                    )
                )
                self.assertLess(len(facts), 2_048)
                for secret in (
                    cwd,
                    project.ref.project_id,
                    thread.ref.thread_id,
                    secret_conversation,
                    secret_message,
                    secret_content,
                ):
                    self.assertNotIn(secret, facts)
                self.assertNotIn(message.message_id, facts)
            finally:
                await subscription.aclose()
                await channel.stop()
                await application.stop()

            self.assertEqual(application.active_observation_workers(thread.ref), 0)
            self.assertFalse(channel.started)
            self.assertFalse(application.started)

    async def test_application_limits_are_positive_and_capacity_is_pre_dispatch(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceApplication(max_projects=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_threads=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_turns_per_thread=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_events_per_thread=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_threads=True)

        with TemporaryDirectory() as cwd:
            application = ReferenceApplication(max_projects=1, max_threads=1)
            project = await application.create_project(
                cwd,
                operation_id="reference-project-one",
            )
            self.assertIs(
                await application.create_project(
                    cwd,
                    operation_id="reference-project-one",
                ),
                project,
            )
            with self.assertRaises(ValueError):
                await application.create_project(
                    f"{cwd}/different",
                    operation_id="reference-project-one",
                )
            with self.assertRaises(ValueError):
                await application.create_project(
                    f"{cwd}/second",
                    operation_id="reference-project-two",
                )
            project_capacity = await application.execute(
                CreateProject(
                    operation_id="reference-project-three",
                    application_ref=application.ref,
                    cwd=f"{cwd}/third",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(project_capacity, ApplicationOperationFailed)
            assert isinstance(project_capacity, ApplicationOperationFailed)
            self.assertEqual(project_capacity.error.code, "capacity_exhausted")

            await application.create_thread(
                project.ref,
                operation_id="reference-thread-one",
            )
            with self.assertRaises(ValueError):
                await application.create_thread(
                    project.ref,
                    operation_id="reference-thread-two",
                )
            with self.assertRaises(KeyError):
                await application.get_thread(
                    ThreadRef(
                        project_ref=ProjectRef("reference-agent", "unknown-project"),
                        thread_id="reference-thread-2",
                    )
                )

            turn_limited = ReferenceApplication(
                max_turns_per_thread=1,
                max_events_per_thread=6,
            )
            turn_project = await turn_limited.create_project(
                cwd,
                operation_id="reference-turn-project",
            )
            turn_thread = await turn_limited.create_thread(
                turn_project.ref,
                operation_id="reference-turn-thread",
            )
            await turn_limited.send_input(
                turn_thread.ref,
                AgentInput(
                    client_message_id="accepted-turn",
                    content=(TextContent("first"),),
                ),
            )
            callback_calls: list[str] = []

            async def before_dispatch(dispatch) -> None:
                callback_calls.append(dispatch.client_message_id)

            with self.assertRaises(ValueError):
                await turn_limited.send_input(
                    turn_thread.ref,
                    AgentInput(
                        client_message_id="rejected-turn",
                        content=(TextContent("second"),),
                    ),
                    before_dispatch=before_dispatch,
                )
            self.assertEqual(callback_calls, [])
            self.assertEqual(len((await _history(turn_limited, turn_thread.ref)).turns), 1)

            event_limited = ReferenceApplication(
                max_turns_per_thread=2,
                max_events_per_thread=3,
            )
            event_project = await event_limited.create_project(
                cwd,
                operation_id="reference-event-project",
            )
            event_thread = await event_limited.create_thread(
                event_project.ref,
                operation_id="reference-event-thread",
            )
            await event_limited.send_input(
                event_thread.ref,
                AgentInput(
                    client_message_id="accepted-event",
                    content=(TextContent("first"),),
                ),
            )
            event_callback_calls: list[str] = []

            async def before_event_dispatch(dispatch) -> None:
                event_callback_calls.append(dispatch.client_message_id)

            with self.assertRaises(ValueError):
                await event_limited.send_input(
                    event_thread.ref,
                    AgentInput(
                        client_message_id="rejected-event",
                        content=(TextContent("rejected"),),
                    ),
                    before_dispatch=before_event_dispatch,
                )
            self.assertEqual(event_callback_calls, [])
            self.assertEqual(len((await _history(event_limited, event_thread.ref)).turns), 1)


async def _history(
    application: ReferenceApplication,
    thread_ref: ThreadRef,
) -> ThreadHistory:
    result = await application.execute(
        GetThreadHistory(
            operation_id="reference-test-history",
            application_ref=ApplicationRef("reference-agent"),
            thread_ref=thread_ref,
            created_at=datetime.now(UTC),
        )
    )
    if not isinstance(result, ThreadHistoryRead):
        raise AssertionError(f"history read failed: {result!r}")
    return result.history

from __future__ import annotations

import asyncio
import inspect
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

from imagent.applications import CodexApplicationAdapter
from imagent.applications.capabilities import ProjectMode, SupportLevel
from imagent.applications.contract import (
    AgentMessage,
    ApplicationRef,
    ProjectRef,
    ThreadRef,
)
from imagent.applications.operations import GetThreadHistory
from imagent.contracts import (
    BindConversationToThread,
    ClearConversationThread,
    ConversationBound,
    GatewayOperationFailed,
)
from imagent.gateway import GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.input.dispatch import TurnAcceptanceOrderingGate
from imagent.gateway.persistence import (
    ConversationBinding,
    IdempotencyClaimStatus,
    ThreadProjectionRoute,
)
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryProjectionRouteRepository,
    InMemoryRequestCorrelationRepository,
)
from imagent.gateway.projection import (
    ProjectionWorkerHealth as facade_projection_worker_health,
)
from imagent.gateway.projection import (
    ThreadProjectionRuntime as facade_thread_projection_runtime,
)
from imagent.gateway.projection.observation import (
    ProjectionWorkerCapacityError,
    ProjectionWorkerHealth,
    ProjectionWorkerState,
    ThreadProjectionRuntime,
)
from imagent.gateway.projection.recovery import ProjectedAgentMessage
from imagent.gateway.routing import ObserveThread, ProjectionPolicy
from imagent.gateway.routing.projection_routes import derive_projection_route_id
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    MessageRole,
    TextContent,
)
from imagent.interaction.operations import OperationErrorCode
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter
from tests.applications.adapters._appserver_fakes import NativeZenClient


class ProjectionObservationOwnershipTests(unittest.TestCase):
    def test_projection_facade_reexports_only_exact_public_observation_contracts(self) -> None:
        import imagent.gateway.projection as projection_facade

        self.assertIs(facade_thread_projection_runtime, ThreadProjectionRuntime)
        self.assertIs(facade_projection_worker_health, ProjectionWorkerHealth)
        self.assertNotIn("ProjectionWorkerCapacityError", projection_facade.__all__)
        self.assertNotIn("ProjectionWorkerState", projection_facade.__all__)

    def test_facade_identity_and_historical_module_absence_hold_in_a_clean_process(
        self,
    ) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.util; "
                "import imagent.gateway.projection as facade; "
                "import imagent.gateway.projection.observation as observation; "
                "assert facade.ThreadProjectionRuntime is observation.ThreadProjectionRuntime; "
                "assert facade.ProjectionWorkerHealth is observation.ProjectionWorkerHealth; "
                "assert all(importlib.util.find_spec(name) is None for name in "
                "('imagent.projection_runtime', 'imagent.projections', "
                "'imagent.projection_routes'))",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


class GatewayConcurrentTurnProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_turns_on_one_thread_do_not_steal_events(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread(application.default_project_ref)
        bindings = InMemoryBindingRepository()
        first_conversation = ConversationRef("fake-channel", "first")
        second_conversation = ConversationRef("fake-channel", "second")
        for conversation in (first_conversation, second_conversation):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("fake-agent"),
                    project_ref=thread.ref.project_ref,
                    thread_ref=thread.ref,
                )
            )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await asyncio.gather(
                channel.on_message(_inbound(first_conversation, "first-message")),
                channel.on_message(_inbound(second_conversation, "second-message")),
            )
            async with asyncio.timeout(1):
                while len(channel.sent) < 8:
                    await asyncio.sleep(0)
        finally:
            await gateway.stop()

        replies = [message.reply_to for message in channel.sent]
        self.assertEqual(replies.count("first-message"), 2)
        self.assertEqual(replies.count("second-message"), 2)
        self.assertEqual(replies.count(None), 4)
        self.assertEqual(len({message.delivery_id for message in channel.sent}), 8)

    async def test_event_overflow_recovers_threads_independently(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(
            project_mode=ProjectMode.FLAT,
            event_buffer_max_pending=2,
        )
        overflow_thread = await application.create_thread(application.default_project_ref)
        healthy_thread = await application.create_thread(application.default_project_ref)
        bindings = InMemoryBindingRepository()
        overflow_conversation = ConversationRef("fake-channel", "overflow")
        healthy_conversation = ConversationRef("fake-channel", "healthy")
        for conversation, thread in (
            (overflow_conversation, overflow_thread),
            (healthy_conversation, healthy_thread),
        ):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("fake-agent"),
                    project_ref=thread.ref.project_ref,
                    thread_ref=thread.ref,
                )
            )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(overflow_conversation, "overflow-input"))
            await channel.on_message(_inbound(healthy_conversation, "healthy-input"))
            async with asyncio.timeout(1):
                while len(channel.sent) < 4:
                    await asyncio.sleep(0)
            overflow_health = gateway.get_projection_health(overflow_thread.ref)
            healthy_health = gateway.get_projection_health(healthy_thread.ref)
            assert overflow_health is not None
            assert healthy_health is not None
            self.assertEqual(overflow_health.event_overflow_count, 1)
            self.assertEqual(
                overflow_health.last_event_overflow,
                "application_event_fanout_overflow",
            )
            self.assertFalse(overflow_health.interactive_request_recovery_degraded)
            self.assertEqual(healthy_health.event_overflow_count, 1)
        finally:
            await gateway.stop()

    async def test_appserver_transport_reset_enters_authoritative_projection_recovery(
        self,
    ) -> None:
        channel = FakeChannelAdapter()
        native = NativeZenClient()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            workspace_id="workspace",
            cwd="/repo",
        )
        thread_ref = ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        native.threads[thread_ref.thread_id] = {
            "id": thread_ref.thread_id,
            "cwd": "/repo",
            "preview": "Recover reset output",
            "status": {"type": "idle"},
        }
        conversation = ConversationRef("fake-channel", "appserver-reset")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                project_ref=thread_ref.project_ref,
                thread_ref=thread_ref,
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(conversation, "before-reset"))
            async with asyncio.timeout(1):
                while gateway.get_projection_health(thread_ref) is None:
                    await asyncio.sleep(0)
            await native.reset_connection()
            async with asyncio.timeout(1):
                while True:
                    health = gateway.get_projection_health(thread_ref)
                    if (
                        health is not None
                        and health.state.value == "running"
                        and health.restart_count == 1
                    ):
                        break
                    await asyncio.sleep(0)
            self.assertEqual(
                health.last_event_gap,
                "application_event_connection_reset",
            )
            self.assertIsNone(health.last_event_overflow)
        finally:
            await gateway.stop()

    async def test_event_overflow_marks_request_recovery_degraded_without_snapshot(
        self,
    ) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(
            project_mode=ProjectMode.FLAT,
            event_buffer_max_pending=2,
        )
        application._summary = replace(
            application.summary,
            capabilities=replace(
                application.summary.capabilities,
                runtime=replace(
                    application.summary.capabilities.runtime,
                    pending_request_snapshot=SupportLevel.UNSUPPORTED,
                ),
            ),
        )
        thread = await application.create_thread(application.default_project_ref)
        conversation = ConversationRef("fake-channel", "degraded")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                project_ref=thread.ref.project_ref,
                thread_ref=thread.ref,
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(conversation, "degraded-input"))
            async with asyncio.timeout(1):
                while True:
                    health = gateway.get_projection_health(thread.ref)
                    if health is not None and health.event_overflow_count == 1:
                        break
                    await asyncio.sleep(0)
            self.assertTrue(health.interactive_request_recovery_degraded)
        finally:
            await gateway.stop()


class GatewayThreadObservationCapacityTests(unittest.IsolatedAsyncioTestCase):
    def test_active_thread_capacity_requires_a_positive_keyword_only_value(self) -> None:
        parameter = inspect.signature(ThreadProjectionRuntime.__init__).parameters[
            "max_active_threads"
        ]
        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        for value in (-1, 0, True, 1.5, "1"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    GatewayLimits(projection_max_active_threads=cast(int, value))
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    self._runtime(max_active_threads=cast(int, value))

    async def test_final_slot_rejects_distinct_thread_before_projection_work(self) -> None:
        application = _CapacityRecordingApplication()
        first_thread = await application.create_thread(
            application.default_project_ref, title="first"
        )
        rejected_thread = await application.create_thread(
            application.default_project_ref, title="rejected"
        )
        channel = FakeChannelAdapter()
        projections = InMemoryProjectionRouteRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
            ),
            limits=GatewayLimits(projection_max_active_threads=1),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            first = await gateway.execute_gateway(
                _observe(
                    "capacity-first", ConversationRef("fake-channel", "first"), first_thread.ref
                )
            )
            self.assertNotIsInstance(first, GatewayOperationFailed)
            history_before = tuple(application.history_threads)

            rejected = await gateway.execute_gateway(
                _observe(
                    "capacity-rejected",
                    ConversationRef("fake-channel", "rejected"),
                    rejected_thread.ref,
                )
            )

            self.assertIsInstance(rejected, GatewayOperationFailed)
            assert isinstance(rejected, GatewayOperationFailed)
            self.assertEqual(rejected.error.code, OperationErrorCode.CAPACITY_EXHAUSTED.value)
            self.assertEqual(
                rejected.error.message,
                "active Thread observation capacity is exhausted",
            )
            self.assertNotIn(rejected_thread.ref.thread_id, rejected.error.message)
            self.assertEqual(application.subscription_threads, [first_thread.ref])
            self.assertEqual(tuple(application.history_threads), history_before)
            self.assertEqual(channel.sent, [])
            self.assertEqual(await projections.list_projection_routes(rejected_thread.ref), ())
        finally:
            await gateway.stop()

    async def test_foreground_final_slot_rejects_switch_before_binding_mutation(
        self,
    ) -> None:
        application = _CapacityRecordingApplication()
        first_thread = await application.create_thread(
            application.default_project_ref, title="first"
        )
        rejected_thread = await application.create_thread(
            application.default_project_ref, title="rejected"
        )
        conversation = ConversationRef("fake-channel", "foreground-capacity")
        channel = FakeChannelAdapter()
        projections = InMemoryProjectionRouteRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
            ),
            limits=GatewayLimits(projection_max_active_threads=1),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )
        await gateway.start()
        try:
            first_bound = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="foreground-capacity-first",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=first_thread.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(first_bound, ConversationBound)
            assert isinstance(first_bound, ConversationBound)
            history_before = tuple(application.history_threads)
            sent_before = tuple(channel.sent)

            rejected = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="foreground-capacity-rejected",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=rejected_thread.ref,
                    expected_generation=first_bound.binding.generation,
                    created_at=datetime.now(UTC),
                )
            )

            self.assertIsInstance(rejected, GatewayOperationFailed)
            assert isinstance(rejected, GatewayOperationFailed)
            self.assertEqual(rejected.error.code, OperationErrorCode.CAPACITY_EXHAUSTED.value)
            self.assertEqual(
                rejected.error.message,
                "active Thread observation capacity is exhausted",
            )
            current = await gateway.get_binding(conversation)
            self.assertEqual(current, first_bound.binding)
            self.assertEqual(application.subscription_threads, [first_thread.ref])
            self.assertEqual(tuple(application.history_threads), history_before)
            self.assertEqual(tuple(channel.sent), sent_before)
            self.assertEqual(await projections.list_projection_routes(rejected_thread.ref), ())

            cleared = await gateway.execute_gateway(
                ClearConversationThread(
                    operation_id="foreground-capacity-clear",
                    conversation_ref=conversation,
                    actor="user",
                    expected_generation=first_bound.binding.generation,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(cleared, ConversationBound)
            assert isinstance(cleared, ConversationBound)
            self.assertIsNone(cleared.binding.thread_ref)
            self.assertIsNone(gateway.get_projection_health(first_thread.ref))

            retried = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="foreground-capacity-retry",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=rejected_thread.ref,
                    expected_generation=cleared.binding.generation,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(retried, ConversationBound)
            assert isinstance(retried, ConversationBound)
            self.assertEqual(retried.binding.thread_ref, rejected_thread.ref)
            self.assertEqual(application.subscription_threads[-1], rejected_thread.ref)
            self.assertEqual(len(await projections.list_projection_routes(rejected_thread.ref)), 1)
        finally:
            await gateway.stop()

    async def test_same_thread_waiter_cancellation_keeps_final_slot(self) -> None:
        runtime = self._runtime(max_active_threads=1)
        first = ThreadRef(ProjectRef("fake-agent", "workspace"), "first")
        second = ThreadRef(ProjectRef("fake-agent", "workspace"), "second")
        worker_started = asyncio.Event()
        release_worker = asyncio.Event()

        async def wait_then_finish(
            thread_ref: ThreadRef,
            ready: asyncio.Event,
            *,
            reconcile_existing: bool,
            require_checkpoint: bool,
        ) -> None:
            del thread_ref, reconcile_existing, require_checkpoint
            worker_started.set()
            await release_worker.wait()
            ready.set()

        runtime._project_thread = wait_then_finish
        owner = asyncio.create_task(runtime._ensure_projection(first))
        await worker_started.wait()
        waiter = asyncio.create_task(runtime._ensure_projection(first))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())

        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertIn(first, runtime._tasks)
        with self.assertRaisesRegex(
            ProjectionWorkerCapacityError,
            "^active Thread observation capacity is exhausted$",
        ):
            await runtime._ensure_projection(second)

        release_worker.set()
        await owner
        await self._wait_for_worker_release(runtime, first)
        self.assertNotIn(first, runtime._tasks)

    async def test_action_route_real_baseline_failure_keeps_fenced_until_replay(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        runtime = self._runtime(
            max_active_threads=1,
            applications={application.summary.ref.application_instance_id: application},
        )
        thread = ThreadRef(application.default_project_ref, "action-route-failure")
        conversation = ConversationRef("fake-channel", "action-route-failure")
        route = ThreadProjectionRoute(
            route_id=derive_projection_route_id(thread, conversation),
            thread_ref=thread,
            conversation_ref=conversation,
        )
        await runtime._projections.put_projection_route(route)
        failed_lease = await runtime.begin_action_route(route.route_id)

        async def ensure_projection(_thread_ref: ThreadRef, **_kwargs: object) -> None:
            return None

        runtime._ensure_projection = ensure_projection  # type: ignore[method-assign]

        with self.assertRaisesRegex(AssertionError, "unexpected Application operation"):
            await runtime.reconcile_action_route(route.route_id, failed_lease)

        barrier = runtime._routes._bootstrap[route.route_id]
        self.assertFalse(barrier.is_set())
        runtime.complete_action_route(failed_lease, False)

        async def complete_baseline(*_args: object, **_kwargs: object) -> None:
            runtime._routes.complete_bootstrap(route.route_id)

        runtime._recovery.reconcile_route = complete_baseline  # type: ignore[method-assign]
        replay_lease = await runtime.begin_action_route(route.route_id)
        await runtime.reconcile_action_route(route.route_id, replay_lease)
        runtime.complete_action_route(replay_lease, True)
        self.assertTrue(barrier.is_set())

    async def test_action_route_holders_cannot_release_another_baseline(self) -> None:
        runtime = self._runtime(max_active_threads=1)
        route_id = "concurrent-action-route"

        first_lease = await runtime.begin_action_route(route_id)
        barrier = runtime._routes._bootstrap[route_id]
        second_owner = asyncio.create_task(runtime.begin_action_route(route_id))
        await asyncio.sleep(0)
        self.assertFalse(second_owner.done())
        self.assertFalse(barrier.is_set())
        runtime.complete_action_route(first_lease, True)
        second_lease = await second_owner
        second_barrier = runtime._routes._bootstrap[route_id]
        self.assertIsNot(second_barrier, barrier)
        self.assertFalse(second_barrier.is_set())
        runtime.complete_action_route(second_lease, True)
        self.assertTrue(second_barrier.is_set())

    async def test_removed_failed_action_route_retires_barrier_and_waiters(self) -> None:
        runtime = self._runtime(max_active_threads=1)
        route_id = "removed-failed-action-route"
        lease = await runtime.begin_action_route(route_id)
        barrier = runtime._routes._bootstrap[route_id]
        runtime.complete_action_route(lease, False)
        self.assertFalse(barrier.is_set())

        await runtime.reconcile_action_route(route_id)

        self.assertTrue(barrier.is_set())
        self.assertNotIn(route_id, runtime._routes._bootstrap)
        self.assertNotIn(route_id, runtime._routes._locks)

    async def test_retired_route_lease_cannot_complete_a_readded_generation(self) -> None:
        runtime = self._runtime(max_active_threads=1)
        route_id = "retired-and-readded-action-route"
        old_lease = await runtime.begin_action_route(route_id)
        old_barrier = runtime._routes._bootstrap[route_id]
        await runtime._routes.forget_route_ids((route_id,))
        self.assertTrue(old_barrier.is_set())

        new_owner = asyncio.create_task(runtime.begin_action_route(route_id))
        await asyncio.sleep(0)
        self.assertFalse(new_owner.done())
        runtime.complete_action_route(old_lease, False)
        new_lease = await new_owner
        new_barrier = runtime._routes._bootstrap[route_id]
        self.assertFalse(new_barrier.is_set())

        runtime.complete_action_route(old_lease, True)
        self.assertFalse(new_barrier.is_set())
        runtime.complete_action_route(new_lease, True)
        self.assertTrue(new_barrier.is_set())

    async def test_live_delivery_rechecks_barrier_after_route_lock_queue(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        runtime = self._runtime(max_active_threads=1)
        thread = ThreadRef(application.default_project_ref, "queued-live-barrier")
        conversation = ConversationRef("fake-channel", "queued-live-barrier")
        route = ThreadProjectionRoute(
            route_id=derive_projection_route_id(thread, conversation),
            thread_ref=thread,
            conversation_ref=conversation,
        )
        await runtime._projections.put_projection_route(route)
        initial_barrier = asyncio.Event()
        initial_barrier.set()
        runtime._routes._bootstrap[route.route_id] = initial_barrier
        route_lock = runtime._routes._locks.setdefault(route.route_id, asyncio.Lock())
        await route_lock.acquire()
        action_owner = asyncio.create_task(runtime.begin_action_route(route.route_id))
        await asyncio.sleep(0)
        live_delivery = asyncio.create_task(
            runtime._routes._deliver_to_route(
                route,
                ProjectedAgentMessage(
                    AgentMessage(
                        agent_item_id="queued-live-item",
                        thread_ref=thread,
                        role=MessageRole.ASSISTANT,
                        content=(TextContent("must remain fenced"),),
                        created_at=datetime.now(UTC),
                    ),
                    turn_id="queued-live-turn",
                ),
            )
        )
        await asyncio.sleep(0)

        route_lock.release()
        lease = await action_owner
        await asyncio.sleep(0)
        self.assertFalse(runtime._routes._bootstrap[route.route_id].is_set())
        self.assertFalse(live_delivery.done())

        live_delivery.cancel()
        await asyncio.gather(live_delivery, return_exceptions=True)
        runtime.complete_action_route(lease, True)

    async def test_cancelled_action_owner_wait_releases_thread_reservation(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        runtime = self._runtime(
            max_active_threads=1,
            applications={application.summary.ref.application_instance_id: application},
        )
        thread = ThreadRef(application.default_project_ref, "cancelled-action-owner")
        conversation = ConversationRef("fake-channel", "cancelled-action-owner")
        route = ThreadProjectionRoute(
            route_id=derive_projection_route_id(thread, conversation),
            thread_ref=thread,
            conversation_ref=conversation,
        )
        await runtime._projections.put_projection_route(route)
        route_lock = runtime._routes._locks.setdefault(route.route_id, asyncio.Lock())
        await route_lock.acquire()
        reconciliation = asyncio.create_task(runtime.reconcile_action_route(route.route_id))
        await asyncio.sleep(0)

        reconciliation.cancel()
        route_lock.release()
        with self.assertRaises(asyncio.CancelledError):
            await reconciliation

        self.assertNotIn(thread, runtime._pending_starts)

    async def test_same_thread_reservation_survives_worker_turnover_before_ensure(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        runtime = self._runtime(max_active_threads=1)
        first = ThreadRef(ProjectRef("fake-agent", "workspace"), "first")
        second = ThreadRef(ProjectRef("fake-agent", "workspace"), "second")
        first_worker_started = asyncio.Event()
        replacement_worker_started = asyncio.Event()
        release_first_worker = asyncio.Event()
        release_replacement_worker = asyncio.Event()
        worker_calls = 0

        async def block_workers(
            thread_ref: ThreadRef,
            ready: asyncio.Event,
            *,
            reconcile_existing: bool,
            require_checkpoint: bool,
        ) -> None:
            nonlocal worker_calls
            del thread_ref, reconcile_existing, require_checkpoint
            worker_calls += 1
            ready.set()
            if worker_calls == 1:
                first_worker_started.set()
                await release_first_worker.wait()
            else:
                replacement_worker_started.set()
                await release_replacement_worker.wait()

        route_persisted = asyncio.Event()
        allow_ensure = asyncio.Event()
        remember_route = runtime._remember_route

        async def pause_after_route_persist(
            thread_ref: ThreadRef,
            conversation_ref: ConversationRef,
            *,
            reply_to_message_id: str | None,
        ):
            remembered = await remember_route(
                thread_ref,
                conversation_ref,
                reply_to_message_id=reply_to_message_id,
            )
            route_persisted.set()
            await allow_ensure.wait()
            return remembered

        runtime._project_thread = block_workers
        runtime._remember_route = pause_after_route_persist
        await runtime._ensure_projection(first)
        await first_worker_started.wait()

        conversation = ConversationRef("fake-channel", "first")
        preparing = asyncio.create_task(
            runtime.prepare_input_route(
                application,
                first,
                conversation,
                thread_was_created=True,
            )
        )
        await route_persisted.wait()
        release_first_worker.set()
        await self._wait_for_worker_release(runtime, first)

        with self.assertRaisesRegex(
            ProjectionWorkerCapacityError,
            "^active Thread observation capacity is exhausted$",
        ):
            await runtime._ensure_projection(second)
        self.assertNotIn(second, runtime._tasks)
        self.assertEqual(await runtime._projections.list_projection_routes(second), ())

        allow_ensure.set()
        route = await preparing
        self.assertEqual(route.thread_ref, first)
        self.assertEqual(
            await runtime._projections.list_projection_routes(first),
            (route,),
        )
        await replacement_worker_started.wait()

        release_replacement_worker.set()
        await self._wait_for_worker_release(runtime, first)

    async def test_terminal_start_failure_and_lifecycle_reset_release_runtime_entries(self) -> None:
        runtime = self._runtime(max_active_threads=1)
        first = ThreadRef(ProjectRef("fake-agent", "workspace"), "first")
        second = ThreadRef(ProjectRef("fake-agent", "workspace"), "second")
        worker_started = asyncio.Event()
        release_worker = asyncio.Event()

        async def fail_start(
            thread_ref: ThreadRef,
            ready: asyncio.Event,
            *,
            reconcile_existing: bool,
            require_checkpoint: bool,
        ) -> None:
            del thread_ref, ready, reconcile_existing, require_checkpoint
            worker_started.set()
            await release_worker.wait()
            raise RuntimeError("simulated start failure")

        runtime._project_thread = fail_start
        failed_start = asyncio.create_task(runtime._ensure_projection(first))
        await worker_started.wait()
        self._seed_worker_entries(runtime, first)
        release_worker.set()
        with self.assertRaisesRegex(RuntimeError, "simulated start failure"):
            await failed_start
        await self._wait_for_worker_release(runtime, first)
        self._assert_worker_entries_released(runtime, first)

        worker_started = asyncio.Event()
        release_worker = asyncio.Event()

        async def terminal_worker(
            thread_ref: ThreadRef,
            ready: asyncio.Event,
            *,
            reconcile_existing: bool,
            require_checkpoint: bool,
        ) -> None:
            del thread_ref, reconcile_existing, require_checkpoint
            worker_started.set()
            await release_worker.wait()
            ready.set()

        runtime._project_thread = terminal_worker
        terminal = asyncio.create_task(runtime._ensure_projection(first))
        await worker_started.wait()
        self._seed_worker_entries(runtime, first)
        release_worker.set()
        await terminal
        await self._wait_for_worker_release(runtime, first)
        self._assert_worker_entries_released(runtime, first)

        worker_started = asyncio.Event()

        async def blocked_worker(
            thread_ref: ThreadRef,
            ready: asyncio.Event,
            *,
            reconcile_existing: bool,
            require_checkpoint: bool,
        ) -> None:
            del thread_ref, ready, reconcile_existing, require_checkpoint
            worker_started.set()
            await asyncio.Event().wait()

        runtime._project_thread = blocked_worker
        cancelled = asyncio.create_task(runtime._ensure_projection(first))
        await worker_started.wait()
        self._seed_worker_entries(runtime, first)
        await runtime.stop()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self._assert_worker_entries_released(runtime, first)

        await runtime.restore()

        async def complete_worker(
            thread_ref: ThreadRef,
            ready: asyncio.Event,
            *,
            reconcile_existing: bool,
            require_checkpoint: bool,
        ) -> None:
            del thread_ref, reconcile_existing, require_checkpoint
            ready.set()

        runtime._project_thread = complete_worker
        await runtime._ensure_projection(second)
        await self._wait_for_worker_release(runtime, second)

    @staticmethod
    def _runtime(
        *,
        max_active_threads: int,
        applications: dict[str, FakeAgentApplicationAdapter] | None = None,
    ) -> ThreadProjectionRuntime:
        async def execute_application(_operation: object) -> object:
            raise AssertionError("unexpected Application operation")

        async def deliver_outbound(*_args: object, **_kwargs: object) -> IdempotencyClaimStatus:
            raise AssertionError("unexpected projected delivery")

        return ThreadProjectionRuntime(
            applications=applications or {},
            bindings=InMemoryBindingRepository(),
            projections=InMemoryProjectionRouteRepository(),
            request_correlations=InMemoryRequestCorrelationRepository(),
            request_presenter=None,
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            execute_application=execute_application,  # type: ignore[arg-type]
            deliver_outbound=deliver_outbound,  # type: ignore[arg-type]
            deliver_request_outbound=deliver_outbound,  # type: ignore[arg-type]
            acceptance_gate=TurnAcceptanceOrderingGate(max_pending=256),
            max_active_threads=max_active_threads,
        )

    @staticmethod
    def _seed_worker_entries(runtime: ThreadProjectionRuntime, thread_ref: ThreadRef) -> None:
        runtime._health[thread_ref] = ProjectionWorkerHealth(
            thread_ref=thread_ref,
            state=ProjectionWorkerState.RUNNING,
        )

    @staticmethod
    async def _wait_for_worker_release(
        runtime: ThreadProjectionRuntime,
        thread_ref: ThreadRef,
    ) -> None:
        async with asyncio.timeout(1):
            while thread_ref in runtime._tasks:
                await asyncio.sleep(0)

    def _assert_worker_entries_released(
        self,
        runtime: ThreadProjectionRuntime,
        thread_ref: ThreadRef,
    ) -> None:
        self.assertNotIn(thread_ref, runtime._tasks)
        self.assertNotIn(thread_ref, runtime._health)


class _CapacityRecordingApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.subscription_threads: list[ThreadRef] = []
        self.history_threads: list[ThreadRef] = []

    def subscribe_thread(self, thread_ref: ThreadRef, after_cursor: str | None = None):
        self.subscription_threads.append(thread_ref)
        return super().subscribe_thread(thread_ref, after_cursor)

    async def execute(self, operation):
        if isinstance(operation, GetThreadHistory):
            self.history_threads.append(operation.thread_ref)
        return await super().execute(operation)


def _observe(
    operation_id: str,
    conversation_ref: ConversationRef,
    thread_ref: ThreadRef,
) -> ObserveThread:
    return ObserveThread(
        operation_id=operation_id,
        conversation_ref=conversation_ref,
        actor="user",
        thread_ref=thread_ref,
        created_at=datetime.now(UTC),
    )


def _inbound(conversation: ConversationRef, message_id: str) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation,
        sender="user-1",
        content=(TextContent("run"),),
        created_at=datetime.now(UTC),
    )

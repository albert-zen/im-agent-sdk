from __future__ import annotations

import asyncio
import inspect
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

from imagent.applications import CodexApplicationAdapter
from imagent.applications.capabilities import ProjectMode, SupportLevel
from imagent.applications.contract import (
    AcceptedTurn,
    AgentInput,
    ApplicationInputDispatchHandler,
    ApplicationRef,
    InputContinuationPreference,
    ThreadRef,
)
from imagent.applications.events import AgentEvent, AgentEventType
from imagent.applications.operations import GetThreadHistory
from imagent.contracts import (
    BindConversationToThread,
    ClearConversationThread,
    ConversationBound,
    GatewayOperationFailed,
    ObserveThread,
)
from imagent.gateway import GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.persistence import (
    ConversationBinding,
    IdempotencyClaimStatus,
    ProjectionPolicy,
)
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryProjectionRouteRepository,
    InMemoryRequestCorrelationRepository,
)
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    TextContent,
)
from imagent.interaction.operations import OperationErrorCode
from imagent.projection_runtime import ProjectionWorkerCapacityError, ThreadProjectionRuntime
from imagent.projections import ProjectionWorkerHealth, ProjectionWorkerState
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter
from tests.applications.adapters._appserver_fakes import NativeZenClient


class GatewayConcurrentTurnProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_turns_on_one_thread_do_not_steal_events(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        bindings = InMemoryBindingRepository()
        first_conversation = ConversationRef("fake-channel", "first")
        second_conversation = ConversationRef("fake-channel", "second")
        for conversation in (first_conversation, second_conversation):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("fake-agent"),
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
        overflow_thread = await application.create_thread()
        healthy_thread = await application.create_thread()
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
            cwd="/repo",
        )
        thread_ref = ThreadRef("codex-main", "thread-1")
        native.threads[thread_ref.native_thread_id] = {
            "id": thread_ref.native_thread_id,
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
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "degraded")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
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
        first_thread = await application.create_thread(title="first")
        rejected_thread = await application.create_thread(title="rejected")
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
            self.assertNotIn(rejected_thread.ref.native_thread_id, rejected.error.message)
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
        first_thread = await application.create_thread(title="first")
        rejected_thread = await application.create_thread(title="rejected")
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
                    expected_revision=first_bound.binding.revision,
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
                    expected_revision=first_bound.binding.revision,
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
                    expected_revision=cleared.binding.revision,
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
        first = ThreadRef("fake-agent", "first")
        second = ThreadRef("fake-agent", "second")
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

    async def test_same_thread_reservation_survives_worker_turnover_before_ensure(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        runtime = self._runtime(max_active_threads=1)
        first = ThreadRef("fake-agent", "first")
        second = ThreadRef("fake-agent", "second")
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
        first = ThreadRef("fake-agent", "first")
        second = ThreadRef("fake-agent", "second")
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

        runtime._pending_turn_acceptances[first] = 1
        runtime._acceptance_ready[first] = asyncio.Event()
        runtime._buffered_events[first] = []
        runtime._buffered_event_overflows.add(first)
        runtime._event_locks[first] = asyncio.Lock()
        await runtime.restore()
        self.assertNotIn(first, runtime._pending_turn_acceptances)
        self.assertNotIn(first, runtime._acceptance_ready)
        self.assertNotIn(first, runtime._buffered_events)
        self.assertNotIn(first, runtime._buffered_event_overflows)
        self.assertNotIn(first, runtime._event_locks)

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

    async def test_worker_terminal_preserves_pending_acceptance_until_correlation_finishes(
        self,
    ) -> None:
        application = _BlockingAcceptanceApplication()
        thread = await application.create_thread()
        runtime = self._runtime(
            max_active_threads=1,
            applications={application.summary.ref.application_instance_id: application},
        )

        async def wait_forever() -> None:
            await asyncio.Event().wait()

        worker = asyncio.create_task(wait_forever())
        runtime._tasks[thread.ref] = worker
        runtime._ready[thread.ref] = asyncio.Event()
        worker.add_done_callback(lambda completed: runtime._finish_task(thread.ref, completed))
        self._seed_worker_entries(runtime, thread.ref)

        accepting = asyncio.create_task(
            runtime.send_input(
                application,
                thread.ref,
                AgentInput(client_message_id="pending-acceptance", content=(TextContent("run"),)),
                conversation_ref=ConversationRef("fake-channel", "acceptance"),
                reply_to_message_id="reply-1",
            )
        )
        await application.acceptance_started.wait()
        buffered_event = AgentEvent(
            event_id="pending-acceptance-event",
            application_instance_id=thread.ref.application_instance_id,
            type=AgentEventType.STATUS_CHANGED,
            data={},
            created_at=datetime.now(UTC),
            thread_ref=thread.ref,
        )
        await runtime._handle_event(buffered_event)
        self.assertEqual(runtime._buffered_events[thread.ref], [buffered_event])
        acceptance_waiter = asyncio.create_task(runtime._wait_for_acceptance(thread.ref))
        await asyncio.sleep(0)

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertNotIn(thread.ref, runtime._tasks)
        self.assertIsNone(runtime.get_health(thread.ref))
        self.assertIn(thread.ref, runtime._acceptance_ready)
        self.assertIn(thread.ref, runtime._event_locks)
        self.assertEqual(runtime._buffered_events[thread.ref], [buffered_event])
        self.assertFalse(acceptance_waiter.done())

        application.release_acceptance.set()
        accepted = await accepting
        correlation = await runtime._projections.get_turn_reply_correlation(
            thread.ref,
            accepted.turn_id,
        )
        self.assertIsNotNone(correlation)
        await acceptance_waiter
        self.assertNotIn(thread.ref, runtime._pending_turn_acceptances)
        self.assertNotIn(thread.ref, runtime._acceptance_ready)
        self.assertNotIn(thread.ref, runtime._buffered_events)
        self.assertNotIn(thread.ref, runtime._event_locks)

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
            max_active_threads=max_active_threads,
        )

    @staticmethod
    def _seed_worker_entries(runtime: ThreadProjectionRuntime, thread_ref: ThreadRef) -> None:
        runtime._health[thread_ref] = ProjectionWorkerHealth(
            thread_ref=thread_ref,
            state=ProjectionWorkerState.RUNNING,
        )
        runtime._event_locks[thread_ref] = asyncio.Lock()

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
        self.assertNotIn(thread_ref, runtime._event_locks)


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


class _BlockingAcceptanceApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.acceptance_started = asyncio.Event()
        self.release_acceptance = asyncio.Event()

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn:
        self.acceptance_started.set()
        await self.release_acceptance.wait()
        return await super().send_input(
            thread_ref,
            message,
            continuation=continuation,
            before_dispatch=before_dispatch,
        )


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

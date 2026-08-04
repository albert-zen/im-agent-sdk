from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import unittest
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import imagent.contracts as contracts_facade
from imagent.applications.capabilities import ProjectMode
from imagent.applications.contract import (
    AcceptedTurn,
    AgentApplicationAdapter,
    AgentInput,
    ApplicationInputDispatch,
    ApplicationInputDispatchHandler,
    InputContinuationPreference,
    InputDisposition,
    ThreadRef,
    TurnReplyCorrelationPolicy,
)
from imagent.applications.events import AgentEvent, AgentEventType
from imagent.gateway import GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.admission import inbound_idempotency_identity
from imagent.gateway.input import derive_client_message_id
from imagent.gateway.input.dispatch import (
    InputDispatchRejected,
    InputDispatchRuntime,
    InputPostAcceptanceError,
    TurnAcceptanceBufferOverflow,
    TurnAcceptanceOrderingGate,
)
from imagent.gateway.persistence import ConversationBinding, IdempotencyClaimStatus
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.interaction.messages import ConversationRef, InboundMessage, TextContent
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _RecordingCorrelator:
    def __init__(self) -> None:
        self.authorized: list[ApplicationInputDispatch] = []
        self.correlated: list[AcceptedTurn] = []
        self.correlation_error: BaseException | None = None

    async def authorize_input_dispatch(
        self,
        dispatch: ApplicationInputDispatch,
        *,
        thread_ref: ThreadRef,
        client_message_id: str,
    ) -> None:
        if dispatch.thread_ref != thread_ref:
            raise ValueError("different Thread")
        if dispatch.client_message_id != client_message_id:
            raise ValueError("different client message")
        self.authorized.append(dispatch)

    async def correlate_accepted_turn(
        self,
        accepted: AcceptedTurn,
        dispatch: ApplicationInputDispatch,
        *,
        thread_ref: ThreadRef,
        client_message_id: str,
        conversation_ref: ConversationRef,
        reply_to_message_id: str,
    ) -> None:
        del dispatch, thread_ref, client_message_id, conversation_ref, reply_to_message_id
        if self.correlation_error is not None:
            raise self.correlation_error
        self.correlated.append(accepted)


class _RecordingEventApplier:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []
        self.recovery_gaps: list[tuple[ThreadRef, str]] = []
        self.worker_active = False

    async def apply_ordered_event(self, event: AgentEvent) -> None:
        self.events.append(event)

    async def recover_ordering_gap(self, thread_ref: ThreadRef, *, gap_code: str) -> None:
        self.recovery_gaps.append((thread_ref, gap_code))

    def has_observing_worker(self, thread_ref: ThreadRef) -> bool:
        del thread_ref
        return self.worker_active


class _RecordingApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.continuations: list[InputContinuationPreference] = []

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
        self.continuations.append(continuation)
        return await super().send_input(
            thread_ref,
            message,
            continuation=continuation,
            before_dispatch=before_dispatch,
        )


class _DoubleDispatchApplication:
    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference,
        before_dispatch: ApplicationInputDispatchHandler | None,
    ) -> AcceptedTurn:
        del continuation
        assert before_dispatch is not None
        dispatch = ApplicationInputDispatch(
            thread_ref=thread_ref,
            client_message_id=message.client_message_id,
            disposition=InputDisposition.STARTED,
            correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
        )
        await before_dispatch(dispatch)
        await before_dispatch(dispatch)
        raise AssertionError("duplicate declaration should have failed")


class GatewayInputDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_derives_identity_authorizes_once_and_uses_default_continuation(
        self,
    ) -> None:
        application = _RecordingApplication()
        thread = await application.create_thread()
        correlator = _RecordingCorrelator()
        event_applier = _RecordingEventApplier()
        runtime = self._runtime(correlator, event_applier)
        message = self._message("message-1")
        side_effect_fence_calls = 0

        async def mark_side_effect_started() -> None:
            nonlocal side_effect_fence_calls
            side_effect_fence_calls += 1

        accepted = await runtime.dispatch(
            application,
            thread.ref,
            message,
            content=(TextContent("transformed"),),
            before_application_send=mark_side_effect_started,
        )

        self.assertEqual(
            application.continuations, [InputContinuationPreference.PREFER_ACTIVE_TURN]
        )
        self.assertEqual(side_effect_fence_calls, 1)
        self.assertEqual(len(correlator.authorized), 1)
        self.assertEqual(correlator.correlated, [accepted])
        self.assertEqual(
            application._inputs[0][1].client_message_id,
            derive_client_message_id(message.conversation_ref, message.message_id),
        )
        self.assertEqual(application._inputs[0][1].content, (TextContent("transformed"),))

    async def test_duplicate_native_dispatch_fact_is_rejected_before_a_second_fence_entry(
        self,
    ) -> None:
        correlator = _RecordingCorrelator()
        event_applier = _RecordingEventApplier()
        runtime = self._runtime(correlator, event_applier)
        thread_ref = ThreadRef("app", "thread")
        side_effect_fence_calls = 0

        async def mark_side_effect_started() -> None:
            nonlocal side_effect_fence_calls
            side_effect_fence_calls += 1

        with self.assertRaises(InputDispatchRejected):
            await runtime.dispatch(
                cast(AgentApplicationAdapter, _DoubleDispatchApplication()),
                thread_ref,
                self._message("duplicate-fact"),
                content=(TextContent("run"),),
                before_application_send=mark_side_effect_started,
            )

        self.assertEqual(len(correlator.authorized), 1)
        self.assertEqual(side_effect_fence_calls, 1)

    async def test_post_acceptance_correlation_error_is_distinct_and_drains_ordered_events(
        self,
    ) -> None:
        application = _RecordingApplication()
        thread = await application.create_thread()
        correlator = _RecordingCorrelator()
        correlator.correlation_error = RuntimeError("correlation write failed")
        event_applier = _RecordingEventApplier()
        gate = TurnAcceptanceOrderingGate(max_pending=2)
        runtime = InputDispatchRuntime(
            correlator=correlator,
            acceptance_gate=gate,
            event_applier=event_applier,
        )
        buffered = self._event(thread.ref, "buffered")
        gate.begin_acceptance(thread.ref)
        await gate.handle_event(buffered, event_applier=event_applier)
        await gate.finish_acceptance(thread.ref, event_applier=event_applier)

        with self.assertRaises(InputPostAcceptanceError) as raised:
            await runtime.dispatch(
                application,
                thread.ref,
                self._message("post-acceptance"),
                content=(TextContent("run"),),
            )

        self.assertEqual(str(raised.exception.cause), "correlation write failed")
        self.assertEqual(event_applier.events, [buffered])

    async def test_acceptance_ordering_gate_buffers_drains_and_preserves_worker_terminal_state(
        self,
    ) -> None:
        gate = TurnAcceptanceOrderingGate(max_pending=2)
        event_applier = _RecordingEventApplier()
        thread_ref = ThreadRef("app", "thread")
        first = self._event(thread_ref, "first")
        second = self._event(thread_ref, "second")

        gate.begin_acceptance(thread_ref)
        await gate.handle_event(first, event_applier=event_applier)
        await gate.handle_event(second, event_applier=event_applier)
        gate.worker_finished(thread_ref)
        self.assertIn(thread_ref, gate._event_locks)
        self.assertEqual(gate._buffered_events[thread_ref], [first, second])

        await gate.finish_acceptance(thread_ref, event_applier=event_applier)

        self.assertEqual(event_applier.events, [first, second])
        self.assertNotIn(thread_ref, gate._event_locks)
        self.assertNotIn(thread_ref, gate._buffered_events)

    async def test_acceptance_ordering_overflow_is_an_explicit_drain_error(self) -> None:
        gate = TurnAcceptanceOrderingGate(max_pending=1)
        event_applier = _RecordingEventApplier()
        thread_ref = ThreadRef("app", "thread")

        gate.begin_acceptance(thread_ref)
        await gate.handle_event(self._event(thread_ref, "first"), event_applier=event_applier)
        with self.assertRaises(TurnAcceptanceBufferOverflow):
            await gate.handle_event(self._event(thread_ref, "second"), event_applier=event_applier)
        with self.assertRaises(TurnAcceptanceBufferOverflow):
            await gate.finish_acceptance(thread_ref, event_applier=event_applier)

    async def test_gate_reports_a_typed_recovery_gap_when_ordered_application_fails(
        self,
    ) -> None:
        gate = TurnAcceptanceOrderingGate(max_pending=1)
        event_applier = _FailingEventApplier()
        thread_ref = ThreadRef("app", "thread")

        gate.begin_acceptance(thread_ref)
        await gate.handle_event(self._event(thread_ref, "failed"), event_applier=event_applier)
        with self.assertRaisesRegex(RuntimeError, "apply failed"):
            await gate.finish_acceptance(thread_ref, event_applier=event_applier)

        self.assertEqual(
            event_applier.recovery_gaps,
            [(thread_ref, "turn_acceptance_ordering_drain_failed")],
        )

    async def test_gate_preserves_ordered_event_failure_when_recovery_scheduling_fails(
        self,
    ) -> None:
        gate = TurnAcceptanceOrderingGate(max_pending=1)
        event_applier = _RecoverySchedulingFailingEventApplier()
        thread_ref = ThreadRef("app", "thread")

        gate.begin_acceptance(thread_ref)
        await gate.handle_event(self._event(thread_ref, "failed"), event_applier=event_applier)
        with self.assertRaisesRegex(RuntimeError, "apply failed") as raised:
            await gate.finish_acceptance(thread_ref, event_applier=event_applier)

        self.assertTrue(
            any("recovery scheduling also failed" in note for note in raised.exception.__notes__)
        )
        self.assertEqual(
            event_applier.recovery_gaps,
            [(thread_ref, "turn_acceptance_ordering_drain_failed")],
        )

    async def test_gate_preserves_ordered_event_cancellation_when_recovery_scheduling_fails(
        self,
    ) -> None:
        gate = TurnAcceptanceOrderingGate(max_pending=1)
        event_applier = _CancellationRecoverySchedulingFailingEventApplier()
        thread_ref = ThreadRef("app", "thread")

        gate.begin_acceptance(thread_ref)
        await gate.handle_event(self._event(thread_ref, "cancelled"), event_applier=event_applier)
        with self.assertRaisesRegex(asyncio.CancelledError, "apply cancelled") as raised:
            await gate.finish_acceptance(thread_ref, event_applier=event_applier)

        self.assertTrue(
            any("recovery scheduling also failed" in note for note in raised.exception.__notes__)
        )
        self.assertEqual(
            event_applier.recovery_gaps,
            [(thread_ref, "turn_acceptance_ordering_drain_failed")],
        )

    async def test_failed_ordered_drain_keeps_accepted_input_terminal_and_recovers(
        self,
    ) -> None:
        application = _PausingAcceptanceApplication()
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "drain-recovery")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=bindings),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        failed_once = False
        original_apply = gateway._projection_runtime.apply_ordered_event

        async def fail_first_ordered_message(event: AgentEvent) -> None:
            nonlocal failed_once
            if event.type is AgentEventType.MESSAGE_COMPLETED and not failed_once:
                failed_once = True
                raise RuntimeError("simulated ordered event apply failure")
            await original_apply(event)

        gateway._projection_runtime.apply_ordered_event = fail_first_ordered_message
        inbound = InboundMessage(
            message_id="accepted-once",
            conversation_ref=conversation,
            sender="user",
            content=(TextContent("run"),),
            created_at=datetime.now(UTC),
        )
        handling = asyncio.create_task(channel.on_message(inbound))
        try:
            await application.acceptance_started.wait()
            await self._wait_until(
                lambda: bool(gateway._turn_acceptance_gate._buffered_events.get(thread.ref))
            )
            original_worker = gateway._projection_runtime._tasks[thread.ref]
            application.release_acceptance.set()
            with self.assertRaisesRegex(RuntimeError, "simulated ordered event apply failure"):
                await handling

            await channel.on_message(inbound)
            await self._wait_until(
                lambda: (
                    len(channel.sent) == 2
                    and (health := gateway.get_projection_health(thread.ref)) is not None
                    and health.last_event_gap == "turn_acceptance_ordering_drain_failed"
                )
            )
            self.assertTrue(failed_once)
            self.assertEqual(application.send_input_calls, 1)
            scope, key = inbound_idempotency_identity(conversation, inbound.message_id)
            self.assertEqual(
                await gateway._idempotency.claim(scope, key),
                IdempotencyClaimStatus.ALREADY_COMPLETED,
            )
            self.assertIs(gateway._projection_runtime._tasks[thread.ref], original_worker)
        finally:
            application.release_acceptance.set()
            if not handling.done():
                handling.cancel()
                await asyncio.gather(handling, return_exceptions=True)
            await gateway.stop()

    async def test_terminal_worker_gap_recovery_leaves_no_stale_cancellation_marker(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "terminal-worker")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=bindings),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        runtime = gateway._projection_runtime
        try:
            await channel.on_message(
                InboundMessage(
                    message_id="start-observation",
                    conversation_ref=conversation,
                    sender="user",
                    content=(TextContent("run"),),
                    created_at=datetime.now(UTC),
                )
            )
            await self._wait_until(lambda: thread.ref in runtime._tasks)
            terminal_worker = runtime._tasks[thread.ref]
            terminal_worker.cancel()
            await asyncio.gather(terminal_worker, return_exceptions=True)
            await self._wait_until(lambda: thread.ref not in runtime._tasks)

            await runtime.recover_ordering_gap(
                thread.ref,
                gap_code="terminal_worker_ordering_gap",
            )

            await self._wait_until(
                lambda: (
                    (health := gateway.get_projection_health(thread.ref)) is not None
                    and health.last_event_gap == "terminal_worker_ordering_gap"
                    and thread.ref in runtime._tasks
                )
            )
            self.assertNotIn(thread.ref, runtime._ordering_recovery_gaps)

            recovered_worker = runtime._tasks[thread.ref]
            recovered_worker.cancel()
            await asyncio.gather(recovered_worker, return_exceptions=True)
            await self._wait_until(lambda: thread.ref not in runtime._tasks)
            self.assertNotIn(thread.ref, runtime._ordering_recovery_gaps)
        finally:
            await gateway.stop()

    async def test_inactive_thread_gap_recovery_drops_its_marker_without_delivery(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
        )
        await gateway.start()
        runtime = gateway._projection_runtime
        try:
            self.assertEqual(await runtime.active_routes(thread.ref), ())

            await runtime.recover_ordering_gap(
                thread.ref,
                gap_code="inactive_thread_ordering_gap",
            )

            await self._wait_until(lambda: thread.ref not in runtime._tasks)
            self.assertNotIn(thread.ref, runtime._ordering_recovery_gaps)
            self.assertIsNone(gateway.get_projection_health(thread.ref))
            self.assertEqual(channel.sent, [])
        finally:
            await gateway.stop()

    def test_client_identity_has_one_input_facade_and_no_contracts_alias(self) -> None:
        self.assertEqual(
            derive_client_message_id.__module__,
            "imagent.gateway.input.dispatch",
        )
        self.assertFalse(hasattr(contracts_facade, "derive_client_message_id"))
        self.assertIsNone(importlib.util.find_spec("imagent.contracts.validators"))
        completed = subprocess.run(
            [sys.executable, "-c", "import imagent.contracts.validators"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ModuleNotFoundError", completed.stderr)

    @staticmethod
    def _runtime(
        correlator: _RecordingCorrelator,
        event_applier: _RecordingEventApplier,
    ) -> InputDispatchRuntime:
        return InputDispatchRuntime(
            correlator=correlator,
            acceptance_gate=TurnAcceptanceOrderingGate(max_pending=2),
            event_applier=event_applier,
        )

    @staticmethod
    def _message(message_id: str) -> InboundMessage:
        return InboundMessage(
            message_id=message_id,
            conversation_ref=ConversationRef("channel", "conversation"),
            sender="user",
            content=(TextContent("original"),),
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _event(thread_ref: ThreadRef, event_id: str) -> AgentEvent:
        return AgentEvent(
            event_id=event_id,
            application_instance_id=thread_ref.application_instance_id,
            type=AgentEventType.STATUS_CHANGED,
            data={},
            created_at=datetime.now(UTC),
            thread_ref=thread_ref,
        )

    @staticmethod
    async def _wait_until(predicate: Callable[[], bool]) -> None:
        async with asyncio.timeout(1):
            while not predicate():
                await asyncio.sleep(0)


class _FailingEventApplier(_RecordingEventApplier):
    async def apply_ordered_event(self, event: AgentEvent) -> None:
        del event
        raise RuntimeError("apply failed")


class _RecoverySchedulingFailingEventApplier(_FailingEventApplier):
    async def recover_ordering_gap(self, thread_ref: ThreadRef, *, gap_code: str) -> None:
        await super().recover_ordering_gap(thread_ref, gap_code=gap_code)
        raise RuntimeError("recovery scheduling failed")


class _CancellationRecoverySchedulingFailingEventApplier(_RecordingEventApplier):
    async def apply_ordered_event(self, event: AgentEvent) -> None:
        del event
        raise asyncio.CancelledError("apply cancelled")

    async def recover_ordering_gap(self, thread_ref: ThreadRef, *, gap_code: str) -> None:
        await super().recover_ordering_gap(thread_ref, gap_code=gap_code)
        raise RuntimeError("recovery scheduling failed")


class _PausingAcceptanceApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.send_input_calls = 0
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
        self.send_input_calls += 1
        accepted = await super().send_input(
            thread_ref,
            message,
            continuation=continuation,
            before_dispatch=before_dispatch,
        )
        self.acceptance_started.set()
        await self.release_acceptance.wait()
        return accepted

from __future__ import annotations

import importlib.util
import inspect
import unittest
from dataclasses import replace
from subprocess import run
from sys import executable
from typing import cast

from imagent.applications import CodexApplicationAdapter
from imagent.applications.capabilities import ProjectMode
from imagent.applications.contract import AgentInput, ThreadRef
from imagent.applications.events import (
    AgentEvent,
    AgentEventType,
    EventBufferOverflow,
)
from imagent.applications.operations import ApplicationOperation, GetThreadHistory
from imagent.gateway.persistence import ThreadProjectionRoute
from imagent.gateway.projection import (
    ProjectionRecoveryUnavailable,
    RecoveryMode,
    ThreadRecovery,
)
from imagent.gateway.projection.recovery import (
    _RecoveryHealthSnapshot,
    _RecoveryLimits,
    _RecoverySupervisor,
    read_bounded_authoritative_projection,
    recover_thread,
)
from imagent.gateway.routing.projection_routes import derive_projection_route_id
from imagent.interaction.messages import ConversationRef, TextContent
from imagent.testing import FakeAgentApplicationAdapter
from tests.test_gateway_vertical_slice import NativeZenClient


class ThreadRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_projection_facade_preserves_exact_recovery_owner_objects(self) -> None:
        from imagent.gateway.projection.recovery import (
            ProjectionRecoveryUnavailable as OwnerProjectionRecoveryUnavailable,
        )
        from imagent.gateway.projection.recovery import RecoveryMode as OwnerRecoveryMode
        from imagent.gateway.projection.recovery import ThreadRecovery as OwnerThreadRecovery

        self.assertIs(ThreadRecovery, OwnerThreadRecovery)
        self.assertIs(RecoveryMode, OwnerRecoveryMode)
        self.assertIs(ProjectionRecoveryUnavailable, OwnerProjectionRecoveryUnavailable)

    def test_historical_recovery_module_is_absent(self) -> None:
        self.assertIsNone(importlib.util.find_spec("imagent.recovery"))
        result = run(
            [executable, "-c", "import imagent.recovery"],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ModuleNotFoundError", result.stderr)
        self.assertIn("imagent.recovery", result.stderr)

    async def test_thread_scoped_sequence_has_no_cross_thread_false_gap(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        first_thread = await application.create_thread()
        second_thread = await application.create_thread()
        first_events = application.subscribe_thread(first_thread.ref)
        second_events = application.subscribe_thread(second_thread.ref)

        await application.send_input(
            first_thread.ref,
            AgentInput(client_message_id="first-1", content=(TextContent("one"),)),
        )
        await application.send_input(
            second_thread.ref,
            AgentInput(client_message_id="second-1", content=(TextContent("two"),)),
        )
        await application.send_input(
            first_thread.ref,
            AgentInput(client_message_id="first-2", content=(TextContent("three"),)),
        )
        try:
            first_sequences = [(await anext(first_events)).sequence for _ in range(6)]
            second_sequences = [(await anext(second_events)).sequence for _ in range(3)]
        finally:
            await _close(first_events)
            await _close(second_events)

        self.assertEqual(first_sequences, [1, 2, 3, 4, 5, 6])
        self.assertEqual(second_sequences, [1, 2, 3])

    async def test_replay_supported_adapter_resumes_after_opaque_cursor(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        first_live = application.subscribe_thread(thread.ref)
        first_turn = await application.send_input(
            thread.ref,
            AgentInput(
                client_message_id="first-input",
                content=(TextContent("first"),),
            ),
        )
        first_events = await _collect_turn(first_live, first_turn.turn_id)
        cursor = first_events[-1].cursor
        self.assertIsNotNone(cursor)

        second_turn = await application.send_input(
            thread.ref,
            AgentInput(
                client_message_id="second-input",
                content=(TextContent("second"),),
            ),
        )
        recovery = await recover_thread(
            application,
            thread.ref,
            recovery_id="recovery:replay",
            after_cursor=cursor,
        )
        try:
            replayed = await _collect_turn(recovery.events, second_turn.turn_id)
        finally:
            await _close(recovery.events)

        self.assertEqual(recovery.mode, RecoveryMode.REPLAY)
        self.assertIsNone(recovery.history)
        self.assertTrue(all(event.cursor is not None for event in replayed))
        self.assertTrue(all(event.sequence is not None for event in replayed))
        self.assertTrue(all(event.sequence_epoch is not None for event in replayed))

    async def test_expired_cursor_falls_back_to_authoritative_history(self) -> None:
        application = FakeAgentApplicationAdapter(
            project_mode=ProjectMode.FLAT,
            event_history_limit=3,
        )
        thread = await application.create_thread()
        live = application.subscribe_thread(thread.ref)
        first_turn = await application.send_input(
            thread.ref,
            AgentInput(client_message_id="first", content=(TextContent("first"),)),
        )
        first_events = await _collect_turn(live, first_turn.turn_id)
        expired_cursor = first_events[-1].cursor
        assert expired_cursor is not None
        await application.send_input(
            thread.ref,
            AgentInput(client_message_id="second", content=(TextContent("second"),)),
        )
        recovery = await recover_thread(
            application,
            thread.ref,
            recovery_id="recovery:expired",
            after_cursor=expired_cursor,
        )
        try:
            self.assertEqual(recovery.mode, RecoveryMode.AUTHORITATIVE)
            self.assertTrue(recovery.cursor_expired)
            self.assertIsNotNone(recovery.history)
            self.assertIsNotNone(recovery.catchup)
        finally:
            await _close(recovery.events)

    async def test_appserver_without_replay_uses_authoritative_fallback(self) -> None:
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=NativeZenClient(),
            cwd="/repo",
        )
        recovery = await recover_thread(
            application,
            ThreadRef("codex-main", "codex-thread"),
            recovery_id="recovery:appserver",
            after_cursor="native-cursor-not-supported",
        )
        try:
            self.assertEqual(recovery.mode, RecoveryMode.AUTHORITATIVE)
            self.assertFalse(recovery.cursor_expired)
            self.assertIsNotNone(recovery.history)
            self.assertIsNotNone(recovery.catchup)
        finally:
            await _close(recovery.events)


class RecoverySupervisorTests(unittest.IsolatedAsyncioTestCase):
    def test_limits_reject_non_integer_or_non_positive_scan_bounds(self) -> None:
        valid = _recovery_limits()
        invalid_values = (
            0,
            -1,
            True,
            cast(int, "1"),
            cast(int, None),
        )
        for field in (
            "baseline_history_limit",
            "recovery_history_page_size",
            "recovery_max_pages",
            "catchup_limit",
            "projection_item_limit",
        ):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "positive integer"):
                        replace(valid, **{field: value})

    def test_limits_reject_non_finite_or_non_numeric_retry_bounds(self) -> None:
        valid = _recovery_limits()
        invalid_values = (
            -1.0,
            True,
            float("nan"),
            float("inf"),
            float("-inf"),
            cast(float, 10**10_000),
            cast(float, "1"),
            cast(float, None),
        )
        for field in ("retry_initial_seconds", "retry_max_seconds"):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "finite non-negative number"):
                        replace(valid, **{field: value})
        with self.assertRaisesRegex(ValueError, "below initial"):
            replace(valid, retry_initial_seconds=1.0, retry_max_seconds=0.5)

    async def test_failure_classification_is_typed_bounded_and_thread_local(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        first = await application.create_thread()
        second = await application.create_thread()
        supervisor = _recovery_supervisor(retry_initial_seconds=0.25, retry_max_seconds=1)
        first_attempt = supervisor.start_attempt(
            reconcile_existing=False,
            require_checkpoint=False,
        )
        second_attempt = supervisor.start_attempt(
            reconcile_existing=False,
            require_checkpoint=False,
        )
        health = _RecoveryHealthSnapshot(
            event_overflow_count=2,
            last_recovery_error="prior-recovery",
        )

        failure = supervisor.record_failure(
            first_attempt,
            EventBufferOverflow("application_event_fanout_overflow", max_pending=2),
            application=application,
            current_health=health,
        )

        self.assertEqual(failure.restart_count, 1)
        self.assertEqual(failure.retry_delay_seconds, 0.25)
        self.assertEqual(failure.event_overflow_count, 3)
        self.assertEqual(failure.last_gap, "application_event_fanout_overflow")
        self.assertEqual(failure.last_event_gap, "application_event_fanout_overflow")
        self.assertEqual(failure.last_event_overflow, "application_event_fanout_overflow")
        self.assertEqual(failure.last_recovery_error, "prior-recovery")
        self.assertTrue(first_attempt.needs_reconciliation)
        self.assertTrue(first_attempt.require_checkpoint)
        self.assertTrue(first_attempt.reconcile_requests)
        self.assertEqual(second_attempt.restart_count, 0)
        self.assertFalse(second_attempt.needs_reconciliation)
        self.assertNotEqual(first.ref, second.ref)

        delays = [failure.retry_delay_seconds]
        for _ in range(4):
            failure = supervisor.record_failure(
                first_attempt,
                RuntimeError("subscription unavailable"),
                application=application,
                current_health=health,
            )
            delays.append(failure.retry_delay_seconds)
        self.assertEqual(delays, [0.25, 0.5, 1, 1, 1])

    async def test_request_snapshot_coordination_uses_only_the_affected_thread(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        affected = await application.create_thread()
        unrelated = await application.create_thread()
        reconciled: list[ThreadRef] = []

        async def reconcile_request_snapshot(
            candidate_application,
            thread_ref,
            *,
            deliver_request,
        ) -> bool:
            del candidate_application, deliver_request
            reconciled.append(thread_ref)
            return True

        supervisor = _recovery_supervisor(
            reconcile_request_snapshot=reconcile_request_snapshot,
        )
        attempt = supervisor.start_attempt(
            reconcile_existing=False,
            require_checkpoint=False,
        )
        supervisor.record_failure(
            attempt,
            RuntimeError("subscription ended"),
            application=application,
            current_health=None,
        )

        degraded = await supervisor.reconcile_thread(
            attempt,
            application,
            affected.ref,
        )

        self.assertTrue(degraded)
        self.assertEqual(reconciled, [affected.ref])
        self.assertNotIn(unrelated.ref, reconciled)
        self.assertFalse(attempt.reconcile_requests)


class BoundedAuthoritativeProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_route_reads_one_bounded_page_and_caps_flattened_items(self) -> None:
        application = _RecordingHistoryApplication()
        thread = await application.create_thread()
        for index in range(3):
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id=f"baseline-{index}",
                    content=(TextContent(str(index)),),
                ),
            )
        route = _route(thread.ref, ConversationRef("test-channel", "baseline"))

        projection = await read_bounded_authoritative_projection(
            application,
            route,
            execute_application=application.execute,
            baseline_history_limit=1,
            recovery_history_page_size=2,
            recovery_max_pages=2,
            catchup_limit=1,
            projection_item_limit=1,
        )

        self.assertEqual(application.history_calls, [(1, 1)])
        self.assertEqual(projection.pages_read, 1)
        self.assertEqual(len(projection.messages), 1)
        self.assertEqual(projection.gap, "projection_window_truncated")

    async def test_existing_route_stops_at_page_bound_without_ordering_checkpoint(
        self,
    ) -> None:
        application = _RecordingHistoryApplication()
        thread = await application.create_thread()
        for index in range(4):
            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id=f"recovery-{index}",
                    content=(TextContent(str(index)),),
                ),
            )
        route = replace(
            _route(thread.ref, ConversationRef("test-channel", "checkpoint")),
            checkpoint_agent_item_id="opaque-checkpoint-not-in-window",
        )

        projection = await read_bounded_authoritative_projection(
            application,
            route,
            execute_application=application.execute,
            baseline_history_limit=1,
            recovery_history_page_size=1,
            recovery_max_pages=2,
            catchup_limit=1,
            projection_item_limit=20,
        )

        self.assertEqual(application.history_calls, [(1, 1), (1, 2)])
        self.assertEqual(projection.pages_read, 2)
        self.assertFalse(projection.checkpoint_found)
        self.assertEqual(projection.gap, "checkpoint_out_of_window")

    async def test_required_missing_checkpoint_stays_explicit(self) -> None:
        application = _RecordingHistoryApplication()
        thread = await application.create_thread()
        route = _route(thread.ref, ConversationRef("test-channel", "missing"))

        projection = await read_bounded_authoritative_projection(
            application,
            route,
            execute_application=application.execute,
            baseline_history_limit=1,
            recovery_history_page_size=1,
            recovery_max_pages=1,
            catchup_limit=1,
            projection_item_limit=1,
            require_checkpoint=True,
        )

        self.assertEqual(projection.gap, "checkpoint_missing")


async def _collect_turn(events, turn_id: str) -> tuple[AgentEvent, ...]:
    observed: list[AgentEvent] = []
    try:
        async for event in events:
            if event.turn_id != turn_id:
                continue
            observed.append(event)
            if event.type in {
                AgentEventType.TURN_COMPLETED,
                AgentEventType.TURN_FAILED,
                AgentEventType.TURN_INTERRUPTED,
            }:
                return tuple(observed)
    finally:
        await _close(events)
    raise AssertionError("event stream ended before terminal Turn event")


async def _close(events) -> None:
    close = getattr(events, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


def _recovery_limits() -> _RecoveryLimits:
    return _RecoveryLimits(
        baseline_history_limit=3,
        recovery_history_page_size=10,
        recovery_max_pages=5,
        catchup_limit=10,
        projection_item_limit=20,
        retry_initial_seconds=0.05,
        retry_max_seconds=2,
    )


class _RecordingHistoryApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.history_calls: list[tuple[int, int]] = []

    async def execute(self, operation: ApplicationOperation):
        if isinstance(operation, GetThreadHistory):
            self.history_calls.append((operation.limit, operation.page))
        return await super().execute(operation)


def _route(
    thread_ref: ThreadRef,
    conversation_ref: ConversationRef,
) -> ThreadProjectionRoute:
    return ThreadProjectionRoute(
        route_id=derive_projection_route_id(thread_ref, conversation_ref),
        thread_ref=thread_ref,
        conversation_ref=conversation_ref,
    )


def _recovery_supervisor(
    *,
    retry_initial_seconds: float = 0,
    retry_max_seconds: float = 0,
    reconcile_request_snapshot=None,
) -> _RecoverySupervisor:
    async def execute_application(operation):
        raise AssertionError(f"unexpected Application operation: {operation}")

    async def active_routes(thread_ref):
        del thread_ref
        return ()

    async def begin_bootstrap(route_id):
        raise AssertionError(f"unexpected route bootstrap: {route_id}")

    async def deliver_authoritative(
        route,
        *,
        read_projection,
        retain_barrier_on_failure=False,
    ):
        del route, read_projection, retain_barrier_on_failure
        raise AssertionError("unexpected authoritative route delivery")

    async def wait_until_delivery_ready():
        return None

    def record_gap(thread_ref, route_id, gap):
        raise AssertionError(f"unexpected recovery gap: {thread_ref}:{route_id}:{gap}")

    async def default_reconcile_request_snapshot(
        application,
        thread_ref,
        *,
        deliver_request,
    ) -> bool:
        del application, thread_ref, deliver_request
        return False

    async def deliver_request(routes, request):
        raise AssertionError(f"unexpected request delivery: {routes}:{request}")

    return _RecoverySupervisor(
        execute_application=execute_application,
        active_routes=active_routes,
        begin_bootstrap=begin_bootstrap,
        deliver_authoritative=deliver_authoritative,
        wait_until_delivery_ready=wait_until_delivery_ready,
        record_gap=record_gap,
        reconcile_request_snapshot=(
            reconcile_request_snapshot or default_reconcile_request_snapshot
        ),
        deliver_request=deliver_request,
        baseline_history_limit=3,
        recovery_history_page_size=10,
        recovery_max_pages=5,
        catchup_limit=10,
        projection_item_limit=20,
        retry_initial_seconds=retry_initial_seconds,
        retry_max_seconds=retry_max_seconds,
    )

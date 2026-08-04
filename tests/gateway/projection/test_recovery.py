from __future__ import annotations

import importlib.util
import inspect
import unittest
from subprocess import run
from sys import executable

from imagent.applications import CodexApplicationAdapter
from imagent.applications.capabilities import ProjectMode
from imagent.applications.contract import AgentInput, ThreadRef
from imagent.applications.events import AgentEvent, AgentEventType
from imagent.gateway.persistence.memory import InMemoryRequestCorrelationRepository
from imagent.gateway.projection import (
    ProjectionRecoveryUnavailable,
    RecoveryMode,
    ThreadRecovery,
)
from imagent.gateway.projection.recovery import recover_thread
from imagent.interaction.messages import TextContent
from imagent.request_projection_runtime import InteractiveRequestProjection
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


class RequestGapRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_snapshot_recovery_is_scoped_to_overflowed_thread(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        first_thread = await application.create_thread()
        second_thread = await application.create_thread()
        first_request = await application.open_approval_request(
            first_thread.ref,
            turn_id="turn-first",
        )
        await application.open_approval_request(
            second_thread.ref,
            turn_id="turn-second",
        )
        delivered = []

        async def active_routes(thread_ref):
            del thread_ref
            return ()

        async def deliver_request(routes, request) -> None:
            del routes
            delivered.append(request)

        async def cancel_request(request_ref) -> None:
            del request_ref

        projection = InteractiveRequestProjection(
            applications={"fake-agent": application},
            correlations=InMemoryRequestCorrelationRepository(),
            active_routes=active_routes,
            deliver_request=deliver_request,
            cancel_request=cancel_request,
        )

        degraded = await projection.reconcile_application_after_event_gap(
            application,
            first_thread.ref,
        )

        self.assertFalse(degraded)
        self.assertEqual(
            [request.request_ref for request in delivered],
            [first_request.request_ref],
        )


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

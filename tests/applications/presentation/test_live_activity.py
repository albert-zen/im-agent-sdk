from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import imagent.applications as applications
from imagent.adapters import IdempotencyClaimStatus
from imagent.applications import (
    ApplicationPresentationCancelled,
    ApplicationPresentationCapacityError,
    ApplicationPresentationError,
    ApplicationPresentationLimits,
    ApplicationPresentationTimeout,
    ApplicationTextPresentation,
    CodexApplicationAdapter,
    CodexLiveActivityFacts,
    CodexLiveActivityKind,
    CodexLiveActivityMethod,
    T3ActivityFacts,
    T3ApplicationAdapter,
    ZenApplicationAdapter,
    presentation,
)
from imagent.applications.contract import (
    AgentInput,
    AgentMessage,
    ThreadRef,
)
from imagent.applications.diagnostics import ApplicationPresentationFailureCode
from imagent.applications.events import AgentEventType, EventStreamReset
from imagent.applications.presentation import (
    ApplicationPresentationRuntime,
    artifact_materialization,
    live_activity,
)
from imagent.gateway.persistence import ThreadProjectionRoute
from imagent.gateway.persistence.memory import InMemoryProjectionRouteRepository
from imagent.gateway.routing.projection_routes import derive_projection_route_id
from imagent.interaction.messages import ConversationRef, MessageRole, TextContent
from imagent.projections import (
    ProjectedAgentMessage,
    deliver_projected_message,
    derive_live_projection_delivery_id,
)


class _AppServerClient:
    def __init__(self) -> None:
        self.handlers = []

    def add_notification_handler(self, handler) -> None:
        self.handlers.append(handler)

    async def close(self) -> None:
        return None

    async def list_threads(self, **params):
        del params
        return {"data": []}

    async def list_thread_turns(self, thread_id: str, **params):
        del thread_id, params
        return {"data": []}

    async def start_thread(self, **params):
        del params
        return {"thread": {"id": "thread-1"}}

    async def read_thread(self, thread_id: str, *, include_turns: bool = False):
        del include_turns
        return {"thread": {"id": thread_id}}

    async def resume_thread(self, **params):
        del params
        return {"thread": {"id": "thread-1"}}

    async def start_turn(self, thread_id: str, text: str | None = None, **kwargs):
        del thread_id, text, kwargs
        return {"turn": {"id": "turn-1"}}

    async def interrupt_turn(self, thread_id: str, turn_id: str):
        del thread_id, turn_id
        return {}


class _T3Client:
    async def shell_snapshot(self):
        return {}

    async def thread_detail(self, thread_id: str):
        del thread_id
        return {}

    async def dispatch(self, command):
        del command
        return {}


class _AcceptedT3Client(_T3Client):
    def __init__(self) -> None:
        self.dispatched = False

    async def thread_detail(self, thread_id: str):
        return {
            "thread": {
                "id": thread_id,
                "messages": [],
                "activities": (
                    [
                        {
                            "id": "activity-after-acceptance",
                            "turnId": "turn-accepted",
                            "kind": "tool.progress",
                            "summary": "Working",
                            "createdAt": "2026-08-03T10:00:00Z",
                        }
                    ]
                    if self.dispatched
                    else []
                ),
                "latestTurn": (
                    {"turnId": "turn-accepted", "state": "running"} if self.dispatched else None
                ),
            }
        }

    async def dispatch(self, command):
        del command
        self.dispatched = True
        return {}


class _PollingFailureT3Client(_T3Client):
    def __init__(self) -> None:
        self.reads = 0

    async def thread_detail(self, thread_id: str):
        self.reads += 1
        return {
            "thread": {
                "id": thread_id,
                "messages": [],
                "activities": (
                    []
                    if self.reads == 1
                    else [
                        {
                            "id": "activity-fails",
                            "turnId": "turn-1",
                            "kind": "tool.progress",
                            "summary": "Working",
                            "createdAt": "2026-08-03T10:00:00Z",
                        }
                    ]
                ),
            }
        }


class _CodexPresenter:
    def __init__(self) -> None:
        self.facts: list[CodexLiveActivityFacts] = []

    async def present_live_activity(
        self,
        facts: CodexLiveActivityFacts,
    ) -> ApplicationTextPresentation:
        self.facts.append(facts)
        return ApplicationTextPresentation((TextContent("Plan refreshed"),))


class _T3Presenter:
    def __init__(self) -> None:
        self.facts: list[T3ActivityFacts] = []

    async def present_activity(
        self,
        facts: T3ActivityFacts,
    ) -> ApplicationTextPresentation:
        self.facts.append(facts)
        return ApplicationTextPresentation((TextContent(f"Activity: {facts.summary}"),))


class _FailingT3Presenter:
    async def present_activity(self, facts: T3ActivityFacts) -> ApplicationTextPresentation:
        del facts
        raise RuntimeError("consumer detail must not escape")


class _BlockingT3Presenter(_T3Presenter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def present_activity(
        self,
        facts: T3ActivityFacts,
    ) -> ApplicationTextPresentation:
        self.facts.append(facts)
        self.started.set()
        await self.release.wait()
        return ApplicationTextPresentation((TextContent("Activity"),))


class ApplicationPresentationTests(unittest.IsolatedAsyncioTestCase):
    def test_presentation_package_is_the_exact_finite_facade(self) -> None:
        live_exports = {
            "ApplicationPresentationCancelled",
            "ApplicationPresentationCapacityError",
            "ApplicationPresentationError",
            "ApplicationPresentationFailed",
            "ApplicationPresentationLimits",
            "ApplicationPresentationRuntime",
            "ApplicationPresentationTimeout",
            "ApplicationTextPresentation",
            "CodexLiveActivityFacts",
            "CodexLiveActivityKind",
            "CodexLiveActivityMethod",
            "CodexLiveActivityPresenter",
            "CodexPlanStep",
            "T3ActivityFacts",
            "T3ActivityPresenter",
        }
        artifact_exports = {
            "AppServerArtifactCandidate",
            "AppServerArtifactMaterializationLimits",
            "AppServerArtifactMaterializer",
            "AppServerArtifactSourceKind",
            "AppServerCompletedItemFacts",
            "AppServerCompletedItemKind",
            "AppServerCompletedItemPhase",
            "AppServerTurnTerminalFacts",
            "AppServerTurnTerminalStatus",
            "ApplicationArtifactMaterialization",
            "ApplicationArtifactMaterializationCancelled",
            "ApplicationArtifactMaterializationCapacityError",
            "ApplicationArtifactMaterializationError",
            "ApplicationArtifactMaterializationFailed",
            "ApplicationArtifactMaterializationTimeout",
        }
        expected_exports = live_exports | artifact_exports

        self.assertEqual(set(presentation.__all__), expected_exports)
        self.assertEqual(len(presentation.__all__), len(expected_exports))
        for name in live_exports:
            self.assertIs(getattr(presentation, name), getattr(live_activity, name))
        for name in artifact_exports:
            self.assertIs(
                getattr(presentation, name),
                getattr(artifact_materialization, name),
            )

        for name in live_exports - {"ApplicationPresentationRuntime"}:
            self.assertIs(getattr(applications, name), getattr(live_activity, name))
        for name in artifact_exports:
            self.assertIs(getattr(applications, name), getattr(artifact_materialization, name))

        package_path = Path(presentation.__file__ or "")
        self.assertEqual(package_path.name, "__init__.py")
        legacy_module_path = package_path.parent.parent / "presentation.py"
        self.assertFalse(legacy_module_path.exists())
        self.assertFalse(hasattr(presentation, "ApplicationPresentationDiagnosticFacts"))
        self.assertTrue(hasattr(presentation, "AppServerArtifactMaterializer"))

    async def test_codex_live_presentation_is_typed_ordered_and_live_only(self) -> None:
        client = _AppServerClient()
        presenter = _CodexPresenter()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/workspace",
            live_activity_presenter=presenter,
        )
        thread_ref = ThreadRef("codex-main", "thread-1")
        events = application.subscribe_thread(thread_ref)
        try:
            await client.handlers[0](
                {
                    "method": "turn/plan/updated",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "eventId": "event-plan-1",
                        "explanation": "Inspect then test",
                        "plan": [
                            {"status": "completed", "step": "Inspect"},
                            {"status": "inProgress", "step": "Test"},
                            {"status": "missing-step"},
                        ],
                        "rawSecret": "must-not-cross-the-seam",
                    },
                }
            )
            event = await anext(events)
            self.assertEqual(event.type, AgentEventType.MESSAGE_CREATED)
            self.assertEqual(event.event_id, "event-plan-1")
            self.assertEqual(event.turn_id, "turn-1")
            message = cast(AgentMessage, event.data["message"])
            self.assertIsInstance(message, AgentMessage)
            self.assertEqual(message.agent_item_id, "event-plan-1")
            self.assertEqual(message.role, MessageRole.SYSTEM)
            self.assertEqual(message.content, (TextContent("Plan refreshed"),))
            self.assertEqual(message.metadata["live_only"], True)

            facts = presenter.facts[0]
            self.assertEqual(facts.kind, CodexLiveActivityKind.PLAN_UPDATED)
            self.assertEqual(facts.native_method, CodexLiveActivityMethod.PLAN_UPDATED)
            self.assertEqual([step.step for step in facts.plan], ["Inspect", "Test"])
            self.assertFalse(hasattr(facts, "rawSecret"))
            await client.handlers[0](
                {
                    "method": "turn/plan/updated",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "eventId": "event-plan-1",
                    },
                }
            )
            self.assertEqual(len(presenter.facts), 1)
            diagnostics = application.diagnostic_facts().presentation
            self.assertIsNotNone(diagnostics)
            self.assertEqual(cast(Any, diagnostics).success_count, 1)
        finally:
            await cast(Any, events).aclose()
            await application.stop()

    async def test_absent_codex_extension_and_zen_surface_remain_unchanged(self) -> None:
        client = _AppServerClient()
        codex = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/workspace",
        )
        self.assertIsNone(codex.diagnostic_facts().presentation)
        await client.handlers[0](
            {
                "method": "turn/plan/updated",
                "params": {"threadId": "thread-1", "eventId": "event-1"},
            }
        )
        self.assertIsNone(codex.diagnostic_facts().presentation)
        await codex.stop()

        with self.assertRaises(TypeError):
            cast(Any, ZenApplicationAdapter)(
                application_instance_id="zen-main",
                client=_AppServerClient(),
                cwd="/workspace",
                live_activity_presenter=_CodexPresenter(),
            )

    async def test_codex_reconnect_exposes_gap_without_replaying_live_presentation(self) -> None:
        client = _AppServerClient()
        presenter = _CodexPresenter()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/workspace",
            live_activity_presenter=presenter,
        )
        events = application.subscribe_thread(ThreadRef("codex-main", "thread-1"))
        try:
            await client.handlers[0](
                {
                    "method": "thread/status/changed",
                    "params": {
                        "threadId": "thread-1",
                        "eventId": "status-1",
                        "status": {"type": "active"},
                    },
                }
            )
            self.assertEqual((await anext(events)).type, AgentEventType.MESSAGE_CREATED)
            await application._handle_event_connection_reset(2)
            with self.assertRaises(EventStreamReset):
                await anext(events)
            self.assertEqual(len(presenter.facts), 1)
        finally:
            await cast(Any, events).aclose()
            await application.stop()

    async def test_t3_recoverable_presentation_is_stable_across_replay(self) -> None:
        presenter = _T3Presenter()
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_T3Client(),
            activity_presenter=presenter,
        )
        thread_ref = ThreadRef("t3-main", "thread-1")
        activity = {
            "id": "activity-1",
            "turnId": "turn-1",
            "kind": "tool.progress",
            "summary": "Compiling",
            "payload": {"detail": "37 files", "secret": "not exposed"},
            "createdAt": "2026-08-03T10:00:00Z",
        }
        try:
            live = await application._t3_activity_message(thread_ref, activity)
            replay = await application._t3_activity_message(thread_ref, activity)
            self.assertIsNotNone(live)
            self.assertEqual(live, replay)
            self.assertEqual(
                cast(AgentMessage, live).agent_item_id, "imagent:t3-activity:activity-1"
            )
            self.assertEqual(
                cast(AgentMessage, live).content, (TextContent("Activity: Compiling"),)
            )
            self.assertEqual(len(presenter.facts), 2)
            self.assertEqual(presenter.facts[0], presenter.facts[1])
            self.assertFalse(hasattr(presenter.facts[0], "payload"))
        finally:
            await application.stop()

    async def test_t3_live_activity_precedes_terminal_and_is_seen_once(self) -> None:
        presenter = _T3Presenter()
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_T3Client(),
            activity_presenter=presenter,
        )
        thread_ref = ThreadRef("t3-main", "thread-1")
        events = application._events.subscribe("thread-1")
        thread = {
            "messages": [],
            "activities": [
                {
                    "id": "activity-1",
                    "turnId": "turn-1",
                    "kind": "tool.progress",
                    "summary": "Compiling",
                    "createdAt": "2026-08-03T10:00:00Z",
                }
            ],
            "latestTurn": {"turnId": "turn-1", "state": "completed"},
        }
        try:
            await application._publish_thread_state(thread_ref, thread)
            message_event = await anext(events)
            terminal_event = await anext(events)
            self.assertEqual(message_event.type, AgentEventType.MESSAGE_COMPLETED)
            self.assertEqual(terminal_event.type, AgentEventType.TURN_COMPLETED)

            await application._publish_thread_state(thread_ref, thread)
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(anext(events), 0.01)
            self.assertEqual(len(presenter.facts), 1)
        finally:
            await cast(Any, events).aclose()
            await application.stop()

    async def test_runtime_bounds_output_lifetime_and_diagnostics(self) -> None:
        runtime = ApplicationPresentationRuntime(
            ApplicationPresentationLimits(
                timeout_seconds=0.01,
                max_items=1,
                max_text_characters=8,
                max_concurrency=1,
            )
        )

        async def too_large() -> ApplicationTextPresentation:
            return ApplicationTextPresentation((TextContent("123456789"),))

        with self.assertRaises(ApplicationPresentationError):
            await runtime.invoke(too_large)

        async def too_slow() -> ApplicationTextPresentation:
            await asyncio.sleep(1)
            return ApplicationTextPresentation((TextContent("late"),))

        with self.assertRaises(ApplicationPresentationTimeout):
            await runtime.invoke(too_slow)
        facts = runtime.diagnostic_facts()
        self.assertEqual(facts.invocation_count, 2)
        self.assertEqual(facts.failure_count, 2)
        self.assertEqual(facts.timeout_count, 1)
        self.assertEqual(facts.last_failure_code, ApplicationPresentationFailureCode.TIMED_OUT)
        await runtime.close()

    async def test_runtime_bounds_cancellation_overrun_and_capacity(self) -> None:
        runtime = ApplicationPresentationRuntime(
            ApplicationPresentationLimits(
                timeout_seconds=0.01,
                max_concurrency=1,
            )
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def stubborn() -> ApplicationTextPresentation:
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release.wait()
            return ApplicationTextPresentation((TextContent("done"),))

        invocation = asyncio.create_task(runtime.invoke(stubborn))
        await started.wait()
        invocation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await invocation
        with self.assertRaises(ApplicationPresentationCapacityError):
            await runtime.invoke(
                lambda: asyncio.sleep(
                    0,
                    result=ApplicationTextPresentation((TextContent("other"),)),
                )
            )
        facts = runtime.diagnostic_facts()
        self.assertEqual(facts.cancellation_count, 1)
        self.assertEqual(facts.cancellation_overrun_count, 1)
        self.assertEqual(facts.capacity_rejection_count, 1)
        release.set()
        await asyncio.sleep(0)
        await runtime.close()

    async def test_presenter_self_cancellation_is_an_explicit_failure(self) -> None:
        runtime = ApplicationPresentationRuntime(ApplicationPresentationLimits())

        async def self_cancel() -> ApplicationTextPresentation:
            raise asyncio.CancelledError

        with self.assertRaises(ApplicationPresentationCancelled):
            await runtime.invoke(self_cancel)
        facts = runtime.diagnostic_facts()
        self.assertEqual(facts.failure_count, 1)
        self.assertEqual(facts.cancellation_count, 1)
        await runtime.close()

    async def test_t3_post_acceptance_presentation_failure_preserves_acceptance(self) -> None:
        client = _AcceptedT3Client()
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
            activity_presenter=_FailingT3Presenter(),
        )
        try:
            accepted = await application.send_input(
                ThreadRef("t3-main", "thread-1"),
                AgentInput(
                    client_message_id="input-1",
                    content=(TextContent("Run"),),
                ),
            )
            self.assertEqual(accepted.turn_id, "turn-accepted")
            diagnostics = application.diagnostic_facts().presentation
            self.assertIsNotNone(diagnostics)
            self.assertEqual(cast(Any, diagnostics).invocation_count, 0)
        finally:
            await application.stop()

    async def test_t3_polling_presentation_failure_is_an_explicit_gap(self) -> None:
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_PollingFailureT3Client(),
            poll_interval=0,
            activity_presenter=_FailingT3Presenter(),
        )
        events = application.subscribe_thread(ThreadRef("t3-main", "thread-1"))
        try:
            with self.assertRaisesRegex(EventStreamReset, "application_event_poll_failed"):
                await asyncio.wait_for(anext(events), 1)
            diagnostics = application.diagnostic_facts().presentation
            self.assertIsNotNone(diagnostics)
            self.assertEqual(cast(Any, diagnostics).failure_count, 1)
        finally:
            await cast(Any, events).aclose()
            await application.stop()

    async def test_t3_live_message_and_activity_match_history_order(self) -> None:
        presenter = _T3Presenter()
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_T3Client(),
            activity_presenter=presenter,
        )
        thread_ref = ThreadRef("t3-main", "thread-1")
        events = application._events.subscribe("thread-1")
        thread = {
            "messages": [
                {
                    "id": "message-newer",
                    "turnId": "turn-1",
                    "role": "assistant",
                    "text": "Newer message",
                    "createdAt": "2026-08-03T10:01:00Z",
                }
            ],
            "activities": [
                {
                    "id": "activity-older",
                    "turnId": "turn-1",
                    "kind": "tool.progress",
                    "summary": "Older activity",
                    "createdAt": "2026-08-03T10:00:00Z",
                }
            ],
        }
        try:
            await application._publish_thread_state(thread_ref, thread)
            first = cast(AgentMessage, (await anext(events)).data["message"])
            second = cast(AgentMessage, (await anext(events)).data["message"])
            self.assertEqual(first.agent_item_id, "imagent:t3-activity:activity-older")
            self.assertEqual(second.agent_item_id, "message-newer")
        finally:
            await cast(Any, events).aclose()
            await application.stop()

    async def test_t3_presentation_thread_state_is_finite(self) -> None:
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_T3Client(),
            activity_presenter=_T3Presenter(),
            presentation_limits=ApplicationPresentationLimits(max_seen_identities=2),
        )
        try:
            for index in range(3):
                await application._publish_thread_state(
                    ThreadRef("t3-main", f"thread-{index}"),
                    {"messages": [], "activities": []},
                )
            self.assertEqual(tuple(application._presentation_states), ("thread-1", "thread-2"))
        finally:
            await application.stop()

    async def test_t3_active_poll_state_is_not_evictable(self) -> None:
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_T3Client(),
            activity_presenter=_T3Presenter(),
            presentation_limits=ApplicationPresentationLimits(max_seen_identities=1),
        )
        try:
            state = application._presentation_state("thread-1")
            state.pinned_by_poll = True
            with self.assertRaises(ApplicationPresentationCapacityError):
                application._presentation_state("thread-2")
            self.assertIs(application._presentation_states["thread-1"], state)
        finally:
            await application.stop()

    async def test_t3_partial_attempt_deduplication_window_is_bounded(self) -> None:
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_T3Client(),
            activity_presenter=_T3Presenter(),
            presentation_limits=ApplicationPresentationLimits(max_seen_identities=2),
        )
        thread_ref = ThreadRef("t3-main", "thread-1")

        def activity(index: int) -> dict[str, object]:
            return {
                "id": f"activity-{index}",
                "turnId": "turn-1",
                "kind": "tool.progress",
                "summary": f"Step {index}",
                "createdAt": f"2026-08-03T10:00:0{index}Z",
            }

        try:
            await application._publish_thread_state(
                thread_ref,
                {"messages": [], "activities": [activity(0), activity(1)]},
            )
            await application._publish_thread_state(
                thread_ref,
                {
                    "messages": [],
                    "activities": [activity(0), activity(1), activity(2), activity(3)],
                },
            )
            state = application._presentation_states["thread-1"]
            self.assertEqual(state.seen_activity_ids, {"activity-2", "activity-3"})
        finally:
            await application.stop()

    async def test_t3_unseen_activity_window_overflow_is_an_explicit_gap(self) -> None:
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_T3Client(),
            activity_presenter=_T3Presenter(),
            presentation_limits=ApplicationPresentationLimits(max_seen_identities=2),
        )
        thread_ref = ThreadRef("t3-main", "thread-1")
        try:
            await application._publish_thread_state(
                thread_ref,
                {"messages": [], "activities": []},
                initialize=True,
            )
            activities = [
                {
                    "id": f"activity-{index}",
                    "turnId": "turn-1",
                    "kind": "tool.progress",
                    "summary": f"Step {index}",
                    "createdAt": f"2026-08-03T10:00:0{index}Z",
                }
                for index in range(3)
            ]
            with self.assertRaisesRegex(EventStreamReset, "application_event_poll_window_gap"):
                await application._publish_thread_state(
                    thread_ref,
                    {"messages": [], "activities": activities},
                )
            diagnostics = application.diagnostic_facts().presentation
            self.assertIsNotNone(diagnostics)
            self.assertEqual(cast(Any, diagnostics).invocation_count, 0)
        finally:
            await application.stop()

    async def test_t3_poll_and_post_send_share_one_presentation_lane(self) -> None:
        presenter = _BlockingT3Presenter()
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_T3Client(),
            activity_presenter=presenter,
        )
        thread_ref = ThreadRef("t3-main", "thread-1")
        thread = {
            "messages": [],
            "activities": [
                {
                    "id": "activity-1",
                    "turnId": "turn-1",
                    "kind": "tool.progress",
                    "summary": "Compiling",
                    "createdAt": "2026-08-03T10:00:00Z",
                }
            ],
        }
        first = asyncio.create_task(application._publish_thread_state(thread_ref, thread))
        await presenter.started.wait()
        second = asyncio.create_task(application._publish_thread_state(thread_ref, thread))
        await asyncio.sleep(0)
        self.assertEqual(len(presenter.facts), 1)
        presenter.release.set()
        try:
            await asyncio.gather(first, second)
            self.assertEqual(len(presenter.facts), 1)
        finally:
            await application.stop()

    async def test_structured_native_values_and_diff_paths_do_not_cross_facts(self) -> None:
        codex_presenter = _CodexPresenter()
        client = _AppServerClient()
        codex = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/workspace",
            live_activity_presenter=codex_presenter,
        )
        await client.handlers[0](
            {
                "method": "turn/diff/updated",
                "params": {
                    "threadId": "thread-1",
                    "eventId": "diff-1",
                    "summary": {"raw": "mapping"},
                    "files": ["/secret/worktree/token.txt", {"path": "/secret/two"}],
                },
            }
        )
        codex_facts = codex_presenter.facts[0]
        self.assertIsNone(codex_facts.summary)
        self.assertEqual(codex_facts.changed_file_count, 2)
        self.assertFalse(hasattr(codex_facts, "details"))
        await codex.stop()

        t3_presenter = _T3Presenter()
        t3 = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_T3Client(),
            activity_presenter=t3_presenter,
        )
        try:
            await t3._t3_activity_message(
                ThreadRef("t3-main", "thread-1"),
                {
                    "id": "activity-1",
                    "turnId": "turn-1",
                    "kind": "tool.progress",
                    "summary": {"raw": "mapping"},
                    "payload": {"detail": ["raw", "list"]},
                    "createdAt": "2026-08-03T10:00:00Z",
                },
            )
            self.assertIsNone(t3_presenter.facts[0].summary)
            self.assertIsNone(t3_presenter.facts[0].detail)
        finally:
            await t3.stop()

    async def test_live_projection_is_idempotent_without_advancing_checkpoint(self) -> None:
        repository = InMemoryProjectionRouteRepository()
        thread_ref = ThreadRef("codex-main", "thread-1")
        conversation_ref = ConversationRef("qq-main", "conversation-1")
        route = await repository.put_projection_route(
            ThreadProjectionRoute(
                route_id=derive_projection_route_id(thread_ref, conversation_ref),
                thread_ref=thread_ref,
                conversation_ref=conversation_ref,
                checkpoint_agent_item_id="history-item-1",
                checkpointed_at=datetime.now(UTC),
            )
        )
        message = AgentMessage(
            agent_item_id="event-1",
            thread_ref=thread_ref,
            role=MessageRole.SYSTEM,
            content=(TextContent("Working"),),
            created_at=datetime.now(UTC),
        )
        delivered = []

        async def deliver(outbound, _context):
            delivered.append(outbound)
            return IdempotencyClaimStatus.ACQUIRED

        projected = ProjectedAgentMessage(
            message=message,
            turn_id="turn-1",
            event_id="event-1",
            checkpoint=False,
        )
        first = await deliver_projected_message(
            repository,
            route,
            projected,
            deliver_outbound=deliver,
            authoritative=False,
        )
        self.assertEqual(first.checkpoint_agent_item_id, "history-item-1")
        self.assertEqual(
            delivered[0].delivery_id,
            derive_live_projection_delivery_id(conversation_ref, thread_ref, "event-1"),
        )
        stored = (await repository.list_projection_routes(thread_ref))[0]
        self.assertEqual(stored.checkpoint_agent_item_id, "history-item-1")

        async def already_completed(outbound, _context):
            delivered.append(outbound)
            return IdempotencyClaimStatus.ALREADY_COMPLETED

        second = await deliver_projected_message(
            repository,
            stored,
            projected,
            deliver_outbound=already_completed,
            authoritative=False,
        )
        self.assertEqual(second.checkpoint_agent_item_id, "history-item-1")

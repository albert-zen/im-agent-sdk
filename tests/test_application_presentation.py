from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any, cast

from imagent.adapters import IdempotencyClaimStatus
from imagent.applications import (
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
)
from imagent.applications.presentation import ApplicationPresentationRuntime
from imagent.contracts import (
    AgentEventType,
    AgentMessage,
    ConversationRef,
    MessageRole,
    TextContent,
    ThreadProjectionRoute,
    ThreadRef,
)
from imagent.diagnostics import ApplicationPresentationFailureCode
from imagent.projections import (
    InMemoryProjectionRouteRepository,
    ProjectedAgentMessage,
    deliver_projected_message,
    derive_live_projection_delivery_id,
    derive_projection_route_id,
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


class ApplicationPresentationTests(unittest.IsolatedAsyncioTestCase):
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

        async def deliver(outbound):
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

        async def already_completed(outbound):
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

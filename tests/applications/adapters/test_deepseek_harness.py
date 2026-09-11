from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import cast

from imagent.applications import DeepSeekHarnessApplicationAdapter
from imagent.applications.adapters.deepseek_harness import (
    DeepSeekHarnessApplicationAdapter as DeepSeekHarnessApplicationAdapterOwner,
)
from imagent.applications.adapters.deepseek_harness import HttpDeepSeekHarnessClient
from imagent.applications.contract import (
    AgentInput,
    InputDisposition,
    ProjectRef,
    TurnReplyCorrelationPolicy,
)
from imagent.applications.events import AgentEvent, AgentEventType
from imagent.applications.operations import (
    ApplicationOperationFailed,
    CreateThread,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetTurnCatchup,
    ListProjects,
    ListThreads,
    ProjectRead,
    ProjectsListed,
    ThreadCreated,
    ThreadHistoryRead,
    ThreadRead,
    ThreadsListed,
    TurnCatchupRead,
)
from imagent.interaction.messages import TextContent


class _FakeDeepSeekHarnessClient:
    """Small in-memory double for the DeepSeek Harness Web Host RPC surface."""

    def __init__(self) -> None:
        self.workspaces: list[dict[str, object]] = [
            {
                "workspaceId": "workspace-1",
                "path": "/repo",
                "title": "repo",
                "sessionIds": [],
            },
            {
                "workspaceId": "workspace-2",
                "path": "/other",
                "title": "other",
                "sessionIds": [],
            },
        ]
        self.sessions: dict[str, dict[str, object]] = {}
        self.histories: dict[str, list[dict[str, object]]] = {}
        self.created: list[dict[str, object]] = []
        self.prompted: list[dict[str, object]] = []
        self.cancelled: list[str] = []
        self._next_session = 1
        self._next_seq = 1
        self._next_message = 1

    def add_session(self, session_id: str, *, workspace_id: str) -> None:
        session = {
            "sessionId": session_id,
            "updatedAt": int(datetime.now(UTC).timestamp() * 1000),
            "running": False,
            "blank": True,
            "cwd": "/repo",
        }
        self.sessions[session_id] = session
        self.histories.setdefault(session_id, [])
        for workspace in self.workspaces:
            if workspace["workspaceId"] == workspace_id:
                ids = list(cast(list[object], workspace.get("sessionIds", [])))
                ids.append(session_id)
                workspace["sessionIds"] = ids

    def _event(
        self, session_id: str, event_type: str, data: dict[str, object]
    ) -> dict[str, object]:
        del session_id
        event = {
            "type": event_type,
            "seq": self._next_seq,
            "time": int(datetime.now(UTC).timestamp() * 1000),
            "data": data,
        }
        self._next_seq += 1
        return event

    async def list_workspaces(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in self.workspaces)

    async def list_sessions(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in self.sessions.values())

    async def create_session(
        self,
        *,
        workspace_id: str | None = None,
        cwd: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        del cwd
        if session_id is None:
            session_id = f"dsh-session-{self._next_session}"
            self._next_session += 1
        self.add_session(session_id, workspace_id=workspace_id or "workspace-1")
        self.created.append({"workspace_id": workspace_id, "session_id": session_id})
        return {"sessionId": session_id}

    async def history(
        self,
        session_id: str,
        *,
        before_seq: int | None = None,
        max_messages: int | None = None,
    ) -> dict[str, object]:
        del max_messages
        events = list(self.histories.get(session_id, []))
        if before_seq is not None:
            events = [event for event in events if event["seq"] < before_seq]
        return {"events": [{"event": event} for event in events], "hasMore": False}

    async def prompt(
        self,
        session_id: str,
        text: str,
        *,
        mode: str = "queue",
    ) -> dict[str, object]:
        del mode
        self.prompted.append({"session_id": session_id, "text": text})
        if session_id not in self.histories:
            raise KeyError(f"unknown session: {session_id}")
        turn = len([event for event in self.histories[session_id] if event["type"] == "turn/start"])
        self._append(session_id, self._event(session_id, "turn/start", {"turn": turn}))
        message_id = f"message-{self._next_message}"
        self._next_message += 1
        self._append(
            session_id,
            self._event(
                session_id,
                "user/message",
                {
                    "turn": turn,
                    "step": 1,
                    "message": {
                        "id": message_id,
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    },
                },
            ),
        )
        self._append(
            session_id,
            self._event(
                session_id,
                "assistant/message",
                {
                    "turn": turn,
                    "step": 1,
                    "message": {
                        "id": message_id + "-assistant",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "DeepSeek Harness answer"}],
                    },
                },
            ),
        )
        self._append(
            session_id,
            self._event(
                session_id,
                "turn/end",
                {"turn": turn, "reason": {"kind": "completed"}},
            ),
        )
        return {"accepted": True}

    async def cancel(self, session_id: str) -> dict[str, object]:
        self.cancelled.append(session_id)
        return {"accepted": True}

    def _append(self, session_id: str, event: dict[str, object]) -> None:
        self.histories.setdefault(session_id, []).append(event)
        session = self.sessions[session_id]
        session["updatedAt"] = int(datetime.now(UTC).timestamp() * 1000)
        session["running"] = event["type"] == "turn/start"
        if event["type"] == "turn/end":
            session["running"] = False
            session["blank"] = False


class _CollectTurnEvents:
    def __init__(self, expected_turn_id: str) -> None:
        self.expected_turn_id = expected_turn_id
        self.events: list[AgentEvent] = []

    async def run(self, events) -> list[AgentEvent]:
        async for event in events:
            if event.turn_ref is not None and event.turn_ref.turn_id != self.expected_turn_id:
                continue
            self.events.append(event)
            if event.type in {
                AgentEventType.TURN_COMPLETED,
                AgentEventType.TURN_FAILED,
                AgentEventType.TURN_INTERRUPTED,
            }:
                return self.events
        return self.events


class DeepSeekHarnessApplicationAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_facade_preserves_owner_identity(self) -> None:
        self.assertEqual(
            DeepSeekHarnessApplicationAdapter,
            DeepSeekHarnessApplicationAdapterOwner,
        )

    def test_http_client_is_constructible_without_network(self) -> None:
        client = HttpDeepSeekHarnessClient(origin="http://127.0.0.1:3080")
        self.assertEqual(client.origin, "http://127.0.0.1:3080")

    def test_capabilities_are_managed_with_native_threads(self) -> None:
        adapter = DeepSeekHarnessApplicationAdapter(
            application_instance_id="dsh-app",
            client=_FakeDeepSeekHarnessClient(),
            poll_interval=0.01,
        )
        capabilities = adapter.summary.capabilities
        self.assertEqual(capabilities.projects.mode.value, "managed")
        self.assertEqual(capabilities.threads.listing.value, "native")
        self.assertEqual(capabilities.runtime.streaming.value, "native")
        self.assertEqual(capabilities.runtime.interruption.value, "native")
        self.assertIsNone(adapter.summary.workspace_identity)
        self.assertEqual(adapter.summary.kind, "deepseek_harness")

    async def test_project_and_thread_operations_round_trip(self) -> None:
        adapter = DeepSeekHarnessApplicationAdapter(
            application_instance_id="dsh-app",
            client=_FakeDeepSeekHarnessClient(),
            poll_interval=0.01,
        )
        await adapter.start()
        try:
            projects_result = await adapter.execute(
                ListProjects(
                    operation_id="op-project-list",
                    application_ref=adapter.summary.ref,
                    created_at=datetime.now(UTC),
                )
            )
            projects = cast(ProjectsListed, projects_result)
            self.assertIsInstance(projects, ProjectsListed)
            self.assertEqual(len(projects.projects.items), 2)

            project_ref = projects.projects.items[0].ref
            project_read_result = await adapter.execute(
                GetProject(
                    operation_id="op-project-get",
                    application_ref=adapter.summary.ref,
                    project_ref=project_ref,
                    created_at=datetime.now(UTC),
                )
            )
            project_read = cast(ProjectRead, project_read_result)
            self.assertIsInstance(project_read, ProjectRead)
            self.assertEqual(project_read.project.ref, project_ref)

            created_result = await adapter.execute(
                CreateThread(
                    operation_id="op-thread-create",
                    application_ref=adapter.summary.ref,
                    project_ref=project_ref,
                    title="DSH thread",
                    created_at=datetime.now(UTC),
                )
            )
            created = cast(ThreadCreated, created_result)
            created = cast(ThreadCreated, created_result)
            self.assertIsInstance(created, ThreadCreated)
            thread_ref = created.thread.ref
            self.assertEqual(thread_ref.project_ref, project_ref)

            listed_result = await adapter.execute(
                ListThreads(
                    operation_id="op-thread-list",
                    application_ref=adapter.summary.ref,
                    project_ref=project_ref,
                    created_at=datetime.now(UTC),
                )
            )
            listed = cast(ThreadsListed, listed_result)
            self.assertIsInstance(listed, ThreadsListed)
            self.assertIn(thread_ref, {item.ref for item in listed.threads.items})

            read_result = await adapter.execute(
                GetThread(
                    operation_id="op-thread-get",
                    application_ref=adapter.summary.ref,
                    thread_ref=thread_ref,
                    created_at=datetime.now(UTC),
                )
            )
            read = cast(ThreadRead, read_result)
            self.assertIsInstance(read, ThreadRead)
            self.assertEqual(read.thread.ref, thread_ref)

            unsupported_result = await adapter.execute(
                CreateThread(
                    operation_id="op-thread-create-context",
                    application_ref=adapter.summary.ref,
                    project_ref=project_ref,
                    title="DSH thread with context",
                    initial_context=(TextContent("context"),),
                    created_at=datetime.now(UTC),
                )
            )
            unsupported = cast(ApplicationOperationFailed, unsupported_result)
            self.assertIsInstance(unsupported, ApplicationOperationFailed)
            self.assertEqual(unsupported.error.code, "unsupported")
        finally:
            await adapter.stop()

    async def test_send_input_streams_assistant_and_terminal_events(self) -> None:
        fake = _FakeDeepSeekHarnessClient()
        adapter = DeepSeekHarnessApplicationAdapter(
            application_instance_id="dsh-app",
            client=fake,
            poll_interval=0.01,
            send_input_turn_timeout=2.0,
        )
        await adapter.start()
        try:
            created_result = await adapter.execute(
                CreateThread(
                    operation_id="op-thread-create",
                    application_ref=adapter.summary.ref,
                    project_ref=ProjectRef("dsh-app", "workspace-1"),
                    title="DSH thread",
                    created_at=datetime.now(UTC),
                )
            )
            created = cast(ThreadCreated, created_result)
            created = cast(ThreadCreated, created_result)
            self.assertIsInstance(created, ThreadCreated)
            thread_ref = created.thread.ref

            first_events = adapter.subscribe_thread(thread_ref)
            second_events = adapter.subscribe_thread(thread_ref)

            collect_first = _CollectTurnEvents("0").run(first_events)
            collect_second = _CollectTurnEvents("0").run(second_events)

            accepted = await adapter.send_input(
                thread_ref,
                AgentInput(
                    client_message_id="client-message-1",
                    content=(TextContent("hello DeepSeek"),),
                ),
            )
            self.assertEqual(accepted.client_message_id, "client-message-1")
            self.assertEqual(accepted.disposition, InputDisposition.STARTED)
            self.assertEqual(
                accepted.correlation_policy,
                TurnReplyCorrelationPolicy.CREATE_NEW,
            )
            self.assertEqual(accepted.turn_ref.thread_ref, thread_ref)
            self.assertEqual(accepted.turn_ref.turn_id, "0")

            first, second = await asyncio.wait_for(
                asyncio.gather(collect_first, collect_second),
                timeout=2.0,
            )

            for observed in (first, second):
                self.assertTrue(
                    any(event.type is AgentEventType.MESSAGE_COMPLETED for event in observed),
                    observed,
                )
                self.assertTrue(
                    any(event.type is AgentEventType.TURN_COMPLETED for event in observed),
                    observed,
                )
            self.assertEqual(
                [event.event_id for event in first],
                [event.event_id for event in second],
            )

            history_result = await adapter.execute(
                GetThreadHistory(
                    operation_id="op-thread-history",
                    application_ref=adapter.summary.ref,
                    thread_ref=thread_ref,
                    limit=5,
                    page=1,
                    created_at=datetime.now(UTC),
                )
            )
            history = cast(ThreadHistoryRead, history_result)
            self.assertIsInstance(history, ThreadHistoryRead)
            self.assertEqual(history.history.thread_ref, thread_ref)
            self.assertEqual(history.history.turns[0].turn_ref.turn_id, "0")

            catchup_result = await adapter.execute(
                GetTurnCatchup(
                    operation_id="op-turn-catchup",
                    application_ref=adapter.summary.ref,
                    thread_ref=thread_ref,
                    limit=5,
                    created_at=datetime.now(UTC),
                )
            )
            catchup = cast(TurnCatchupRead, catchup_result)
            self.assertIsInstance(catchup, TurnCatchupRead)
            self.assertEqual(catchup.catchup.thread_ref, thread_ref)
            catchup_turn_ref = catchup.catchup.turn_ref
            assert catchup_turn_ref is not None
            self.assertEqual(catchup_turn_ref.turn_id, "0")
        finally:
            await adapter.stop()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Mapping
from unittest.mock import AsyncMock, patch

import httpx

from imagent.applications import HttpT3Client
from imagent.applications.adapters.t3 import T3ApplicationAdapter, _T3StateCapacityError
from imagent.applications.contract import AgentInput, ThreadRef
from imagent.contracts.errors import ApplicationInputOutcomeUnknown
from imagent.interaction.messages import TextContent


class _ScriptedT3Client:
    def __init__(self) -> None:
        self.dispatches: list[dict[str, object]] = []
        self.detail_calls = 0
        self.order: list[str] = []
        self.dispatch_error: BaseException | None = None
        self.detail_error_after_dispatch: BaseException | None = None
        self.missing_turn_id = False
        self.block_dispatch = False
        self.dispatch_started = asyncio.Event()
        self.release_dispatch = asyncio.Event()
        self.latest_turn_ids: dict[str, str] = {}

    async def shell_snapshot(self) -> dict[str, object]:
        return {"projects": [], "threads": []}

    async def thread_detail(self, thread_id: str) -> dict[str, object]:
        self.detail_calls += 1
        if self.detail_calls > 1 and self.detail_error_after_dispatch is not None:
            raise self.detail_error_after_dispatch
        latest_turn_id = None if self.missing_turn_id else self.latest_turn_ids.get(thread_id)
        latest_turn: dict[str, object] = {"state": "running"}
        if latest_turn_id is not None:
            latest_turn["id"] = latest_turn_id
        return {
            "thread": {
                "id": thread_id,
                "messages": [],
                "latestTurn": latest_turn,
            }
        }

    async def dispatch(self, command: Mapping[str, object]) -> dict[str, object]:
        self.order.append("dispatch")
        self.dispatches.append(dict(command))
        self.dispatch_started.set()
        if self.dispatch_error is not None:
            raise self.dispatch_error
        if self.block_dispatch:
            await self.release_dispatch.wait()
        thread_id = str(command["threadId"])
        self.latest_turn_ids[thread_id] = f"turn-{len(self.dispatches)}"
        return {"accepted": True}


def _input(client_message_id: str) -> AgentInput:
    return AgentInput(
        client_message_id=client_message_id,
        content=(TextContent("run"),),
    )


def _thread(thread_id: str) -> ThreadRef:
    return ThreadRef("t3-main", thread_id)


class HttpT3ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_shell_thread_and_dispatch_routes(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/orchestration/shell":
                return httpx.Response(200, json={"projects": [], "threads": []})
            if request.url.raw_path == b"/api/orchestration/threads/thread%2Fone":
                return httpx.Response(200, json={"thread": {"id": "thread/one"}})
            if request.url.path == "/api/orchestration/dispatch":
                return httpx.Response(200, json={"sequence": 2})
            return httpx.Response(404)

        client = HttpT3Client(
            "http://t3.local",
            lambda: "secret-token",
            transport=httpx.MockTransport(handler),
        )
        async with client:
            await client.shell_snapshot()
            await client.thread_detail("thread/one")
            await client.dispatch({"type": "thread.archive", "threadId": "one"})

        self.assertEqual(
            [request.method for request in requests],
            ["GET", "GET", "POST"],
        )
        self.assertTrue(
            all(request.headers["authorization"] == "Bearer secret-token" for request in requests)
        )
        self.assertEqual(
            json.loads(requests[-1].content),
            {"type": "thread.archive", "threadId": "one"},
        )


class T3InputOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_is_last_check_and_accepted_turn_returns_without_publish(self) -> None:
        client = _ScriptedT3Client()
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
        )
        try:
            order: list[str] = []

            async def before_dispatch(plan: object) -> None:
                self.assertEqual(getattr(plan, "disposition").value, "started")
                self.assertEqual(getattr(plan, "correlation_policy").value, "create_new")
                order.append("callback")

            with patch.object(adapter, "_publish_thread_state", new_callable=AsyncMock) as publish:
                accepted = await adapter.send_input(
                    _thread("thread-1"),
                    _input("input-1"),
                    before_dispatch=before_dispatch,
                )
            publish.assert_not_awaited()
            order.extend(client.order)
            self.assertEqual(order, ["callback", "dispatch"])
            self.assertEqual(accepted.turn_id, "turn-1")
            self.assertEqual(len(client.dispatches), 1)
            self.assertEqual(adapter._turn_baselines, {("thread-1", "turn-1"): frozenset()})
        finally:
            await adapter.stop()

    async def test_callback_failure_dispatches_nothing_and_releases_reservation(self) -> None:
        client = _ScriptedT3Client()
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
            turn_baseline_max_entries=1,
        )
        try:
            cause = ValueError("callback rejected")

            async def reject(_plan: object) -> None:
                raise cause

            with self.assertRaises(ValueError) as raised:
                await adapter.send_input(
                    _thread("thread-1"),
                    _input("input-rejected"),
                    before_dispatch=reject,
                )
            self.assertIs(raised.exception, cause)
            self.assertEqual(client.dispatches, [])
            self.assertEqual(adapter._reserved_turn_baselines, 0)

            accepted = await adapter.send_input(_thread("thread-1"), _input("input-accepted"))
            self.assertEqual(accepted.turn_id, "turn-1")
            self.assertEqual(len(client.dispatches), 1)
        finally:
            await adapter.stop()

    async def test_dispatch_exception_is_unknown_once_without_fallback(self) -> None:
        client = _ScriptedT3Client()
        cause = RuntimeError("native dispatch failed")
        client.dispatch_error = cause
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
            turn_baseline_max_entries=1,
        )
        try:
            with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
                await adapter.send_input(_thread("thread-1"), _input("input-unknown"))
            self.assertIs(raised.exception.cause, cause)
            self.assertIs(raised.exception.__cause__, cause)
            self.assertEqual(len(client.dispatches), 1)
            self.assertEqual(adapter._reserved_turn_baselines, 0)

            client.dispatch_error = None
            accepted = await adapter.send_input(
                _thread("thread-1"), _input("input-retry-by-caller")
            )
            self.assertEqual(accepted.turn_id, "turn-2")
            self.assertEqual(len(client.dispatches), 2)
        finally:
            await adapter.stop()

    async def test_follow_up_detail_exception_is_unknown_without_fallback(self) -> None:
        client = _ScriptedT3Client()
        cause = RuntimeError("authoritative detail unavailable")
        client.detail_error_after_dispatch = cause
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
        )
        try:
            with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
                await adapter.send_input(_thread("thread-1"), _input("input-detail-unknown"))
            self.assertIs(raised.exception.cause, cause)
            self.assertEqual(len(client.dispatches), 1)
            self.assertEqual(adapter._turn_baselines, {})
        finally:
            await adapter.stop()

    async def test_missing_turn_id_is_unknown_and_does_not_dispatch_again(self) -> None:
        client = _ScriptedT3Client()
        client.missing_turn_id = True
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
        )
        try:
            with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
                await adapter.send_input(_thread("thread-1"), _input("input-missing-id"))
            self.assertIsInstance(raised.exception.cause, RuntimeError)
            self.assertIn("accepted turn id", str(raised.exception.cause))
            self.assertEqual(len(client.dispatches), 1)
            self.assertEqual(adapter._reserved_turn_baselines, 0)
        finally:
            await adapter.stop()

    async def test_existing_unknown_is_not_wrapped_again(self) -> None:
        client = _ScriptedT3Client()
        cause = ApplicationInputOutcomeUnknown("already unknown", RuntimeError("native"))
        client.dispatch_error = cause
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
        )
        try:
            with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
                await adapter.send_input(_thread("thread-1"), _input("input-already-unknown"))
            self.assertIs(raised.exception, cause)
            self.assertEqual(len(client.dispatches), 1)
        finally:
            await adapter.stop()

    async def test_cancelled_dispatch_attempt_is_unknown(self) -> None:
        client = _ScriptedT3Client()
        client.dispatch_error = asyncio.CancelledError()
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
        )
        try:
            with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
                await adapter.send_input(_thread("thread-1"), _input("input-cancelled"))
            self.assertIsInstance(raised.exception.cause, asyncio.CancelledError)
            self.assertEqual(len(client.dispatches), 1)
        finally:
            await adapter.stop()


class T3StateBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_baseline_capacity_rejects_before_callback_and_native_mutation(
        self,
    ) -> None:
        client = _ScriptedT3Client()
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
            turn_baseline_max_entries=1,
        )
        try:
            first = await adapter.send_input(_thread("thread-1"), _input("input-first"))
            callback_calls: list[str] = []

            async def before_dispatch(_plan: object) -> None:
                callback_calls.append("called")

            with self.assertRaises(_T3StateCapacityError):
                await adapter.send_input(
                    _thread("thread-2"),
                    _input("input-capacity"),
                    before_dispatch=before_dispatch,
                )
            self.assertEqual(callback_calls, [])
            self.assertEqual(len(client.dispatches), 1)

            await adapter._publish_thread_state(
                _thread("thread-1"),
                {"messages": [], "latestTurn": {"id": first.turn_id, "state": "completed"}},
            )
            second = await adapter.send_input(_thread("thread-2"), _input("input-after-terminal"))
            self.assertEqual(second.turn_id, "turn-2")
            self.assertEqual(tuple(adapter._turn_baselines), (("thread-2", "turn-2"),))
        finally:
            await adapter.stop()

    async def test_send_lock_counts_waiters_and_cancellation_releases_one_user(self) -> None:
        client = _ScriptedT3Client()
        client.block_dispatch = True
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=client,
            send_lock_max_threads=1,
        )
        first_task = asyncio.create_task(
            adapter.send_input(_thread("thread-1"), _input("input-owner"))
        )
        try:
            await client.dispatch_started.wait()
            waiter_task = asyncio.create_task(
                adapter.send_input(_thread("thread-1"), _input("input-waiter"))
            )
            async with asyncio.timeout(1):
                while adapter._send_locks["thread-1"].users < 2:
                    await asyncio.sleep(0)
            waiter_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter_task
            self.assertEqual(adapter._send_locks["thread-1"].users, 1)
            with self.assertRaises(_T3StateCapacityError):
                await adapter.send_input(_thread("thread-2"), _input("input-other-thread"))
            client.release_dispatch.set()
            accepted = await first_task
            self.assertEqual(accepted.turn_id, "turn-1")
            self.assertEqual(adapter._send_locks, {})
        finally:
            if not first_task.done():
                client.release_dispatch.set()
                first_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first_task
            await adapter.stop()

    async def test_seen_and_terminal_identity_windows_evict_oldest_first(self) -> None:
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=_ScriptedT3Client(),
            seen_message_max_entries=2,
            terminal_turn_max_entries=2,
        )
        try:
            adapter._remember_seen_message("thread-1", "message-1")
            adapter._remember_seen_message("thread-2", "message-2")
            adapter._remember_seen_message("thread-1", "message-3")
            self.assertEqual(
                tuple(adapter._seen_message_order),
                (("thread-2", "message-2"), ("thread-1", "message-3")),
            )
            self.assertEqual(
                adapter._seen_messages,
                {
                    "thread-2": {"message-2"},
                    "thread-1": {"message-3"},
                },
            )

            self.assertTrue(adapter._remember_terminal_turn("thread-1", "turn-1"))
            self.assertTrue(adapter._remember_terminal_turn("thread-2", "turn-2"))
            self.assertTrue(adapter._remember_terminal_turn("thread-1", "turn-3"))
            self.assertEqual(
                tuple(adapter._terminal_turn_order),
                (("thread-2", "turn-2"), ("thread-1", "turn-3")),
            )
            self.assertEqual(
                adapter._terminal_turns,
                {
                    "thread-2": {"turn-2"},
                    "thread-1": {"turn-3"},
                },
            )
        finally:
            await adapter.stop()

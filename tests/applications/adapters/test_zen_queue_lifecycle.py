from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from imagent.applications import ZenApplicationAdapter, codex_app_server_client
from imagent.applications.adapters.appserver.client.handoff import (
    AppServerDispatchPosition,
    AppServerResponse,
)
from imagent.applications.adapters.appserver.transport import AppServerTransport
from imagent.applications.contract import (
    ApplicationInputOutcomeUnknown,
    ProjectRef,
    ThreadRef,
)
from imagent.applications.events import EventStreamGap, EventStreamOverflow
from imagent.applications.queued_input import (
    MAX_QUEUED_INPUT_READ_IDS,
    QueuedInputControl,
    QueuedInputNoticeKind,
    QueuedInputObserver,
    QueuedInputSnapshotReader,
)
from imagent.interaction.operations import ContractViolation
from tests.applications.adapters.test_zen_queue import _QueueClient


class _LifecycleClient(_QueueClient):
    def __init__(self):
        super().__init__()
        self.connection_epoch = self.epoch
        self.handler = None
        self.items = [
            {
                "id": "queued",
                "threadId": "thread",
                "type": "user_message_queued",
                "clientId": "client",
                "input": [{"type": "text", "text": "original"}],
            }
        ]
        self.snapshot = {
            "threadId": "thread",
            "processEpoch": "host",
            "watermark": 0,
            "events": [],
            "thread": {"id": "thread", "cwd": "/repo", "items": self.items},
        }
        self.resume_entered = asyncio.Event()
        self.resume_release = asyncio.Event()
        self.resume_release.set()
        self.resumes = 0
        self.cancel_calls = 0
        self.cancel_state = "cancelled"
        self.cancel_result: Any = None

    def add_notification_handler(self, handler):
        self.handler = handler

    async def call_with_dispatch_position(self, method, params):
        if method == "thread/resume":
            self.native_calls.append((method, params))
            return AppServerResponse(
                {"thread": deepcopy(self.snapshot["thread"])},
                AppServerDispatchPosition(self.epoch, 0),
            )
        return await super().call_with_dispatch_position(method, params)

    async def call(self, method, params):
        if method == "zen/thread/read":
            self.native_calls.append((method, params))
            return {"thread": deepcopy(self.snapshot["thread"])}
        return await super().call(method, params)

    async def resume_zen_thread(self, thread_id, *, expected_connection_epoch):
        assert expected_connection_epoch == self.epoch
        self.resumes += 1
        self.resume_entered.set()
        await self.resume_release.wait()
        return AppServerResponse(deepcopy(self.snapshot), AppServerDispatchPosition(self.epoch, 0))

    async def cancel_zen_queued_input(
        self, thread_id, client, queued, *, expected_connection_epoch
    ):
        self.cancel_calls += 1
        if self.cancel_result is not None:
            return self.cancel_result
        if self.cancel_state == "cancelled":
            self.items.append(
                {
                    "id": "cancelled",
                    "threadId": thread_id,
                    "clientId": client,
                    "queuedItemId": queued,
                    "type": "user_message_queue_cancelled",
                }
            )
        elif self.cancel_state == "admitted":
            self.items.append(
                {
                    "id": "human",
                    "threadId": thread_id,
                    "clientId": client,
                    "turnId": "original-turn",
                    "type": "user_message",
                }
            )
        return {
            "results": [
                {
                    "clientId": client,
                    "queuedItemId": queued,
                    "status": "cancelled"
                    if self.cancel_state == "cancelled"
                    else "already_started",
                }
            ]
        }

    async def emit(self, watermark, *, kind="queue_failed", epoch=None, process="host"):
        assert self.handler is not None
        event = {
            "type": kind,
            "threadId": "thread",
            "clientId": "client",
            "queuedItemId": "queued",
            "attemptId": f"attempt-{watermark}",
            "message": "secret error text",
        }
        await self.handler(
            {
                "method": "zen/thread/event",
                "_connection_epoch": self.epoch if epoch is None else epoch,
                "params": {
                    "threadId": "thread",
                    "processEpoch": process,
                    "watermark": watermark,
                    "event": event,
                },
            }
        )

    async def close(self):
        self.resume_release.set()


class ZenQueueLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = _LifecycleClient()
        self.adapter = ZenApplicationAdapter(
            application_instance_id="zen",
            client=self.client,
            workspace_id="workspace",
            cwd="/repo",
            event_buffer_max_pending=2,
        )
        self.thread = ThreadRef(ProjectRef("zen", "workspace"), "thread")

    async def asyncTearDown(self):
        await self.adapter.stop()

    async def _emit_canonical(self, watermark, item):
        assert self.client.handler is not None
        await self.client.handler(
            {
                "method": "zen/thread/event",
                "_connection_epoch": self.client.epoch,
                "params": {
                    "threadId": "thread",
                    "processEpoch": "host",
                    "watermark": watermark,
                    "event": {"type": "item_completed", "item": item},
                },
            }
        )

    async def test_native_history_is_identity_only_and_owned_receipt_stays_strict(self):
        from tests.applications.adapters.test_zen_queue_bulk import _foreign_native_queued_items

        original = await self.adapter.read_queued_input_receipt(self.thread, "client")
        foreign = _foreign_native_queued_items()
        self.client.items.extend(foreign)
        sub = await self.adapter.subscribe_queued_inputs(self.thread)
        self.assertEqual(
            await self.adapter.read_queued_input_receipt(self.thread, "client"), original
        )
        for item in foreign:
            with self.subTest(client=item["clientId"]), self.assertRaises(ContractViolation):
                await self.adapter.read_queued_input_receipt(self.thread, item["clientId"])
        owner = self.adapter._queue_observation
        assert owner is not None
        self.assertEqual(
            owner.entries[self.thread].queued_ids,
            {item["clientId"]: item["id"] for item in self.client.items},
        )
        await self.client.emit(1)
        notice = await anext(sub)
        self.assertEqual(notice.client_message_id, "client")
        await sub.aclose()

    async def test_native_live_append_edit_cancel_are_redacted_wakeups(self):
        from tests.applications.adapters.test_zen_queue_bulk import _foreign_native_queued_items

        sub = await self.adapter.subscribe_queued_inputs(self.thread)
        watermark = 0
        for item in _foreign_native_queued_items():
            for kind in (
                "user_message_queued",
                "user_message_queue_edited",
                "user_message_queue_cancelled",
            ):
                watermark += 1
                event = (
                    item
                    if kind == "user_message_queued"
                    else {
                        **item,
                        "id": f"{item['id']}-{kind}",
                        "type": kind,
                        "queuedItemId": item["id"],
                    }
                )
                await self._emit_canonical(watermark, event)
                notice = await anext(sub)
                self.assertEqual(notice.client_message_id, item["clientId"])
                self.assertEqual(notice.queued_item_id, item["id"])
                self.assertIs(notice.kind, QueuedInputNoticeKind.CHANGED)
                for content in ("Host-loaded", "native batch text", "/native/", "attachment"):
                    self.assertNotIn(content, repr(notice))
        await self.client.emit(watermark + 1)
        self.assertEqual((await anext(sub)).client_message_id, "client")
        await sub.aclose()

    async def test_native_tail_payload_does_not_poison_reinstallation(self):
        from tests.applications.adapters.test_zen_queue_bulk import _foreign_native_queued_items

        foreign = _foreign_native_queued_items()[1]
        self.client.items.append(foreign)
        self.client.snapshot["watermark"] = 1
        self.client.snapshot["events"] = [
            {
                "threadId": "thread",
                "processEpoch": "host",
                "watermark": 1,
                "event": {"type": "item_completed", "item": foreign},
            }
        ]
        sub = await self.adapter.subscribe_queued_inputs(self.thread)
        self.assertEqual((await anext(sub)).client_message_id, foreign["clientId"])
        await sub.aclose()
        sub = await self.adapter.subscribe_queued_inputs(self.thread)
        self.assertEqual((await anext(sub)).queued_item_id, foreign["id"])
        await self.client.emit(2)
        self.assertEqual((await anext(sub)).client_message_id, "client")
        await sub.aclose()

    async def test_foreign_history_still_requires_scope_ids_and_unambiguous_lineage(self):
        from tests.applications.adapters.test_zen_queue_bulk import _foreign_native_queued_items

        foreign = _foreign_native_queued_items()[0]
        original = deepcopy(self.client.items)
        for invalid in (
            {**foreign, "threadId": "other"},
            {**foreign, "id": "queued"},
            {**foreign, "clientId": "client"},
            {**foreign, "clientId": ""},
        ):
            self.client.items[:] = [*original, invalid]
            with self.subTest(invalid=invalid), self.assertRaises(ContractViolation):
                await self.adapter.subscribe_queued_inputs(self.thread)
        self.client.items[:] = original
        sub = await self.adapter.subscribe_queued_inputs(self.thread)
        await self._emit_canonical(1, {**foreign, "clientId": "client"})
        with self.assertRaises(EventStreamGap):
            await anext(sub)

    async def test_owned_content_change_cannot_pass_cancel_fingerprint_fence(self):
        from tests.applications.adapters.test_zen_queue_bulk import _foreign_native_queued_items

        receipt = await self.adapter.read_queued_input_receipt(self.thread, "client")
        assert receipt is not None
        self.client.items.extend(_foreign_native_queued_items())
        sub = await self.adapter.subscribe_queued_inputs(self.thread)
        self.client.items[0]["input"] = [{"type": "text", "text": "changed original"}]
        with self.assertRaises(ContractViolation):
            await self.adapter.cancel_queued_input_receipt(receipt)
        self.assertEqual(self.client.cancel_calls, 0)
        await sub.aclose()

    async def test_bulk_one_snapshot_no_subscription_and_no_cached_outcomes(self):
        self.assertIsInstance(self.adapter, QueuedInputSnapshotReader)
        self.assertIsInstance(self.adapter, QueuedInputControl)
        self.assertIsInstance(self.adapter, QueuedInputObserver)
        receipts = await self.adapter.read_queued_input_receipts(
            self.thread, ("absent", "client", "client")
        )
        self.assertIsNone(receipts[0])
        self.assertEqual(receipts[1], receipts[2])
        self.assertEqual(len(self.client.native_calls), 1)
        self.assertEqual(self.client.resumes, 0)
        self.client.items.clear()
        self.assertIsNone(await self.adapter.read_queued_input_receipt(self.thread, "client"))
        with self.assertRaises(ContractViolation):
            await self.adapter.read_queued_input_receipts(
                self.thread, ("client",) * (MAX_QUEUED_INPUT_READ_IDS + 1)
            )
        self.assertEqual(len(self.client.native_calls), 2)

    async def test_cancel_reads_real_cancelled_or_original_admitted_turn(self):
        original = deepcopy(self.client.items)
        for outcome in ("cancelled", "admitted"):
            self.client.items[:] = original
            self.client.cancel_state = outcome
            receipt = await self.adapter.read_queued_input_receipt(self.thread, "client")
            assert receipt is not None
            terminal = await self.adapter.cancel_queued_input_receipt(receipt)
            self.assertEqual(terminal.state.value, outcome)
            if terminal.turn_ref:
                self.assertEqual(terminal.turn_ref.turn_id, "original-turn")
            self.assertEqual(terminal.input_fingerprint, receipt.input_fingerprint)
            # Already-terminal canonical evidence requires no second mutation.
            count = self.client.cancel_calls
            self.assertEqual(await self.adapter.cancel_queued_input_receipt(receipt), terminal)
            self.assertEqual(self.client.cancel_calls, count)

    async def test_cancel_scope_fingerprint_fences_before_dispatch(self):
        receipt = await self.adapter.read_queued_input_receipt(self.thread, "client")
        assert receipt is not None
        with self.assertRaises(ContractViolation):
            await self.adapter.cancel_queued_input_receipt(
                replace(receipt, input_fingerprint="0" * 64)
            )
        self.assertEqual(self.client.cancel_calls, 0)

    async def test_cancel_reserved_or_malformed_result_is_unknown_once(self):
        receipt = await self.adapter.read_queued_input_receipt(self.thread, "client")
        assert receipt is not None
        self.client.cancel_state = "reserved"
        with self.assertRaises(ApplicationInputOutcomeUnknown):
            await self.adapter.cancel_queued_input_receipt(receipt)
        self.assertEqual(self.client.cancel_calls, 1)
        self.client.cancel_result = {"results": []}
        with self.assertRaises(ApplicationInputOutcomeUnknown):
            await self.adapter.cancel_queued_input_receipt(receipt)
        self.assertEqual(self.client.cancel_calls, 2)

    async def test_immediate_event_before_response_survives_and_is_redacted(self):
        self.client.resume_release.clear()
        task = asyncio.create_task(self.adapter.subscribe_queued_inputs(self.thread))
        await self.client.resume_entered.wait()
        await self.client.emit(1)
        self.client.resume_release.set()
        sub = await task
        notice = await asyncio.wait_for(anext(sub), 1)
        self.assertIs(notice.kind, QueuedInputNoticeKind.ATTEMPT_FAILED)
        self.assertEqual((notice.client_message_id, notice.queued_item_id), ("client", "queued"))
        self.assertNotIn("secret", repr(notice))
        self.assertLessEqual(len(notice.notice_id), 512)
        await sub.aclose()

    async def test_same_thread_overlap_and_cancelled_waiter_share_barrier(self):
        self.client.resume_release.clear()
        first = asyncio.create_task(self.adapter.subscribe_queued_inputs(self.thread))
        await self.client.resume_entered.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(self.adapter.subscribe_queued_inputs(self.thread))
        await asyncio.sleep(0)
        self.assertEqual(self.client.resumes, 1)
        self.client.resume_release.set()
        sub = await second
        await sub.aclose()

    async def test_overflow_ends_only_lagging_observer_and_close_wakes_waiter(self):
        slow = await self.adapter.subscribe_queued_inputs(self.thread)
        fast = await self.adapter.subscribe_queued_inputs(self.thread)
        self.assertEqual(self.client.resumes, 1)
        for wm in range(1, 4):
            await self.client.emit(wm)
            self.assertIs((await anext(fast)).kind, QueuedInputNoticeKind.ATTEMPT_FAILED)
        with self.assertRaises(EventStreamOverflow):
            await anext(slow)
        waiting = asyncio.ensure_future(anext(fast))
        await asyncio.sleep(0)
        await fast.aclose()
        with self.assertRaises(StopAsyncIteration):
            await waiting

    async def test_epoch_and_watermark_gaps_are_explicit(self):
        cases: tuple[dict[str, Any], ...] = ({"epoch": 500}, {"process": "other"}, {})
        for kwargs in cases:
            sub = await self.adapter.subscribe_queued_inputs(self.thread)
            await self.client.emit(3, **kwargs)
            with self.assertRaises(EventStreamGap):
                await anext(sub)

    async def test_snapshot_tail_and_covered_callbacks_are_deduplicated(self):
        self.client.snapshot["watermark"] = 1
        self.client.snapshot["events"] = [
            {
                "threadId": "thread",
                "processEpoch": "host",
                "watermark": 1,
                "event": {
                    "type": "queue_failed",
                    "threadId": "thread",
                    "clientId": "client",
                    "queuedItemId": "queued",
                    "attemptId": "one",
                },
            }
        ]
        sub = await self.adapter.subscribe_queued_inputs(self.thread)
        first = await anext(sub)
        await self.client.emit(1)
        await self.client.emit(2)
        second = await anext(sub)
        self.assertNotEqual(first.notice_id, second.notice_id)
        await sub.aclose()

    async def test_pre_response_overflow_and_partial_snapshot_fail_closed(self):
        self.client.resume_release.clear()
        waiter = asyncio.create_task(self.adapter.subscribe_queued_inputs(self.thread))
        await self.client.resume_entered.wait()
        for watermark in range(1, 4):
            await self.client.emit(watermark)
        self.client.resume_release.set()
        with self.assertRaises(EventStreamGap):
            await waiter
        self.client.snapshot["thread"]["partial"] = True
        with self.assertRaises(ContractViolation):
            await self.adapter.subscribe_queued_inputs(self.thread)
        self.assertEqual(self.client.resumes, 2)

    async def test_reset_discards_old_notices_and_reinstalls_one_barrier(self):
        sub = await self.adapter.subscribe_queued_inputs(self.thread)
        await self.client.emit(1)
        await self.adapter._handle_event_connection_reset(self.client.epoch)
        with self.assertRaises(EventStreamGap):
            await anext(sub)
        self.client.epoch += 1
        self.client.connection_epoch = self.client.epoch
        new = await self.adapter.subscribe_queued_inputs(self.thread)
        self.assertEqual(self.client.resumes, 2)
        await self.client.emit(1)
        self.assertIs((await anext(new)).kind, QueuedInputNoticeKind.ATTEMPT_FAILED)
        await new.aclose()

    async def test_close_joins_resume_and_fences_new_subscribers(self):
        self.client.resume_release.clear()
        waiter = asyncio.create_task(self.adapter.subscribe_queued_inputs(self.thread))
        await self.client.resume_entered.wait()
        await self.adapter.stop()
        with self.assertRaises(EventStreamGap):
            await waiter
        with self.assertRaises(RuntimeError):
            await self.adapter.subscribe_queued_inputs(self.thread)
        owner = self.adapter._queue_observation
        assert owner is not None
        self.assertFalse(owner.tasks)
        self.assertFalse(owner.entries)


class _Transport:
    async def close(self):
        pass


class NativeQueueClientLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        client = codex_app_server_client(endpoint="stdio://")
        client.connection_epoch = 1
        client._transport = cast(AppServerTransport, _Transport())
        return client

    async def test_production_cancel_has_one_dispatch_for_errors_and_malformed_success(self):
        for response in (
            {"result": []},
            {"error": {"code": -32001, "message": "overloaded"}},
            {
                "error": {
                    "code": -32000,
                    "message": "unknown",
                    "data": {"zenCode": "admission_outcome_unknown"},
                }
            },
        ):
            client = self.client()
            once = AsyncMock(return_value=(response, AppServerDispatchPosition(1, 0)))
            with (
                patch.object(client, "_ensure_ready", AsyncMock()),
                patch.object(client, "_request_once_without_initialize", once),
            ):
                with self.assertRaises(ApplicationInputOutcomeUnknown):
                    await client.cancel_zen_queued_input(
                        "thread", "client", "queued", expected_connection_epoch=1
                    )
            self.assertEqual(once.await_count, 1)
            self.assertEqual(once.call_args.args[0], "zen/thread/queue/cancel")
            self.assertEqual(
                once.call_args.args[1],
                {"threadId": "thread", "items": [{"clientId": "client", "queuedItemId": "queued"}]},
            )
            await client.close()

    async def test_generic_and_explicit_resume_share_task_after_caller_cancellation(self):
        client = self.client()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def once(method, params):
            calls.append(method)
            entered.set()
            await release.wait()
            return {"result": {}}, AppServerDispatchPosition(1, 0)

        with (
            patch.object(client, "_ensure_ready", AsyncMock()),
            patch.object(client, "_request_once_without_initialize", once),
        ):
            first = asyncio.create_task(client.call("zen/thread/resume", {"threadId": "thread"}))
            await entered.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(
                client.resume_zen_thread("thread", expected_connection_epoch=1)
            )
            await asyncio.sleep(0)
            self.assertEqual(calls, ["zen/thread/resume"])
            release.set()
            await second
        await client.close()

    async def test_real_cancel_timeout_caller_cancel_and_close_are_unknown_without_replay(self):
        for mode in ("timeout", "cancel", "close"):
            client = self.client()
            client._request_timeout_s = 0.01
            sent = asyncio.Event()
            dispatches = []

            async def send(payload, **kwargs):
                dispatches.append(payload)
                sent.set()

            with (
                patch.object(client, "_ensure_ready", AsyncMock()),
                patch.object(client, "_send_json", send),
            ):
                task = asyncio.create_task(
                    client.cancel_zen_queued_input(
                        "thread", "client", "queued", expected_connection_epoch=1
                    )
                )
                await sent.wait()
                if mode == "cancel":
                    task.cancel()
                elif mode == "close":
                    await client.close()
                with self.assertRaises(ApplicationInputOutcomeUnknown):
                    await task
                self.assertEqual(len(dispatches), 1)
            await client.close()

    async def test_close_resets_unresolved_real_rpc_and_joins_owner(self):
        client = self.client()
        sent = asyncio.Event()

        async def send(*args, **kwargs):
            sent.set()

        with (
            patch.object(client, "_ensure_ready", AsyncMock()),
            patch.object(client, "_send_json", send),
        ):
            task = asyncio.create_task(client.resume_zen_thread("thread"))
            await sent.wait()
            await client.close()
            with self.assertRaises(Exception):
                await task
            self.assertFalse(client._zen_resume_tasks)
            self.assertFalse(client._pending_futures)

    async def test_codex_public_resumes_serialize_fresh_options_after_caller_cancel(self):
        client = self.client()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def request(method, params, **kwargs):
            calls.append((method, params))
            if len(calls) == 1:
                entered.set()
                await release.wait()
            return AppServerResponse({"snapshot": len(calls)}, AppServerDispatchPosition(1, 0))

        with (
            patch.object(client, "_ensure_ready", AsyncMock()),
            patch.object(client, "_request_without_initialize_with_dispatch_position", request),
        ):
            first = asyncio.create_task(client.resume_thread(thread_id="same"))
            await entered.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            waiting = asyncio.create_task(client.resume_thread(thread_id="same", model="cancelled"))
            await asyncio.sleep(0)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            second = asyncio.create_task(
                client.resume_thread_for_recovery(thread_id="same", model="fresh")
            )
            generic = asyncio.create_task(
                client.call("thread/resume", {"threadId": "same", "model": "generic"})
            )
            positioned = asyncio.create_task(
                client.call_with_dispatch_position(
                    "thread/resume", {"threadId": "same", "model": "positioned"}
                )
            )
            await client.resume_thread(thread_id="independent")
            self.assertEqual([p["threadId"] for _, p in calls], ["same", "independent"])
            release.set()
            await asyncio.gather(second, generic, positioned)
            self.assertEqual(
                [p.get("model") for _, p in calls], [None, None, "fresh", "generic", "positioned"]
            )
        await client.close()
        self.assertFalse(client._codex_resume_tasks)

    async def test_cancelled_close_keeps_cleanup_owner_and_fences_waiters(self):
        client = self.client()
        await client._connect_lock.acquire()
        close = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        close.cancel()
        await asyncio.sleep(0)
        close.cancel()
        await asyncio.sleep(0)
        try:
            self.assertFalse(close.done(), "cancelled close must join its cleanup owner")
            with self.assertRaises(Exception):
                await client.resume_thread(thread_id="late")
        finally:
            client._connect_lock.release()
            with self.assertRaises(asyncio.CancelledError):
                await close
            await client.close()
        self.assertIsNone(client._transport)
        self.assertFalse(client._codex_resume_tasks)
        self.assertFalse(client._zen_resume_tasks)

    async def test_codex_timeout_and_close_join_real_rpc_and_waiters_never_dispatch(self):
        for mode in ("timeout", "close"):
            client = self.client()
            client._request_timeout_s = 0.01
            sent = asyncio.Event()
            calls = []

            async def send(payload, **kwargs):
                calls.append(payload)
                sent.set()

            with (
                patch.object(client, "_ensure_ready", AsyncMock()),
                patch.object(client, "_ensure_connected", AsyncMock()),
                patch.object(client, "_send_json", send),
            ):
                first = asyncio.create_task(client.resume_thread(thread_id="thread"))
                await sent.wait()
                if mode == "close":
                    waiting = asyncio.create_task(
                        client.resume_thread_for_recovery(thread_id="thread")
                    )
                    await asyncio.sleep(0)
                    await client.close()
                    with self.assertRaises(Exception):
                        await waiting
                with self.assertRaises(Exception):
                    await first
                self.assertEqual(len(calls), 1)
                self.assertFalse(client._codex_resume_tasks)
                self.assertFalse(client._pending_futures)
            await client.close()

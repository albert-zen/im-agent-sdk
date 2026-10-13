from __future__ import annotations

import base64
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from imagent.applications import ZenApplicationAdapter
from imagent.applications.adapters.appserver.client.handoff import (
    AppServerDispatchPosition,
    AppServerResponse,
)
from imagent.applications.adapters.zen_queue import (
    _native_input_capabilities,
    _queued_admission_receipt,
    _queued_input_fingerprint,
    _queued_receipt_from_items,
)
from imagent.applications.contract import (
    AgentInput,
    ApplicationInputOutcomeUnknown,
    InputContinuationPreference,
    ProjectRef,
    QueuedInputDispatch,
    QueuedInputReceipt,
    QueuedInputState,
    ThreadRef,
    TurnRef,
)
from imagent.interaction.media import AttachmentContent, LocalPath
from imagent.interaction.messages import TextContent
from imagent.interaction.operations import ContractViolation
from tests.applications.adapters.test_zen import _ZenInputClient


class ZenQueueMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thread = ThreadRef(ProjectRef("zen", "workspace"), "thread")
        self.queued = {
            "id": "queued",
            "threadId": "thread",
            "type": "user_message_queued",
            "clientId": "client",
            "input": [{"type": "text", "text": "private"}],
        }
        self.admitted = {
            "id": "message",
            "threadId": "thread",
            "type": "user_message",
            "clientId": "client",
            "turnId": "original-turn",
        }
        self.cancelled = {
            "id": "cancelled",
            "threadId": "thread",
            "type": "user_message_queue_cancelled",
            "clientId": "client",
            "queuedItemId": "queued",
        }

    def test_pending_admitted_cancelled_are_derived_from_original_human_items(self) -> None:
        for items, state, turn in (
            ([self.queued], QueuedInputState.QUEUED, None),
            (
                [self.queued, self.admitted],
                QueuedInputState.ADMITTED,
                TurnRef(self.thread, "original-turn"),
            ),
            ([self.queued, self.cancelled], QueuedInputState.CANCELLED, None),
        ):
            result = _queued_receipt_from_items(items, self.thread, "client")
            assert result is not None
            self.assertIs(result.state, state)
            self.assertEqual(result.queued_item_id, "queued")
            self.assertEqual(result.turn_ref, turn)
            self.assertNotIn("private", repr(result))

    def test_queue_failure_is_not_invented_from_canonical_pending(self) -> None:
        receipt = _queued_receipt_from_items([self.queued], self.thread, "client")
        assert receipt is not None
        self.assertIs(receipt.state, QueuedInputState.QUEUED)
        self.assertFalse(hasattr(receipt, "failure_reason"))

    def test_same_client_id_from_agent_or_host_is_not_human_evidence(self) -> None:
        for kind in (
            "host_message",
            "host_message_queued",
            "thread_message",
            "thread_message_queued",
        ):
            foreign = {**self.queued, "type": kind}
            self.assertIsNone(_queued_receipt_from_items([foreign], self.thread, "client"))
        self.assertIsNone(_queued_receipt_from_items([], self.thread, "client"))

    def test_conflicting_scope_mode_lineage_and_terminals_fail_closed(self) -> None:
        for items in (
            [self.admitted],
            [self.cancelled],
            [self.queued, self.admitted, self.cancelled],
            [{**self.queued, "deliveryMode": "batch-next"}],
            [{**self.queued, "threadId": "other-thread"}],
            [self.queued, {**self.cancelled, "queuedItemId": "other-item"}],
            [self.queued, deepcopy(self.queued)],
            [self.queued, None],
            [self.queued, {**self.admitted, "turnId": ""}],
        ):
            with self.subTest(items=items), self.assertRaises(ContractViolation):
                _queued_receipt_from_items(items, self.thread, "client")

    def test_snapshot_limits_fail_instead_of_treating_truncation_as_absence(self) -> None:
        for items in (None, {}, (), [self.queued] * 10_001):
            with self.assertRaises(ContractViolation):
                _queued_receipt_from_items(items, self.thread, "client")

    def test_positive_response_requires_exact_client_item_and_original_turn(self) -> None:
        for state in ("queued", "admitted"):
            admission = {"state": state, "clientId": "client", "queuedItemId": "queued"}
            if state == "admitted":
                admission["turnId"] = "original-turn"
            receipt = _queued_admission_receipt(
                {"admission": admission}, self.thread, "client", "a" * 64
            )
            self.assertEqual(receipt.state.value, state)
        for result in (
            {},
            {"turnId": "current-turn"},
            {"admission": {"state": "queued", "clientId": "other", "queuedItemId": "q"}},
            {"admission": {"state": "admitted", "clientId": "client", "turnId": "t"}},
            {
                "admission": {
                    "state": "queued",
                    "clientId": "client",
                    "queuedItemId": "q",
                    "turnId": "t",
                }
            },
            {"admission": {"state": "cancelled", "clientId": "client", "queuedItemId": "q"}},
        ):
            with self.subTest(result=result), self.assertRaises(ContractViolation):
                _queued_admission_receipt(result, self.thread, "client", "a" * 64)

    def test_old_server_has_no_capability_and_malformed_descriptor_is_rejected(self) -> None:
        self.assertEqual(_native_input_capabilities({"processEpoch": "epoch"}), frozenset())
        for result in (
            {},
            {"processEpoch": "e", "capabilities": "all"},
            {"processEpoch": "e", "capabilities": [True]},
        ):
            with self.assertRaises(ContractViolation):
                _native_input_capabilities(result)


class _QueueClient(_ZenInputClient):
    def __init__(self) -> None:
        super().__init__(active_turn_id="active")
        self.capabilities = ["queued-input-admission-v1", "inline-image-input-v1"]
        self.epoch = 1
        self.native_calls = []
        self.items = []
        self.result = {
            "admission": {"state": "queued", "clientId": "client", "queuedItemId": "item"}
        }

    async def call_with_dispatch_position(self, method, params):
        self.native_calls.append((method, params))
        return AppServerResponse(
            {"processEpoch": "host", "capabilities": self.capabilities},
            AppServerDispatchPosition(self.epoch, 0),
        )

    async def call(self, method, params):
        self.native_calls.append((method, params))
        return {
            "thread": {"id": params["threadId"], "cwd": self.workspace_cwd, "items": self.items}
        }

    async def queue_zen_input(
        self, thread_id, client_message_id, *, input_items, expected_connection_epoch
    ):
        if expected_connection_epoch != self.epoch:
            raise RuntimeError("connection epoch changed")
        self.native_calls.append(
            (
                "zen/turn/send",
                {
                    "threadId": thread_id,
                    "clientUserMessageId": client_message_id,
                    "input": input_items,
                    "mode": "queue",
                },
            )
        )
        return self.result


class ZenQueueAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = _QueueClient()
        self.adapter = ZenApplicationAdapter(
            application_instance_id="zen", client=self.client, workspace_id="workspace", cwd="/repo"
        )
        self.thread = ThreadRef(ProjectRef("zen", "workspace"), "thread")
        self.message = AgentInput("client", (TextContent("queue this"),))

    async def test_missing_capability_rejects_before_dispatch_without_start_or_steer(self) -> None:
        self.client.capabilities = []
        calls = []

        async def fence(plan):
            calls.append(plan)

        with self.assertRaisesRegex(NotImplementedError, "queued-input-admission-v1"):
            await self.adapter.send_input(
                self.thread,
                self.message,
                continuation=InputContinuationPreference.QUEUE,
                before_dispatch=fence,
            )
        self.assertEqual(calls, [])
        self.assertEqual(self.client.started, [])
        self.assertEqual(self.client.steered, [])
        self.assertEqual([call[0] for call in self.client.native_calls], ["zen/initialize"])

    async def test_negotiated_protocol_fixture_returns_admission_without_turn(self) -> None:
        plans = []

        async def fence(plan):
            plans.append(plan)

        receipt = await self.adapter.send_input(
            self.thread,
            self.message,
            continuation=InputContinuationPreference.QUEUE,
            before_dispatch=fence,
        )
        assert isinstance(receipt, QueuedInputReceipt)
        self.assertIs(receipt.state, QueuedInputState.QUEUED)
        self.assertIsNone(receipt.turn_ref)
        self.assertEqual(len(plans), 1)
        self.assertIsInstance(plans[0], QueuedInputDispatch)
        self.assertEqual(len(plans[0].input_fingerprint), 64)
        self.assertEqual(self.client.native_calls[-1][1]["mode"], "queue")
        self.assertEqual(self.client.started + self.client.steered, [])

    async def test_images_require_capability_before_file_access_or_fence(self) -> None:
        self.client.capabilities = ["queued-input-admission-v1"]
        message = AgentInput(
            "client",
            (AttachmentContent("image", "image/png", LocalPath("/missing.png"), size_bytes=4),),
        )
        with (
            patch("imagent.applications.adapters.zen.read_local_attachment") as acquire,
            self.assertRaisesRegex(NotImplementedError, "inline-image-input-v1"),
        ):
            await self.adapter.send_input(
                self.thread, message, continuation=InputContinuationPreference.QUEUE
            )
        acquire.assert_not_called()
        self.assertNotIn("zen/turn/send", [call[0] for call in self.client.native_calls])

    async def test_rooted_bytes_and_text_order_are_bound_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            captured = b"original image bytes"
            path.write_bytes(captured)
            adapter = ZenApplicationAdapter(
                application_instance_id="zen",
                client=self.client,
                workspace_id="workspace",
                cwd="/repo",
                shared_filesystem_root=directory,
            )
            message = AgentInput(
                "client",
                (
                    TextContent("first"),
                    TextContent("second"),
                    AttachmentContent(
                        "image", "image/png", LocalPath(str(path)), size_bytes=len(captured)
                    ),
                    TextContent("last"),
                ),
            )
            plans = []

            async def replace_path(plan):
                plans.append(plan)
                path.write_bytes(b"replaced after fence")

            receipt = await adapter.send_input(
                self.thread,
                message,
                continuation=InputContinuationPreference.QUEUE,
                before_dispatch=replace_path,
            )
            assert isinstance(receipt, QueuedInputReceipt)
            wire = self.client.native_calls[-1][1]["input"]
            self.assertEqual(
                [part["type"] for part in wire], ["text", "text", "image_bytes", "text"]
            )
            self.assertEqual(base64.b64decode(wire[2]["dataBase64"]), captured)
            self.assertEqual(plans[0].input_fingerprint, _queued_input_fingerprint(wire))
            self.assertEqual(receipt.input_fingerprint, plans[0].input_fingerprint)
            self.assertEqual(self.client.started + self.client.steered, [])

    async def test_scope_and_capability_epoch_change_do_not_dispatch(self) -> None:
        async def reset(plan):
            self.client.epoch += 1

        with self.assertRaisesRegex(RuntimeError, "epoch changed"):
            await self.adapter.send_input(
                self.thread,
                self.message,
                continuation=InputContinuationPreference.QUEUE,
                before_dispatch=reset,
            )
        self.assertNotIn("zen/turn/send", [call[0] for call in self.client.native_calls])
        self.client.workspace_cwd = "/another-workspace"
        with self.assertRaises(ValueError):
            await self.adapter.send_input(
                self.thread, self.message, continuation=InputContinuationPreference.QUEUE
            )
        self.assertNotIn("zen/turn/send", [call[0] for call in self.client.native_calls])

    async def test_lost_or_empty_admission_is_unknown_and_not_fallback_start(self) -> None:
        self.client.result = {}
        with self.assertRaises(ApplicationInputOutcomeUnknown):
            await self.adapter.send_input(
                self.thread, self.message, continuation=InputContinuationPreference.QUEUE
            )
        self.assertEqual([call[0] for call in self.client.native_calls].count("zen/turn/send"), 1)
        self.assertEqual(self.client.started + self.client.steered, [])

    async def test_malformed_client_acknowledgements_are_typed_unknown(self) -> None:
        from unittest.mock import AsyncMock

        for result in (None, [], "wrong", 42, {}):
            request = AsyncMock(return_value=result)
            with patch.object(self.client, "queue_zen_input", new=request):
                with self.subTest(result=result), self.assertRaises(ApplicationInputOutcomeUnknown):
                    await self.adapter.send_input(
                        self.thread, self.message, continuation=InputContinuationPreference.QUEUE
                    )
            self.assertEqual(request.await_count, 1)
            self.assertEqual(self.client.started + self.client.steered, [])

    async def test_recovery_reads_only_and_checks_native_workspace(self) -> None:
        self.client.items = [
            {
                "id": "item",
                "threadId": "thread",
                "type": "user_message_queued",
                "clientId": "client",
                "input": [{"type": "text", "text": "queue this"}],
            },
            {
                "id": "user",
                "threadId": "thread",
                "type": "user_message",
                "clientId": "client",
                "turnId": "original",
            },
        ]
        receipt = await self.adapter.read_queued_input_receipt(self.thread, "client")
        assert receipt is not None
        self.assertEqual(receipt.turn_ref, TurnRef(self.thread, "original"))
        self.assertEqual([call[0] for call in self.client.native_calls], ["zen/thread/read"])
        self.client.workspace_cwd = "/foreign"
        with self.assertRaises(ValueError):
            await self.adapter.read_queued_input_receipt(self.thread, "client")


class ZenQueueClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_wire_malformed_success_is_unknown_without_resend(self) -> None:
        from tests.applications.adapters.appserver.test_transport_lifecycle import (
            _client,
            _ScriptedProcess,
        )

        valid = {"admission": {"state": "queued", "clientId": "client", "queuedItemId": "q"}}
        for result in (None, [], "wrong", 42, {}, valid):
            with self.subTest(result=result):
                process = _ScriptedProcess(
                    {
                        "initialize": [{"result": {}}],
                        "zen/initialize": [
                            {
                                "result": {
                                    "processEpoch": "epoch",
                                    "capabilities": ["queued-input-admission-v1"],
                                }
                            }
                        ],
                        "thread/read": [{"result": {"thread": {"id": "thread", "cwd": "/repo"}}}],
                        "zen/turn/send": [{"result": result}],
                    }
                )
                client = _client(process)
                adapter = ZenApplicationAdapter(
                    application_instance_id="zen",
                    client=client,
                    workspace_id="workspace",
                    cwd="/repo",
                )
                thread = ThreadRef(ProjectRef("zen", "workspace"), "thread")
                try:
                    if result is valid:
                        receipt = await adapter.send_input(
                            thread,
                            AgentInput("client", (TextContent("x"),)),
                            continuation=InputContinuationPreference.QUEUE,
                        )
                        self.assertEqual(receipt.queued_item_id, "q")
                    else:
                        with self.assertRaises(ApplicationInputOutcomeUnknown):
                            await adapter.send_input(
                                thread,
                                AgentInput("client", (TextContent("x"),)),
                                continuation=InputContinuationPreference.QUEUE,
                            )
                finally:
                    await client.close()
                self.assertEqual(
                    sum(message.get("method") == "zen/turn/send" for message in process.sent), 1
                )

    async def test_queue_dispatch_never_retries_native_error_even_when_overloaded(self) -> None:
        from unittest.mock import AsyncMock, patch

        from imagent.applications.adapters.appserver.client import AppServerClient

        client = object.__new__(AppServerClient)
        client.connection_epoch = 7
        response = {"id": 1, "error": {"code": -32000, "message": "overloaded"}}
        with (
            patch.object(client, "_ensure_ready", new=AsyncMock()),
            patch.object(
                client,
                "_request_once_without_initialize",
                new=AsyncMock(return_value=(response, AppServerDispatchPosition(7, 0))),
            ) as request,
        ):
            with self.assertRaises(ApplicationInputOutcomeUnknown):
                await client.queue_zen_input(
                    "thread",
                    "client",
                    input_items=[{"type": "text", "text": "x"}],
                    expected_connection_epoch=7,
                )
            self.assertEqual(request.await_count, 1)
            self.assertTrue(request.call_args.kwargs["outcome_unknown_on_dispatch"])

    async def test_capability_epoch_is_fenced_before_native_mutation(self) -> None:
        from unittest.mock import AsyncMock, patch

        from imagent.applications.adapters.appserver.client import AppServerClient, AppServerError

        client = object.__new__(AppServerClient)
        client.connection_epoch = 8
        with (
            patch.object(client, "_ensure_ready", new=AsyncMock()),
            patch.object(
                client,
                "_request_once_without_initialize",
                new=AsyncMock(),
            ) as request,
        ):
            with self.assertRaises(AppServerError):
                await client.queue_zen_input(
                    "thread",
                    "client",
                    input_items=[{"type": "text", "text": "x"}],
                    expected_connection_epoch=7,
                )
            request.assert_not_awaited()


class ZenQueueFingerprintTests(unittest.TestCase):
    def test_wire_bytes_and_canonical_attachment_have_the_same_domain_digest(self) -> None:
        import base64
        import hashlib

        from imagent.applications.adapters.zen_queue import _queued_input_fingerprint

        content = b"captured bytes"
        inline = {
            "type": "image_bytes",
            "mediaType": "image/png",
            "dataBase64": base64.b64encode(content).decode(),
        }
        canonical = {
            "type": "image",
            "attachment": {
                "type": "attachment",
                "mediaType": "image/png",
                "sha256": hashlib.sha256(content).hexdigest(),
                "byteLength": len(content),
                "width": 1,
                "height": 1,
            },
        }
        prefix = [{"type": "text", "text": "before"}]
        self.assertEqual(
            _queued_input_fingerprint(prefix + [inline]),
            _queued_input_fingerprint(prefix + [canonical]),
        )
        for change in (
            {"sha256": "a" * 64},
            {"mediaType": "image/jpeg"},
            {"byteLength": len(content) + 1},
        ):
            altered = {"type": "image", "attachment": {**canonical["attachment"], **change}}
            self.assertNotEqual(
                _queued_input_fingerprint(prefix + [canonical]),
                _queued_input_fingerprint(prefix + [altered]),
            )

    def test_text_boundaries_order_and_exact_utf8_are_not_collapsed(self) -> None:
        from imagent.applications.adapters.zen_queue import _queued_input_fingerprint

        original = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        candidates = [
            [{"type": "text", "text": "ab"}],
            [{"type": "text", "text": "a\nb"}],
            list(reversed(original)),
            [{"type": "text", "text": "a "}, {"type": "text", "text": "b"}],
        ]
        for candidate in candidates:
            self.assertNotEqual(
                _queued_input_fingerprint(original), _queued_input_fingerprint(candidate)
            )

    def test_canonical_edits_do_not_rewrite_original_admission_fingerprint(self) -> None:
        from imagent.applications.adapters.zen_queue import _queued_input_fingerprint

        thread = ThreadRef(ProjectRef("zen", "workspace"), "thread")
        queued = {
            "id": "queue",
            "threadId": "thread",
            "clientId": "client",
            "type": "user_message_queued",
            "input": [{"type": "text", "text": "original"}],
        }
        edited = {
            "id": "edit",
            "threadId": "thread",
            "clientId": "client",
            "type": "user_message_queue_edited",
            "queuedItemId": "queue",
            "revision": 1,
            "input": [{"type": "text", "text": "changed"}],
        }
        admitted = {
            "id": "human",
            "threadId": "thread",
            "clientId": "client",
            "type": "user_message",
            "turnId": "original-turn",
            "text": "changed",
        }
        receipt = _queued_receipt_from_items([queued, edited, admitted], thread, "client")
        assert receipt is not None
        self.assertEqual(receipt.input_fingerprint, _queued_input_fingerprint(queued["input"]))
        self.assertNotEqual(receipt.input_fingerprint, _queued_input_fingerprint(edited["input"]))

    def test_image_wire_encoding_and_capacity_are_checked_before_digest(self) -> None:
        from imagent.applications.adapters.zen_queue import _queued_input_fingerprint

        for parts in (
            [{"type": "image_bytes", "mediaType": "image/png", "dataBase64": "!invalid"}],
            [{"type": "text", "text": "x"}] * 65,
            [
                {
                    "type": "image",
                    "attachment": {
                        "type": "attachment",
                        "mediaType": "image/png",
                        "sha256": "a" * 64,
                        "byteLength": True,
                    },
                }
            ],
        ):
            with self.assertRaises(ContractViolation):
                _queued_input_fingerprint(parts)

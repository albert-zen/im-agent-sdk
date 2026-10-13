"""Native-only queue history coexists with exact SDK receipts across restart."""

from __future__ import annotations

import os
import unittest
from copy import deepcopy

from imagent.applications import codex_app_server_client
from imagent.applications.adapters.appserver.transport import AppServerError
from imagent.applications.contract import QueuedInputState
from imagent.gateway import GatewayExtensions, QueuedInputStatusKind
from imagent.gateway.outcomes import Succeeded
from tests.integration.test_queued_gateway_native import _native, _Presenter


@unittest.skipUnless(os.environ.get("IMAGENT_ZEN_ROOT"), "requires exact external Zen source")
class QueuedNativeCoexistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_batch_and_canonical_images_preserve_sdk_restart_controls_and_reply(self):
        async with _native(self) as h:
            await h.start()
            await h.active_pair()
            await h.emit("cancel-me")
            before = await h.snapshot()
            own = next(
                item
                for item in before["items"]
                if item["type"] == "user_message_queued"
                and item["input"] == [{"type": "text", "text": "second"}]
            )
            original = await h.adapter.read_queued_input_receipt(h.thread, own["clientId"])
            assert original is not None
            native = codex_app_server_client(
                endpoint=h.endpoint, auth_token="synthetic-gateway-token"
            )
            try:
                batch = await native.call(
                    "zen/turn/send",
                    {
                        "threadId": h.thread.thread_id,
                        "clientUserMessageId": "native-batch",
                        "mode": "batch-next",
                        "input": [{"type": "text", "text": "other native surface"}],
                    },
                )
                imported = await native.call(
                    "zen/turn/send",
                    {
                        "threadId": h.thread.thread_id,
                        "clientUserMessageId": "native-image-import",
                        "mode": "queue",
                        "input": [
                            {
                                "type": "image_bytes",
                                "mediaType": "image/png",
                                "dataBase64": (
                                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
                                    "42YAAAAASUVORK5CYII="
                                ),
                            }
                        ],
                    },
                )
                imported_item = next(
                    item
                    for item in (await h.snapshot())["items"]
                    if item["id"] == imported["admission"]["queuedItemId"]
                )
                image = imported_item["input"][0]
                self.assertEqual(image["type"], "image")
                self.assertEqual(image["attachment"]["type"], "attachment")
                # Canonical-only native envelopes keep their native limits;
                # 17 actual Host-imported refs exceed the SDK admission budget.
                images = await native.call(
                    "zen/turn/send",
                    {
                        "threadId": h.thread.thread_id,
                        "clientUserMessageId": "native-canonical-images",
                        "mode": "queue",
                        "input": [deepcopy(image) for _ in range(17)],
                    },
                )
                for result in (batch, imported, images):
                    self.assertEqual(result["admission"]["state"], "queued")

                # A native same-client mode collision is still rejected, not
                # turned into another SDK receipt or an extra queue append.
                with self.assertRaises(AppServerError) as conflict:
                    await native.call(
                        "zen/turn/send",
                        {
                            "threadId": h.thread.thread_id,
                            "clientUserMessageId": own["clientId"],
                            "mode": "batch-next",
                            "input": own["input"],
                        },
                    )
                assert isinstance(conflict.exception.data, dict)
                self.assertEqual(conflict.exception.data["zenCode"], "idempotency_conflict")
                await h.gateway.stop()
                await native.call(
                    "zen/thread/queue/edit",
                    {
                        "threadId": h.thread.thread_id,
                        "clientId": own["clientId"],
                        "queuedItemId": own["id"],
                        "expectedRevision": 0,
                        "editId": "edit-owned-while-offline",
                        "input": [{"type": "text", "text": "native edited execution text"}],
                    },
                )
                cancelled_batch = await native.call(
                    "zen/thread/queue/cancel",
                    {
                        "threadId": h.thread.thread_id,
                        "items": [
                            {
                                "clientId": "native-batch",
                                "queuedItemId": batch["admission"]["queuedItemId"],
                            }
                        ],
                    },
                )
                self.assertEqual(cancelled_batch["results"][0]["status"], "cancelled")
                presenter = _Presenter()
                await h.start(extensions=GatewayExtensions(queued_input_presenter=presenter))
                self.assertIs((await h.status("second")).state, QueuedInputStatusKind.PENDING)
                self.assertEqual(
                    await h.adapter.read_queued_input_receipt(h.thread, own["clientId"]), original
                )
                cancelled = await h.actions.cancel_queued_input("cancel-me", action_id="cancel")
                self.assertIsInstance(cancelled, Succeeded)
                self.assertIs((await h.status("cancel-me")).state, QueuedInputStatusKind.CANCELLED)

                # Foreign lifecycle events wake reconciliation without becoming
                # SDK statuses, correlations or user-facing queue notices.
                for result in (imported, images):
                    admission = result["admission"]
                    await native.call(
                        "zen/thread/queue/cancel",
                        {
                            "threadId": h.thread.thread_id,
                            "items": [
                                {
                                    "clientId": admission["clientId"],
                                    "queuedItemId": admission["queuedItemId"],
                                }
                            ],
                        },
                    )
                await h.command({"action": "release", "key": "first"})
                await h.wait_answers(2)
                self.assertEqual([message.reply_to for message in h.answers()], ["first", "second"])
                admitted = await h.adapter.read_queued_input_receipt(h.thread, own["clientId"])
                assert admitted is not None
                self.assertIs(admitted.state, QueuedInputState.ADMITTED)
                self.assertEqual(admitted.queued_item_id, original.queued_item_id)
                self.assertEqual(admitted.input_fingerprint, original.input_fingerprint)
                self.assertTrue(
                    all(
                        status.message_id in {"first", "second", "cancel-me"}
                        for status, _ in presenter.calls
                    )
                )
                after = await h.snapshot()
                self.assertEqual(
                    sum(item["type"] == "user_message_queued" for item in after["items"]), 6
                )
                self.assertEqual(sum(item["type"] == "user_message" for item in after["items"]), 2)
            finally:
                await native.close()

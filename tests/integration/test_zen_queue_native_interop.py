"""Opt-in native capability-negative and canonical recovery evidence.

This does not claim that Gateway ordinary-input queue persistence is implemented.
Queue truth is seeded through native human input and recovered through the typed
SDK adapter. The fixture uses synthetic credentials/model/channel and fresh SQLite.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from imagent import Gateway, ProjectionPolicy, SQLiteGatewayStore
from imagent.applications import ZenApplicationAdapter, codex_app_server_client
from imagent.applications.contract import (
    AgentInput,
    InputContinuationPreference,
    ProjectRef,
    QueuedInputReceipt,
    QueuedInputState,
    ThreadRef,
    TurnRef,
)
from imagent.gateway.outcomes import Succeeded
from imagent.interaction.media import AttachmentContent, LocalPath
from imagent.interaction.messages import ConversationRef, InboundMessage, TextContent
from imagent.testing import FakeChannelAdapter


@unittest.skipUnless(os.environ.get("IMAGENT_ZEN_ROOT"), "requires exact external Zen source")
class ZenQueueNativeInteropTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_capability_gate_and_fresh_adapter_receipt_recovery(self) -> None:
        zen = Path(os.environ["IMAGENT_ZEN_ROOT"]).resolve()
        with tempfile.TemporaryDirectory(prefix="sdk-queue-native-") as directory:
            process = await asyncio.create_subprocess_exec(
                "node",
                "--max-old-space-size=256",
                "--import",
                str(zen / "node_modules/tsx/dist/loader.mjs"),
                str(Path(__file__).with_name("zen_native_server.mjs")),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdin and process.stdout and process.stderr
            stdin, stdout, stderr = process.stdin, process.stdout, process.stderr

            async def command(payload):
                stdin.write((json.dumps(payload) + "\n").encode())
                await stdin.drain()
                raw = await asyncio.wait_for(stdout.readline(), 15)
                if not raw:
                    self.fail((await stderr.read()).decode())
                value = json.loads(raw)
                self.assertNotIn("error", value, value)
                return value

            gateway = None
            fresh_client = None
            try:
                endpoint = await command({"cwd": directory, "token": "synthetic-queue-token"})
                client = codex_app_server_client(
                    endpoint=endpoint["url"], auth_token="synthetic-queue-token"
                )
                adapter = ZenApplicationAdapter(
                    application_instance_id="zen",
                    client=client,
                    workspace_id="workspace",
                    cwd=directory,
                    shared_filesystem_root=directory,
                )
                channel = FakeChannelAdapter("im")
                conversation = ConversationRef("im", "private")
                gateway = Gateway(
                    gateway_id="native-queue",
                    channels=[channel],
                    applications=[adapter],
                    store=SQLiteGatewayStore(Path(directory) / "bridge.sqlite3"),
                    projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
                )
                await gateway.start()
                created = await gateway.actions(conversation, actor="owner").create_and_bind_thread(
                    ProjectRef("zen", "workspace"), action_id="create"
                )
                assert isinstance(created, Succeeded) and isinstance(created.value.ref, ThreadRef)
                thread = created.value.ref
                await command({"action": "hold", "key": "active"})
                await channel.emit_message(
                    InboundMessage(
                        message_id="original",
                        conversation_ref=conversation,
                        sender="owner",
                        content=(TextContent("original input"),),
                        created_at=datetime.now(UTC),
                    )
                )
                await command({"action": "arrived", "key": "active"})
                original = await command({"action": "snapshot", "threadId": thread.thread_id})
                original_turn = next(
                    item["turnId"] for item in original["items"] if item["type"] == "user_message"
                )
                features = await client.call("zen/initialize")
                message = AgentInput("queue-client", (TextContent("queued input"),))
                supports_queue = "queued-input-admission-v1" in features.get("capabilities", [])
                image = Path(directory) / "image.png"
                if supports_queue:
                    self.assertIn("inline-image-input-v1", features["capabilities"])
                    image.write_bytes(
                        base64.b64decode(
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
                            "42YAAAAASUVORK5CYII="
                        )
                    )
                    message = AgentInput(
                        "queue-client",
                        (
                            TextContent("queued"),
                            TextContent(" input"),
                            AttachmentContent(
                                "image",
                                "image/png",
                                LocalPath(str(image)),
                                size_bytes=image.stat().st_size,
                            ),
                            TextContent("after image"),
                        ),
                    )
                fences = []

                async def fence(plan):
                    fences.append(plan)

                if not supports_queue:
                    with self.assertRaisesRegex(NotImplementedError, "queued-input-admission-v1"):
                        await adapter.send_input(
                            thread,
                            message,
                            continuation=InputContinuationPreference.QUEUE,
                            before_dispatch=fence,
                        )
                    self.assertEqual(fences, [])
                    self.assertEqual(
                        (await command({"action": "snapshot", "threadId": thread.thread_id}))[
                            "items"
                        ],
                        original["items"],
                    )
                    # Native fixture seed is distinct from supported Gateway queue ingress.
                    await client.call(
                        "zen/turn/send",
                        {
                            "threadId": thread.thread_id,
                            "mode": "queue",
                            "clientUserMessageId": "queue-client",
                            "input": [{"type": "text", "text": "queued input"}],
                        },
                    )
                else:
                    receipt = await asyncio.wait_for(
                        adapter.send_input(
                            thread,
                            message,
                            continuation=InputContinuationPreference.QUEUE,
                            before_dispatch=fence,
                        ),
                        2,
                    )
                    self.assertIsInstance(receipt, QueuedInputReceipt)
                    self.assertEqual(len(fences), 1)
                pending = await adapter.read_queued_input_receipt(thread, "queue-client")
                assert pending is not None
                self.assertIs(pending.state, QueuedInputState.QUEUED)
                self.assertIsNone(pending.turn_ref)
                if supports_queue:
                    self.assertEqual(pending.input_fingerprint, fences[0].input_fingerprint)
                    self.assertEqual(
                        await asyncio.wait_for(
                            adapter.send_input(
                                thread,
                                message,
                                continuation=InputContinuationPreference.QUEUE,
                            ),
                            2,
                        ),
                        pending,
                    )
                    raw = await command({"action": "snapshot", "threadId": thread.thread_id})
                    queued = next(
                        item for item in raw["items"] if item["id"] == pending.queued_item_id
                    )
                    self.assertEqual(
                        [part["type"] for part in queued["input"]],
                        ["text", "text", "image", "text"],
                    )
                    self.assertNotIn("dataBase64", json.dumps(queued))
                    self.assertNotIn(str(image), json.dumps(queued))
                fresh_client = codex_app_server_client(
                    endpoint=endpoint["url"], auth_token="synthetic-queue-token"
                )
                fresh = ZenApplicationAdapter(
                    application_instance_id="zen",
                    client=fresh_client,
                    workspace_id="workspace",
                    cwd=directory,
                    shared_filesystem_root=directory,
                )
                self.assertEqual(
                    await fresh.read_queued_input_receipt(thread, "queue-client"), pending
                )
                before = await command({"action": "snapshot", "threadId": thread.thread_id})
                self.assertEqual(
                    [item["turnId"] for item in before["items"] if item["type"] == "user_message"],
                    [original_turn],
                )
                await command({"action": "release", "key": "active"})
                async with asyncio.timeout(15):
                    while True:
                        admitted = await fresh.read_queued_input_receipt(thread, "queue-client")
                        assert admitted is not None
                        if admitted.state is QueuedInputState.ADMITTED:
                            break
                        await asyncio.sleep(0.01)
                self.assertEqual(admitted.queued_item_id, pending.queued_item_id)
                assert isinstance(admitted.turn_ref, TurnRef)
                self.assertNotEqual(admitted.turn_ref.turn_id, original_turn)
                cancel = await fresh_client.call(
                    "zen/thread/queue/cancel",
                    {
                        "threadId": thread.thread_id,
                        "items": [
                            {"queuedItemId": pending.queued_item_id, "clientId": "queue-client"}
                        ],
                    },
                )
                self.assertEqual(cancel["results"][0]["status"], "already_started")
                self.assertEqual(
                    await fresh.read_queued_input_receipt(thread, "queue-client"), admitted
                )
                after = await command({"action": "snapshot", "threadId": thread.thread_id})
                self.assertEqual(
                    sum(
                        item["type"] == "user_message_queued"
                        and item.get("clientId") == "queue-client"
                        for item in after["items"]
                    ),
                    1,
                )
                self.assertEqual(
                    sum(
                        item["type"] == "user_message" and item.get("clientId") == "queue-client"
                        for item in after["items"]
                    ),
                    1,
                )
            finally:
                if fresh_client is not None:
                    await fresh_client.close()
                if gateway is not None:
                    await gateway.stop()
                stdin.write(b'{"action":"close"}\n')
                await stdin.drain()
                stdin.close()
                await stdin.wait_closed()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                if process.returncode:
                    self.fail((await stderr.read()).decode())

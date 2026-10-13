"""Opt-in Application lifecycle evidence using the real authenticated Zen source."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from imagent.applications import ZenApplicationAdapter, codex_app_server_client
from imagent.applications.contract import (
    AgentInput,
    InputContinuationPreference,
    ProjectRef,
    QueuedInputState,
    ThreadRef,
)
from imagent.applications.queued_input import QueuedInputNoticeKind
from imagent.interaction.messages import TextContent


@unittest.skipUnless(os.environ.get("IMAGENT_ZEN_ROOT"), "requires exact external Zen source")
class ZenQueueNativeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_observe_edit_cancel_and_admission(self):
        zen = Path(os.environ["IMAGENT_ZEN_ROOT"]).resolve()
        fixture = Path(__file__).resolve().parents[2] / "integration/zen_native_server.mjs"
        with tempfile.TemporaryDirectory(prefix="sdk-queue-lifecycle-") as directory:
            process = await asyncio.create_subprocess_exec(
                "node",
                "--max-old-space-size=256",
                "--import",
                str(zen / "node_modules/tsx/dist/loader.mjs"),
                str(fixture),
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
                result = json.loads(raw)
                self.assertNotIn("error", result)
                return result

            adapter = None
            try:
                endpoint = await command({"cwd": directory, "token": "synthetic-lifecycle-token"})
                client = codex_app_server_client(
                    endpoint=endpoint["url"], auth_token="synthetic-lifecycle-token"
                )
                adapter = ZenApplicationAdapter(
                    application_instance_id="zen",
                    client=client,
                    workspace_id="workspace",
                    cwd=directory,
                )
                await adapter.start()
                created = await client.start_thread(cwd=directory)
                thread = ThreadRef(ProjectRef("zen", "workspace"), created["thread"]["id"])
                await command({"action": "hold", "key": "active"})
                active = await adapter.send_input(
                    thread,
                    AgentInput("active", (TextContent("hold"),)),
                    continuation=InputContinuationPreference.START_NEW_TURN,
                )
                await command({"action": "arrived", "key": "active"})
                sub = await adapter.subscribe_queued_inputs(thread)
                cancelled = await adapter.send_input(
                    thread,
                    AgentInput("cancel", (TextContent("original"),)),
                    continuation=InputContinuationPreference.QUEUE,
                )
                wake = await asyncio.wait_for(anext(sub), 5)
                self.assertIs(wake.kind, QueuedInputNoticeKind.CHANGED)
                self.assertEqual(wake.queued_item_id, cancelled.queued_item_id)
                await client.call(
                    "zen/thread/queue/edit",
                    {
                        "threadId": thread.thread_id,
                        "queuedItemId": cancelled.queued_item_id,
                        "clientId": "cancel",
                        "expectedRevision": 0,
                        "editId": "edit-once",
                        "input": [{"type": "text", "text": "edited"}],
                    },
                )
                edit_wake = await asyncio.wait_for(anext(sub), 5)
                self.assertEqual(edit_wake.queued_item_id, cancelled.queued_item_id)
                edited = await adapter.read_queued_input_receipt(thread, "cancel")
                self.assertEqual(edited, cancelled)
                terminal = await adapter.cancel_queued_input_receipt(cancelled)
                self.assertIs(terminal.state, QueuedInputState.CANCELLED)
                self.assertEqual(terminal.input_fingerprint, cancelled.input_fingerprint)
                cancel_wake = await asyncio.wait_for(anext(sub), 5)
                self.assertEqual(cancel_wake.queued_item_id, cancelled.queued_item_id)
                queued = await adapter.send_input(
                    thread,
                    AgentInput("admit", (TextContent("next"),)),
                    continuation=InputContinuationPreference.QUEUE,
                )
                await asyncio.wait_for(anext(sub), 5)
                await command({"action": "release", "key": "active"})
                async with asyncio.timeout(15):
                    while True:
                        wake = await anext(sub)
                        if wake.client_message_id == "admit":
                            admitted = await adapter.read_queued_input_receipt(thread, "admit")
                            if admitted is not None and admitted.state is QueuedInputState.ADMITTED:
                                break
                self.assertEqual(admitted.queued_item_id, queued.queued_item_id)
                self.assertIsNotNone(admitted.turn_ref)
                self.assertNotEqual(admitted.turn_ref, active.turn_ref)
                self.assertEqual(await adapter.cancel_queued_input_receipt(queued), admitted)
                epoch = client.connection_epoch
                await sub.aclose()
                self.assertEqual(client.connection_epoch, epoch)
                self.assertEqual(
                    await adapter.read_queued_input_receipts(thread, ("cancel", "absent", "admit")),
                    (terminal, None, admitted),
                )
            finally:
                if adapter is not None:
                    await adapter.stop()
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

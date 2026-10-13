"""Opt-in production SDK + Zen source interoperability, with isolated synthetic data.

IMAGENT_ZEN_ROOT must point to an independently verified Zen checkout with its
normal tsx dependency installed. No provider account, live channel, or user DB is used.
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

from imagent import Gateway, GatewayExtensions, ProjectionPolicy, SQLiteGatewayStore
from imagent.applications import ZenApplicationAdapter, codex_app_server_client
from imagent.applications.contract import ApplicationInputRejected, ProjectRef, ThreadRef
from imagent.gateway.input import InboundFailurePhase
from imagent.gateway.outcomes import Succeeded
from imagent.interaction.media import AttachmentContent, LocalPath
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
)
from imagent.testing import FakeChannelAdapter


class _Presenter:
    async def present_failure(self, phase: InboundFailurePhase, **identity) -> OutboundMessage:
        return OutboundMessage(
            conversation_ref=identity["conversation_ref"],
            delivery_id=identity["delivery_id"],
            reply_to=identity["reply_to_message_id"],
            content=(TextContent(phase.value),),
            created_at=datetime.now(UTC),
        )


@unittest.skipUnless(os.environ.get("IMAGENT_ZEN_ROOT"), "requires an exact external Zen checkout")
class ZenNativeInteropTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_gateway_real_zen_steering_images_rejections_and_restart(self) -> None:
        zen_root = Path(os.environ["IMAGENT_ZEN_ROOT"]).resolve()
        with tempfile.TemporaryDirectory(prefix="sdk-zen-native-") as directory:
            root = Path(directory)
            process = await asyncio.create_subprocess_exec(
                "node",
                "--import",
                str(zen_root / "node_modules/tsx/dist/loader.mjs"),
                str(Path(__file__).with_name("zen_native_server.mjs")),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdin is not None and process.stdout is not None

            stdin, stdout = process.stdin, process.stdout

            async def command(payload):
                stdin.write((json.dumps(payload) + "\n").encode())
                await stdin.drain()
                line = await asyncio.wait_for(stdout.readline(), 15)
                if not line:
                    assert process.stderr is not None
                    self.fail((await process.stderr.read()).decode())
                result = json.loads(line)
                self.assertNotIn("error", result, result)
                return result

            gateway = None
            try:
                endpoint = await command({"cwd": directory, "token": "synthetic-interop-token"})
                client = codex_app_server_client(
                    endpoint=endpoint["url"],
                    auth_token="synthetic-interop-token",
                )
                application = ZenApplicationAdapter(
                    application_instance_id="zen",
                    client=client,
                    workspace_id="workspace",
                    cwd=directory,
                    shared_filesystem_root=directory,
                )
                channel = FakeChannelAdapter("im")
                conversation = ConversationRef("im", "private")
                database = root / "bridge.sqlite3"
                gateway = Gateway(
                    gateway_id="native-interop",
                    channels=[channel],
                    applications=[application],
                    store=SQLiteGatewayStore(database),
                    projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
                    extensions=GatewayExtensions(inbound_failure_presenter=_Presenter()),
                )
                await gateway.start()
                created = await gateway.actions(conversation, actor="owner").create_and_bind_thread(
                    ProjectRef("zen", "workspace"),
                    action_id="create-bind",
                )
                self.assertIsInstance(created, Succeeded, created)
                assert isinstance(created, Succeeded)
                thread = created.value.ref
                assert isinstance(thread, ThreadRef)

                async def emit(message_id, *content):
                    await channel.emit_message(
                        InboundMessage(
                            message_id=message_id,
                            conversation_ref=conversation,
                            sender="owner",
                            content=content,
                            created_at=datetime.now(UTC),
                        )
                    )

                await command({"action": "hold", "key": "first"})
                await emit("original", TextContent("original input"))
                await command({"action": "arrived", "key": "first"})
                before = await command({"action": "snapshot", "threadId": thread.thread_id})
                original_turn = next(
                    item["turnId"] for item in before["items"] if item["type"] == "turn_started"
                )
                await emit("guidance", TextContent("active guidance"))
                image = root / "image.png"
                image.write_bytes(
                    base64.b64decode(
                        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
                        "42YAAAAASUVORK5CYII="
                    )
                )
                await emit(
                    "image",
                    AttachmentContent(
                        attachment_id="image",
                        source=LocalPath(str(image)),
                        media_type="image/png",
                        size_bytes=image.stat().st_size,
                    ),
                )
                active = await command({"action": "snapshot", "threadId": thread.thread_id})
                messages = [item for item in active["items"] if item["type"] == "user_message"]
                self.assertEqual(len(messages), 3, active)
                self.assertEqual({item["turnId"] for item in messages}, {original_turn})
                self.assertEqual(sum(item["type"] == "turn_started" for item in active["items"]), 1)
                with self.assertRaises(ApplicationInputRejected) as busy:
                    await client.start_turn(thread.thread_id, "must not append while busy")
                self.assertEqual(busy.exception.reason, "thread_busy")
                unchanged = await command({"action": "snapshot", "threadId": thread.thread_id})
                self.assertEqual(unchanged["items"], active["items"])
                with self.assertRaises(ApplicationInputRejected) as stale:
                    await client.steer_turn(thread.thread_id, "not-the-active-turn", "stale input")
                self.assertEqual(stale.exception.reason, "turn_ended")
                self.assertEqual(
                    (await command({"action": "snapshot", "threadId": thread.thread_id}))["items"],
                    active["items"],
                )
                await command({"action": "release", "key": "first"})
                async with asyncio.timeout(15):
                    while not any(message.reply_to == "original" for message in channel.sent):
                        await asyncio.sleep(0.01)
                self.assertTrue(all(message.reply_to != "guidance" for message in channel.sent))
                snapshot = await command({"action": "snapshot", "threadId": thread.thread_id})
                self.assertTrue(snapshot["modelSawImage"], snapshot)
                with self.assertRaises(ApplicationInputRejected) as ended:
                    await client.steer_turn(thread.thread_id, original_turn, "must not start")
                self.assertEqual(ended.exception.reason, "turn_ended")
                self.assertEqual(
                    (await command({"action": "snapshot", "threadId": thread.thread_id}))["items"],
                    snapshot["items"],
                )
                await command({"action": "hold", "key": "external"})
                await client.start_turn(thread.thread_id, "external native input")
                await command({"action": "arrived", "key": "external"})
                external = await command({"action": "snapshot", "threadId": thread.thread_id})
                await emit("uncorrelated", TextContent("must not append to foreign Turn"))
                self.assertEqual(
                    (await command({"action": "snapshot", "threadId": thread.thread_id}))["items"],
                    external["items"],
                )
                self.assertTrue(
                    any(
                        message.reply_to == "uncorrelated"
                        and message.content == (TextContent("pre_acceptance"),)
                        for message in channel.sent
                    )
                )
                await command({"action": "release", "key": "external"})
                async with asyncio.timeout(15):
                    while True:
                        finished = await command(
                            {"action": "snapshot", "threadId": thread.thread_id}
                        )
                        if sum(item["type"] == "turn_completed" for item in finished["items"]) == 2:
                            break
                        await asyncio.sleep(0.01)
                await gateway.stop()
                gateway = None
                restarted_client = codex_app_server_client(
                    endpoint=endpoint["url"],
                    auth_token="synthetic-interop-token",
                )
                restarted_channel = FakeChannelAdapter("im")
                gateway = Gateway(
                    gateway_id="native-interop",
                    channels=[restarted_channel],
                    applications=[
                        ZenApplicationAdapter(
                            application_instance_id="zen",
                            client=restarted_client,
                            workspace_id="workspace",
                            cwd=directory,
                            shared_filesystem_root=directory,
                        )
                    ],
                    store=SQLiteGatewayStore(database),
                    projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
                )
                await gateway.start()
                binding = await gateway.actions(conversation, actor="owner").get_binding()
                assert binding is not None
                self.assertEqual(binding.thread_ref, thread)
                await restarted_channel.emit_message(
                    InboundMessage(
                        message_id="after-restart",
                        conversation_ref=conversation,
                        sender="owner",
                        content=(TextContent("after restart"),),
                        created_at=datetime.now(UTC),
                    )
                )
                async with asyncio.timeout(15):
                    while not any(
                        message.reply_to == "after-restart" for message in restarted_channel.sent
                    ):
                        await asyncio.sleep(0.01)
                self.assertFalse(
                    any(message.reply_to == "original" for message in restarted_channel.sent)
                )

            finally:
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
                if process.returncode != 0:
                    assert process.stderr is not None
                    self.fail((await process.stderr.read()).decode())

"""Public Gateway queue ingress and controls against authenticated real Core."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from imagent import Gateway, ProjectionPolicy, SQLiteGatewayStore
from imagent.applications import ZenApplicationAdapter, codex_app_server_client
from imagent.applications.contract import InputContinuationPreference, ProjectRef, ThreadRef
from imagent.gateway import GatewayExtensions, QueuedInputNoticeReason, QueuedInputStatusKind
from imagent.gateway.outcomes import Failed, Succeeded
from imagent.gateway.persistence.effects import ActionErrorCode
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
)
from imagent.interaction.operations import OperationErrorCode
from imagent.testing import FakeChannelAdapter


class _Presenter:
    def __init__(self):
        self.calls = []

    async def present_queued_input(
        self, status, *, reason, conversation_ref, delivery_id, reply_to_message_id
    ):
        self.calls.append((status, reason))
        return OutboundMessage(
            delivery_id=delivery_id,
            conversation_ref=conversation_ref,
            content=(
                TextContent(f"queue:{reason.value}:{status.state.value}:{status.message_id}"),
            ),
            reply_to=reply_to_message_id,
            created_at=datetime.now(UTC),
        )


class _Harness:
    def __init__(self, case, process, directory):
        self.case = case
        self.process = process
        self.directory = directory
        self.gateway: Gateway
        self.endpoint = ""
        self.conversation = ConversationRef("im", "private")

    async def command(self, payload):
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()
        raw = await asyncio.wait_for(self.process.stdout.readline(), 15)
        if not raw:
            self.case.fail((await self.process.stderr.read()).decode())
        value = json.loads(raw)
        self.case.assertNotIn("error", value, value)
        return value

    async def start(
        self,
        *,
        mode=InputContinuationPreference.QUEUE,
        extensions=GatewayExtensions(),
        capacity=4096,
        profile=None,
    ):
        client = codex_app_server_client(
            endpoint=self.endpoint, auth_token="synthetic-gateway-token"
        )
        self.adapter = ZenApplicationAdapter(
            application_instance_id="zen",
            client=client,
            workspace_id="workspace",
            cwd=self.directory,
            shared_filesystem_root=self.directory,
            thread_start_options=profile,
        )
        self.channel = FakeChannelAdapter("im")
        self.gateway = Gateway(
            gateway_id="native-queue",
            channels=[self.channel],
            applications=[self.adapter],
            store=SQLiteGatewayStore(
                Path(self.directory) / "bridge.sqlite3", max_effect_receipts=capacity
            ),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            input_continuation=mode,
            extensions=extensions,
        )
        await self.gateway.start()
        self.actions = self.gateway.actions(self.conversation, actor="owner")

    async def create(self):
        created = await self.actions.create_and_bind_thread(
            ProjectRef("zen", "workspace"), action_id="create"
        )
        assert isinstance(created, Succeeded) and isinstance(created.value.ref, ThreadRef)
        self.thread = created.value.ref

    async def emit(self, identity, *, text=None, actor="owner"):
        await asyncio.wait_for(
            self.channel.emit_message(
                InboundMessage(
                    message_id=identity,
                    conversation_ref=self.conversation,
                    sender=actor,
                    content=(TextContent(text or identity),),
                    created_at=datetime.now(UTC),
                )
            ),
            2,
        )

    async def active_pair(self):
        await self.create()
        await self.command({"action": "hold", "key": "first"})
        await self.emit("first")
        await self.command({"action": "arrived", "key": "first"})
        await self.emit("second")
        self.case.assertIs((await self.status("second")).state, QueuedInputStatusKind.PENDING)

    async def status(self, identity):
        result = await self.actions.read_queued_input(identity)
        assert isinstance(result, Succeeded), result
        return result.value

    def answers(self):
        return [
            message
            for message in self.channel.sent
            if any(
                isinstance(part, TextContent) and part.text == "native SDK answer"
                for part in message.content
            )
        ]

    async def wait_answers(self, count):
        async with asyncio.timeout(15):
            while len(self.answers()) < count:
                await asyncio.sleep(0.01)

    async def snapshot(self):
        return await self.command({"action": "snapshot", "threadId": self.thread.thread_id})


@asynccontextmanager
async def _native(case):
    zen = Path(os.environ["IMAGENT_ZEN_ROOT"]).resolve()
    with tempfile.TemporaryDirectory(prefix="sdk-gateway-queue-") as directory:
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
        assert (
            process.stdin is not None and process.stdout is not None and process.stderr is not None
        )
        harness = _Harness(case, process, directory)
        try:
            endpoint = await harness.command({"cwd": directory, "token": "synthetic-gateway-token"})
            harness.endpoint = endpoint["url"]
            yield harness
        finally:
            if hasattr(harness, "gateway"):
                await harness.gateway.stop()
            process.stdin.write(b'{"action":"close"}\n')
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
            if process.returncode:
                case.fail((await process.stderr.read()).decode())


@unittest.skipUnless(os.environ.get("IMAGENT_ZEN_ROOT"), "requires exact external Zen source")
class QueuedGatewayNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_active_cancel_actor_and_original_reply(self):
        async with _native(self) as h:
            await h.start()
            await h.active_pair()
            before = await h.snapshot()
            self.assertEqual(sum(item["type"] == "user_message" for item in before["items"]), 1)
            await h.emit("cancel-me")
            denied = await h.gateway.actions(h.conversation, actor="other").cancel_queued_input(
                "cancel-me", action_id="denied"
            )
            assert isinstance(denied, Failed)
            self.assertIs(
                denied.error.operation_error_code, OperationErrorCode.UNAUTHORIZED_DESTINATION
            )
            foreign = await h.gateway.actions(
                ConversationRef("im", "other"), actor="owner"
            ).read_queued_input("cancel-me")
            assert isinstance(foreign, Failed)
            self.assertIs(foreign.error.operation_error_code, OperationErrorCode.NOT_FOUND)
            cancelled = await h.actions.cancel_queued_input("cancel-me", action_id="cancel")
            assert isinstance(cancelled, Succeeded)
            self.assertIs(cancelled.value.state, QueuedInputStatusKind.CANCELLED)
            self.assertEqual(
                await h.actions.cancel_queued_input("cancel-me", action_id="cancel"), cancelled
            )
            await h.command({"action": "release", "key": "first"})
            await h.wait_answers(2)
            self.assertEqual([message.reply_to for message in h.answers()], ["first", "second"])
            self.assertIs((await h.status("second")).state, QueuedInputStatusKind.ADMITTED)
            rejected = await h.actions.cancel_queued_input("second", action_id="too-late")
            assert isinstance(rejected, Failed)
            self.assertIs(rejected.error.code, ActionErrorCode.QUEUE_ALREADY_ADMITTED)
            await h.emit("second", text="edited replay must not dispatch")
            self.assertEqual(
                sum(
                    item["type"] == "user_message_queued" for item in (await h.snapshot())["items"]
                ),
                3,
            )

    async def test_capacity_rejects_cancel_without_native_effect_read_still_works(self):
        async with _native(self) as h:
            await h.start(capacity=3)
            await h.active_pair()
            result = await h.actions.cancel_queued_input("second", action_id="cancel-at-capacity")
            assert isinstance(result, Failed)
            self.assertIs(result.error.code, ActionErrorCode.CAPACITY_EXHAUSTED)
            self.assertIs((await h.status("second")).state, QueuedInputStatusKind.PENDING)
            self.assertFalse(
                any(
                    item["type"] == "user_message_queue_cancelled"
                    for item in (await h.snapshot())["items"]
                )
            )
            await h.command({"action": "release", "key": "first"})
            await h.wait_answers(2)

    async def test_fresh_gateway_sqlite_restart_and_changed_mode_preserve_original_queue(self):
        async with _native(self) as h:
            await h.start()
            await h.active_pair()
            await h.gateway.stop()
            await h.start(mode=InputContinuationPreference.PREFER_ACTIVE_TURN)
            self.assertIs((await h.status("second")).state, QueuedInputStatusKind.PENDING)
            await h.emit("second", text="changed text with changed default")
            self.assertEqual(
                sum(
                    item["type"] == "user_message_queued" for item in (await h.snapshot())["items"]
                ),
                2,
            )
            await h.command({"action": "release", "key": "first"})
            await h.wait_answers(2)
            self.assertEqual([message.reply_to for message in h.answers()], ["first", "second"])
            await h.emit("ordinary-after-restart")
            await h.wait_answers(3)
            self.assertEqual(h.answers()[-1].reply_to, "ordinary-after-restart")

    async def test_binding_clear_suppresses_notices_but_original_controls_remain(self):
        async with _native(self) as h:
            presenter = _Presenter()
            await h.start(extensions=GatewayExtensions(queued_input_presenter=presenter))
            await h.active_pair()
            self.assertTrue(
                any(
                    status.message_id == "second" and reason is QueuedInputNoticeReason.ADMISSION
                    for status, reason in presenter.calls
                )
            )
            cleared = await h.actions.clear_thread(action_id="paw-clear")
            self.assertIsInstance(cleared, Succeeded)
            before = len(h.channel.sent)
            cancelled = await h.actions.cancel_queued_input("second", action_id="cancel-after-paw")
            assert isinstance(cancelled, Succeeded)
            await h.command({"action": "release", "key": "first"})
            async with asyncio.timeout(10):
                while not any(
                    item["type"] == "agent_message" for item in (await h.snapshot())["items"]
                ):
                    await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            self.assertEqual(len(h.channel.sent), before)
            self.assertIs((await h.status("second")).state, QueuedInputStatusKind.CANCELLED)

    async def test_approval_uses_original_queue_turn_and_scoped_response(self):
        from imagent.applications.requests import ApprovalRequest, ApprovalResponse
        from imagent.interaction.controllers.request_presentation import MarkdownRequestPresenter

        class RecordingRequestPresenter(MarkdownRequestPresenter):
            def __init__(self):
                self.requests = []

            def present_request(self, request, **kwargs):
                self.requests.append(request)
                return super().present_request(request, **kwargs)

        async with _native(self) as h:
            presenter = RecordingRequestPresenter()
            await h.start(
                extensions=GatewayExtensions(request_presenter=presenter),
                profile={"approvalPolicy": "on-request"},
            )
            await h.create()
            await h.command({"action": "approval-next"})
            await h.emit("approval-origin")
            async with asyncio.timeout(10):
                while not presenter.requests or not h.channel.sent:
                    await asyncio.sleep(0.01)
            request = presenter.requests[0]
            assert isinstance(request, ApprovalRequest)
            self.assertEqual(h.channel.sent[0].reply_to, "approval-origin")
            self.assertIs((await h.status("approval-origin")).state, QueuedInputStatusKind.ADMITTED)
            decline = next(
                choice.choice_id
                for choice in request.choices
                if "decline" in choice.choice_id.lower() or "reject" in choice.choice_id.lower()
            )
            result = await h.actions.respond_request(
                request.request_ref, ApprovalResponse(decline), action_id="decline-safe-command"
            )
            self.assertIsInstance(result, Succeeded)
            await h.wait_answers(1)
            self.assertEqual(h.answers()[0].reply_to, "approval-origin")
            self.assertTrue(
                any(
                    item["type"] == "tool_result" and item.get("executionStatus") == "declined"
                    for item in (await h.snapshot())["items"]
                )
            )

    async def test_queue_failed_is_live_pending_and_restart_needs_explicit_resume(self):
        async with _native(self) as h:
            presenter = _Presenter()
            await h.start(extensions=GatewayExtensions(queued_input_presenter=presenter))
            await h.create()
            await h.command({"action": "fail-next-admission"})
            await h.emit("failed-attempt")
            async with asyncio.timeout(10):
                while not any(
                    reason is QueuedInputNoticeReason.ATTEMPT_FAILED
                    for _, reason in presenter.calls
                ):
                    await asyncio.sleep(0.01)
            self.assertIs((await h.status("failed-attempt")).state, QueuedInputStatusKind.PENDING)
            before = await h.snapshot()
            self.assertFalse(any(item["type"] == "user_message" for item in before["items"]))
            await h.gateway.stop()
            fresh_presenter = _Presenter()
            await h.start(extensions=GatewayExtensions(queued_input_presenter=fresh_presenter))
            await h.emit("failed-attempt", text="redelivery cannot resume")
            self.assertIs((await h.status("failed-attempt")).state, QueuedInputStatusKind.PENDING)
            self.assertEqual((await h.snapshot())["items"], before["items"])
            self.assertFalse(
                any(
                    reason is QueuedInputNoticeReason.ATTEMPT_FAILED
                    for _, reason in fresh_presenter.calls
                )
            )
            await h.command({"action": "resume-queue", "threadId": h.thread.thread_id})
            try:
                await h.wait_answers(1)
            except TimeoutError:
                self.fail(
                    f"snapshot={await h.snapshot()} sent={h.channel.sent} "
                    f"diagnostics={h.gateway.diagnostics()}"
                )
            self.assertEqual(h.answers()[0].reply_to, "failed-attempt")

    async def test_gateway_queued_image_edit_and_restart_keep_original_fingerprint(self):
        import base64

        from imagent.interaction.media import AttachmentContent, LocalPath

        async with _native(self) as h:
            await h.start()
            await h.active_pair()
            image = Path(h.directory) / "image.png"
            image.write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                )
            )
            await h.channel.emit_message(
                InboundMessage(
                    message_id="image-origin",
                    conversation_ref=h.conversation,
                    sender="owner",
                    content=(
                        TextContent("before"),
                        AttachmentContent(
                            "image",
                            "image/png",
                            LocalPath(str(image)),
                            size_bytes=image.stat().st_size,
                        ),
                        TextContent("after"),
                    ),
                    created_at=datetime.now(UTC),
                )
            )
            queued = next(
                item
                for item in (await h.snapshot())["items"]
                if item["type"] == "user_message_queued"
                and any(part.get("type") == "image" for part in item["input"])
            )
            editor = codex_app_server_client(
                endpoint=h.endpoint, auth_token="synthetic-gateway-token"
            )
            try:
                await editor.call(
                    "zen/thread/queue/edit",
                    {
                        "threadId": h.thread.thread_id,
                        "queuedItemId": queued["id"],
                        "clientId": queued["clientId"],
                        "expectedRevision": 0,
                        "editId": "external-editor",
                        "input": [
                            *queued["input"],
                            {"type": "text", "text": "edited execution only"},
                        ],
                    },
                )
            finally:
                await editor.close()
            self.assertIs((await h.status("image-origin")).state, QueuedInputStatusKind.PENDING)
            await h.gateway.stop()
            await h.start()
            self.assertIs((await h.status("image-origin")).state, QueuedInputStatusKind.PENDING)
            await h.command({"action": "release", "key": "first"})
            await h.wait_answers(3)
            self.assertEqual(
                [message.reply_to for message in h.answers()], ["first", "second", "image-origin"]
            )
            self.assertTrue((await h.snapshot())["modelSawImage"])
            self.assertIs((await h.status("image-origin")).state, QueuedInputStatusKind.ADMITTED)

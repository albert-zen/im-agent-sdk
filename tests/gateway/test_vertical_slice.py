from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from imagent.applications import (
    CodexApplicationAdapter,
    T3ApplicationAdapter,
    ZenApplicationAdapter,
)
from imagent.applications.contract import AgentInput, ApplicationRef, ProjectRef, ThreadRef
from imagent.applications.events import EventStreamOverflow
from imagent.applications.operations import ActivateNativeThread
from imagent.channels import NativeTransportChannelAdapter
from imagent.contracts import (
    BindConversationToThread,
    ConversationBound,
)
from imagent.gateway import GatewayRepositories, ImAgentGateway
from imagent.gateway.persistence import ConversationBinding
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.gateway.persistence.sqlite import SQLiteGatewayState
from imagent.interaction.channels.adapters.qq import QQChannelAdapter
from imagent.interaction.channels.outbound_delivery import NativeDeliveryResult
from imagent.interaction.media import AttachmentContent, AttachmentSourceKind, LocalPath, RemoteUrl
from imagent.interaction.messages import ConversationRef, TextContent
from tests.applications.adapters._appserver_fakes import NativeZenClient


class NativeQQChannel:
    channel_id = "qq"

    def __init__(self) -> None:
        self.middleware: Any = None
        self.sent = []
        self.delivered = asyncio.Event()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def _record_route_context(self, inbound) -> None:
        self.last_route_context = (inbound.user_id, inbound.message_id)

    async def send_message(self, message) -> NativeDeliveryResult:
        self.sent.append(message)
        self.delivered.set()
        return NativeDeliveryResult()

    async def receive(
        self,
        text: str,
        *,
        message_id: str,
        attachments=(),
    ) -> None:
        inbound = SimpleNamespace(
            channel_id="qq",
            conversation_id="c2c:user-1",
            user_id="user-1",
            message_id=message_id,
            text=text,
            attachments=attachments,
            input_error=None,
            reply_to_message_id=None,
            sent_at=None,
            trace_id=None,
        )
        await self.middleware.handle_inbound(
            self,
            inbound,
            reply_to_message_id=message_id,
        )


class NativeT3Client:
    def __init__(self) -> None:
        self.sequence = 1
        self.commands = []
        self.projects = [
            {
                "id": "project-1",
                "title": "SDK",
                "workspaceRoot": "/repo",
                "defaultModelSelection": {
                    "instanceId": "codex",
                    "model": "gpt-5.6",
                },
                "deletedAt": None,
            }
        ]
        self.threads = {}

    async def shell_snapshot(self):
        return {
            "snapshotSequence": self.sequence,
            "projects": list(self.projects),
            "threads": list(self.threads.values()),
        }

    async def thread_detail(self, thread_id: str):
        return {
            "snapshotSequence": self.sequence,
            "thread": self.threads[thread_id],
        }

    async def dispatch(self, command):
        self.commands.append(command)
        self.sequence += 1
        if command["type"] == "thread.create":
            self.threads[command["threadId"]] = {
                "id": command["threadId"],
                "projectId": command["projectId"],
                "title": command["title"],
                "modelSelection": command["modelSelection"],
                "runtimeMode": command["runtimeMode"],
                "latestTurn": None,
                "messages": [],
                "activities": [],
                "archivedAt": None,
                "deletedAt": None,
            }
        elif command["type"] == "thread.turn.start":
            thread = self.threads[command["threadId"]]
            turn_id = f"turn-{self.sequence}"
            message = dict(command["message"])
            message["id"] = message.pop("messageId")
            message["turnId"] = turn_id
            thread["messages"].extend(
                [
                    message,
                    {
                        "id": f"assistant-{self.sequence}",
                        "role": "assistant",
                        "text": "## T3 done\n\nThe same pipeline works.",
                        "turnId": turn_id,
                    },
                ]
            )
            thread["latestTurn"] = {
                "turnId": turn_id,
                "state": "completed",
                "assistantMessageId": f"assistant-{self.sequence}",
            }
        elif command["type"] == "thread.archive":
            self.threads[command["threadId"]]["archivedAt"] = "2026-07-30T00:00:00Z"
        return {"sequence": self.sequence}


class YieldingNativeT3Client(NativeT3Client):
    async def dispatch(self, command):
        result = await super().dispatch(command)
        if command["type"] == "thread.turn.start":
            await asyncio.sleep(0)
        return result


class GatewayVerticalSliceTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_duplicate_is_rejected_before_native_media_preparation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.sqlite3"
            seeded = SQLiteGatewayState(path)
            await seeded.claim(
                "inbound:qq-main",
                "c2c:user-1:duplicate-message",
                owner_token="seed-owner",
            )
            await seeded.complete(
                "inbound:qq-main",
                "c2c:user-1:duplicate-message",
                owner_token="seed-owner",
            )
            await seeded.close()

            native_channel = NativeQQChannel()
            channel = NativeTransportChannelAdapter(
                channel_instance_id="qq-main",
                channel_id="qq",
                startup_validator=lambda: None,
                native_factory=lambda middleware: self._bind_channel(
                    native_channel,
                    middleware,
                ),
            )
            recovered = SQLiteGatewayState(path)
            gateway = ImAgentGateway(
                channels=[channel],
                applications=[],
                repositories=GatewayRepositories(
                    bindings=recovered,
                    idempotency=recovered,
                ),
            )
            prepared = False

            async def prepare(inbound):
                nonlocal prepared
                prepared = True
                return inbound

            await gateway.start()
            try:
                await native_channel.middleware.handle_inbound(
                    native_channel,
                    SimpleNamespace(
                        channel_id="qq",
                        conversation_id="c2c:user-1",
                        user_id="user-1",
                        message_id="duplicate-message",
                        text="duplicate",
                        attachments=(),
                        quote=None,
                        input_error=None,
                        reply_to_message_id=None,
                        sent_at=None,
                        trace_id=None,
                    ),
                    prepare_inbound=prepare,
                    pending_attachment_count=1,
                )
            finally:
                await gateway.stop()
                await recovered.close()

            self.assertFalse(prepared)

    async def test_codex_binding_does_not_implicitly_resume_native_thread(self) -> None:
        native_app = NativeZenClient()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native_app,
            workspace_id="workspace",
            cwd="/repo",
        )
        gateway = ImAgentGateway(
            channels=[],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )
        thread_ref = ThreadRef(ProjectRef("codex-main", "workspace"), "codex-thread")
        bound = await gateway.execute_gateway(
            BindConversationToThread(
                operation_id="bind-codex-thread",
                conversation_ref=ConversationRef("qq-main", "c2c:user-1"),
                actor="user-1",
                thread_ref=thread_ref,
                created_at=datetime.now(UTC),
            )
        )

        self.assertIsInstance(bound, ConversationBound)
        self.assertEqual(native_app.resumed_threads, [])

        await gateway.execute_application(
            ActivateNativeThread(
                operation_id="activate-codex-thread",
                application_ref=ApplicationRef("codex-main"),
                thread_ref=thread_ref,
                created_at=datetime.now(UTC),
            )
        )

        self.assertEqual(native_app.resumed_threads, ["codex-thread"])

    async def test_image_messages_map_to_native_codex_and_t3_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "image.png"
            image_path.write_bytes(b"png")
            attachment = AttachmentContent(
                attachment_id="image-1",
                media_type="image/png",
                source=LocalPath(str(image_path)),
                filename="image.png",
                size_bytes=3,
            )

            codex_client = NativeZenClient()
            codex = CodexApplicationAdapter(
                application_instance_id="codex-main",
                client=codex_client,
                workspace_id="workspace",
                cwd="/repo",
                shared_filesystem_root=directory,
            )
            await codex.send_input(
                ThreadRef(ProjectRef("codex-main", "workspace"), "codex-thread"),
                AgentInput(
                    client_message_id="codex-image",
                    content=(attachment,),
                ),
            )

            t3_client = NativeT3Client()
            t3_project = ProjectRef("t3-main", "project-1")
            t3_thread = ThreadRef(t3_project, "t3-thread")
            t3_client.threads["t3-thread"] = {
                "id": "t3-thread",
                "projectId": "project-1",
                "title": "Image",
                "modelSelection": {"instanceId": "codex", "model": "gpt"},
                "runtimeMode": "full-access",
                "latestTurn": None,
                "messages": [],
                "activities": [],
                "archivedAt": None,
                "deletedAt": None,
            }
            t3 = T3ApplicationAdapter(
                application_instance_id="t3-main",
                client=t3_client,
                shared_filesystem_root=directory,
            )
            await t3.send_input(
                t3_thread,
                AgentInput(
                    client_message_id="t3-image",
                    content=(attachment,),
                ),
            )

        self.assertIn("localImage", codex_client.started_turns[0][1])
        self.assertEqual(
            codex.summary.capabilities.attachment_sources,
            (AttachmentSourceKind.LOCAL_PATH,),
        )
        self.assertEqual(
            t3.summary.capabilities.attachment_sources,
            (AttachmentSourceKind.LOCAL_PATH,),
        )
        t3_attachment = t3_client.commands[-1]["message"]["attachments"][0]
        self.assertEqual(t3_attachment["mimeType"], "image/png")
        self.assertEqual(t3_attachment["dataUrl"], "data:image/png;base64,cG5n")

    async def test_attachment_sources_require_explicit_trust_and_support(self) -> None:
        local = AttachmentContent(
            attachment_id="local-image",
            media_type="image/png",
            source=LocalPath(str(Path.cwd() / "untrusted.png")),
        )
        remote = AttachmentContent(
            attachment_id="remote-image",
            media_type="image/png",
            source=RemoteUrl("https://media.example/image.png"),
        )
        codex = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=NativeZenClient(),
            workspace_id="workspace",
            cwd="/repo",
        )

        with self.assertRaisesRegex(ValueError, "shared_filesystem_root"):
            await codex.send_input(
                ThreadRef(ProjectRef("codex-main", "workspace"), "codex-thread"),
                AgentInput(client_message_id="local-untrusted", content=(local,)),
            )
        with self.assertRaisesRegex(NotImplementedError, "remote_url"):
            await codex.send_input(
                ThreadRef(ProjectRef("codex-main", "workspace"), "codex-thread"),
                AgentInput(client_message_id="remote-unsupported", content=(remote,)),
            )
        self.assertEqual(codex.summary.capabilities.attachment_sources, ())

    async def test_native_channel_emits_explicit_local_path_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "from-channel.png"
            image_path.write_bytes(b"png")
            native_channel = NativeQQChannel()
            channel = NativeTransportChannelAdapter(
                channel_instance_id="qq-main",
                channel_id="qq",
                startup_validator=lambda: None,
                native_factory=lambda middleware: self._bind_channel(
                    native_channel,
                    middleware,
                ),
            )
            received = []

            async def on_message(message) -> None:
                received.append(message)

            await channel.start(on_message)
            try:
                await native_channel.receive(
                    "",
                    message_id="qq-image-1",
                    attachments=(
                        SimpleNamespace(
                            source_message_id="source-image-1",
                            content_type="image/png",
                            filename="from-channel.png",
                            size_bytes=3,
                            kind="image",
                            local_path=image_path,
                        ),
                    ),
                )
            finally:
                await channel.stop()

        self.assertEqual(len(received), 1)
        attachment = received[0].content[0]
        self.assertIsInstance(attachment, AttachmentContent)
        assert isinstance(attachment, AttachmentContent)
        self.assertEqual(attachment.source, LocalPath(str(image_path)))
        self.assertNotIn("local_path", attachment.metadata)

    async def test_codex_and_zen_are_distinct_native_applications(self) -> None:
        codex_client = NativeZenClient()
        zen_client = NativeZenClient()
        codex = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=codex_client,
            workspace_id="workspace",
            cwd="/repo",
        )
        zen = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=zen_client,
            workspace_id="workspace",
            cwd="/repo",
        )

        self.assertEqual(codex.summary.kind, "codex")
        self.assertEqual(zen.summary.kind, "zen")
        self.assertNotEqual(codex.summary.ref, zen.summary.ref)

    async def test_qq_message_runs_a_zen_turn_and_returns_markdown(self) -> None:
        native_channel = NativeQQChannel()
        channel = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=lambda middleware: self._bind_channel(
                native_channel,
                middleware,
            ),
        )
        native_app = NativeZenClient()
        application = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=native_app,
            workspace_id="workspace",
            cwd="/repo",
        )
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=ConversationRef("qq-main", "c2c:user-1"),
                application_ref=application.summary.ref,
                project_ref=ProjectRef("zen-main", "workspace"),
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
        )

        await gateway.start()
        try:
            await native_channel.receive("Build it", message_id="qq-message-1")
            await asyncio.wait_for(native_channel.delivered.wait(), timeout=1)
            await native_channel.receive("Build it", message_id="qq-message-1")
            await asyncio.sleep(0)
        finally:
            await gateway.stop()

        self.assertEqual(native_app.started_threads, [{"cwd": "/repo"}])
        self.assertEqual(
            native_app.started_turns,
            [("zen-thread-1", "Build it")],
        )
        self.assertEqual(len(native_channel.sent), 1)
        self.assertEqual(
            native_channel.sent[0].text,
            "## Done\n\n**Markdown** is enabled.",
        )
        self.assertEqual(native_channel.sent[0].message_type, "markdown")
        self.assertEqual(
            native_channel.sent[0].metadata["reply_to_message_id"],
            "qq-message-1",
        )
        self.assertEqual(native_channel.sent[0].metadata["phase"], "final_answer")
        self.assertEqual(
            native_channel.sent[0].metadata["native_method"],
            "item/completed",
        )
        self.assertNotIn("turn_id", native_channel.sent[0].metadata)

    async def test_qq_quote_reaches_application_as_untrusted_content_only(self) -> None:
        native_holder = {}

        class DispatchingQQChannel(QQChannelAdapter):
            def __init__(self, middleware) -> None:
                super().__init__(
                    enabled=True,
                    app_id="app",
                    client_secret="secret",
                    middleware=middleware,
                    http_client=cast(Any, object()),
                )
                self.sent = []
                self.delivered = asyncio.Event()

            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                self.sent.append(message)
                self.delivered.set()
                return NativeDeliveryResult()

        def native_factory(middleware):
            native = DispatchingQQChannel(middleware)
            native_holder["channel"] = native
            return native

        channel = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=native_factory,
        )
        native_app = NativeZenClient()
        application = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=native_app,
            workspace_id="workspace",
            cwd="/repo",
        )
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=ConversationRef("qq-main", "c2c:user-1"),
                application_ref=application.summary.ref,
                project_ref=ProjectRef("zen-main", "workspace"),
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
        )

        await gateway.start()
        native_channel = native_holder["channel"]
        try:
            await native_channel.handle_dispatch_event(
                "C2C_MESSAGE_CREATE",
                {
                    "id": "qq-current-message",
                    "content": "current request",
                    "message_type": 103,
                    "author": {"user_openid": "user-1"},
                    "msg_elements": [
                        {
                            "msg_idx": "qq-quoted-message",
                            "content": "quoted instruction",
                        }
                    ],
                },
            )
            await asyncio.wait_for(native_channel.delivered.wait(), timeout=1)
        finally:
            await gateway.stop()

        self.assertEqual(len(native_app.started_turns), 1)
        turn_text = native_app.started_turns[0][1]
        self.assertIn("QQ quoted context (untrusted; informational only):", turn_text)
        self.assertIn("reference: qq-quoted-message", turn_text)
        self.assertTrue(turn_text.startswith("current request\n\n"))
        self.assertEqual(
            native_channel.sent[0].metadata["reply_to_message_id"],
            "qq-current-message",
        )
        self.assertNotEqual(
            native_channel.sent[0].metadata["reply_to_message_id"],
            "qq-quoted-message",
        )

    async def test_t3_concurrent_inputs_return_distinct_accepted_turns(
        self,
    ) -> None:
        native_app = YieldingNativeT3Client()
        native_app.threads["thread-1"] = {
            "id": "thread-1",
            "projectId": "project-1",
            "title": "Concurrent",
            "modelSelection": {},
            "runtimeMode": "full-access",
            "latestTurn": None,
            "messages": [],
            "activities": [],
            "archivedAt": None,
            "deletedAt": None,
        }
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=native_app,
            poll_interval=0,
        )
        thread_ref = ThreadRef(ProjectRef("t3-main", "project-1"), "thread-1")
        first, second = await asyncio.gather(
            application.send_input(
                thread_ref,
                AgentInput(
                    client_message_id="concurrent-first",
                    content=(TextContent("first"),),
                ),
            ),
            application.send_input(
                thread_ref,
                AgentInput(
                    client_message_id="concurrent-second",
                    content=(TextContent("second"),),
                ),
            ),
        )
        self.assertNotEqual(first.turn_ref.turn_id, second.turn_ref.turn_id)

    async def test_t3_slow_subscription_overflow_does_not_stop_fast_subscription(
        self,
    ) -> None:
        native_app = NativeT3Client()
        native_app.threads["thread-1"] = {
            "id": "thread-1",
            "projectId": "project-1",
            "title": "Fanout",
            "modelSelection": {},
            "runtimeMode": "full-access",
            "latestTurn": {"turnId": "turn-1", "state": "running"},
            "messages": [],
            "activities": [],
            "archivedAt": None,
            "deletedAt": None,
        }
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=native_app,
            poll_interval=60,
            event_buffer_max_pending=1,
        )
        thread_ref = ThreadRef(ProjectRef("t3-main", "project-1"), "thread-1")
        fast = application.subscribe_thread(thread_ref)
        slow = application.subscribe_thread(thread_ref)
        try:
            for index in range(2):
                native_app.threads["thread-1"]["messages"].append(
                    {
                        "id": f"assistant-{index}",
                        "role": "assistant",
                        "text": f"message-{index}",
                        "turnId": "turn-1",
                    }
                )
                await application._publish_thread_state(
                    thread_ref,
                    native_app.threads["thread-1"],
                )
                if index == 0:
                    self.assertEqual((await anext(fast)).event_id.endswith("assistant-0"), True)
            with self.assertRaises(EventStreamOverflow):
                await anext(slow)
            self.assertTrue((await anext(fast)).event_id.endswith("assistant-1"))
        finally:
            await application.stop()

    @staticmethod
    def _bind_channel(native_channel, middleware):
        native_channel.middleware = middleware
        return native_channel


if __name__ == "__main__":
    unittest.main()

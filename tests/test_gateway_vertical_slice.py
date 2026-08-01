from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from imagent.applications import (
    CodexApplicationAdapter,
    T3ApplicationAdapter,
    ZenApplicationAdapter,
)
from imagent.bindings import InMemoryBindingRepository
from imagent.channels import NativeTransportChannelAdapter
from imagent.channels.native.models import NativeDeliveryResult
from imagent.contracts import (
    ActivateNativeThread,
    AgentInput,
    ApplicationRef,
    AttachmentContent,
    AttachmentSourceKind,
    BindConversationToThread,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    LocalPath,
    ProjectRef,
    RemoteUrl,
    TextContent,
    ThreadRef,
)
from imagent.controllers import SlashController
from imagent.gateway import ImAgentGateway
from imagent.storage import SQLiteGatewayState


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

    async def send_message(self, message) -> NativeDeliveryResult:
        self.sent.append(message)
        self.delivered.set()
        return NativeDeliveryResult()

    async def receive(self, text: str, *, message_id: str, attachments=()) -> None:
        inbound = SimpleNamespace(
            channel_id="qq",
            conversation_id="c2c:user-1",
            user_id="user-1",
            message_id=message_id,
            text=text,
            attachments=attachments,
            quote=None,
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


class NativeZenClient:
    def __init__(self) -> None:
        self.handlers = []
        self.started_threads = []
        self.started_turns = []
        self.resumed_threads = []
        self.threads = {}

    def add_notification_handler(self, handler) -> None:
        self.handlers.append(handler)

    async def list_threads(self, **_params):
        return {"data": list(self.threads.values())}

    async def list_thread_turns(self, thread_id: str, **_params):
        return {
            "data": [
                {
                    "id": "turn-live",
                    "status": "inProgress",
                    "items": [
                        {
                            "id": "user-live",
                            "type": "userMessage",
                            "text": "Refactor the adapters",
                        },
                        {
                            "id": "progress-1",
                            "type": "agentMessage",
                            "phase": "commentary",
                            "text": "Inspecting the existing adapters.",
                        },
                        {
                            "id": "progress-2",
                            "type": "agentMessage",
                            "phase": "commentary",
                            "text": "Running the focused tests.",
                        },
                    ],
                },
                {
                    "id": "turn-old",
                    "status": "completed",
                    "items": [
                        {
                            "id": "user-old",
                            "type": "userMessage",
                            "text": "Design the SDK",
                        },
                        {
                            "id": "assistant-old",
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": "The SDK design is complete.",
                        },
                    ],
                },
            ],
            "nextCursor": None,
        }

    async def start_thread(self, **params):
        self.started_threads.append(params)
        thread = {
            "id": f"zen-thread-{len(self.started_threads)}",
            "cwd": params["cwd"],
            "preview": "",
            "status": {"type": "idle"},
        }
        self.threads[thread["id"]] = thread
        return {"thread": thread}

    async def read_thread(self, thread_id: str, *, include_turns: bool = False):
        return {
            "thread": {
                "id": thread_id,
                "cwd": "/repo",
                "preview": "Build the SDK",
                "status": {"type": "idle"},
                "turns": [] if include_turns else None,
            }
        }

    async def resume_thread(self, **params):
        self.resumed_threads.append(str(params["threadId"]))
        return await self.read_thread(str(params["threadId"]))

    async def start_turn(
        self,
        thread_id: str,
        text: str | None = None,
        **params,
    ):
        if text is None:
            text = str(params)
        self.started_turns.append((thread_id, text))
        await self._notify(
            {
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": "turn-1",
                    "item": {
                        "id": "assistant-1",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "## Done\n\n**Markdown** is enabled.",
                    },
                },
            }
        )
        await self._notify(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        return {"turn": {"id": "turn-1"}}

    async def interrupt_turn(self, thread_id: str, turn_id: str):
        return {"threadId": thread_id, "turnId": turn_id}

    async def _notify(self, notification) -> None:
        for handler in self.handlers:
            result = handler(notification)
            if inspect.isawaitable(result):
                await result


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
                native_factory=lambda middleware: self._bind_channel(
                    native_channel,
                    middleware,
                ),
            )
            recovered = SQLiteGatewayState(path)
            gateway = ImAgentGateway(
                channels=[channel],
                applications=[],
                bindings=recovered,
                idempotency=recovered,
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
            cwd="/repo",
        )
        gateway = ImAgentGateway(
            channels=[],
            applications=[application],
            bindings=InMemoryBindingRepository(),
        )
        thread_ref = ThreadRef("codex-main", "codex-thread")
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

    async def test_appserver_catchup_and_history_restore_user_context(self) -> None:
        native_channel = NativeQQChannel()
        channel = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            native_factory=lambda middleware: self._bind_channel(
                native_channel,
                middleware,
            ),
        )
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=NativeZenClient(),
            cwd="/repo",
        )
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=ConversationRef(
                    "qq-main",
                    "c2c:user-1",
                ),
                application_ref=ApplicationRef("codex-main"),
                thread_ref=ThreadRef("codex-main", "codex-thread"),
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            bindings=bindings,
            controller=SlashController(),
        )

        await gateway.start()
        try:
            await native_channel.receive("/catchup 2", message_id="catchup-1")
            await native_channel.receive("/history 2", message_id="history-1")
        finally:
            await gateway.stop()

        catchup, history = [message.text for message in native_channel.sent]
        self.assertIn("## Recent Activity", catchup)
        self.assertIn("Inspecting the existing adapters.", catchup)
        self.assertIn("Running the focused tests.", catchup)
        self.assertNotIn("Design the SDK", catchup)
        self.assertIn("## Thread History", history)
        self.assertIn("Design the SDK", history)
        self.assertIn("The SDK design is complete.", history)
        self.assertIn("Refactor the adapters", history)
        self.assertIn("Inspecting the existing adapters.", history)
        self.assertIn("Running the focused tests.", history)

    async def test_t3_catchup_and_history_use_native_turn_grouping(self) -> None:
        native_channel = NativeQQChannel()
        channel = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            native_factory=lambda middleware: self._bind_channel(
                native_channel,
                middleware,
            ),
        )
        native_app = NativeT3Client()
        native_app.threads["t3-thread"] = {
            "id": "t3-thread",
            "projectId": "project-1",
            "title": "SDK",
            "modelSelection": {"instanceId": "codex", "model": "gpt"},
            "runtimeMode": "full-access",
            "latestTurn": {
                "turnId": "turn-live",
                "state": "running",
                "assistantMessageId": "assistant-live",
            },
            "messages": [
                {
                    "id": "user-old",
                    "role": "user",
                    "text": "Create the first adapter",
                    "turnId": "turn-old",
                },
                {
                    "id": "assistant-old",
                    "role": "assistant",
                    "text": "The first adapter works.",
                    "turnId": "turn-old",
                    "streaming": False,
                },
                {
                    "id": "user-live",
                    "role": "user",
                    "text": "Add history support",
                    "turnId": "turn-live",
                },
                {
                    "id": "assistant-live",
                    "role": "assistant",
                    "text": "Inspecting the T3 read model.",
                    "turnId": "turn-live",
                    "streaming": True,
                },
            ],
            "activities": [
                {
                    "id": "activity-live",
                    "kind": "task.progress",
                    "summary": "Mapping messages by turn",
                    "payload": {"detail": "Grouping native messages by turnId."},
                    "turnId": "turn-live",
                }
            ],
            "checkpoints": [{"turnId": "turn-old"}],
            "archivedAt": None,
            "deletedAt": None,
        }
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=native_app,
        )
        bindings = InMemoryBindingRepository()
        project = ProjectRef("t3-main", "project-1")
        await bindings.put(
            ConversationBinding(
                conversation_ref=ConversationRef(
                    "qq-main",
                    "c2c:user-1",
                ),
                application_ref=ApplicationRef("t3-main"),
                project_ref=project,
                thread_ref=ThreadRef("t3-main", "t3-thread", project),
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            bindings=bindings,
            controller=SlashController(),
        )

        await gateway.start()
        try:
            await native_channel.receive("/catchup 2", message_id="catchup-t3")
            await native_channel.receive("/history 2", message_id="history-t3")
        finally:
            await gateway.stop()

        catchup, history = [message.text for message in native_channel.sent]
        self.assertIn("## Recent Activity", catchup)
        self.assertIn("Inspecting the T3 read model.", catchup)
        self.assertIn("Grouping native messages by turnId.", catchup)
        self.assertIn("## Thread History", history)
        self.assertIn("Create the first adapter", history)
        self.assertIn("The first adapter works.", history)
        self.assertIn("Add history support", history)

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
                cwd="/repo",
                shared_filesystem_root=directory,
            )
            await codex.send_input(
                ThreadRef("codex-main", "codex-thread"),
                AgentInput(
                    client_message_id="codex-image",
                    content=(attachment,),
                ),
            )

            t3_client = NativeT3Client()
            t3_project = ProjectRef("t3-main", "project-1")
            t3_thread = ThreadRef("t3-main", "t3-thread", t3_project)
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
            cwd="/repo",
        )

        with self.assertRaisesRegex(ValueError, "shared_filesystem_root"):
            await codex.send_input(
                ThreadRef("codex-main", "codex-thread"),
                AgentInput(client_message_id="local-untrusted", content=(local,)),
            )
        with self.assertRaisesRegex(NotImplementedError, "remote_url"):
            await codex.send_input(
                ThreadRef("codex-main", "codex-thread"),
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
                native_factory=lambda middleware: self._bind_channel(
                    native_channel,
                    middleware,
                ),
            )
            received = []

            async def on_message(message) -> None:
                received.append(message)

            async def on_operation(_operation) -> None:
                return None

            await channel.start(on_message, on_operation)
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
            cwd="/repo",
        )
        zen = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=zen_client,
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
            native_factory=lambda middleware: self._bind_channel(
                native_channel,
                middleware,
            ),
        )
        native_app = NativeZenClient()
        application = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=native_app,
            cwd="/repo",
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            bindings=InMemoryBindingRepository(),
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

    async def test_slash_commands_manage_a_t3_project_and_thread(self) -> None:
        native_channel = NativeQQChannel()
        channel = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            native_factory=lambda middleware: self._bind_channel(
                native_channel,
                middleware,
            ),
        )
        native_app = NativeT3Client()
        application = T3ApplicationAdapter(
            application_instance_id="t3-main",
            client=native_app,
            runtime_mode="full-access",
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            bindings=InMemoryBindingRepository(),
            controller=SlashController(),
        )

        await gateway.start()
        try:
            await native_channel.receive("/projects", message_id="t3-1")
            await native_channel.receive("/use 1", message_id="t3-2")
            await native_channel.receive("/new SDK task", message_id="t3-3")
            await native_channel.receive("Build it", message_id="t3-4")
            async with asyncio.timeout(1):
                while not any(
                    message.text == "## T3 done\n\nThe same pipeline works."
                    for message in native_channel.sent
                ):
                    await asyncio.sleep(0)
        finally:
            await gateway.stop()

        self.assertEqual(
            [command["type"] for command in native_app.commands],
            ["thread.create", "thread.turn.start"],
        )
        self.assertEqual(native_app.commands[0]["runtimeMode"], "full-access")
        rendered = [message.text for message in native_channel.sent]
        self.assertTrue(any("SDK" in text and "project-1" in text for text in rendered))
        self.assertTrue(any("Selected project" in text for text in rendered))
        self.assertTrue(any("Created thread" in text for text in rendered))
        self.assertIn(
            "## T3 done\n\nThe same pipeline works.",
            rendered,
        )
        t3_output = next(
            message
            for message in native_channel.sent
            if message.text == "## T3 done\n\nThe same pipeline works."
        )
        self.assertEqual(
            t3_output.metadata["reply_to_message_id"],
            "t3-4",
        )
        self.assertTrue(all(message.message_type == "markdown" for message in native_channel.sent))

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
        thread_ref = ThreadRef(
            "t3-main",
            "thread-1",
            ProjectRef("t3-main", "project-1"),
        )
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
        self.assertNotEqual(first.turn_id, second.turn_id)

    @staticmethod
    def _bind_channel(native_channel, middleware):
        native_channel.middleware = middleware
        return native_channel


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from imagent.applications import (
    CodexApplicationAdapter,
    T3ApplicationAdapter,
    ZenApplicationAdapter,
)
from imagent.bindings import InMemoryBindingRepository
from imagent.channels import ImcodexChannelAdapter
from imagent.contracts import (
    AgentInput,
    ApplicationRef,
    AttachmentContent,
    ConversationBinding,
    ConversationRef,
    ProjectRef,
    ThreadRef,
)
from imagent.gateway import ImAgentGateway


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

    async def send_message(self, message) -> None:
        self.sent.append(message)
        self.delivered.set()

    async def receive(self, text: str, *, message_id: str) -> None:
        inbound = SimpleNamespace(
            channel_id="qq",
            conversation_id="c2c:user-1",
            user_id="user-1",
            message_id=message_id,
            text=text,
            attachments=(),
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

    def add_notification_handler(self, handler) -> None:
        self.handlers.append(handler)

    async def list_threads(self, **_params):
        return {"data": []}

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
        return {
            "thread": {
                "id": "zen-thread-1",
                "cwd": params["cwd"],
                "preview": "",
                "status": {"type": "idle"},
            }
        }

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


class GatewayVerticalSliceTests(unittest.IsolatedAsyncioTestCase):
    async def test_appserver_catchup_and_history_restore_user_context(self) -> None:
        native_channel = NativeQQChannel()
        channel = ImcodexChannelAdapter(
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

    async def test_t3_catchup_and_history_use_native_turn_grouping(self) -> None:
        native_channel = NativeQQChannel()
        channel = ImcodexChannelAdapter(
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
                filename="image.png",
                size_bytes=3,
                metadata={"local_path": str(image_path)},
            )

            codex_client = NativeZenClient()
            codex = CodexApplicationAdapter(
                application_instance_id="codex-main",
                client=codex_client,
                cwd="/repo",
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
            )
            await t3.send_input(
                t3_thread,
                AgentInput(
                    client_message_id="t3-image",
                    content=(attachment,),
                ),
            )

        self.assertIn("localImage", codex_client.started_turns[0][1])
        t3_attachment = t3_client.commands[-1]["message"]["attachments"][0]
        self.assertEqual(t3_attachment["mimeType"], "image/png")
        self.assertEqual(t3_attachment["dataUrl"], "data:image/png;base64,cG5n")

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
        channel = ImcodexChannelAdapter(
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

    async def test_slash_commands_manage_a_t3_project_and_thread(self) -> None:
        native_channel = NativeQQChannel()
        channel = ImcodexChannelAdapter(
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
        )

        await gateway.start()
        try:
            await native_channel.receive("/projects", message_id="t3-1")
            await native_channel.receive("/use 1", message_id="t3-2")
            await native_channel.receive("/new SDK task", message_id="t3-3")
            await native_channel.receive("Build it", message_id="t3-4")
            await asyncio.wait_for(native_channel.delivered.wait(), timeout=1)
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
        self.assertTrue(all(message.message_type == "markdown" for message in native_channel.sent))

    @staticmethod
    def _bind_channel(native_channel, middleware):
        native_channel.middleware = middleware
        return native_channel


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from imagent.applications import CodexApplicationAdapter, ZenApplicationAdapter
from imagent.contracts import (
    AgentInput,
    AttachmentContent,
    LocalPath,
    TextContent,
    ThreadRef,
)


class _InputClient:
    def __init__(self) -> None:
        self.started: list[dict[str, object]] = []

    def add_notification_handler(self, handler) -> None:
        del handler

    def local_image_paths_epoch(self) -> int:
        return 7

    async def list_threads(self, **params: object) -> dict[str, object]:
        del params
        return {"data": []}

    async def list_thread_turns(
        self,
        thread_id: str,
        **params: object,
    ) -> dict[str, object]:
        del thread_id, params
        return {"data": []}

    async def start_thread(self, **params: object) -> dict[str, object]:
        del params
        return {"thread": {"id": "thread-created"}}

    async def read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool = False,
    ) -> dict[str, object]:
        del thread_id, include_turns
        return {"thread": {"id": "thread-1", "status": {"type": "idle"}}}

    async def resume_thread(self, **params: object) -> dict[str, object]:
        del params
        return {"thread": {"id": "thread-resumed"}}

    async def interrupt_turn(self, thread_id: str, turn_id: str) -> dict[str, object]:
        del thread_id, turn_id
        return {}

    async def start_turn(
        self,
        thread_id: str,
        text: str | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        self.started.append({"thread_id": thread_id, "text": text, **kwargs})
        return {"turn": {"id": "turn-started"}}


class AppServerInputIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_file_is_explicitly_unsupported_without_consumer_encoding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "report.txt")
            path.write_text("report", encoding="utf-8")
            for adapter_type, application_id in (
                (CodexApplicationAdapter, "codex-main"),
                (ZenApplicationAdapter, "zen-main"),
            ):
                with self.subTest(adapter=adapter_type.__name__):
                    client = _InputClient()
                    adapter = adapter_type(
                        application_instance_id=application_id,
                        client=client,
                        cwd=directory,
                        shared_filesystem_root=directory,
                    )
                    with self.assertRaisesRegex(ValueError, "image attachments only"):
                        await adapter.send_input(
                            ThreadRef(application_id, "thread-1"),
                            AgentInput(
                                client_message_id=f"message-{application_id}",
                                content=(
                                    TextContent("Please inspect this file."),
                                    AttachmentContent(
                                        attachment_id="file-1",
                                        media_type="text/plain",
                                        source=LocalPath(str(path)),
                                        filename="report.txt",
                                        size_bytes=6,
                                    ),
                                ),
                            ),
                        )

                    self.assertEqual(client.started, [])


if __name__ == "__main__":
    unittest.main()

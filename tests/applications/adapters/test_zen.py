from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from imagent.applications import ZenApplicationAdapter
from imagent.applications.adapters.zen import ZenApplicationAdapter as ZenApplicationAdapterOwner
from imagent.contracts import (
    AgentInput,
    AttachmentContent,
    CreateThread,
    LocalPath,
    ThreadCreated,
    ThreadRef,
)


class _ZenInputClient:
    def __init__(self, *, local_image_epoch: int | None = 7) -> None:
        self.local_image_epoch = local_image_epoch
        self.created_threads: list[dict[str, object]] = []
        self.started: list[dict[str, object]] = []

    def add_notification_handler(self, handler) -> None:
        del handler

    def local_image_paths_epoch(self) -> int | None:
        return self.local_image_epoch

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
        self.created_threads.append(deepcopy(dict(params)))
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


class ZenApplicationAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_zen_facade_preserves_exact_owner_identity(self) -> None:
        self.assertEqual(
            ZenApplicationAdapterOwner.__module__,
            "imagent.applications.adapters.zen",
        )
        self.assertIs(ZenApplicationAdapter, ZenApplicationAdapterOwner)

    async def test_thread_creation_forwards_an_isolated_deployment_profile(self) -> None:
        class MutatingClient(_ZenInputClient):
            async def start_thread(self, **params: object) -> dict[str, object]:
                self.created_threads.append(deepcopy(dict(params)))
                native_nested = params.get("nativeNested")
                assert isinstance(native_nested, dict)
                native_nested["mode"] = "mutated-by-client"
                return {"thread": {"id": "thread-created"}}

        client = MutatingClient()
        nested = {"mode": "configured"}
        profile: dict[str, object] = {
            "sandbox": "danger-full-access",
            "approval_policy": "on-request",
            "nativeNested": nested,
        }
        adapter = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=client,
            cwd="/repo",
            thread_start_options=profile,
        )
        profile["approval_policy"] = "never"
        nested["mode"] = "mutated-by-caller"

        for index in range(2):
            result = await adapter.execute(
                CreateThread(
                    operation_id=f"create-zen-thread-{index}",
                    application_ref=adapter.summary.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(result, ThreadCreated)

        expected = {
            "cwd": "/repo",
            "sandbox": "danger-full-access",
            "approval_policy": "on-request",
            "nativeNested": {"mode": "configured"},
        }
        self.assertEqual(client.created_threads, [expected, expected])

    async def test_adapter_specific_create_can_select_a_conversation_profile(self) -> None:
        client = _ZenInputClient()
        adapter = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=client,
            cwd="/repo",
            thread_start_options={"approval_policy": "never"},
        )

        thread = await adapter.create_thread_with_options(
            thread_start_options={
                "sandbox": "danger-full-access",
                "approval_policy": "on-request",
            }
        )

        self.assertEqual(thread.ref, ThreadRef("zen-main", "thread-created"))
        self.assertEqual(
            client.created_threads,
            [
                {
                    "cwd": "/repo",
                    "sandbox": "danger-full-access",
                    "approval_policy": "on-request",
                }
            ],
        )

    def test_thread_creation_profile_cannot_override_adapter_cwd(self) -> None:
        with self.assertRaisesRegex(ValueError, "adapter-owned fields: cwd"):
            cast(Any, ZenApplicationAdapter)(
                application_instance_id="zen-main",
                client=_ZenInputClient(),
                cwd="/repo",
                thread_start_options={"cwd": "/other"},
            )

    def test_thread_creation_profile_rejects_normalized_alias_collisions(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate native fields"):
            ZenApplicationAdapter(
                application_instance_id="zen-main",
                client=_ZenInputClient(),
                cwd="/repo",
                thread_start_options={
                    "approval_policy": "never",
                    "approvalPolicy": "on-request",
                },
            )

    async def test_local_image_uses_shared_verified_connection_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "image.png")
            path.write_bytes(b"png")
            client = _ZenInputClient(local_image_epoch=29)
            adapter = ZenApplicationAdapter(
                application_instance_id="zen-main",
                client=client,
                cwd=directory,
                shared_filesystem_root=directory,
            )

            await adapter.send_input(
                ThreadRef("zen-main", "thread-1"),
                AgentInput(
                    client_message_id="message-zen-image",
                    content=(
                        AttachmentContent(
                            attachment_id="image-1",
                            media_type="image/png",
                            source=LocalPath(str(path)),
                            filename="image.png",
                            size_bytes=3,
                        ),
                    ),
                ),
            )

        self.assertEqual(client.started[0]["expected_local_image_epoch"], 29)

    def test_zen_does_not_expose_codex_active_turn_policy(self) -> None:
        with self.assertRaisesRegex(TypeError, "steer_active_turn"):
            cast(Any, ZenApplicationAdapter)(
                application_instance_id="zen-main",
                client=_ZenInputClient(),
                cwd="/repo",
                steer_active_turn=True,
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from imagent.applications import ZenApplicationAdapter
from imagent.applications.adapters.zen import ZenApplicationAdapter as ZenApplicationAdapterOwner
from imagent.applications.contract import (
    AgentInput,
    ApplicationInputDispatch,
    InputContinuationPreference,
    InputDisposition,
    ProjectRef,
    ThreadRef,
    TurnRef,
    TurnReplyCorrelationPolicy,
)
from imagent.applications.events import EventStreamReset
from imagent.applications.operations import CreateThread, ThreadCreated
from imagent.interaction.media import AttachmentContent, AttachmentSourceKind, LocalPath, RemoteUrl
from imagent.interaction.messages import TextContent
from tests.applications.adapters._appserver_fakes import NativeZenClient


class _ZenInputClient:
    def __init__(
        self,
        *,
        active_turn_id: str | None = None,
        local_image_epoch: int | None = 7,
        workspace_cwd: str = "/repo",
    ) -> None:
        self.active_turn_id = active_turn_id
        self.local_image_epoch = local_image_epoch
        self.workspace_cwd = workspace_cwd
        self.read_calls: list[tuple[str, bool]] = []
        self.trace: list[str] = []
        self.created_threads: list[dict[str, object]] = []
        self.started: list[dict[str, object]] = []
        self.steered: list[dict[str, object]] = []

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
        return {"thread": {"id": "thread-created", "cwd": params["cwd"]}}

    async def read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool = False,
    ) -> dict[str, object]:
        self.trace.append("read")
        self.read_calls.append((thread_id, include_turns))
        turns = (
            [{"id": self.active_turn_id, "status": "inProgress"}]
            if self.active_turn_id is not None
            else []
        )
        return {
            "thread": {
                "id": thread_id,
                "cwd": self.workspace_cwd,
                "status": {"type": "active" if turns else "idle"},
                "turns": turns if include_turns else None,
            }
        }

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
        self.trace.append("start")
        self.started.append({"thread_id": thread_id, "text": text, **kwargs})
        return {"turn": {"id": "turn-started"}}

    async def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        text: str | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        self.trace.append("steer")
        self.steered.append({"thread_id": thread_id, "turn_id": turn_id, "text": text, **kwargs})
        return {"turn": {"id": turn_id}}


class ZenApplicationAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_zen_facade_preserves_exact_owner_identity(self) -> None:
        self.assertEqual(
            ZenApplicationAdapterOwner.__module__,
            "imagent.applications.adapters.zen",
        )
        self.assertIs(ZenApplicationAdapter, ZenApplicationAdapterOwner)

    def test_shared_base_contains_no_codex_steer_policy_or_dispatch(self) -> None:
        base_path = (
            Path(__file__).resolve().parents[3]
            / "src/imagent/applications/adapters/appserver/_base.py"
        )
        source = base_path.read_text()
        for forbidden in (
            "steer_active_turn",
            "_steer_active_turn",
            "steer_turn",
            "turn/steer",
            "_read_active_turn_id",
            "PRESERVE_EXISTING",
            "InputDisposition.STEERED",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("before_dispatch", source)
        self.assertIn("InputDisposition.STARTED", source)
        self.assertIn("TurnReplyCorrelationPolicy.CREATE_NEW", source)

    async def test_prefer_active_turn_is_fenced_steer_with_authoritative_discovery(self) -> None:
        client = _ZenInputClient(active_turn_id="turn-active")
        adapter = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        dispatches: list[ApplicationInputDispatch] = []

        async def before_dispatch(dispatch: ApplicationInputDispatch) -> None:
            client.trace.append("fence")
            dispatches.append(dispatch)

        accepted = await adapter.send_input(
            ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1"),
            AgentInput(
                client_message_id="message-zen-start",
                content=(TextContent("begin"),),
            ),
            before_dispatch=before_dispatch,
        )

        self.assertEqual(client.trace, ["read", "fence", "steer"])
        self.assertEqual(client.read_calls, [("thread-1", True)])
        self.assertEqual(len(client.steered), 1)
        self.assertEqual(client.started, [])
        self.assertEqual(len(dispatches), 1)
        self.assertIs(dispatches[0].disposition, InputDisposition.STEERED)
        self.assertIs(
            dispatches[0].correlation_policy,
            TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
        )
        self.assertEqual(
            dispatches[0].expected_turn_ref,
            TurnRef(ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1"), "turn-active"),
        )
        self.assertIs(accepted.disposition, InputDisposition.STEERED)
        self.assertIs(
            accepted.correlation_policy,
            TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
        )

    async def test_fence_failure_prevents_zen_native_start(self) -> None:
        client = _ZenInputClient(active_turn_id="turn-active")
        adapter = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )

        async def reject_dispatch(dispatch: ApplicationInputDispatch) -> None:
            del dispatch
            raise RuntimeError("fence rejected")

        with self.assertRaisesRegex(RuntimeError, "fence rejected"):
            await adapter.send_input(
                ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1"),
                AgentInput(
                    client_message_id="message-zen-rejected",
                    content=(TextContent("begin"),),
                ),
                before_dispatch=reject_dispatch,
            )

        self.assertEqual(client.read_calls, [("thread-1", True)])
        self.assertEqual(client.steered, [])
        self.assertEqual(client.started, [])

    async def test_start_new_and_deployment_opt_out_skip_active_discovery(self) -> None:
        for steer_active_turn, preference in (
            (False, InputContinuationPreference.PREFER_ACTIVE_TURN),
            (True, InputContinuationPreference.START_NEW_TURN),
        ):
            client = _ZenInputClient(active_turn_id="turn-active")
            adapter = ZenApplicationAdapter(
                application_instance_id="zen-main",
                client=client,
                workspace_id="workspace",
                cwd="/repo",
                steer_active_turn=steer_active_turn,
            )
            accepted = await adapter.send_input(
                ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1"),
                AgentInput(client_message_id="explicit-start", content=(TextContent("begin"),)),
                continuation=preference,
            )
            self.assertIs(accepted.disposition, InputDisposition.STARTED)
            self.assertEqual(client.read_calls, [("thread-1", False)])
            self.assertEqual(client.steered, [])

    async def test_native_steer_rejection_never_falls_back_to_start(self) -> None:
        class RejectingClient(_ZenInputClient):
            async def steer_turn(self, *args, **kwargs):
                await super().steer_turn(*args, **kwargs)
                raise RuntimeError("native turn ended")

        client = RejectingClient(active_turn_id="turn-active")
        adapter = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        with self.assertRaisesRegex(RuntimeError, "native turn ended"):
            await adapter.send_input(
                ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1"),
                AgentInput(client_message_id="rejected-steer", content=(TextContent("begin"),)),
            )
        self.assertEqual(len(client.steered), 1)
        self.assertEqual(client.started, [])

    async def test_thread_creation_forwards_an_isolated_deployment_profile(self) -> None:
        class MutatingClient(_ZenInputClient):
            async def start_thread(self, **params: object) -> dict[str, object]:
                self.created_threads.append(deepcopy(dict(params)))
                native_nested = params.get("nativeNested")
                assert isinstance(native_nested, dict)
                native_nested["mode"] = "mutated-by-client"
                return {"thread": {"id": "thread-created", "cwd": params["cwd"]}}

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
            workspace_id="workspace",
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
                    project_ref=ProjectRef(
                        adapter.summary.ref.application_instance_id, "workspace"
                    ),
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
            workspace_id="workspace",
            cwd="/repo",
            thread_start_options={"approval_policy": "never"},
        )

        thread = await adapter.create_thread_with_options(
            thread_start_options={
                "sandbox": "danger-full-access",
                "approval_policy": "on-request",
            }
        )

        self.assertEqual(
            thread.ref, ThreadRef(ProjectRef("zen-main", "workspace"), "thread-created")
        )
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
                workspace_id="workspace",
                cwd="/repo",
                thread_start_options={"cwd": "/other"},
            )

    def test_thread_creation_profile_rejects_normalized_alias_collisions(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate native fields"):
            ZenApplicationAdapter(
                application_instance_id="zen-main",
                client=_ZenInputClient(),
                workspace_id="workspace",
                cwd="/repo",
                thread_start_options={
                    "approval_policy": "never",
                    "approvalPolicy": "on-request",
                },
            )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires a FIFO-capable platform")
    def test_fifo_image_rejection_preserves_loop_responsiveness(self) -> None:
        script = """
import asyncio, os, tempfile
from pathlib import Path
from imagent.applications import ZenApplicationAdapter
from imagent.applications.contract import AgentInput, ProjectRef, ThreadRef
from imagent.interaction.media import AttachmentContent, LocalPath
from tests.applications.adapters.test_zen import _ZenInputClient
async def main():
    with tempfile.TemporaryDirectory() as root:
        image = Path(root, "image.png")
        os.mkfifo(image)
        client = _ZenInputClient(workspace_cwd=root)
        adapter = ZenApplicationAdapter(application_instance_id="zen", client=client,
            workspace_id="workspace", cwd=root, shared_filesystem_root=root)
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        try:
            await adapter.send_input(ThreadRef(ProjectRef("zen", "workspace"), "thread"),
                AgentInput(client_message_id="fifo", content=(AttachmentContent(
                    attachment_id="fifo", media_type="image/png",
                    source=LocalPath(str(image)), size_bytes=1),)))
        except ValueError as error:
            assert "regular file" in str(error), error
        else:
            raise AssertionError("FIFO was accepted")
        await asyncio.wait_for(heartbeat.wait(), 0.1)
        assert client.trace == [], client.trace
asyncio.run(main())
"""
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=3
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_rooted_image_bytes_start_and_steer_without_local_path_epoch(self) -> None:
        for active in (None, "turn-active"):
            with self.subTest(active=active), tempfile.TemporaryDirectory() as directory:
                path = Path(directory, "image.png")
                payload = b"trusted image bytes"
                path.write_bytes(payload)
                client = _ZenInputClient(
                    active_turn_id=active,
                    local_image_epoch=None,
                    workspace_cwd=directory,
                )
                adapter = ZenApplicationAdapter(
                    application_instance_id="zen-main",
                    client=client,
                    workspace_id="workspace",
                    cwd=directory,
                    shared_filesystem_root=directory,
                )

                async def replace_path(_dispatch):
                    path.unlink()
                    path.write_bytes(b"replacement must not be sent")

                await adapter.send_input(
                    ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1"),
                    AgentInput(
                        client_message_id="image-message",
                        content=(
                            TextContent("inspect"),
                            AttachmentContent(
                                attachment_id="image",
                                media_type="image/png",
                                source=LocalPath(str(path)),
                                size_bytes=len(payload),
                            ),
                        ),
                    ),
                    before_dispatch=replace_path,
                )
                calls = client.steered if active else client.started
                self.assertEqual(len(calls), 1)
                self.assertIsNone(calls[0]["text"])
                self.assertEqual(
                    calls[0]["input_items"],
                    [
                        {"type": "text", "text": "inspect"},
                        {
                            "type": "image",
                            "url": "data:image/png;base64,"
                            + base64.b64encode(payload).decode("ascii"),
                        },
                    ],
                )
                self.assertNotIn("expected_local_image_epoch", calls[0])
                self.assertEqual(
                    adapter.summary.capabilities.attachment_sources,
                    (AttachmentSourceKind.LOCAL_PATH,),
                )

    async def test_invalid_images_fail_before_native_io(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            image = Path(directory, "image.png")
            image.write_bytes(b"png")
            foreign = Path(outside, "secret.png")
            foreign.write_bytes(b"png")
            link = Path(directory, "link.png")
            link.symlink_to(foreign)
            cases = (
                (LocalPath(str(image)), "image/png", None),
                (LocalPath(str(image)), "image/png", True),
                (LocalPath(str(image)), "image/png", 2),
                (LocalPath(str(image)), "image/png", 21 * 1024 * 1024),
                (LocalPath(str(foreign)), "image/png", 3),
                (LocalPath(str(link)), "image/png", 3),
                (RemoteUrl("https://example.test/image.png"), "image/png", 3),
                (LocalPath(str(image)), "image/svg+xml", 3),
            )
            for source, media_type, size in cases:
                client = _ZenInputClient(workspace_cwd=directory)
                adapter = ZenApplicationAdapter(
                    application_instance_id="zen-main",
                    client=client,
                    workspace_id="workspace",
                    cwd=directory,
                    shared_filesystem_root=directory,
                )
                with self.subTest(source=source, media_type=media_type, size=size):
                    with self.assertRaises((ValueError, NotImplementedError)):
                        await adapter.send_input(
                            ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1"),
                            AgentInput(
                                client_message_id="bad-image",
                                content=(
                                    AttachmentContent(
                                        attachment_id="image",
                                        media_type=media_type,
                                        source=source,
                                        size_bytes=cast(int, size),
                                    ),
                                ),
                            ),
                        )
                    self.assertEqual(client.trace, [])

    async def test_image_batch_is_preflighted_before_reading_any_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = ZenApplicationAdapter(
                application_instance_id="zen-main",
                client=_ZenInputClient(),
                workspace_id="workspace",
                cwd=directory,
                shared_filesystem_root=directory,
            )
            item = AttachmentContent(
                attachment_id="image",
                media_type="image/png",
                source=LocalPath(str(Path(directory, "image.png"))),
                size_bytes=10 * 1024 * 1024,
            )
            with patch("imagent.applications.adapters.zen.read_local_attachment") as read:
                with self.assertRaises(ValueError):
                    await adapter.send_input(
                        ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1"),
                        AgentInput(client_message_id="oversize-batch", content=(item,) * 4),
                    )
                read.assert_not_called()

    async def test_invalid_native_turn_identity_stops_before_event_dispatch(self) -> None:
        native = NativeZenClient()
        adapter = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=native,
            workspace_id="workspace",
            cwd="/repo",
        )
        events = adapter.subscribe_thread(
            ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1")
        )
        try:
            await native._notify(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "", "status": "completed"},
                    },
                }
            )
            with self.assertRaises(EventStreamReset) as raised:
                await anext(events)
            self.assertEqual(raised.exception.gap_code, "application_native_mapping_failed")
        finally:
            await cast(Any, events).aclose()


if __name__ == "__main__":
    unittest.main()

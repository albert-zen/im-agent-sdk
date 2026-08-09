from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from imagent.applications import ZenApplicationAdapter
from imagent.applications.adapters.zen import ZenApplicationAdapter as ZenApplicationAdapterOwner
from imagent.applications.contract import (
    AgentInput,
    ApplicationInputDispatch,
    InputDisposition,
    ProjectRef,
    ThreadRef,
    TurnReplyCorrelationPolicy,
)
from imagent.applications.events import EventStreamReset
from imagent.applications.operations import CreateThread, ThreadCreated
from imagent.interaction.media import AttachmentContent, LocalPath
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
        raise AssertionError("Zen must not steer")


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

    async def test_prefer_active_turn_is_fenced_start_without_active_turn_discovery(self) -> None:
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

        self.assertEqual(client.trace, ["read", "fence", "start"])
        self.assertEqual(client.read_calls, [("thread-1", False)])
        self.assertEqual(client.steered, [])
        self.assertEqual(len(client.started), 1)
        self.assertEqual(len(dispatches), 1)
        self.assertIs(dispatches[0].disposition, InputDisposition.STARTED)
        self.assertIs(
            dispatches[0].correlation_policy,
            TurnReplyCorrelationPolicy.CREATE_NEW,
        )
        self.assertIsNone(dispatches[0].expected_turn_ref)
        self.assertIs(accepted.disposition, InputDisposition.STARTED)
        self.assertIs(
            accepted.correlation_policy,
            TurnReplyCorrelationPolicy.CREATE_NEW,
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

        self.assertEqual(client.read_calls, [("thread-1", False)])
        self.assertEqual(client.steered, [])
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

    async def test_local_image_uses_shared_verified_connection_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "image.png")
            path.write_bytes(b"png")
            client = _ZenInputClient(local_image_epoch=29, workspace_cwd=directory)
            adapter = ZenApplicationAdapter(
                application_instance_id="zen-main",
                client=client,
                workspace_id="workspace",
                cwd=directory,
                shared_filesystem_root=directory,
            )

            await adapter.send_input(
                ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1"),
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

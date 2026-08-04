from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from imagent.applications import CodexApplicationAdapter
from imagent.applications.adapters.appserver.client import AppServerError
from imagent.applications.adapters.codex import (
    CodexApplicationAdapter as CodexApplicationAdapterOwner,
)
from imagent.contracts import (
    AgentInput,
    ApplicationInputDispatch,
    ApplicationInputOutcomeUnknown,
    AttachmentContent,
    CreateThread,
    InputContinuationPreference,
    InputDisposition,
    LocalPath,
    TextContent,
    ThreadCreated,
    ThreadRef,
    TurnReplyCorrelationPolicy,
)


class _InputClient:
    def __init__(
        self,
        *,
        active_turn_id: str | None = None,
        local_image_epoch: int | None = 7,
    ) -> None:
        self.active_turn_id = active_turn_id
        self.local_image_epoch = local_image_epoch
        self.notification_handlers = []
        self.read_calls: list[tuple[str, bool]] = []
        self.trace: list[str] = []
        self.started: list[dict[str, object]] = []
        self.created_threads: list[dict[str, object]] = []
        self.steered: list[dict[str, object]] = []

    def add_notification_handler(self, handler) -> None:
        self.notification_handlers.append(handler)

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

    async def resume_thread(self, **params: object) -> dict[str, object]:
        del params
        return {"thread": {"id": "thread-resumed"}}

    async def interrupt_turn(
        self,
        thread_id: str,
        turn_id: str,
    ) -> dict[str, object]:
        del thread_id, turn_id
        return {}

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
                "status": {"type": "active" if turns else "idle"},
                "turns": turns if include_turns else None,
            }
        }

    async def start_turn(
        self,
        thread_id: str,
        text: str | None = None,
        **kwargs,
    ) -> dict[str, object]:
        self.trace.append("start")
        self.started.append({"thread_id": thread_id, "text": text, **kwargs})
        return {"turn": {"id": "turn-started"}}

    async def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        text: str | None = None,
        **kwargs,
    ) -> dict[str, object]:
        self.trace.append("steer")
        self.steered.append({"thread_id": thread_id, "turn_id": turn_id, "text": text, **kwargs})
        return {"turnId": turn_id}


class _StaleSteerClient(_InputClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.steer_error = AppServerError("no active turn")

    async def read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool = False,
    ) -> dict[str, object]:
        if self.read_calls:
            raise RuntimeError("unexpected second read after native steer rejection")
        return await super().read_thread(thread_id, include_turns=include_turns)

    async def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        text: str | None = None,
        **kwargs,
    ) -> dict[str, object]:
        self.steered.append({"thread_id": thread_id, "turn_id": turn_id, "text": text, **kwargs})
        raise self.steer_error


class _ReplacedTurnClient(_InputClient):
    async def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        text: str | None = None,
        **kwargs,
    ) -> dict[str, object]:
        self.steered.append({"thread_id": thread_id, "turn_id": turn_id, "text": text, **kwargs})
        self.active_turn_id = "turn-replacement"
        return {"turnId": "turn-replacement"}


class _MissingSteerIdentityClient(_InputClient):
    async def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        text: str | None = None,
        **kwargs,
    ) -> dict[str, object]:
        self.steered.append({"thread_id": thread_id, "turn_id": turn_id, "text": text, **kwargs})
        return {"ok": True}


class _MissingStartIdentityClient(_InputClient):
    async def start_turn(
        self,
        thread_id: str,
        text: str | None = None,
        **kwargs,
    ) -> dict[str, object]:
        self.started.append({"thread_id": thread_id, "text": text, **kwargs})
        return {"ok": True}


class _ActiveTurnWithoutIdentityClient(_InputClient):
    async def read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool = False,
    ) -> dict[str, object]:
        self.read_calls.append((thread_id, include_turns))
        return {
            "thread": {
                "id": thread_id,
                "status": {"type": "active"},
                "turns": [] if include_turns else None,
            }
        }


class AppServerApplicationInputTests(unittest.IsolatedAsyncioTestCase):
    def test_codex_facade_preserves_exact_owner_identity(self) -> None:
        self.assertEqual(
            CodexApplicationAdapterOwner.__module__,
            "imagent.applications.adapters.codex",
        )
        self.assertIs(CodexApplicationAdapter, CodexApplicationAdapterOwner)

    async def test_thread_creation_without_profile_preserves_cwd_only(self) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
        )

        result = await adapter.execute(
            CreateThread(
                operation_id="create-default-thread",
                application_ref=adapter.summary.ref,
                created_at=datetime.now(UTC),
            )
        )

        self.assertIsInstance(result, ThreadCreated)
        self.assertEqual(client.created_threads, [{"cwd": "/repo"}])

    async def test_local_image_uses_verified_connection_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "image.png")
            path.write_bytes(b"png")
            client = _InputClient(local_image_epoch=17)
            adapter = CodexApplicationAdapter(
                application_instance_id="codex-main",
                client=client,
                cwd=directory,
                shared_filesystem_root=directory,
            )

            await adapter.send_input(
                ThreadRef("codex-main", "thread-1"),
                AgentInput(
                    client_message_id="message-image",
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

        self.assertEqual(client.started[0]["expected_local_image_epoch"], 17)

    async def test_local_image_steer_uses_verified_connection_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "image.png")
            path.write_bytes(b"png")
            client = _InputClient(active_turn_id="turn-active", local_image_epoch=23)
            adapter = CodexApplicationAdapter(
                application_instance_id="codex-main",
                client=client,
                cwd=directory,
                shared_filesystem_root=directory,
                steer_active_turn=True,
            )

            accepted = await adapter.send_input(
                ThreadRef("codex-main", "thread-1"),
                AgentInput(
                    client_message_id="message-steered-image",
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

        self.assertEqual(client.started, [])
        self.assertEqual(client.steered[0]["expected_local_image_epoch"], 23)
        self.assertEqual(accepted.turn_id, "turn-active")

    async def test_local_image_requires_a_verified_connection_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "image.png")
            path.write_bytes(b"png")
            client = _InputClient(local_image_epoch=None)
            adapter = CodexApplicationAdapter(
                application_instance_id="codex-main",
                client=client,
                cwd=directory,
                shared_filesystem_root=directory,
            )

            with self.assertRaisesRegex(RuntimeError, "verified shared filesystem"):
                await adapter.send_input(
                    ThreadRef("codex-main", "thread-1"),
                    AgentInput(
                        client_message_id="message-unverified-image",
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

        self.assertEqual(client.started, [])

    async def test_active_codex_turn_is_steered_before_starting_another_turn(self) -> None:
        client = _InputClient(active_turn_id="turn-active")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
        )

        accepted = await adapter.send_input(
            ThreadRef("codex-main", "thread-1"),
            AgentInput(
                client_message_id="message-followup",
                content=(TextContent("continue"),),
            ),
        )

        self.assertEqual(client.read_calls, [("thread-1", True)])
        self.assertEqual(
            client.steered,
            [
                {
                    "thread_id": "thread-1",
                    "turn_id": "turn-active",
                    "text": "continue",
                }
            ],
        )
        self.assertEqual(client.started, [])
        self.assertEqual(accepted.turn_id, "turn-active")
        self.assertEqual(accepted.client_message_id, "message-followup")
        self.assertIs(accepted.disposition, InputDisposition.STEERED)
        self.assertIs(
            accepted.correlation_policy,
            TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
        )

    async def test_codex_steer_fence_precedes_exactly_one_native_steer(self) -> None:
        client = _InputClient(active_turn_id="turn-active")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
        )
        dispatches: list[ApplicationInputDispatch] = []

        async def before_dispatch(dispatch: ApplicationInputDispatch) -> None:
            client.trace.append("fence")
            dispatches.append(dispatch)

        await adapter.send_input(
            ThreadRef("codex-main", "thread-1"),
            AgentInput(
                client_message_id="message-fenced-steer",
                content=(TextContent("continue"),),
            ),
            before_dispatch=before_dispatch,
        )

        self.assertEqual(client.trace, ["read", "fence", "steer"])
        self.assertEqual(len(client.steered), 1)
        self.assertEqual(client.started, [])
        self.assertEqual(len(dispatches), 1)
        self.assertIs(dispatches[0].disposition, InputDisposition.STEERED)
        self.assertIs(
            dispatches[0].correlation_policy,
            TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
        )
        self.assertEqual(dispatches[0].expected_turn_id, "turn-active")

    async def test_codex_fence_failure_prevents_native_steer(self) -> None:
        client = _InputClient(active_turn_id="turn-active")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
        )

        async def reject_dispatch(dispatch: ApplicationInputDispatch) -> None:
            del dispatch
            raise RuntimeError("fence rejected")

        with self.assertRaisesRegex(RuntimeError, "fence rejected"):
            await adapter.send_input(
                ThreadRef("codex-main", "thread-1"),
                AgentInput(
                    client_message_id="message-rejected-steer",
                    content=(TextContent("continue"),),
                ),
                before_dispatch=reject_dispatch,
            )

        self.assertEqual(client.steered, [])
        self.assertEqual(client.started, [])

    async def test_active_turn_steering_can_be_disabled_by_deployment(self) -> None:
        client = _InputClient(active_turn_id="turn-active")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
            steer_active_turn=False,
        )
        dispatches: list[ApplicationInputDispatch] = []

        async def before_dispatch(dispatch: ApplicationInputDispatch) -> None:
            client.trace.append("fence")
            dispatches.append(dispatch)

        await adapter.send_input(
            ThreadRef("codex-main", "thread-1"),
            AgentInput(
                client_message_id="message-default-start",
                content=(TextContent("continue"),),
            ),
            before_dispatch=before_dispatch,
        )

        self.assertEqual(client.trace, ["fence", "start"])
        self.assertEqual(client.read_calls, [])
        self.assertEqual(client.steered, [])
        self.assertEqual(len(client.started), 1)
        self.assertEqual(len(dispatches), 1)
        self.assertIs(dispatches[0].disposition, InputDisposition.STARTED)
        self.assertIs(
            dispatches[0].correlation_policy,
            TurnReplyCorrelationPolicy.CREATE_NEW,
        )
        self.assertIsNone(dispatches[0].expected_turn_id)

    async def test_explicit_start_new_turn_bypasses_active_turn_discovery(self) -> None:
        client = _InputClient(active_turn_id="turn-active")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
        )

        accepted = await adapter.send_input(
            ThreadRef("codex-main", "thread-1"),
            AgentInput(
                client_message_id="message-explicit-start",
                content=(TextContent("new work"),),
            ),
            continuation=InputContinuationPreference.START_NEW_TURN,
        )

        self.assertEqual(client.read_calls, [])
        self.assertEqual(client.steered, [])
        self.assertEqual(len(client.started), 1)
        self.assertIs(accepted.disposition, InputDisposition.STARTED)
        self.assertIs(
            accepted.correlation_policy,
            TurnReplyCorrelationPolicy.CREATE_NEW,
        )

    async def test_completed_active_turn_between_read_and_steer_does_not_fallback_start(
        self,
    ) -> None:
        client = _StaleSteerClient(active_turn_id="turn-stale")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
            steer_active_turn=True,
        )

        with self.assertRaises(AppServerError) as raised:
            await adapter.send_input(
                ThreadRef("codex-main", "thread-1"),
                AgentInput(
                    client_message_id="message-raced-completion",
                    content=(TextContent("continue"),),
                ),
            )

        self.assertIs(raised.exception, client.steer_error)
        self.assertEqual(
            client.read_calls,
            [("thread-1", True)],
        )
        self.assertEqual(len(client.steered), 1)
        self.assertEqual(client.started, [])

    async def test_replaced_active_turn_uses_native_steer_response_for_reconciliation(
        self,
    ) -> None:
        client = _ReplacedTurnClient(active_turn_id="turn-observed")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
            steer_active_turn=True,
        )

        accepted = await adapter.send_input(
            ThreadRef("codex-main", "thread-1"),
            AgentInput(
                client_message_id="message-raced-replacement",
                content=(TextContent("continue"),),
            ),
        )

        self.assertEqual(client.read_calls, [("thread-1", True)])
        self.assertEqual(client.steered[0]["turn_id"], "turn-observed")
        self.assertEqual(client.started, [])
        self.assertEqual(accepted.turn_id, "turn-replacement")

    async def test_active_thread_without_turn_identity_fails_closed(self) -> None:
        client = _ActiveTurnWithoutIdentityClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
            steer_active_turn=True,
        )

        with self.assertRaisesRegex(RuntimeError, "did not expose an active Turn identity"):
            await adapter.send_input(
                ThreadRef("codex-main", "thread-1"),
                AgentInput(
                    client_message_id="message-missing-active-id",
                    content=(TextContent("continue"),),
                ),
            )

        self.assertEqual(client.started, [])
        self.assertEqual(client.steered, [])

    async def test_steer_without_native_turn_identity_has_unknown_outcome(self) -> None:
        client = _MissingSteerIdentityClient(active_turn_id="turn-active")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
            steer_active_turn=True,
        )

        with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
            await adapter.send_input(
                ThreadRef("codex-main", "thread-1"),
                AgentInput(
                    client_message_id="message-missing-steer-id",
                    content=(TextContent("continue"),),
                ),
            )

        self.assertRegex(str(raised.exception), "native Turn identity is unknown")
        self.assertIsInstance(raised.exception.cause, RuntimeError)
        self.assertEqual(client.started, [])

    async def test_start_without_native_turn_identity_has_unknown_outcome(self) -> None:
        client = _MissingStartIdentityClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            cwd="/repo",
        )

        with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
            await adapter.send_input(
                ThreadRef("codex-main", "thread-1"),
                AgentInput(
                    client_message_id="message-missing-start-id",
                    content=(TextContent("begin"),),
                ),
            )

        self.assertRegex(str(raised.exception), "turn/start was accepted")
        self.assertIsInstance(raised.exception.cause, RuntimeError)
        self.assertEqual(len(client.started), 1)
        self.assertEqual(client.steered, [])


if __name__ == "__main__":
    unittest.main()

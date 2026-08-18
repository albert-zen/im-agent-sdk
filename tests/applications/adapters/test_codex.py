from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from imagent.applications.adapters.appserver.client import AppServerError
from imagent.applications.adapters.appserver.mapping import APP_SERVER_MAPPING_ERROR_MESSAGE
from imagent.applications.adapters.codex import (
    CodexApplicationAdapter,
)
from imagent.applications.adapters.codex import (
    CodexApplicationAdapter as CodexApplicationAdapterOwner,
)
from imagent.applications.contract import (
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    ApplicationInputOutcomeUnknown,
    ApplicationRef,
    InputContinuationPreference,
    InputDisposition,
    ProjectRef,
    ThreadRef,
    TurnRef,
    TurnReplyCorrelationPolicy,
)
from imagent.applications.events import (
    AgentEventType,
    EventStreamOverflow,
    EventStreamReset,
)
from imagent.applications.operations import (
    ActivateNativeThread,
    ApplicationOperationFailed,
    CreateProject,
    CreateThread,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetTurnCatchup,
    InterruptTurn,
    ListProjects,
    ListThreads,
    ProjectRead,
    ProjectsListed,
    ThreadCreated,
    ThreadHistoryRead,
    ThreadsListed,
    TurnCatchupRead,
)
from imagent.interaction.media import AttachmentContent, LocalPath
from imagent.interaction.messages import TextContent
from imagent.interaction.operations import ContractViolation
from tests.applications.adapters._appserver_fakes import NativeZenClient


class _InputClient:
    def __init__(
        self,
        *,
        active_turn_id: str | None = None,
        local_image_epoch: int | None = 7,
        workspace_cwd: str | None = "/repo",
    ) -> None:
        self.connection_epoch = 1
        self.active_turn_id = active_turn_id
        self.local_image_epoch = local_image_epoch
        self.workspace_cwd = workspace_cwd
        self.notification_handlers = []
        self.read_calls: list[tuple[str, bool]] = []
        self.trace: list[str] = []
        self.started: list[dict[str, object]] = []
        self.created_threads: list[dict[str, object]] = []
        self.steered: list[dict[str, object]] = []
        self.resumed: list[str] = []
        self.interrupted: list[tuple[str, str]] = []
        self.turn_list_calls: list[str] = []

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
        del params
        self.turn_list_calls.append(thread_id)
        return {"data": []}

    async def start_thread(self, **params: object) -> dict[str, object]:
        self.created_threads.append(deepcopy(dict(params)))
        return {
            "thread": {
                "id": "thread-created",
                "cwd": params["cwd"],
                "sessionId": "session-thread-created",
                "createdAt": 1,
                "updatedAt": 1,
                "recencyAt": 1,
            }
        }

    async def resume_thread(self, **params: object) -> dict[str, object]:
        self.resumed.append(str(params["threadId"]))
        return {"thread": {"id": "thread-resumed"}}

    async def interrupt_turn(
        self,
        thread_id: str,
        turn_id: str,
    ) -> dict[str, object]:
        self.interrupted.append((thread_id, turn_id))
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
                "cwd": self.workspace_cwd,
                "sessionId": f"session-{thread_id}",
                "createdAt": 1,
                "updatedAt": 1,
                "recencyAt": 1,
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


class _InteractiveInputClient(_InputClient):
    def __init__(self) -> None:
        super().__init__()
        self.server_request_handlers = []
        self.connection_reset_handlers = []
        self.request_replies: list[tuple[object, object, object]] = []
        self.request_errors: list[tuple[object, object, object, object]] = []

    def add_server_request_handler(self, handler) -> None:
        self.server_request_handlers.append(handler)

    def add_connection_reset_handler(self, handler) -> None:
        self.connection_reset_handlers.append(handler)

    async def reply_to_transport_request(
        self,
        request_id: object,
        result: object,
        *,
        expected_connection_epoch: object = None,
    ) -> None:
        self.request_replies.append((request_id, result, expected_connection_epoch))

    async def reply_error_to_transport_request(
        self,
        request_id: object,
        *,
        code: object,
        message: object,
        expected_connection_epoch: object = None,
    ) -> None:
        self.request_errors.append((request_id, code, message, expected_connection_epoch))


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
                "cwd": self.workspace_cwd,
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

    def test_workspace_root_must_be_explicit_and_bounded(self) -> None:
        for root in ("", "x" * 4097):
            with (
                self.subTest(root_length=len(root)),
                self.assertRaisesRegex(ValueError, "workspace root"),
            ):
                CodexApplicationAdapter(
                    application_instance_id="codex-main",
                    client=_InputClient(),
                    workspace_id="workspace",
                    cwd=root,
                )
        with self.assertRaisesRegex(ContractViolation, "project_id"):
            CodexApplicationAdapter(
                application_instance_id="codex-main",
                client=_InputClient(),
                workspace_id="",
                cwd="/repo",
            )

    async def test_workspace_project_projection_is_stable_and_management_is_local(self) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        project_ref = ProjectRef("codex-main", "workspace")
        listed = await adapter.execute(
            ListProjects(
                operation_id="list-projects",
                application_ref=adapter.summary.ref,
                created_at=datetime.now(UTC),
            )
        )
        read = await adapter.execute(
            GetProject(
                operation_id="get-project",
                application_ref=adapter.summary.ref,
                project_ref=project_ref,
                created_at=datetime.now(UTC),
            )
        )
        unsupported = await adapter.execute(
            CreateProject(
                operation_id="create-project",
                application_ref=adapter.summary.ref,
                cwd="/other",
                created_at=datetime.now(UTC),
            )
        )

        self.assertIsInstance(listed, ProjectsListed)
        assert isinstance(listed, ProjectsListed)
        self.assertEqual(tuple(item.ref for item in listed.projects.items), (project_ref,))
        self.assertIsInstance(read, ProjectRead)
        assert isinstance(read, ProjectRead)
        self.assertEqual(read.project, listed.projects.items[0])
        self.assertIsNotNone(read.project.workspace_root_fingerprint)
        self.assertIsInstance(unsupported, ApplicationOperationFailed)
        assert isinstance(unsupported, ApplicationOperationFailed)
        self.assertEqual(unsupported.error.code, "unsupported")
        self.assertEqual(client.created_threads, [])
        self.assertEqual(client.read_calls, [])

    async def test_foreign_workspace_is_rejected_before_native_io(self) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        foreign_project = ProjectRef("codex-main", "other-workspace")
        create = await adapter.execute(
            CreateThread(
                operation_id="foreign-create",
                application_ref=adapter.summary.ref,
                project_ref=foreign_project,
                created_at=datetime.now(UTC),
            )
        )
        read = await adapter.execute(
            GetThread(
                operation_id="foreign-read",
                application_ref=adapter.summary.ref,
                thread_ref=ThreadRef(foreign_project, "thread-1"),
                created_at=datetime.now(UTC),
            )
        )

        self.assertIsInstance(create, ApplicationOperationFailed)
        self.assertIsInstance(read, ApplicationOperationFailed)
        self.assertEqual(client.created_threads, [])
        self.assertEqual(client.read_calls, [])

    async def test_native_thread_without_matching_cwd_cannot_be_mutated(self) -> None:
        thread_ref = ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        for native_cwd in (None, "/other-workspace"):
            with self.subTest(native_cwd=native_cwd):
                client = _InputClient(
                    workspace_cwd=native_cwd,
                    active_turn_id="turn-active",
                )
                adapter = CodexApplicationAdapter(
                    application_instance_id="codex-main",
                    client=client,
                    workspace_id="workspace",
                    cwd="/repo",
                )
                with self.assertRaisesRegex(ValueError, "configured workspace cwd"):
                    await adapter.send_input(
                        thread_ref,
                        AgentInput(
                            client_message_id="blocked-input",
                            content=(TextContent("must not dispatch"),),
                        ),
                    )
                activated = await adapter.execute(
                    ActivateNativeThread(
                        operation_id="blocked-activation",
                        application_ref=adapter.summary.ref,
                        thread_ref=thread_ref,
                        created_at=datetime.now(UTC),
                    )
                )
                interrupted = await adapter.execute(
                    InterruptTurn(
                        operation_id="blocked-interrupt",
                        application_ref=adapter.summary.ref,
                        thread_ref=thread_ref,
                        turn_ref=TurnRef(thread_ref, "turn-active"),
                        created_at=datetime.now(UTC),
                    )
                )

                self.assertIsInstance(activated, ApplicationOperationFailed)
                self.assertIsInstance(interrupted, ApplicationOperationFailed)
                self.assertEqual(client.started, [])
                self.assertEqual(client.steered, [])
                self.assertEqual(client.resumed, [])
                self.assertEqual(client.interrupted, [])

    async def test_thread_creation_without_profile_preserves_cwd_only(self) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )

        result = await adapter.execute(
            CreateThread(
                operation_id="create-default-thread",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef(adapter.summary.ref.application_instance_id, "workspace"),
                created_at=datetime.now(UTC),
            )
        )

        self.assertIsInstance(result, ThreadCreated)
        self.assertEqual(client.created_threads, [{"cwd": "/repo"}])

    async def test_created_pre_input_thread_has_exact_empty_baseline_until_dispatch(
        self,
    ) -> None:
        class RejectingUnmaterializedHistoryClient(_InputClient):
            def __init__(self) -> None:
                super().__init__()
                self.materialized = False

            async def list_thread_turns(
                self,
                thread_id: str,
                **params: object,
            ) -> dict[str, object]:
                self.turn_list_calls.append(thread_id)
                del params
                if not self.materialized:
                    raise RuntimeError("native history resource is absent")
                return {"data": []}

            async def read_thread(
                self,
                thread_id: str,
                *,
                include_turns: bool = False,
            ) -> dict[str, object]:
                if include_turns and not self.materialized:
                    raise RuntimeError("native turn-bearing read is absent")
                return await super().read_thread(thread_id, include_turns=include_turns)

            async def start_turn(
                self,
                thread_id: str,
                text: str | None = None,
                **kwargs: object,
            ) -> dict[str, object]:
                self.materialized = True
                return await super().start_turn(thread_id, text, **kwargs)

        client = RejectingUnmaterializedHistoryClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        project_ref = ProjectRef("codex-main", "workspace")
        created = await adapter.execute(
            CreateThread(
                operation_id="create-pre-input-thread",
                application_ref=adapter.summary.ref,
                project_ref=project_ref,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        thread_ref = created.thread.ref

        for handler in tuple(client.notification_handlers):
            handled = handler(
                {
                    "method": "thread/status/changed",
                    "params": {"threadId": thread_ref.thread_id, "status": "idle"},
                }
            )
            if inspect.isawaitable(handled):
                await handled

        history = await adapter.execute(
            GetThreadHistory(
                operation_id="empty-pre-input-history",
                application_ref=adapter.summary.ref,
                thread_ref=thread_ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        catchup = await adapter.execute(
            GetTurnCatchup(
                operation_id="empty-pre-input-catchup",
                application_ref=adapter.summary.ref,
                thread_ref=thread_ref,
                limit=3,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        self.assertIsInstance(catchup, TurnCatchupRead)
        assert isinstance(history, ThreadHistoryRead)
        assert isinstance(catchup, TurnCatchupRead)
        self.assertEqual(history.history.turns, ())
        self.assertEqual(catchup.catchup.messages, ())
        self.assertEqual(client.turn_list_calls, [])

        await adapter.send_input(
            thread_ref,
            AgentInput(
                client_message_id="materialize-thread",
                content=(TextContent("first input"),),
            ),
        )
        strict_history = await adapter.execute(
            GetThreadHistory(
                operation_id="strict-post-dispatch-history",
                application_ref=adapter.summary.ref,
                thread_ref=thread_ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(strict_history, ThreadHistoryRead)
        self.assertEqual(client.turn_list_calls, [thread_ref.thread_id])
        self.assertNotIn((thread_ref.thread_id, True), client.read_calls)

    async def test_created_pre_input_evidence_is_session_and_identity_scoped(self) -> None:
        client = _InputClient(active_turn_id="external-running")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-session-scoped-thread",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        thread_ref = created.thread.ref

        client.connection_epoch = 2
        await adapter.send_input(
            thread_ref,
            AgentInput(
                client_message_id="strict-after-session-change",
                content=(TextContent("external state must be read"),),
            ),
        )
        self.assertEqual(client.read_calls, [(thread_ref.thread_id, True)])
        self.assertEqual(
            client.steered,
            [
                {
                    "thread_id": thread_ref.thread_id,
                    "turn_id": "external-running",
                    "text": "external state must be read",
                }
            ],
        )
        self.assertEqual(client.started, [])

        foreign_client = _InputClient()
        foreign = CodexApplicationAdapter(
            application_instance_id="codex-foreign",
            client=foreign_client,
            workspace_id="workspace",
            cwd="/repo",
        )
        foreign_created = await foreign.execute(
            CreateThread(
                operation_id="create-before-foreign-turn",
                application_ref=foreign.summary.ref,
                project_ref=ProjectRef("codex-foreign", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(foreign_created, ThreadCreated)
        assert isinstance(foreign_created, ThreadCreated)
        foreign_original_read = foreign_client.read_thread

        async def externally_updated_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            result = await foreign_original_read(thread_id, include_turns=include_turns)
            native_thread = result["thread"]
            assert isinstance(native_thread, dict)
            native_thread["updatedAt"] = 2
            native_thread["recencyAt"] = 2
            return result

        foreign_client.read_thread = externally_updated_read  # type: ignore[method-assign]
        foreign_history = await foreign.execute(
            GetThreadHistory(
                operation_id="strict-after-unobserved-foreign-turn",
                application_ref=foreign.summary.ref,
                thread_ref=foreign_created.thread.ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(foreign_history, ThreadHistoryRead)
        self.assertEqual(
            foreign_client.turn_list_calls,
            [foreign_created.thread.ref.thread_id],
        )

        foreign_active_client = _InputClient(active_turn_id="foreign-active-turn")
        foreign_active = CodexApplicationAdapter(
            application_instance_id="codex-foreign-active",
            client=foreign_active_client,
            workspace_id="workspace",
            cwd="/repo",
        )
        foreign_active_created = await foreign_active.execute(
            CreateThread(
                operation_id="create-before-foreign-active-turn",
                application_ref=foreign_active.summary.ref,
                project_ref=ProjectRef("codex-foreign-active", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(foreign_active_created, ThreadCreated)
        assert isinstance(foreign_active_created, ThreadCreated)
        foreign_active_original_read = foreign_active_client.read_thread

        async def externally_active_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            result = await foreign_active_original_read(
                thread_id,
                include_turns=include_turns,
            )
            native_thread = result["thread"]
            assert isinstance(native_thread, dict)
            native_thread["updatedAt"] = 2
            native_thread["recencyAt"] = 2
            return result

        foreign_active_client.read_thread = (  # type: ignore[method-assign]
            externally_active_read
        )
        await foreign_active.send_input(
            foreign_active_created.thread.ref,
            AgentInput(
                client_message_id="strict-foreign-active-input",
                content=(TextContent("steer foreign active turn"),),
            ),
        )
        self.assertIn(
            (foreign_active_created.thread.ref.thread_id, True),
            foreign_active_client.read_calls,
        )
        self.assertEqual(
            foreign_active_client.steered[0]["turn_id"],
            "foreign-active-turn",
        )
        self.assertEqual(foreign_active_client.started, [])

        recreated_client = _InputClient()
        recreated = CodexApplicationAdapter(
            application_instance_id="codex-recreated",
            client=recreated_client,
            workspace_id="workspace",
            cwd="/repo",
        )
        recreated_result = await recreated.execute(
            CreateThread(
                operation_id="create-before-same-id-reuse",
                application_ref=recreated.summary.ref,
                project_ref=ProjectRef("codex-recreated", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(recreated_result, ThreadCreated)
        assert isinstance(recreated_result, ThreadCreated)

        original_read = recreated_client.read_thread

        async def same_id_recreated_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            result = await original_read(thread_id, include_turns=include_turns)
            native_thread = result["thread"]
            assert isinstance(native_thread, dict)
            native_thread["sessionId"] = "replacement-session"
            native_thread["createdAt"] = 2
            native_thread["updatedAt"] = 2
            native_thread["recencyAt"] = 2
            return result

        recreated_client.read_thread = same_id_recreated_read  # type: ignore[method-assign]
        history = await recreated.execute(
            GetThreadHistory(
                operation_id="strict-after-same-id-reuse",
                application_ref=recreated.summary.ref,
                thread_ref=recreated_result.thread.ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        self.assertEqual(
            recreated_client.turn_list_calls,
            [recreated_result.thread.ref.thread_id],
        )

    async def test_delayed_partial_thread_started_preserves_exact_pre_input_evidence(
        self,
    ) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-delayed-thread-started",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        thread_ref = created.thread.ref
        original_read = client.read_thread
        lifecycle_revision = 1

        async def lifecycle_revision_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            nonlocal lifecycle_revision
            lifecycle_revision += 1
            result = await original_read(thread_id, include_turns=include_turns)
            native_thread = result["thread"]
            assert isinstance(native_thread, dict)
            native_thread["createdAt"] = lifecycle_revision
            native_thread["updatedAt"] = lifecycle_revision
            native_thread["recencyAt"] = lifecycle_revision
            return result

        client.read_thread = lifecycle_revision_read  # type: ignore[method-assign]

        for handler in tuple(client.notification_handlers):
            handled = handler(
                {
                    "method": "thread/started",
                    "_connection_epoch": client.connection_epoch,
                    "params": {"thread": {"id": thread_ref.thread_id}},
                }
            )
            if inspect.isawaitable(handled):
                await handled

        history = await adapter.execute(
            GetThreadHistory(
                operation_id="empty-after-delayed-thread-started",
                application_ref=adapter.summary.ref,
                thread_ref=thread_ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        assert isinstance(history, ThreadHistoryRead)
        self.assertEqual(history.history.turns, ())
        accepted = await adapter.send_input(
            thread_ref,
            AgentInput(
                client_message_id="first-input-after-delayed-thread-started",
                content=(TextContent("materialize once"),),
            ),
        )

        self.assertEqual(accepted.turn_ref.turn_id, "turn-started")
        self.assertEqual(client.turn_list_calls, [])
        self.assertEqual(client.started[0]["thread_id"], thread_ref.thread_id)
        self.assertNotIn((thread_ref.thread_id, True), client.read_calls)

    async def test_partial_thread_started_uses_scope_to_reject_foreign_session(
        self,
    ) -> None:
        client = _InputClient(active_turn_id="replacement-turn")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-foreign-thread-started",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        thread_ref = created.thread.ref
        original_read = client.read_thread

        async def foreign_session_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            result = await original_read(thread_id, include_turns=include_turns)
            native_thread = result["thread"]
            assert isinstance(native_thread, dict)
            native_thread["sessionId"] = "foreign-session"
            native_thread["createdAt"] = 2
            native_thread["updatedAt"] = 2
            native_thread["recencyAt"] = 2
            return result

        client.read_thread = foreign_session_read  # type: ignore[method-assign]
        for handler in tuple(client.notification_handlers):
            handled = handler(
                {
                    "method": "thread/started",
                    "_connection_epoch": client.connection_epoch,
                    "params": {"thread": {"id": thread_ref.thread_id}},
                }
            )
            if inspect.isawaitable(handled):
                await handled
        client.read_thread = original_read  # type: ignore[method-assign]

        accepted = await adapter.send_input(
            thread_ref,
            AgentInput(
                client_message_id="strict-input-after-foreign-session",
                content=(TextContent("continue replacement"),),
            ),
        )

        self.assertEqual(accepted.turn_ref.turn_id, "replacement-turn")
        self.assertIn((thread_ref.thread_id, True), client.read_calls)
        self.assertEqual(client.started, [])
        self.assertEqual(len(client.steered), 1)

    async def test_partial_thread_started_rejects_non_authorized_revision(self) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-revision-drift",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        original_read = client.read_thread

        async def revision_drift_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            result = await original_read(thread_id, include_turns=include_turns)
            native_thread = result["thread"]
            assert isinstance(native_thread, dict)
            native_thread["updatedAt"] = 2
            native_thread["recencyAt"] = 2
            return result

        client.read_thread = revision_drift_read  # type: ignore[method-assign]
        for handler in tuple(client.notification_handlers):
            handled = handler(
                {
                    "method": "thread/started",
                    "_connection_epoch": client.connection_epoch,
                    "params": {"thread": {"id": created.thread.ref.thread_id}},
                }
            )
            if inspect.isawaitable(handled):
                await handled
        client.read_thread = original_read  # type: ignore[method-assign]

        history = await adapter.execute(
            GetThreadHistory(
                operation_id="strict-after-thread-started-revision-drift",
                application_ref=adapter.summary.ref,
                thread_ref=created.thread.ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        self.assertEqual(client.turn_list_calls, [created.thread.ref.thread_id])

    async def test_partial_thread_started_rejects_same_session_native_identity_drift(
        self,
    ) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-native-identity-drift",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        original_read = client.read_thread

        async def recreated_identity_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            result = await original_read(thread_id, include_turns=include_turns)
            native_thread = result["thread"]
            assert isinstance(native_thread, dict)
            native_thread["path"] = "/native/recreated-same-id.jsonl"
            native_thread["createdAt"] = 2
            native_thread["updatedAt"] = 2
            native_thread["recencyAt"] = 2
            return result

        client.read_thread = recreated_identity_read  # type: ignore[method-assign]
        for handler in tuple(client.notification_handlers):
            handled = handler(
                {
                    "method": "thread/started",
                    "_connection_epoch": client.connection_epoch,
                    "params": {"thread": {"id": created.thread.ref.thread_id}},
                }
            )
            if inspect.isawaitable(handled):
                await handled
        client.read_thread = original_read  # type: ignore[method-assign]

        history = await adapter.execute(
            GetThreadHistory(
                operation_id="strict-after-native-identity-drift",
                application_ref=adapter.summary.ref,
                thread_ref=created.thread.ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        self.assertEqual(client.turn_list_calls, [created.thread.ref.thread_id])

    async def test_partial_thread_started_reset_race_retires_captured_generation(
        self,
    ) -> None:
        client = _InputClient(active_turn_id="post-reset-turn")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-thread-started-reset",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        thread_ref = created.thread.ref
        original_read = client.read_thread
        read_started = asyncio.Event()
        release_read = asyncio.Event()
        held_once = False

        async def reset_racing_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            nonlocal held_once
            if not held_once and not include_turns:
                held_once = True
                read_started.set()
                await release_read.wait()
            return await original_read(thread_id, include_turns=include_turns)

        client.read_thread = reset_racing_read  # type: ignore[method-assign]
        notification_task = asyncio.create_task(
            adapter._handle_notification(
                {
                    "method": "thread/started",
                    "_connection_epoch": client.connection_epoch,
                    "params": {"thread": {"id": thread_ref.thread_id}},
                }
            )
        )
        await read_started.wait()
        client.connection_epoch = 2
        await adapter._handle_event_connection_reset(1)
        release_read.set()
        await notification_task

        await adapter.send_input(
            thread_ref,
            AgentInput(
                client_message_id="strict-input-after-thread-started-reset",
                content=(TextContent("continue post-reset Turn"),),
            ),
        )

        self.assertIn((thread_ref.thread_id, True), client.read_calls)
        self.assertEqual(client.started, [])
        self.assertEqual(client.steered[0]["turn_id"], "post-reset-turn")

    async def test_delayed_thread_started_cannot_retire_same_id_successor(
        self,
    ) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        project_ref = ProjectRef("codex-main", "workspace")
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-delayed-successor-race",
                application_ref=adapter.summary.ref,
                project_ref=project_ref,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        thread_ref = created.thread.ref
        original_read = client.read_thread
        read_started = asyncio.Event()
        release_read = asyncio.Event()
        held_once = False

        async def successor_racing_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            nonlocal held_once
            if not held_once and not include_turns:
                held_once = True
                read_started.set()
                await release_read.wait()
            return await original_read(thread_id, include_turns=include_turns)

        client.read_thread = successor_racing_read  # type: ignore[method-assign]
        notification_task = asyncio.create_task(
            adapter._handle_notification(
                {
                    "method": "thread/started",
                    "_connection_epoch": client.connection_epoch,
                    "params": {"thread": {"id": thread_ref.thread_id}},
                }
            )
        )
        await read_started.wait()
        successor = await adapter.execute(
            CreateThread(
                operation_id="same-id-successor-during-delayed-notification",
                application_ref=adapter.summary.ref,
                project_ref=project_ref,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(successor, ThreadCreated)
        release_read.set()
        await notification_task

        history = await adapter.execute(
            GetThreadHistory(
                operation_id="successor-empty-after-delayed-notification",
                application_ref=adapter.summary.ref,
                thread_ref=thread_ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        assert isinstance(history, ThreadHistoryRead)
        self.assertEqual(history.history.turns, ())
        self.assertEqual(client.turn_list_calls, [])

    async def test_stale_epoch_thread_started_cannot_retire_post_reset_successor(
        self,
    ) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        project_ref = ProjectRef("codex-main", "workspace")
        original = await adapter.execute(
            CreateThread(
                operation_id="create-before-stale-epoch-notification",
                application_ref=adapter.summary.ref,
                project_ref=project_ref,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(original, ThreadCreated)
        assert isinstance(original, ThreadCreated)
        client.connection_epoch = 2
        await adapter._handle_event_connection_reset(1)
        successor = await adapter.execute(
            CreateThread(
                operation_id="same-id-successor-after-reset",
                application_ref=adapter.summary.ref,
                project_ref=project_ref,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(successor, ThreadCreated)
        assert isinstance(successor, ThreadCreated)

        for handler in tuple(client.notification_handlers):
            handled = handler(
                {
                    "method": "thread/started",
                    "_connection_epoch": 1,
                    "params": {"thread": {"id": original.thread.ref.thread_id}},
                }
            )
            if inspect.isawaitable(handled):
                await handled

        history = await adapter.execute(
            GetThreadHistory(
                operation_id="post-reset-successor-remains-exact",
                application_ref=adapter.summary.ref,
                thread_ref=successor.thread.ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        assert isinstance(history, ThreadHistoryRead)
        self.assertEqual(history.history.turns, ())
        self.assertEqual(client.turn_list_calls, [])

    async def test_partial_thread_started_scope_failure_retires_evidence(self) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-thread-started-scope-failure",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        original_read = client.read_thread

        async def fail_scope_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            del thread_id, include_turns
            raise RuntimeError("scope unavailable during delayed notification")

        client.read_thread = fail_scope_read  # type: ignore[method-assign]
        for handler in tuple(client.notification_handlers):
            handled = handler(
                {
                    "method": "thread/started",
                    "_connection_epoch": client.connection_epoch,
                    "params": {"thread": {"id": created.thread.ref.thread_id}},
                }
            )
            if inspect.isawaitable(handled):
                await handled
        client.read_thread = original_read  # type: ignore[method-assign]

        history = await adapter.execute(
            GetThreadHistory(
                operation_id="strict-after-thread-started-scope-failure",
                application_ref=adapter.summary.ref,
                thread_ref=created.thread.ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        self.assertEqual(client.turn_list_calls, [created.thread.ref.thread_id])

    async def test_created_pre_input_dispatch_revalidates_after_connection_reset(self) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-dispatch-reset",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)

        async def reset_before_dispatch(dispatch: ApplicationInputDispatch) -> None:
            del dispatch
            client.connection_epoch = 2
            client.active_turn_id = "replacement-turn"
            await adapter._handle_event_connection_reset(1)

        with self.assertRaisesRegex(
            RuntimeError,
            "pre-input evidence changed before native dispatch",
        ):
            await adapter.send_input(
                created.thread.ref,
                AgentInput(
                    client_message_id="input-racing-reset",
                    content=(TextContent("must not start a second Turn"),),
                ),
                before_dispatch=reset_before_dispatch,
            )

        self.assertEqual(
            client.read_calls,
            [
                (created.thread.ref.thread_id, False),
                (created.thread.ref.thread_id, False),
            ],
        )
        self.assertEqual(client.started, [])
        self.assertEqual(client.steered, [])

    async def test_created_pre_input_dispatch_is_invalidated_by_turn_notification(
        self,
    ) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-turn-notification",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)

        async def notify_before_dispatch(dispatch: ApplicationInputDispatch) -> None:
            del dispatch
            client.active_turn_id = "native-turn-racing"
            for handler in tuple(client.notification_handlers):
                handled = handler(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": created.thread.ref.thread_id,
                            "turnId": "native-turn-racing",
                        },
                    }
                )
                if inspect.isawaitable(handled):
                    await handled

        with self.assertRaisesRegex(
            RuntimeError,
            "pre-input evidence changed before native dispatch",
        ):
            await adapter.send_input(
                created.thread.ref,
                AgentInput(
                    client_message_id="input-racing-notification",
                    content=(TextContent("must not start a second Turn"),),
                ),
                before_dispatch=notify_before_dispatch,
            )

        self.assertEqual(client.started, [])
        self.assertEqual(client.steered, [])
        self.assertNotIn((created.thread.ref.thread_id, True), client.read_calls)

    async def test_created_pre_input_dispatch_is_invalidated_by_supported_request(
        self,
    ) -> None:
        client = _InteractiveInputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-turn-request",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        self.assertEqual(len(client.server_request_handlers), 1)

        async def request_before_dispatch(dispatch: ApplicationInputDispatch) -> None:
            del dispatch
            for handler in tuple(client.server_request_handlers):
                handled = handler(
                    {
                        "id": 7,
                        "method": "item/commandExecution/requestApproval",
                        "_connection_epoch": client.connection_epoch,
                        "params": {
                            "threadId": created.thread.ref.thread_id,
                            "turnId": "native-request-turn",
                            "itemId": "native-request-item",
                            "command": "git status",
                        },
                    }
                )
                if inspect.isawaitable(handled):
                    await handled

        with self.assertRaisesRegex(
            RuntimeError,
            "pre-input evidence changed before native dispatch",
        ):
            await adapter.send_input(
                created.thread.ref,
                AgentInput(
                    client_message_id="input-racing-request",
                    content=(TextContent("must not start a second Turn"),),
                ),
                before_dispatch=request_before_dispatch,
            )

        self.assertEqual(client.started, [])
        self.assertEqual(client.steered, [])
        self.assertEqual(client.request_errors, [])

    async def test_created_pre_input_dispatch_does_not_retire_same_id_successor(
        self,
    ) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        project_ref = ProjectRef("codex-main", "workspace")
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-same-id-dispatch-race",
                application_ref=adapter.summary.ref,
                project_ref=project_ref,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)

        async def recreate_before_dispatch(dispatch: ApplicationInputDispatch) -> None:
            del dispatch
            replacement = await adapter.execute(
                CreateThread(
                    operation_id="replace-same-id-during-dispatch",
                    application_ref=adapter.summary.ref,
                    project_ref=project_ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(replacement, ThreadCreated)

        with self.assertRaisesRegex(
            RuntimeError,
            "pre-input evidence changed before native dispatch",
        ):
            await adapter.send_input(
                created.thread.ref,
                AgentInput(
                    client_message_id="input-racing-same-id-replacement",
                    content=(TextContent("must not consume successor evidence"),),
                ),
                before_dispatch=recreate_before_dispatch,
            )

        history = await adapter.execute(
            GetThreadHistory(
                operation_id="successor-still-has-empty-baseline",
                application_ref=adapter.summary.ref,
                thread_ref=created.thread.ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        assert isinstance(history, ThreadHistoryRead)
        self.assertEqual(history.history.turns, ())
        self.assertEqual(client.turn_list_calls, [])
        self.assertEqual(client.started, [])
        self.assertEqual(client.steered, [])

    async def test_inflight_empty_baseline_survives_allowlisted_turn_event_race(self) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-racing-baseline-thread",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        thread_ref = created.thread.ref
        original_read = client.read_thread
        injected = False

        async def racing_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            nonlocal injected
            result = await original_read(thread_id, include_turns=include_turns)
            if not injected and not include_turns:
                injected = True
                for handler in tuple(client.notification_handlers):
                    handled = handler(
                        {
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": "racing-turn",
                                "item": {
                                    "id": "racing-item",
                                    "type": "agentMessage",
                                    "text": "racing output",
                                },
                            },
                        }
                    )
                    if inspect.isawaitable(handled):
                        await handled
            return result

        client.read_thread = racing_read  # type: ignore[method-assign]
        events = adapter.subscribe_thread(thread_ref)
        history = await adapter.execute(
            GetThreadHistory(
                operation_id="racing-empty-baseline",
                application_ref=adapter.summary.ref,
                thread_ref=thread_ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(history, ThreadHistoryRead)
        assert isinstance(history, ThreadHistoryRead)
        self.assertEqual(history.history.turns, ())
        self.assertEqual(client.turn_list_calls, [])
        event = await anext(events)
        self.assertEqual(event.type, AgentEventType.MESSAGE_COMPLETED)
        strict = await adapter.execute(
            GetThreadHistory(
                operation_id="strict-after-racing-event",
                application_ref=adapter.summary.ref,
                thread_ref=thread_ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(strict, ThreadHistoryRead)
        self.assertEqual(client.turn_list_calls, [thread_ref.thread_id])

    async def test_only_allowlisted_turn_events_retire_and_scope_failure_stays_strict(
        self,
    ) -> None:
        client = _InputClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-hostile-event-thread",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        thread_ref = created.thread.ref
        for notification in (
            {
                "method": "thread/status/changed",
                "params": {
                    "threadId": thread_ref.thread_id,
                    "turnId": "hostile-status-turn",
                    "status": "idle",
                },
            },
            {
                "method": "vendor/unknown",
                "params": {
                    "threadId": thread_ref.thread_id,
                    "turnId": "hostile-unknown-turn",
                },
            },
        ):
            for handler in tuple(client.notification_handlers):
                handled = handler(notification)
                if inspect.isawaitable(handled):
                    await handled
        retained = await adapter.execute(
            GetThreadHistory(
                operation_id="hostile-events-do-not-retire",
                application_ref=adapter.summary.ref,
                thread_ref=thread_ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(retained, ThreadHistoryRead)
        self.assertEqual(client.turn_list_calls, [])

        original_read = client.read_thread

        async def failing_scope_read(
            thread_id: str,
            *,
            include_turns: bool = False,
        ) -> dict[str, object]:
            del thread_id, include_turns
            raise RuntimeError("scope unavailable")

        client.read_thread = failing_scope_read  # type: ignore[method-assign]
        for handler in tuple(client.notification_handlers):
            handled = handler(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": thread_ref.thread_id,
                        "turnId": "turn-before-scope-failure",
                    },
                }
            )
            if inspect.isawaitable(handled):
                await handled
        client.read_thread = original_read  # type: ignore[method-assign]
        strict = await adapter.execute(
            GetThreadHistory(
                operation_id="strict-after-scope-failed-turn-event",
                application_ref=adapter.summary.ref,
                thread_ref=thread_ref,
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(strict, ThreadHistoryRead)
        self.assertEqual(client.turn_list_calls, [thread_ref.thread_id])

    async def test_non_created_or_reconstructed_thread_history_failure_is_strict(self) -> None:
        class RejectingHistoryClient(_InputClient):
            async def list_thread_turns(
                self,
                thread_id: str,
                **params: object,
            ) -> dict[str, object]:
                self.turn_list_calls.append(thread_id)
                del params
                raise RuntimeError("ambiguous native history failure")

        client = RejectingHistoryClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await adapter.execute(
            CreateThread(
                operation_id="create-before-reconstruction",
                application_ref=adapter.summary.ref,
                project_ref=ProjectRef("codex-main", "workspace"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)
        reconstructed = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        for operation_id, thread_ref in (
            (
                "strict-existing-history",
                ThreadRef(ProjectRef("codex-main", "workspace"), "native-existing"),
            ),
            ("strict-reconstructed-history", created.thread.ref),
        ):
            with self.subTest(operation_id=operation_id):
                result = await reconstructed.execute(
                    GetThreadHistory(
                        operation_id=operation_id,
                        application_ref=reconstructed.summary.ref,
                        thread_ref=thread_ref,
                        limit=3,
                        page=1,
                        created_at=datetime.now(UTC),
                    )
                )
                self.assertIsInstance(result, ApplicationOperationFailed)
        self.assertEqual(
            client.turn_list_calls,
            ["native-existing", created.thread.ref.thread_id],
        )

    async def test_created_pre_input_evidence_is_finite_and_evicts_to_strict_history(
        self,
    ) -> None:
        class ManyThreadClient(_InputClient):
            def __init__(self) -> None:
                super().__init__()
                self.next_thread = 0

            async def start_thread(self, **params: object) -> dict[str, object]:
                self.next_thread += 1
                return {
                    "thread": {
                        "id": f"thread-{self.next_thread}",
                        "cwd": params["cwd"],
                        "sessionId": f"session-thread-{self.next_thread}",
                        "createdAt": 1,
                        "updatedAt": 1,
                        "recencyAt": 1,
                    }
                }

            async def list_thread_turns(
                self,
                thread_id: str,
                **params: object,
            ) -> dict[str, object]:
                self.turn_list_calls.append(thread_id)
                del params
                raise RuntimeError("history must remain strict after evidence eviction")

        client = ManyThreadClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created_refs: list[ThreadRef] = []
        for index in range(257):
            created = await adapter.execute(
                CreateThread(
                    operation_id=f"bounded-create-{index}",
                    application_ref=adapter.summary.ref,
                    project_ref=ProjectRef("codex-main", "workspace"),
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(created, ThreadCreated)
            assert isinstance(created, ThreadCreated)
            created_refs.append(created.thread.ref)

        evicted = await adapter.execute(
            GetThreadHistory(
                operation_id="evicted-created-history",
                application_ref=adapter.summary.ref,
                thread_ref=created_refs[0],
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        retained = await adapter.execute(
            GetThreadHistory(
                operation_id="retained-created-history",
                application_ref=adapter.summary.ref,
                thread_ref=created_refs[-1],
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(evicted, ApplicationOperationFailed)
        self.assertIsInstance(retained, ThreadHistoryRead)
        self.assertEqual(client.turn_list_calls, [created_refs[0].thread_id])

        for handler in tuple(client.notification_handlers):
            handled = handler(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": created_refs[-1].thread_id,
                        "turnId": "native-turn",
                    },
                }
            )
            if inspect.isawaitable(handled):
                await handled
        after_turn_evidence = await adapter.execute(
            GetThreadHistory(
                operation_id="turn-evidence-retires-created-history",
                application_ref=adapter.summary.ref,
                thread_ref=created_refs[-1],
                limit=3,
                page=1,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(after_turn_evidence, ApplicationOperationFailed)
        self.assertEqual(
            client.turn_list_calls,
            [created_refs[0].thread_id, created_refs[-1].thread_id],
        )

    async def test_thread_create_and_list_require_native_cwd_evidence(self) -> None:
        class ScopedListingClient(_InputClient):
            async def list_threads(self, **params: object) -> dict[str, object]:
                del params
                return {
                    "data": [
                        {"id": "own", "cwd": "/repo"},
                        {"id": "missing"},
                        {"id": "foreign", "cwd": "/other-workspace"},
                    ]
                }

        class MissingCreatedScopeClient(ScopedListingClient):
            async def start_thread(self, **params: object) -> dict[str, object]:
                self.created_threads.append(deepcopy(dict(params)))
                return {"thread": {"id": "thread-created"}}

        project_ref = ProjectRef("codex-main", "workspace")
        client = ScopedListingClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )
        listed = await adapter.execute(
            ListThreads(
                operation_id="list-scoped-threads",
                application_ref=adapter.summary.ref,
                project_ref=project_ref,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(listed, ThreadsListed)
        assert isinstance(listed, ThreadsListed)
        self.assertEqual(
            tuple(thread.ref.thread_id for thread in listed.threads.items),
            ("own",),
        )

        missing_client = MissingCreatedScopeClient()
        missing_adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=missing_client,
            workspace_id="workspace",
            cwd="/repo",
        )
        created = await missing_adapter.execute(
            CreateThread(
                operation_id="create-without-scope",
                application_ref=missing_adapter.summary.ref,
                project_ref=project_ref,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(created, ApplicationOperationFailed)

    async def test_path_only_local_image_is_rejected_before_native_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "image.png")
            path.write_bytes(b"png")
            client = _InputClient(local_image_epoch=17, workspace_cwd=directory)
            adapter = CodexApplicationAdapter(
                application_instance_id="codex-main",
                client=client,
                workspace_id="workspace",
                cwd=directory,
                shared_filesystem_root=directory,
            )

            with self.assertRaisesRegex(NotImplementedError, "path-only image input"):
                await adapter.send_input(
                    ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
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

        self.assertEqual(client.started, [])
        self.assertEqual(adapter.summary.capabilities.attachment_sources, ())

    async def test_path_only_local_image_is_rejected_before_native_steer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "image.png")
            path.write_bytes(b"png")
            client = _InputClient(
                active_turn_id="turn-active",
                local_image_epoch=23,
                workspace_cwd=directory,
            )
            adapter = CodexApplicationAdapter(
                application_instance_id="codex-main",
                client=client,
                workspace_id="workspace",
                cwd=directory,
                shared_filesystem_root=directory,
                steer_active_turn=True,
            )

            with self.assertRaisesRegex(NotImplementedError, "path-only image input"):
                await adapter.send_input(
                    ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
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
        self.assertEqual(client.steered, [])

    async def test_path_only_rejection_precedes_connection_epoch_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "image.png")
            path.write_bytes(b"png")
            client = _InputClient(local_image_epoch=None, workspace_cwd=directory)
            adapter = CodexApplicationAdapter(
                application_instance_id="codex-main",
                client=client,
                workspace_id="workspace",
                cwd=directory,
                shared_filesystem_root=directory,
            )

            with self.assertRaisesRegex(NotImplementedError, "path-only image input"):
                await adapter.send_input(
                    ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
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
            workspace_id="workspace",
            cwd="/repo",
        )

        accepted = await adapter.send_input(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
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
        self.assertEqual(accepted.turn_ref.turn_id, "turn-active")
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
            workspace_id="workspace",
            cwd="/repo",
        )
        dispatches: list[ApplicationInputDispatch] = []

        async def before_dispatch(dispatch: ApplicationInputDispatch) -> None:
            client.trace.append("fence")
            dispatches.append(dispatch)

        await adapter.send_input(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
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
        self.assertEqual(
            dispatches[0].expected_turn_ref,
            TurnRef(ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"), "turn-active"),
        )

    async def test_codex_fence_failure_prevents_native_steer(self) -> None:
        client = _InputClient(active_turn_id="turn-active")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )

        async def reject_dispatch(dispatch: ApplicationInputDispatch) -> None:
            del dispatch
            raise RuntimeError("fence rejected")

        with self.assertRaisesRegex(RuntimeError, "fence rejected"):
            await adapter.send_input(
                ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
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
            workspace_id="workspace",
            cwd="/repo",
            steer_active_turn=False,
        )
        dispatches: list[ApplicationInputDispatch] = []

        async def before_dispatch(dispatch: ApplicationInputDispatch) -> None:
            client.trace.append("fence")
            dispatches.append(dispatch)

        await adapter.send_input(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
            AgentInput(
                client_message_id="message-default-start",
                content=(TextContent("continue"),),
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

    async def test_explicit_start_new_turn_bypasses_active_turn_discovery(self) -> None:
        client = _InputClient(active_turn_id="turn-active")
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
        )

        accepted = await adapter.send_input(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
            AgentInput(
                client_message_id="message-explicit-start",
                content=(TextContent("new work"),),
            ),
            continuation=InputContinuationPreference.START_NEW_TURN,
        )

        self.assertEqual(client.read_calls, [("thread-1", False)])
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
            workspace_id="workspace",
            cwd="/repo",
            steer_active_turn=True,
        )

        with self.assertRaises(AppServerError) as raised:
            await adapter.send_input(
                ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
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
            workspace_id="workspace",
            cwd="/repo",
            steer_active_turn=True,
        )

        accepted = await adapter.send_input(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
            AgentInput(
                client_message_id="message-raced-replacement",
                content=(TextContent("continue"),),
            ),
        )

        self.assertEqual(client.read_calls, [("thread-1", True)])
        self.assertEqual(client.steered[0]["turn_id"], "turn-observed")
        self.assertEqual(client.started, [])
        self.assertEqual(accepted.turn_ref.turn_id, "turn-replacement")

    async def test_active_thread_without_turn_identity_fails_closed(self) -> None:
        client = _ActiveTurnWithoutIdentityClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/repo",
            steer_active_turn=True,
        )

        with self.assertRaisesRegex(RuntimeError, "did not expose an active Turn identity"):
            await adapter.send_input(
                ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
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
            workspace_id="workspace",
            cwd="/repo",
            steer_active_turn=True,
        )

        with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
            await adapter.send_input(
                ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
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
            workspace_id="workspace",
            cwd="/repo",
        )

        with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
            await adapter.send_input(
                ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
                AgentInput(
                    client_message_id="message-missing-start-id",
                    content=(TextContent("begin"),),
                ),
            )

        self.assertRegex(str(raised.exception), "turn/start was accepted")
        self.assertIsInstance(raised.exception.cause, RuntimeError)
        self.assertEqual(len(client.started), 1)
        self.assertEqual(client.steered, [])


class CodexEventFanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_delta_without_native_turn_identity_emits_recovery_gap(self) -> None:
        native = NativeZenClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            workspace_id="workspace",
            cwd="/repo",
        )
        events = adapter.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        )
        try:
            await native._notify(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": "thread-1", "delta": "must not escape"},
                }
            )
            with self.assertRaisesRegex(
                EventStreamReset,
                "application_native_mapping_failed",
            ):
                await anext(events)
        finally:
            await _close(events)

    async def test_transient_scope_read_failure_emits_recovery_gap(self) -> None:
        class FailingReadClient(NativeZenClient):
            async def read_thread(self, thread_id: str, *, include_turns: bool = False):
                del thread_id, include_turns
                raise RuntimeError("transient native read failure")

        native = FailingReadClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            workspace_id="workspace",
            cwd="/repo",
        )
        events = adapter.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        )
        try:
            await native._notify(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "status": "completed",
                    },
                }
            )
            with self.assertRaisesRegex(EventStreamReset, "application_native_mapping_failed"):
                await anext(events)
        finally:
            await _close(events)

    async def test_notification_without_matching_native_cwd_fails_closed(self) -> None:
        class ScopedNativeClient(NativeZenClient):
            def __init__(self, native_cwd: str | None) -> None:
                super().__init__()
                self.native_cwd = native_cwd

            async def read_thread(self, thread_id: str, *, include_turns: bool = False):
                thread: dict[str, object] = {
                    "id": thread_id,
                    "status": {"type": "idle"},
                    "turns": [] if include_turns else None,
                }
                if self.native_cwd is not None:
                    thread["cwd"] = self.native_cwd
                return {"thread": thread}

        for native_cwd in (None, "/other-workspace"):
            with self.subTest(native_cwd=native_cwd):
                native = ScopedNativeClient(native_cwd)
                adapter = CodexApplicationAdapter(
                    application_instance_id="codex-main",
                    client=native,
                    workspace_id="workspace",
                    cwd="/repo",
                )
                events = adapter.subscribe_thread(
                    ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
                )
                try:
                    await native._notify(
                        {
                            "method": "item/completed",
                            "params": {
                                "threadId": "thread-1",
                                "turnId": "turn-1",
                                "item": {
                                    "id": "foreign-message",
                                    "type": "agentMessage",
                                    "text": "must not be published",
                                },
                            },
                        }
                    )
                    with self.assertRaises(EventStreamReset):
                        await anext(events)
                finally:
                    await _close(events)

    async def test_appserver_fans_out_multiple_messages_before_terminal_event(self) -> None:
        native = NativeZenClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            workspace_id="workspace",
            cwd="/repo",
        )
        thread_ref = ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        first = adapter.subscribe_thread(thread_ref)
        second = adapter.subscribe_thread(thread_ref)

        for item_id, phase in (("progress-1", "commentary"), ("answer-1", "final_answer")):
            await asyncio.wait_for(
                native._notify(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {
                                "id": item_id,
                                "type": "agentMessage",
                                "phase": phase,
                                "text": phase,
                            },
                        },
                    }
                ),
                timeout=0.1,
            )
        await asyncio.wait_for(
            native._notify(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    },
                }
            ),
            timeout=0.1,
        )

        first_events = [await anext(first) for _ in range(3)]
        second_events = [await anext(second) for _ in range(3)]
        self.assertEqual(
            [event.event_id for event in first_events],
            [event.event_id for event in second_events],
        )
        self.assertEqual(
            [event.type for event in first_events],
            [
                AgentEventType.MESSAGE_COMPLETED,
                AgentEventType.MESSAGE_COMPLETED,
                AgentEventType.TURN_COMPLETED,
            ],
        )
        self.assertTrue(all(event.sequence is None for event in first_events))
        self.assertTrue(all(event.sequence_epoch is None for event in first_events))
        self.assertTrue(all(event.cursor is None for event in first_events))
        messages = [event.data["message"] for event in first_events[:2]]
        self.assertTrue(all(isinstance(message, AgentMessage) for message in messages))
        phases = [
            message.metadata["phase"] for message in messages if isinstance(message, AgentMessage)
        ]
        self.assertEqual(phases, ["commentary", "final_answer"])
        await _close(first)
        await _close(second)

    async def test_appserver_overflow_is_subscription_scoped(self) -> None:
        native = NativeZenClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            workspace_id="workspace",
            cwd="/repo",
            event_buffer_max_pending=2,
        )
        thread_ref = ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        fast = adapter.subscribe_thread(thread_ref)
        slow = adapter.subscribe_thread(thread_ref)

        for index in range(3):
            await native._notify(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "id": f"answer-{index}",
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": str(index),
                        },
                    },
                }
            )
            message = (await anext(fast)).data["message"]
            self.assertIsInstance(message, AgentMessage)
            assert isinstance(message, AgentMessage)
            self.assertEqual(message.agent_item_id, f"answer-{index}")

        with self.assertRaises(EventStreamOverflow):
            await anext(slow)
        self.assertEqual(adapter._events.subscriber_count("thread-1"), 1)
        await _close(fast)

    async def test_appserver_connection_reset_becomes_application_event_gap(self) -> None:
        native = NativeZenClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            workspace_id="workspace",
            cwd="/repo",
        )
        events = adapter.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        )
        await native._notify(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "lost-on-reset",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "recover from native history",
                    },
                },
            }
        )

        await native.reset_connection()

        completed = await anext(events)
        self.assertEqual(completed.type, AgentEventType.MESSAGE_COMPLETED)
        with self.assertRaises(EventStreamReset) as raised:
            await anext(events)
        self.assertEqual(raised.exception.gap_code, "application_event_connection_reset")

    async def test_invalid_native_item_identity_stops_before_event_dispatch(self) -> None:
        native = NativeZenClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=native,
            workspace_id="workspace",
            cwd="/repo",
        )
        events = adapter.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        )
        try:
            await native._notify(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "type": "agentMessage",
                            "text": "must not become an Agent message",
                        },
                    },
                }
            )
            with self.assertRaises(EventStreamReset) as raised:
                await anext(events)
            self.assertEqual(raised.exception.gap_code, "application_native_mapping_failed")
        finally:
            await _close(events)

    async def test_invalid_native_history_identity_stops_before_history_result(self) -> None:
        class InvalidHistoryClient(NativeZenClient):
            async def list_thread_turns(self, thread_id: str, **params):
                del thread_id, params
                return {
                    "data": [
                        {
                            "id": "",
                            "status": "completed",
                            "items": [],
                        }
                    ]
                }

        adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=InvalidHistoryClient(),
            workspace_id="workspace",
            cwd="/repo",
        )
        result = await adapter.execute(
            GetThreadHistory(
                operation_id="invalid-native-history",
                application_ref=ApplicationRef("codex-main"),
                thread_ref=ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(result, ApplicationOperationFailed)
        assert isinstance(result, ApplicationOperationFailed)
        self.assertEqual(result.error.message, APP_SERVER_MAPPING_ERROR_MESSAGE)


async def _close(events) -> None:
    close = getattr(events, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast, get_type_hints
from unittest.mock import patch

import imagent.applications as applications
from imagent.applications import (
    ApplicationArtifactMaterialization,
    ApplicationArtifactMaterializationCapacityError,
    ApplicationArtifactMaterializationError,
    ApplicationArtifactMaterializationTimeout,
    AppServerArtifactMaterializationLimits,
    AppServerArtifactSourceKind,
    AppServerCompletedItemFacts,
    AppServerCompletedItemPhase,
    AppServerTurnTerminalFacts,
    CodexApplicationAdapter,
    ZenApplicationAdapter,
    presentation,
)
from imagent.applications.adapters.appserver.client import AppServerClient as NativeAppServerClient
from imagent.applications.contract import (
    AgentMessage,
    ApplicationRef,
    ProjectRef,
    ThreadRef,
)
from imagent.applications.diagnostics import ApplicationArtifactMaterializationFailureCode
from imagent.applications.events import AgentEventType, EventStreamReset
from imagent.applications.operations import (
    ApplicationOperationFailed,
    GetThreadHistory,
    ThreadHistoryRead,
)
from imagent.applications.presentation import artifact_materialization
from imagent.applications.presentation.artifact_materialization import (
    AppServerArtifactMaterializationRuntime,
    appserver_completed_item_facts,
)
from imagent.gateway import GatewayExtensions, GatewayRepositories, ImAgentGateway
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.interaction.channels import ChannelCapabilities, DeliverySupportLevel
from imagent.interaction.media import AttachmentContent, AttachmentSourceKind, LocalPath
from imagent.interaction.messages import ConversationRef, OutboundMessage, TextContent
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _AppServerClient:
    def __init__(self) -> None:
        self.handlers = []
        self.turns = []

    def add_notification_handler(self, handler) -> None:
        self.handlers.append(handler)

    async def close(self) -> None:
        return None

    async def list_threads(self, **params):
        del params
        return {"data": []}

    async def list_thread_turns(self, thread_id: str, **params):
        del thread_id, params
        return {"data": self.turns, "nextCursor": None}

    async def start_thread(self, **params):
        return {"thread": {"id": "thread-1", "cwd": params["cwd"]}}

    async def read_thread(self, thread_id: str, *, include_turns: bool = False):
        del include_turns
        return {
            "thread": {
                "id": thread_id,
                "cwd": "/workspace",
                "turns": self.turns,
            }
        }

    async def resume_thread(self, **params):
        del params
        return {"thread": {"id": "thread-1"}}

    async def start_turn(self, thread_id: str, text: str | None = None, **kwargs):
        del thread_id, text, kwargs
        return {"turn": {"id": "turn-1"}}

    async def interrupt_turn(self, thread_id: str, turn_id: str):
        del thread_id, turn_id
        return {}

    async def notify(self, payload: dict) -> None:
        await self.handlers[0](payload)


class _AssociatingMaterializer:
    def __init__(self) -> None:
        self.item_facts: list[AppServerCompletedItemFacts] = []
        self.terminal_facts: list[AppServerTurnTerminalFacts] = []
        self.materialized_candidate_ids: set[str] = set()
        self.materialization_count = 0
        self.pending: dict[tuple[ThreadRef, str], list[AttachmentContent]] = {}

    async def materialize_completed_item(
        self,
        facts: AppServerCompletedItemFacts,
    ) -> ApplicationArtifactMaterialization | None:
        self.item_facts.append(facts)
        key = (facts.thread_ref, facts.turn_id)
        for candidate in facts.artifact_candidates:
            if candidate.candidate_id not in self.materialized_candidate_ids:
                self.materialized_candidate_ids.add(candidate.candidate_id)
                self.materialization_count += 1
            self.pending.setdefault(key, []).append(
                AttachmentContent(
                    attachment_id=candidate.candidate_id,
                    media_type="image/png",
                    filename="output.png",
                    size_bytes=3,
                    source=LocalPath("/consumer-spool/output.png"),
                    metadata={"lease": "lease-1"},
                )
            )
        if facts.phase is not AppServerCompletedItemPhase.FINAL_ANSWER:
            return None
        attachments = tuple(self.pending.pop(key, ()))
        return ApplicationArtifactMaterialization(attachments) if attachments else None

    async def materialize_turn_terminal(
        self,
        facts: AppServerTurnTerminalFacts,
    ) -> ApplicationArtifactMaterialization | None:
        self.terminal_facts.append(facts)
        attachments = tuple(self.pending.pop((facts.thread_ref, facts.turn_id), ()))
        return ApplicationArtifactMaterialization(attachments) if attachments else None


class _LeaseReleaseObserver:
    def __init__(self) -> None:
        self.released_attachment_ids: list[str] = []
        self.called = asyncio.Event()

    async def observe_delivery_outcome(self, context, outcome) -> None:
        self.asserted_receipt = outcome.receipt
        for item in context.message.content:
            if isinstance(item, AttachmentContent):
                self.released_attachment_ids.append(item.attachment_id)
        self.called.set()


class ApplicationArtifactFacadeTests(unittest.TestCase):
    def test_artifact_contracts_have_one_finite_facade_identity(self) -> None:
        artifact_exports = {
            "AppServerArtifactCandidate",
            "AppServerArtifactMaterializationLimits",
            "AppServerArtifactMaterializer",
            "AppServerArtifactSourceKind",
            "AppServerCompletedItemFacts",
            "AppServerCompletedItemKind",
            "AppServerCompletedItemPhase",
            "AppServerTurnTerminalFacts",
            "AppServerTurnTerminalStatus",
            "ApplicationArtifactMaterialization",
            "ApplicationArtifactMaterializationCancelled",
            "ApplicationArtifactMaterializationCapacityError",
            "ApplicationArtifactMaterializationError",
            "ApplicationArtifactMaterializationFailed",
            "ApplicationArtifactMaterializationTimeout",
        }
        self.assertEqual(
            {name for name in presentation.__all__ if name in artifact_exports},
            artifact_exports,
        )
        for name in artifact_exports:
            with self.subTest(name=name):
                self.assertIs(getattr(applications, name), getattr(presentation, name))
                self.assertIs(
                    getattr(presentation, name),
                    getattr(artifact_materialization, name),
                )

        self.assertNotIn("AppServerArtifactMaterializationRuntime", presentation.__all__)
        self.assertNotIn("appserver_completed_item_facts", presentation.__all__)

    def test_historical_artifact_module_is_absent(self) -> None:
        owner_path = Path(artifact_materialization.__file__ or "")
        self.assertFalse((owner_path.parent.parent / "appserver_artifacts.py").exists())
        self.assertIsNone(importlib.util.find_spec("imagent.applications.appserver_artifacts"))

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import imagent.applications.appserver_artifacts",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ModuleNotFoundError", completed.stderr)

    def test_presentation_artifact_facade_cold_import_is_adapter_independent(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; "
                "import imagent.applications.presentation as presentation; "
                "from imagent.applications import AppServerArtifactCandidate as top; "
                "assert top is presentation.AppServerArtifactCandidate; "
                "assert 'imagent.applications.adapters.codex' not in sys.modules; "
                "assert 'imagent.applications.adapters.zen' not in sys.modules; "
                "assert 'imagent.applications.adapters.appserver.client' not in sys.modules; "
                "assert 'imagent.applications.appserver_artifacts' not in sys.modules",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_owner_type_hints_resolve_without_historical_module(self) -> None:
        item_hints = get_type_hints(
            artifact_materialization.AppServerArtifactMaterializer.materialize_completed_item
        )
        terminal_hints = get_type_hints(
            artifact_materialization.AppServerArtifactMaterializer.materialize_turn_terminal
        )
        invoke_hints = get_type_hints(
            artifact_materialization.AppServerArtifactMaterializationRuntime.invoke
        )

        self.assertIs(item_hints["facts"], artifact_materialization.AppServerCompletedItemFacts)
        self.assertEqual(
            item_hints["return"],
            artifact_materialization.ApplicationArtifactMaterialization | None,
        )
        self.assertIs(terminal_hints["facts"], artifact_materialization.AppServerTurnTerminalFacts)
        self.assertEqual(
            terminal_hints["return"],
            artifact_materialization.ApplicationArtifactMaterialization | None,
        )
        self.assertEqual(
            invoke_hints["return"],
            artifact_materialization.ApplicationArtifactMaterialization | None,
        )
        for hints in (item_hints, terminal_hints, invoke_hints):
            self.assertNotIn("appserver_artifacts", repr(hints))


class AppServerArtifactMaterializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_association_is_ordered_typed_and_duplicate_safe(self) -> None:
        client = _AppServerClient()
        materializer = _AssociatingMaterializer()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/workspace",
            artifact_materializer=materializer,
        )
        events = application.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        )
        artifact = _artifact_notification()
        await client.notify(artifact)
        await client.notify(artifact)
        await client.notify(_answer_notification())
        await client.notify(_terminal_notification("completed"))

        message_event = await anext(events)
        terminal_event = await anext(events)
        await _close(events)

        self.assertEqual(message_event.type, AgentEventType.MESSAGE_COMPLETED)
        message = message_event.data["message"]
        assert isinstance(message, AgentMessage)
        self.assertEqual(message.agent_item_id, "answer-1")
        text = message.content[0]
        assert isinstance(text, TextContent)
        self.assertEqual(text.text, "Done")
        attachment = message.content[1]
        assert isinstance(attachment, AttachmentContent)
        self.assertEqual(attachment.attachment_id, "tool-1:image:0")
        self.assertEqual(attachment.source, LocalPath("/consumer-spool/output.png"))
        self.assertIsNone(getattr(attachment.metadata, "__setitem__", None))
        self.assertEqual(terminal_event.type, AgentEventType.TURN_COMPLETED)
        self.assertEqual(materializer.materialization_count, 1)
        self.assertEqual(
            [facts.kind.value for facts in materializer.item_facts],
            ["dynamic_tool_call", "agent_message"],
        )
        candidate = materializer.item_facts[0].artifact_candidates[0]
        self.assertEqual(candidate.source_kind, AppServerArtifactSourceKind.FILE_URL)
        self.assertEqual(candidate.locator, "file:///native/output.png")
        diagnostics = application.diagnostic_facts().artifact_materialization
        assert diagnostics is not None
        self.assertEqual(diagnostics.invocation_count, 3)
        self.assertEqual(diagnostics.live_duplicate_count, 1)

    async def test_live_duplicate_window_is_finite_and_evicted_items_may_reinvoke(self) -> None:
        client = _AppServerClient()
        materializer = _AssociatingMaterializer()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/workspace",
            artifact_materializer=materializer,
            artifact_materialization_limits=AppServerArtifactMaterializationLimits(
                max_seen_identities=1
            ),
        )
        await client.notify(_artifact_notification(item_id="tool-1"))
        await client.notify(_artifact_notification(item_id="tool-2"))
        await client.notify(_artifact_notification(item_id="tool-1"))

        self.assertEqual(len(materializer.item_facts), 3)
        self.assertEqual(len(application._seen_live_artifact_identities), 1)
        facts = application.diagnostic_facts().artifact_materialization
        assert facts is not None
        self.assertEqual(facts.live_duplicate_count, 0)

    async def test_authoritative_history_reproduces_association_by_candidate_identity(
        self,
    ) -> None:
        client = _AppServerClient()
        client.turns = [_native_turn("completed")]
        materializer = _AssociatingMaterializer()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/workspace",
            artifact_materializer=materializer,
        )
        operation = GetThreadHistory(
            operation_id="history-artifacts",
            application_ref=ApplicationRef("codex-main"),
            thread_ref=ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
            limit=10,
            page=1,
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
        )

        first = await application.execute(operation)
        second = await application.execute(operation)

        assert isinstance(first, ThreadHistoryRead)
        assert isinstance(second, ThreadHistoryRead)
        for result in (first, second):
            messages = result.history.turns[0].agent_messages
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].agent_item_id, "answer-1")
            attachment = messages[0].content[-1]
            assert isinstance(attachment, AttachmentContent)
            self.assertEqual(attachment.attachment_id, "tool-1:image:0")
        self.assertEqual(materializer.materialization_count, 1)
        candidates = [
            facts.artifact_candidates[0].candidate_id
            for facts in materializer.item_facts
            if facts.artifact_candidates
        ]
        self.assertEqual(candidates, ["tool-1:image:0", "tool-1:image:0"])
        self.assertTrue(all(facts.authoritative for facts in materializer.item_facts))

    async def test_artifact_only_terminal_fallback_precedes_each_terminal_status(self) -> None:
        for status, event_type in (
            ("completed", AgentEventType.TURN_COMPLETED),
            ("failed", AgentEventType.TURN_FAILED),
            ("interrupted", AgentEventType.TURN_INTERRUPTED),
        ):
            with self.subTest(status=status):
                client = _AppServerClient()
                materializer = _AssociatingMaterializer()
                application = CodexApplicationAdapter(
                    application_instance_id="codex-main",
                    client=client,
                    workspace_id="workspace",
                    cwd="/workspace",
                    artifact_materializer=materializer,
                )
                events = application.subscribe_thread(
                    ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
                )
                await client.notify(_artifact_notification())
                await client.notify(_terminal_notification(status))

                fallback = await anext(events)
                terminal = await anext(events)
                await _close(events)

                self.assertEqual(fallback.type, AgentEventType.MESSAGE_COMPLETED)
                message = fallback.data["message"]
                assert isinstance(message, AgentMessage)
                self.assertTrue(
                    message.agent_item_id.startswith("imagent:appserver-artifact-terminal:")
                )
                self.assertEqual(len(message.content), 1)
                self.assertEqual(terminal.type, event_type)
                self.assertEqual(materializer.terminal_facts[0].status.value, status)

    def test_candidate_facts_are_finite_stable_and_untrusted(self) -> None:
        limits = AppServerArtifactMaterializationLimits(
            max_candidates=2,
            max_candidate_locator_characters=64,
        )
        item = {
            "id": "tool-1",
            "type": "dynamicToolCall",
            "contentItems": [
                {"type": "inputImage", "imageUrl": "data:image/png;base64,eA=="},
                {"type": "inputImage", "imageUrl": "file:///native/two.png"},
                {"type": "inputImage", "imageUrl": "file:///native/ignored.png"},
            ],
        }
        thread_ref = ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        first = appserver_completed_item_facts(
            item,
            thread_ref=thread_ref,
            turn_id="turn-1",
            authoritative=False,
            default_message_id=None,
            limits=limits,
        )
        second = appserver_completed_item_facts(
            item,
            thread_ref=thread_ref,
            turn_id="turn-1",
            authoritative=True,
            default_message_id=None,
            limits=limits,
        )

        self.assertEqual(first.item_id, second.item_id)
        self.assertEqual(first.artifact_candidates, second.artifact_candidates)
        self.assertEqual(len(first.artifact_candidates), 2)
        self.assertEqual(
            tuple(candidate.source_kind for candidate in first.artifact_candidates),
            (
                AppServerArtifactSourceKind.DATA_URL,
                AppServerArtifactSourceKind.FILE_URL,
            ),
        )
        oversized = appserver_completed_item_facts(
            {
                "id": "image-1",
                "type": "imageGeneration",
                "savedPath": "/" + "x" * 65,
            },
            thread_ref=thread_ref,
            turn_id="turn-1",
            authoritative=False,
            default_message_id=None,
            limits=limits,
        )
        self.assertEqual(oversized.artifact_candidates, ())

        same_locator = {
            "type": "dynamicToolCall",
            "contentItems": [{"type": "inputImage", "imageUrl": "file:///native/shared.png"}],
        }
        distinct = tuple(
            appserver_completed_item_facts(
                {"id": item_id, **same_locator},
                thread_ref=thread_ref,
                turn_id="turn-1",
                authoritative=False,
                default_message_id=None,
                limits=limits,
            )
            for item_id in ("tool-a", "tool-b")
        )
        self.assertNotEqual(distinct[0].item_id, distinct[1].item_id)
        self.assertNotEqual(
            distinct[0].artifact_candidates[0].candidate_id,
            distinct[1].artifact_candidates[0].candidate_id,
        )

    async def test_runtime_bounds_output_timeout_capacity_and_redacted_diagnostics(self) -> None:
        limits = AppServerArtifactMaterializationLimits(
            timeout_seconds=0.001,
            max_output_string_characters=32,
            max_concurrency=1,
        )
        runtime = AppServerArtifactMaterializationRuntime(limits)

        async def oversized() -> ApplicationArtifactMaterialization:
            return ApplicationArtifactMaterialization(
                (
                    AttachmentContent(
                        attachment_id="attachment",
                        media_type="image/png",
                        source=LocalPath("/" + "secret" * 20),
                    ),
                )
            )

        with self.assertRaises(ApplicationArtifactMaterializationError):
            await runtime.invoke(oversized)

        release = asyncio.Event()

        async def blocking() -> ApplicationArtifactMaterialization | None:
            await release.wait()
            return None

        first = asyncio.create_task(runtime.invoke(blocking))
        await asyncio.sleep(0)
        with self.assertRaises(ApplicationArtifactMaterializationCapacityError):
            await runtime.invoke(blocking)
        with self.assertRaises(ApplicationArtifactMaterializationTimeout):
            await first
        release.set()
        facts = runtime.diagnostic_facts()
        self.assertEqual(facts.failure_count, 3)
        self.assertEqual(facts.timeout_count, 1)
        self.assertEqual(facts.capacity_rejection_count, 1)
        self.assertNotIn("secret", repr(facts))
        self.assertEqual(
            facts.last_failure_code,
            ApplicationArtifactMaterializationFailureCode.TIMED_OUT,
        )
        await runtime.close()

    async def test_runtime_cancellation_overrun_retains_capacity_until_cleanup(self) -> None:
        runtime = AppServerArtifactMaterializationRuntime(
            AppServerArtifactMaterializationLimits(
                timeout_seconds=0.001,
                max_concurrency=1,
            )
        )
        release = asyncio.Event()

        async def stubborn() -> ApplicationArtifactMaterialization | None:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release.wait()
            return None

        with self.assertRaises(ApplicationArtifactMaterializationTimeout) as raised:
            await runtime.invoke(stubborn)
        self.assertTrue(raised.exception.cancellation_overrun)
        with self.assertRaises(ApplicationArtifactMaterializationCapacityError):
            await runtime.invoke(stubborn)
        await runtime.close()
        facts = runtime.diagnostic_facts()
        self.assertEqual(facts.cancellation_overrun_count, 1)
        self.assertEqual(facts.capacity_rejection_count, 1)
        release.set()
        await asyncio.sleep(0)

    async def test_caller_cancellation_joins_materializer_and_records_fixed_failure(self) -> None:
        runtime = AppServerArtifactMaterializationRuntime(
            AppServerArtifactMaterializationLimits(timeout_seconds=1)
        )
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocking() -> ApplicationArtifactMaterialization | None:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        invocation = asyncio.create_task(runtime.invoke(blocking))
        await entered.wait()
        invocation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await invocation
        self.assertTrue(cancelled.is_set())
        facts = runtime.diagnostic_facts()
        self.assertEqual(facts.cancellation_count, 1)
        self.assertEqual(
            facts.last_failure_code,
            ApplicationArtifactMaterializationFailureCode.CANCELLED,
        )

    async def test_materialized_attachment_reaches_o2_for_clean_process_lease_release(
        self,
    ) -> None:
        client = _AppServerClient()
        materializer = _AssociatingMaterializer()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/workspace",
            artifact_materializer=materializer,
        )
        events = application.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        )
        await client.notify(_artifact_notification())
        await client.notify(_answer_notification())
        projected = (await anext(events)).data["message"]
        await _close(events)
        assert isinstance(projected, AgentMessage)

        channel = FakeChannelAdapter()
        channel._capabilities = ChannelCapabilities(
            markdown=DeliverySupportLevel.NATIVE,
            attachments=DeliverySupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
            attachment_media_types=("image/png",),
            max_attachment_count=4,
            max_attachment_size=1_024,
        )
        observer = _LeaseReleaseObserver()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[FakeAgentApplicationAdapter()],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            extensions=GatewayExtensions(delivery_outcome_observer=observer),
        )
        outbound = OutboundMessage(
            delivery_id="materialized-cleanup",
            conversation_ref=ConversationRef("fake-channel", "conversation"),
            content=projected.content,
            created_at=projected.created_at,
            metadata=projected.metadata,
        )
        await gateway.start()
        try:
            await gateway._deliver_outbound(outbound)
            await asyncio.wait_for(observer.called.wait(), timeout=1)
        finally:
            await gateway.stop()

        self.assertEqual(observer.released_attachment_ids, ["tool-1:image:0"])
        self.assertEqual(len(channel.sent), 2)

    async def test_materializer_failure_is_redacted_and_remains_recoverable(self) -> None:
        class Failing:
            async def materialize_completed_item(self, facts):
                del facts
                raise RuntimeError("native path /secret and consumer detail")

            async def materialize_turn_terminal(self, facts):
                del facts
                return None

        client = _AppServerClient()
        client.turns = [_native_turn("completed")]
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/workspace",
            artifact_materializer=Failing(),
        )
        first_events = application.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        )
        await client.notify(_artifact_notification())
        with self.assertRaises(EventStreamReset) as first:
            await anext(first_events)
        self.assertEqual(
            first.exception.gap_code,
            "application_artifact_materialization_failed",
        )
        self.assertNotIn("secret", str(first.exception))

        retry_events = application.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        )
        await client.notify(_artifact_notification())
        with self.assertRaises(EventStreamReset) as retry:
            await anext(retry_events)
        self.assertEqual(
            retry.exception.gap_code,
            "application_artifact_materialization_failed",
        )

        result = await application.execute(
            GetThreadHistory(
                operation_id="failed-history",
                application_ref=ApplicationRef("codex-main"),
                thread_ref=ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1"),
                limit=10,
                page=1,
                created_at=datetime(2026, 8, 3, tzinfo=UTC),
            )
        )
        assert isinstance(result, ApplicationOperationFailed)
        self.assertNotIn("secret", result.error.message)
        facts = application.diagnostic_facts().artifact_materialization
        assert facts is not None
        self.assertEqual(facts.invocation_count, 3)
        self.assertEqual(facts.failure_count, 3)
        self.assertEqual(facts.live_duplicate_count, 0)
        self.assertEqual(
            facts.last_failure_code,
            ApplicationArtifactMaterializationFailureCode.MATERIALIZER_FAILED,
        )

    async def test_production_dispatch_contains_failure_but_cannot_cross_gap(self) -> None:
        class FailingArtifactOnly:
            def __init__(self) -> None:
                self.invocations = 0

            async def materialize_completed_item(self, facts):
                self.invocations += 1
                if facts.artifact_candidates:
                    raise RuntimeError("consumer path /secret")
                return None

            async def materialize_turn_terminal(self, facts):
                del facts
                return None

        client = NativeAppServerClient(
            supervisor=object(),
            client_info={"name": "sdk-test", "title": "SDK Test", "version": "0"},
        )
        materializer = FailingArtifactOnly()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=cast(Any, client),
            workspace_id="workspace",
            cwd="/workspace",
            artifact_materializer=materializer,
        )
        events = application.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        )

        async def scoped_read_thread(thread_id: str, *, include_turns: bool = False):
            return {
                "thread": {
                    "id": thread_id,
                    "cwd": "/workspace",
                    "turns": [] if include_turns else None,
                }
            }

        with patch.object(client, "read_thread", scoped_read_thread):
            await client._dispatch_one(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "delta": "before",
                    },
                },
                1,
                queue_kind="notification",
            )
            await client._dispatch_one(
                _artifact_notification(),
                1,
                queue_kind="notification",
            )
            await client._dispatch_one(
                _answer_notification(),
                1,
                queue_kind="notification",
            )

        before = await anext(events)
        self.assertEqual(before.type, AgentEventType.MESSAGE_DELTA)
        with self.assertRaises(EventStreamReset) as gap:
            await anext(events)
        self.assertEqual(
            gap.exception.gap_code,
            "application_artifact_materialization_failed",
        )
        self.assertEqual(materializer.invocations, 2)

    async def test_invalid_native_identity_fails_before_consumer_with_fixed_diagnostics(
        self,
    ) -> None:
        client = _AppServerClient()
        materializer = _AssociatingMaterializer()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/workspace",
            artifact_materializer=materializer,
        )
        notification = _artifact_notification()
        notification["params"]["threadId"] = "secret" * 100
        events = application.subscribe_thread(
            ThreadRef(ProjectRef("codex-main", "workspace"), "secret" * 100)
        )
        await client.notify(notification)
        with self.assertRaises(EventStreamReset) as raised:
            await anext(events)

        self.assertNotIn("secret", str(raised.exception))
        self.assertEqual(
            raised.exception.gap_code,
            "application_native_mapping_failed",
        )
        self.assertEqual(materializer.item_facts, [])
        facts = application.diagnostic_facts().artifact_materialization
        assert facts is not None
        self.assertEqual(facts.invocation_count, 0)
        self.assertEqual(facts.failure_count, 0)
        self.assertIsNone(facts.last_failure_code)

    async def test_missing_native_turn_and_item_ids_fail_closed(self) -> None:
        client = _AppServerClient()
        materializer = _AssociatingMaterializer()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/workspace",
            artifact_materializer=materializer,
        )
        thread_ref = ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")

        missing_turn = _artifact_notification()
        del missing_turn["params"]["turnId"]
        turn_events = application.subscribe_thread(thread_ref)
        await client.notify(missing_turn)
        with self.assertRaises(EventStreamReset) as turn_gap:
            await anext(turn_events)
        self.assertEqual(
            turn_gap.exception.gap_code,
            "application_native_mapping_failed",
        )

        missing_item = _artifact_notification()
        del missing_item["params"]["item"]["id"]
        item_events = application.subscribe_thread(thread_ref)
        await client.notify(missing_item)
        with self.assertRaises(EventStreamReset) as item_gap:
            await anext(item_events)
        self.assertEqual(
            item_gap.exception.gap_code,
            "application_native_mapping_failed",
        )

        conflicting_item = _artifact_notification()
        conflicting_item["params"]["itemId"] = "item-conflicts-with-nested-id"
        conflicting_events = application.subscribe_thread(thread_ref)
        await client.notify(conflicting_item)
        with self.assertRaises(EventStreamReset) as conflicting_gap:
            await anext(conflicting_events)
        self.assertEqual(
            conflicting_gap.exception.gap_code,
            "application_native_mapping_failed",
        )

        missing_terminal_turn = _terminal_notification("completed")
        del missing_terminal_turn["params"]["turn"]["id"]
        terminal_events = application.subscribe_thread(thread_ref)
        await client.notify(missing_terminal_turn)
        with self.assertRaises(EventStreamReset) as terminal_gap:
            await anext(terminal_events)
        self.assertEqual(
            terminal_gap.exception.gap_code,
            "application_native_mapping_failed",
        )
        self.assertEqual(materializer.item_facts, [])
        self.assertEqual(materializer.terminal_facts, [])

        client.turns = [_native_turn("completed")]
        del client.turns[0]["items"][0]["id"]
        result = await application.execute(
            GetThreadHistory(
                operation_id="missing-item-history",
                application_ref=ApplicationRef("codex-main"),
                thread_ref=thread_ref,
                limit=10,
                page=1,
                created_at=datetime(2026, 8, 3, tzinfo=UTC),
            )
        )
        self.assertIsInstance(result, ApplicationOperationFailed)

        diagnostics = application.diagnostic_facts().artifact_materialization
        assert diagnostics is not None
        self.assertEqual(diagnostics.invocation_count, 0)
        self.assertEqual(diagnostics.failure_count, 0)
        self.assertIsNone(diagnostics.last_failure_code)

    async def test_conflicting_history_turn_alias_fails_before_history_or_materializer(
        self,
    ) -> None:
        client = _AppServerClient()
        materializer = _AssociatingMaterializer()
        application = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=client,
            workspace_id="workspace",
            cwd="/workspace",
            artifact_materializer=materializer,
        )
        thread_ref = ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
        conflicting_turn = _native_turn("completed")
        conflicting_turn["turnId"] = "turn-conflicts-with-id"
        client.turns = [conflicting_turn]

        result = await application.execute(
            GetThreadHistory(
                operation_id="conflicting-history-turn",
                application_ref=ApplicationRef("codex-main"),
                thread_ref=thread_ref,
                limit=10,
                page=1,
                created_at=datetime(2026, 8, 3, tzinfo=UTC),
            )
        )

        self.assertIsInstance(result, ApplicationOperationFailed)
        self.assertEqual(materializer.item_facts, [])
        self.assertEqual(materializer.terminal_facts, [])
        diagnostics = application.diagnostic_facts().artifact_materialization
        assert diagnostics is not None
        self.assertEqual(diagnostics.invocation_count, 0)
        self.assertEqual(diagnostics.failure_count, 0)
        self.assertIsNone(diagnostics.last_failure_code)

    async def test_absent_materializer_preserves_codex_and_zen_diagnostics(self) -> None:
        codex = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=_AppServerClient(),
            workspace_id="workspace",
            cwd="/workspace",
        )
        zen = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=_AppServerClient(),
            workspace_id="workspace",
            cwd="/workspace",
        )
        self.assertIsNone(codex.diagnostic_facts().artifact_materialization)
        self.assertIsNone(zen.diagnostic_facts().artifact_materialization)


def _artifact_notification(*, item_id: str = "tool-1") -> dict:
    return {
        "method": "item/completed",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {
                "id": item_id,
                "type": "dynamicToolCall",
                "contentItems": [{"type": "inputImage", "imageUrl": "file:///native/output.png"}],
            },
        },
    }


def _answer_notification() -> dict:
    return {
        "method": "item/completed",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {
                "id": "answer-1",
                "type": "agentMessage",
                "phase": "final_answer",
                "text": "Done",
            },
        },
    }


def _terminal_notification(status: str) -> dict:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": "thread-1",
            "turn": {"id": "turn-1", "status": status},
        },
    }


def _native_turn(status: str) -> dict:
    return {
        "id": "turn-1",
        "status": status,
        "createdAt": "2026-08-03T00:00:00Z",
        "items": [
            _artifact_notification()["params"]["item"],
            _answer_notification()["params"]["item"],
        ],
    }


async def _close(events) -> None:
    close = getattr(events, "aclose", None)
    if close is not None:
        await close()

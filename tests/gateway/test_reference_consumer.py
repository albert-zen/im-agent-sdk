from __future__ import annotations

import ast
import asyncio
import hashlib
import os
import pickle
import sqlite3
import unittest
import zlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from examples.reference_consumer.application import ReferenceApplication
from examples.reference_consumer.gateway import ReferenceArtifactLedger, build_reference_consumer
from examples.reference_consumer.interaction import (
    ReferenceChannel,
    ReferenceStatusService,
    build_command_registry,
)
from examples.reference_consumer.main import (
    _assembled_from_fragments,
    _inspect_sqlite_bridge_state,
    _inspect_sqlite_files,
    run_reference_consumer,
)
from imagent import (
    Failed,
    Gateway,
    GatewayLimits,
    MemoryGatewayStore,
    ProjectionPolicy,
    Succeeded,
)
from imagent.applications.capabilities import SupportLevel
from imagent.applications.contract import (
    AgentInput,
    ApplicationRef,
    ProjectRef,
    ThreadHistory,
    ThreadRef,
)
from imagent.applications.operations import (
    ApplicationOperationFailed,
    CreateProject,
    CreateThread,
    GetThreadHistory,
    ListProjects,
    ProjectCreated,
    ProjectsListed,
    ThreadCreated,
    ThreadHistoryRead,
)
from imagent.applications.requests import ApprovalResponse
from imagent.gateway.composition import _GatewayRuntimeDependencies
from imagent.gateway.delivery import (
    ConversationDeliveryTarget,
    DeliveryAuthorizationError,
    DeliveryIntent,
    DeliveryPrincipal,
    DeliverySubmissionCapacityError,
    DeliverySubmissionState,
    ScopedDeliveryAuthorizer,
)
from imagent.gateway.lifecycle import (
    GatewayLifecycleFailure,
    GatewayNotRunning,
    GatewayStartupOverflow,
    _public_lifecycle_error,
)
from imagent.gateway.orchestration import _GatewayRuntime
from imagent.gateway.persistence import InMemoryIdempotencyRepository
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.persistence.store import GatewayStoreSession, RuntimeLease
from imagent.gateway.projection.observation import ThreadProjectionRuntime
from imagent.interaction.channels import (
    DeliveryReceiptStatus,
    DeliverySupportLevel,
    InboundAdmissionHandler,
    MessageHandler,
)
from imagent.interaction.controllers import (
    CommandDefinition,
    CommandRegistry,
    CommandRegistryFrozenError,
    CommandResult,
)
from imagent.interaction.media import AttachmentContent, LocalPath, RemoteUrl
from imagent.interaction.messages import ConversationRef, OutboundMessage, TextContent
from imagent.interaction.operations import OperationErrorCode

_UNSAFE_HUGE_CLEANUP_DETAIL = "secret\x1b[2J\nline\u202e" + ("x" * 1_000_000)


class _HostileShortLifecycleError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("raw-argument-secret")
        self.secret = "SECRET_PAYLOAD"
        self.nested = {"secret": "NESTED_SECRET_PAYLOAD"}
        self.__cause__ = RuntimeError("CAUSE_SECRET_PAYLOAD")

    def __str__(self) -> str:
        return "ok"

    def __repr__(self) -> str:
        return "REPR_SECRET_PAYLOAD"


class _HostileNotRunning(GatewayNotRunning):
    pass


class _HostileOverflow(GatewayStartupOverflow):
    pass


class _HostileLifecycleFailure(GatewayLifecycleFailure):
    pass


class _PausingReferenceChannel(ReferenceChannel):
    def __init__(self, *, trusted_attachment_root: Path) -> None:
        super().__init__(trusted_attachment_root=trusted_attachment_root)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, message: OutboundMessage):
        self.entered.set()
        await self.release.wait()
        return await super().send(message)


class _CountingMemoryGatewayStore(MemoryGatewayStore):
    def __init__(self) -> None:
        super().__init__()
        self.acquire_count = 0
        self.close_count = 0

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ):
        self.acquire_count += 1
        return await super().acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )

    async def close(self) -> None:
        self.close_count += 1
        await super().close()


class _CountingSQLiteGatewayStore(SQLiteGatewayStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.acquire_count = 0
        self.close_count = 0

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ):
        self.acquire_count += 1
        return await super().acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )

    async def close(self) -> None:
        self.close_count += 1
        await super().close()


class _HugeCloseFailureStore(_CountingMemoryGatewayStore):
    async def close(self) -> None:
        await super().close()
        raise RuntimeError(_UNSAFE_HUGE_CLEANUP_DETAIL)


class _HugeReleaseIdempotencyRepository:
    def __init__(self) -> None:
        self._delegate = InMemoryIdempotencyRepository()
        self.release_count = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    async def release(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        del scope, key, owner_token
        self.release_count += 1
        raise RuntimeError(_UNSAFE_HUGE_CLEANUP_DETAIL)


class _FailingRenewSession:
    def __init__(
        self,
        delegate: GatewayStoreSession,
        failed: asyncio.Event,
        allow_failure: asyncio.Event,
    ) -> None:
        self._delegate = delegate
        self._failed = failed
        self._allow_failure = allow_failure

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    async def renew(self, *, lease_duration_seconds: float):
        del lease_duration_seconds
        await self._allow_failure.wait()
        self._failed.set()
        raise RuntimeError("simulated lease renewal loss")


class _WorkspaceIdentityFailureSession:
    def __init__(self, delegate: GatewayStoreSession) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    async def check_workspace_identities(self, identities) -> None:
        del identities
        raise RuntimeError("RAW_SECRET_AFTER_ACQUISITION")


class _WorkspaceIdentityFailureStore(_CountingMemoryGatewayStore):
    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> Any:
        session = await super().acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        return cast(GatewayStoreSession, _WorkspaceIdentityFailureSession(session))


class _FailingRenewMemoryGatewayStore:
    def __init__(self) -> None:
        self._delegate = _CountingMemoryGatewayStore()
        self.renew_failed = asyncio.Event()
        self._allow_failure = asyncio.Event()

    @property
    def max_effect_receipts(self) -> int:
        return self._delegate.max_effect_receipts

    @property
    def close_count(self) -> int:
        return self._delegate.close_count

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ):
        session = await self._delegate.acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        return cast(
            GatewayStoreSession,
            _FailingRenewSession(session, self.renew_failed, self._allow_failure),
        )

    async def close(self) -> None:
        await self._delegate.close()

    def allow_renew_failure(self) -> None:
        self._allow_failure.set()


class _BlockingAcquireMemoryGatewayStore(_CountingMemoryGatewayStore):
    def __init__(self) -> None:
        super().__init__()
        self.first_acquired = asyncio.Event()
        self.allow_first_return = asyncio.Event()

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ):
        session = await super().acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        if self.acquire_count == 1:
            self.first_acquired.set()
            await self.allow_first_return.wait()
        return session


class _MalformedSession:
    def __init__(self) -> None:
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


class _MalformedSessionStore:
    max_effect_receipts = 16

    def __init__(self) -> None:
        self.acquire_count = 0
        self.close_count = 0
        self.session = _MalformedSession()

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> Any:
        del gateway_id, owner_token, lease_duration_seconds
        self.acquire_count += 1
        return self.session

    async def close(self) -> None:
        self.close_count += 1


class _CancellationResistantMalformedSession(_MalformedSession):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False
        self.release = asyncio.Event()

    async def close(self) -> None:
        self.close_count += 1
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            await self.release.wait()


class _CancellationResistantMalformedSessionStore(_MalformedSessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.session = _CancellationResistantMalformedSession()


class _ProtocolStubSession(GatewayStoreSession):
    def __init__(self, delegate: GatewayStoreSession) -> None:
        self._delegate = delegate
        self.close_count = 0

    @property
    def lease(self) -> RuntimeLease:
        return self._delegate.lease

    async def close(self) -> None:
        self.close_count += 1
        await self._delegate.close()


class _ProtocolStubSessionStore:
    def __init__(self) -> None:
        self._delegate = _CountingMemoryGatewayStore()
        self.session: _ProtocolStubSession | None = None

    @property
    def max_effect_receipts(self) -> int:
        return self._delegate.max_effect_receipts

    @property
    def acquire_count(self) -> int:
        return self._delegate.acquire_count

    @property
    def close_count(self) -> int:
        return self._delegate.close_count

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> GatewayStoreSession:
        delegate = await self._delegate.acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        # The inherited Protocol methods intentionally make this nominal test
        # class abstract to static analyzers; runtime construction is the
        # counterexample under test.
        self.session = cast(Any, _ProtocolStubSession)(delegate)
        return cast(GatewayStoreSession, self.session)

    async def close(self) -> None:
        await self._delegate.close()


class _DriftingCompositionStore:
    def __init__(self, delegate: Any, channel: ReferenceChannel, application: ReferenceApplication):
        self._delegate = delegate
        self._channel = channel
        self._application = application
        self.acquire_count = 0
        self.close_count = 0

    @property
    def max_effect_receipts(self) -> int:
        return self._delegate.max_effect_receipts

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> GatewayStoreSession:
        self.acquire_count += 1
        session = await self._delegate.acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        self._channel._capabilities = replace(  # type: ignore[reportPrivateUsage]
            self._channel.capabilities,
            plain_text=DeliverySupportLevel.UNSUPPORTED,
        )
        self._application._summary = replace(  # type: ignore[reportPrivateUsage]
            self._application.summary,
            kind="drifted-kind",
        )
        return cast(GatewayStoreSession, session)

    async def close(self) -> None:
        self.close_count += 1
        await self._delegate.close()


class _ChangedLeaseSession:
    def __init__(self, delegate: GatewayStoreSession, lease: RuntimeLease) -> None:
        self._delegate = delegate
        self._lease = lease
        self.close_count = 0

    @property
    def lease(self) -> RuntimeLease:
        return self._lease

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    async def close(self) -> None:
        self.close_count += 1
        await self._delegate.close()


class _ChangedLeaseStore:
    def __init__(self, changed_fields: dict[str, Any]) -> None:
        self._delegate = _CountingMemoryGatewayStore()
        self._changed_fields = changed_fields
        self.session: _ChangedLeaseSession | None = None

    @property
    def max_effect_receipts(self) -> int:
        return self._delegate.max_effect_receipts

    @property
    def acquire_count(self) -> int:
        return self._delegate.acquire_count

    @property
    def close_count(self) -> int:
        return self._delegate.close_count

    async def acquire_runtime(
        self,
        *,
        gateway_id: str,
        owner_token: str,
        lease_duration_seconds: float,
    ) -> GatewayStoreSession:
        delegate = await self._delegate.acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        self.session = _ChangedLeaseSession(
            delegate,
            replace(delegate.lease, **self._changed_fields),
        )
        return cast(GatewayStoreSession, self.session)

    async def close(self) -> None:
        await self._delegate.close()


class _CleanupFailingChannel(ReferenceChannel):
    def __init__(
        self,
        channel_instance_id: str,
        *,
        fail_start: bool = False,
        start_error_detail: str | None = None,
        fail_stop_before_cleanup: bool = False,
        fail_stop_after_cleanup: bool = False,
        stop_error_detail: str | None = None,
    ) -> None:
        super().__init__(channel_instance_id)
        self._fail_start = fail_start
        self._start_error_detail = start_error_detail
        self._fail_stop_before_cleanup = fail_stop_before_cleanup
        self._fail_stop_after_cleanup = fail_stop_after_cleanup
        self._stop_error_detail = stop_error_detail
        self.stop_count = 0

    async def start(
        self,
        on_message: MessageHandler,
        on_admission: InboundAdmissionHandler | None = None,
    ) -> None:
        await super().start(on_message, on_admission)
        if self._fail_start:
            raise RuntimeError(
                self._start_error_detail or f"{self.channel_instance_id} start failed"
            )

    async def stop(self) -> None:
        self.stop_count += 1
        if self._fail_stop_before_cleanup:
            raise RuntimeError(self._stop_error_detail or f"{self.channel_instance_id} stop failed")
        await super().stop()
        if self._fail_stop_after_cleanup:
            raise RuntimeError(self._stop_error_detail or f"{self.channel_instance_id} stop failed")


class _StartupClaimReleaseFailureChannel(ReferenceChannel):
    def __init__(self) -> None:
        super().__init__("reference-startup-claim-release")
        conversation = self.conversation("startup-claim-release")
        self._message = conversation.text_message(
            message_id="startup-claim-release-message",
            text="startup claim release",
        )

    async def start(
        self,
        on_message: MessageHandler,
        on_admission: InboundAdmissionHandler | None = None,
    ) -> None:
        await super().start(on_message, on_admission)
        if on_admission is None:
            raise AssertionError("reference startup test requires admission")
        admission = await on_admission(
            self._message.conversation_ref,
            self._message.message_id,
        )
        if admission is None:
            raise AssertionError("reference startup claim was not admitted")
        await admission.deliver(self._message)
        raise RuntimeError(_UNSAFE_HUGE_CLEANUP_DETAIL)


class _CleanupFailingApplication(ReferenceApplication):
    def __init__(
        self,
        application_instance_id: str = "reference-agent",
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
    ) -> None:
        super().__init__(application_instance_id=application_instance_id)
        self._fail_start = fail_start
        self._fail_stop = fail_stop
        self.stop_count = 0

    async def start(self) -> None:
        await super().start()
        if self._fail_start:
            raise RuntimeError(f"{self.ref.application_instance_id} start failed")

    async def stop(self) -> None:
        self.stop_count += 1
        await super().stop()
        if self._fail_stop:
            raise RuntimeError("application stop failed")


class _HugeCleanupApplication(ReferenceApplication):
    def __init__(self) -> None:
        super().__init__(application_instance_id="reference-huge-cleanup")
        self.stop_count = 0

    async def stop(self) -> None:
        self.stop_count += 1
        await super().stop()
        raise RuntimeError(_UNSAFE_HUGE_CLEANUP_DETAIL)


class _CleanupFailingRegistry(CommandRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1
        await super().close()
        raise RuntimeError("controller close failed")


class _BlockingStartChannel(ReferenceChannel):
    def __init__(self, on_blocked: Callable[[], None]) -> None:
        super().__init__()
        self._on_blocked = on_blocked
        self.start_blocked = asyncio.Event()
        self._never = asyncio.Event()

    async def start(
        self,
        on_message: MessageHandler,
        on_admission: InboundAdmissionHandler | None = None,
    ) -> None:
        await super().start(on_message, on_admission)
        self.start_blocked.set()
        self._on_blocked()
        await self._never.wait()


class ReferenceConsumerExampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_executable_entry_point_proves_the_v1_golden_path(self) -> None:
        with TemporaryDirectory() as cwd:
            report = await run_reference_consumer(cwd)

        self.assertIs(report.projection_policy, ProjectionPolicy.FOREGROUND_ONLY)
        self.assertEqual(report.project_count, 1)
        self.assertEqual(report.thread_count, 2)
        self.assertEqual(report.conversation_count, 2)
        self.assertEqual(report.command_count, 3)
        self.assertEqual(report.project_ref.project_id, "reference-project-1")
        self.assertEqual(report.first_thread_ref.thread_id, "reference-thread-1")
        self.assertEqual(report.second_thread_ref.thread_id, "reference-thread-2")
        self.assertEqual(report.first_thread_ref.project_ref, report.project_ref)
        self.assertEqual(report.second_thread_ref.project_ref, report.project_ref)

        conversation_a = ConversationRef("reference-channel", "conversation-a")
        conversation_b = ConversationRef("reference-channel", "conversation-b")
        self.assertEqual(report.initial_thread_conversations, (conversation_a,))
        self.assertEqual(
            report.shared_thread_conversations,
            (conversation_a, conversation_b),
        )
        self.assertEqual(report.switched_old_thread_conversations, (conversation_b,))
        self.assertEqual(report.switched_new_thread_conversations, (conversation_a,))
        self.assertEqual(report.switched_back_conversations, (conversation_a, conversation_b))

        self.assertEqual(report.worker_max_active, (1, 1))
        self.assertEqual(report.worker_subscription_calls, (2, 2))
        self.assertEqual(
            report.recovered_conversations,
            (conversation_a, conversation_b),
        )
        self.assertEqual(report.recovered_delivery_count, 2)
        self.assertTrue(report.reconstructed_bindings)
        self.assertTrue(report.reconstructed_checkpoints)
        self.assertTrue(report.reconstructed_idempotency)
        self.assertTrue(report.reconstructed_receipts)
        self.assertTrue(report.duplicate_input_suppressed)
        self.assertFalse(report.recovery_redispatched_input)
        self.assertGreaterEqual(report.sqlite_files_inspected, 3)
        self.assertEqual(report.sqlite_table_count, 13)
        self.assertTrue(report.sqlite_bridge_state_allowlisted)
        self.assertEqual(report.request_delivered_destinations, 2)
        self.assertTrue(report.request_nonrecipient_rejected)
        self.assertTrue(report.request_first_writer_won)
        self.assertTrue(report.request_duplicate_rejected)
        self.assertTrue(report.request_replay_idempotent)
        self.assertTrue(report.request_restart_pending_recovered)
        self.assertTrue(report.live_only_checkpoint_stable)
        self.assertTrue(report.recoverable_presentation_parity)
        self.assertEqual(report.proactive_destination_count, 2)
        self.assertTrue(report.proactive_routes_pinned)
        self.assertTrue(report.proactive_partial_isolated)
        self.assertTrue(report.proactive_unknown_sticky)
        self.assertTrue(report.proactive_restart_replayed)
        self.assertTrue(report.media_preflight_side_effect_free)
        self.assertTrue(report.artifact_startup_swept)
        self.assertTrue(report.artifact_cleanup_complete)

        self.assertEqual(report.diagnostics_schema_version, 8)
        self.assertLess(report.diagnostics_size, 4_096)
        self.assertFalse(report.diagnostics_authoritative)
        self.assertTrue(report.adapters_stopped)
        self.assertEqual(report.active_workers_after_shutdown, 0)
        self.assertEqual(report.registry_active_after_shutdown, 0)
        self.assertEqual(report.owned_tasks_after_shutdown, 0)

    def test_consumer_artifact_ledger_recovers_and_sweeps_partial_startup(self) -> None:
        with TemporaryDirectory() as cwd:
            root = Path(cwd) / "artifacts"
            first = ReferenceArtifactLedger(root, max_leases=2)
            staged = first.stage(
                "partial-startup",
                b"consumer-owned-partial-bytes",
                expected_destinations=2,
            )
            self.assertIsInstance(staged.source, LocalPath)
            assert isinstance(staged.source, LocalPath)
            staged_path = Path(staged.source.path)
            self.assertTrue(staged_path.is_file())
            partial_path = root / ("artifact-" + ("f" * 64) + ".part")
            partial_path.write_bytes(b"partial-uncommitted-bytes")

            restarted = ReferenceArtifactLedger(root, max_leases=2)
            self.assertEqual(restarted.active_leases, 1)
            self.assertEqual(restarted.sweep(), 2)
            self.assertEqual(restarted.active_leases, 0)
            self.assertFalse(staged_path.exists())
            self.assertFalse(partial_path.exists())

    def test_consumer_artifact_ledger_is_bounded_and_rejects_escaped_restart_path(
        self,
    ) -> None:
        with TemporaryDirectory() as cwd:
            root = Path(cwd) / "artifacts"
            ledger = ReferenceArtifactLedger(root, max_leases=1)
            ledger.stage("first", b"one", expected_destinations=1)
            with self.assertRaisesRegex(RuntimeError, "capacity"):
                ledger.stage("second", b"two", expected_destinations=1)

            ledger_path = root / "consumer-artifact-ledger.json"
            ledger_path.write_text(
                '{"escaped":{"path":"/tmp/not-consumer-owned","expected":1,'
                '"observed":0,"sha256":"00","size":1}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "escaped"):
                ReferenceArtifactLedger(root, max_leases=2)

    def test_consumer_artifact_ledger_load_rejects_symlink(self) -> None:
        with TemporaryDirectory() as cwd:
            root = Path(cwd) / "artifacts"
            root.mkdir()
            outside = Path(cwd) / "outside-ledger.json"
            outside.write_text("{}", encoding="utf-8")
            (root / "consumer-artifact-ledger.json").symlink_to(outside)

            with self.assertRaisesRegex(RuntimeError, "trusted regular file"):
                ReferenceArtifactLedger(root)

    def test_consumer_artifact_ledger_load_rejects_path_replacement(self) -> None:
        with TemporaryDirectory() as cwd:
            root = Path(cwd) / "artifacts"
            first = ReferenceArtifactLedger(root)
            first.stage("retained", b"bytes", expected_destinations=1)
            ledger_path = root / "consumer-artifact-ledger.json"
            original_read = os.read
            swapped = False

            def replace_after_open(descriptor: int, size: int) -> bytes:
                nonlocal swapped
                if not swapped:
                    replacement = root / "replacement.json"
                    replacement.write_text("{}", encoding="utf-8")
                    os.replace(replacement, ledger_path)
                    swapped = True
                return original_read(descriptor, size)

            with (
                patch("examples.reference_consumer.gateway.os.read", replace_after_open),
                self.assertRaisesRegex(RuntimeError, "changed during startup"),
            ):
                ReferenceArtifactLedger(root)
            self.assertTrue(swapped)

    def test_consumer_artifact_ledger_load_rejects_growth(self) -> None:
        with TemporaryDirectory() as cwd:
            root = Path(cwd) / "artifacts"
            first = ReferenceArtifactLedger(root)
            first.stage("retained", b"bytes", expected_destinations=1)
            ledger_path = root / "consumer-artifact-ledger.json"
            original_read = os.read
            grown = False

            def grow_after_first_read(descriptor: int, size: int) -> bytes:
                nonlocal grown
                chunk = original_read(descriptor, size)
                if not grown:
                    with ledger_path.open("ab") as stream:
                        stream.write(b" ")
                        stream.flush()
                        os.fsync(stream.fileno())
                    grown = True
                return chunk

            with (
                patch("examples.reference_consumer.gateway.os.read", grow_after_first_read),
                self.assertRaisesRegex(RuntimeError, "changed during startup"),
            ):
                ReferenceArtifactLedger(root)
            self.assertTrue(grown)

    async def test_public_restart_marks_request_stale_without_authoritative_snapshot(
        self,
    ) -> None:
        with TemporaryDirectory() as cwd:
            database_path = Path(cwd) / "request-stale.sqlite3"
            channel = ReferenceChannel()
            application = ReferenceApplication(
                pending_request_snapshot_support=SupportLevel.UNSUPPORTED,
            )
            conversation = channel.conversation("request-stale")
            first = build_reference_consumer(
                application=application,
                channel=channel,
                store=SQLiteGatewayStore(database_path),
            )
            async with first.gateway:
                actions = first.gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="request-stale:project",
                )
                self.assertIsInstance(project, Succeeded)
                assert isinstance(project, Succeeded)
                self.assertIsInstance(project.value.ref, ProjectRef)
                assert isinstance(project.value.ref, ProjectRef)
                thread = await actions.create_and_bind_thread(
                    project.value.ref,
                    action_id="request-stale:thread",
                )
                self.assertIsInstance(thread, Succeeded)
                assert isinstance(thread, Succeeded)
                self.assertIsInstance(thread.value.ref, ThreadRef)
                assert isinstance(thread.value.ref, ThreadRef)
                after = len(channel.sent)
                request = await application.receive_native_approval_request(
                    thread.value.ref,
                    prompt="Request that cannot be snapshotted",
                )
                async with asyncio.timeout(2.0):
                    while len(channel.sent) == after:
                        await asyncio.sleep(0)

            native_calls = application.request_response_calls
            restarted = build_reference_consumer(
                application=application,
                channel=channel,
                store=SQLiteGatewayStore(database_path),
            )
            async with restarted.gateway:
                actions = restarted.gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                result = await actions.respond_request(
                    request.request_ref,
                    ApprovalResponse("approve"),
                    action_id="request-stale:response",
                )
                self.assertIsInstance(result, Failed)
                assert isinstance(result, Failed)
                self.assertIs(
                    result.error.operation_error_code,
                    OperationErrorCode.REQUEST_STALE,
                )
                self.assertEqual(application.request_response_calls, native_calls)

    async def test_public_memory_proactive_path_is_scoped_bounded_and_idempotent(
        self,
    ) -> None:
        channel = ReferenceChannel()
        conversation = channel.conversation("memory-proactive")
        authorizer = ScopedDeliveryAuthorizer(max_principals=1)
        credential = await authorizer.issue(
            DeliveryPrincipal(
                principal_id="memory-proactive-principal",
                allowed_conversations=(conversation.ref,),
            ),
            credential="memory-proactive-credential",
        )
        consumer = build_reference_consumer(
            channel=channel,
            store=MemoryGatewayStore(max_delivery_submission_records=1),
            delivery_authorizer=authorizer,
        )
        target = ConversationDeliveryTarget(conversation.ref)
        first_intent = DeliveryIntent(
            delivery_id="memory-proactive:first",
            target=target,
            content=(TextContent("memory proactive"),),
            created_at=datetime.now(UTC),
        )
        async with consumer.gateway:
            with self.assertRaises(DeliveryAuthorizationError):
                await consumer.gateway.authorize_proactive_target(
                    target,
                    credential="invalid-credential",
                )
            await consumer.gateway.authorize_proactive_target(
                target,
                credential=credential,
            )
            first = await consumer.gateway.deliver_proactively(
                first_intent,
                credential=credential,
            )
            self.assertIs(first.state, DeliverySubmissionState.ACCEPTED)
            native_calls = channel.native_send_calls
            replay = await consumer.gateway.deliver_proactively(
                first_intent,
                credential=credential,
            )
            self.assertTrue(all(item.replayed for item in replay.destinations))
            self.assertEqual(channel.native_send_calls, native_calls)

            self.assertTrue(await authorizer.revoke(credential))
            revoked_replay = await consumer.gateway.deliver_proactively(
                first_intent,
                credential=credential,
            )
            self.assertTrue(all(item.replayed for item in revoked_replay.destinations))
            rotated_credential = await authorizer.issue(
                DeliveryPrincipal(
                    principal_id="rotated-principal",
                    allowed_conversations=(conversation.ref,),
                ),
                credential=credential,
            )
            rotated_replay = await consumer.gateway.deliver_proactively(
                first_intent,
                credential=rotated_credential,
            )
            self.assertTrue(all(item.replayed for item in rotated_replay.destinations))
            self.assertEqual(channel.native_send_calls, native_calls)

            with self.assertRaises(DeliverySubmissionCapacityError):
                await consumer.gateway.deliver_proactively(
                    DeliveryIntent(
                        delivery_id="memory-proactive:capacity",
                        target=target,
                        content=(TextContent("must not send"),),
                        created_at=datetime.now(UTC),
                    ),
                    credential=credential,
                )
            self.assertEqual(channel.native_send_calls, native_calls)

            unsupported = DeliveryIntent(
                delivery_id="memory-proactive:unsupported",
                target=target,
                content=(
                    AttachmentContent(
                        "remote",
                        "text/plain",
                        RemoteUrl("https://example.invalid/not-acquired"),
                        size_bytes=1,
                    ),
                ),
                created_at=datetime.now(UTC),
            )
            with self.assertRaises(DeliverySubmissionCapacityError):
                await consumer.gateway.deliver_proactively(
                    unsupported,
                    credential=credential,
                )
            self.assertEqual(channel.native_send_calls, native_calls)

    async def test_public_sqlite_terminal_replay_ignores_revoked_credential(self) -> None:
        with TemporaryDirectory() as cwd:
            database = Path(cwd) / "proactive-replay.sqlite3"
            channel = ReferenceChannel()
            conversation = channel.conversation("sqlite-proactive-replay")
            authorizer = ScopedDeliveryAuthorizer(max_principals=1)
            credential = await authorizer.issue(
                DeliveryPrincipal(
                    principal_id="original-principal",
                    allowed_conversations=(conversation.ref,),
                ),
                credential="rotating-credential",
            )
            intent = DeliveryIntent(
                delivery_id="sqlite-proactive:stable",
                target=ConversationDeliveryTarget(conversation.ref),
                content=(TextContent("one execution"),),
                created_at=datetime.now(UTC),
            )
            first = build_reference_consumer(
                channel=channel,
                store=SQLiteGatewayStore(database),
                delivery_authorizer=authorizer,
            )
            async with first.gateway:
                result = await first.gateway.deliver_proactively(
                    intent,
                    credential=credential,
                )
                self.assertIs(result.state, DeliverySubmissionState.ACCEPTED)
            self.assertTrue(await authorizer.revoke(credential))

            restarted = build_reference_consumer(
                channel=channel,
                store=SQLiteGatewayStore(database),
                delivery_authorizer=authorizer,
            )
            async with restarted.gateway:
                replay = await restarted.gateway.deliver_proactively(
                    intent,
                    credential=credential,
                )
                self.assertTrue(all(item.replayed for item in replay.destinations))
                rotated = await authorizer.issue(
                    DeliveryPrincipal(
                        principal_id="sqlite-rotated-principal",
                        allowed_conversations=(conversation.ref,),
                    ),
                    credential=credential,
                )
                rotated_replay = await restarted.gateway.deliver_proactively(
                    intent,
                    credential=rotated,
                )
                self.assertTrue(all(item.replayed for item in rotated_replay.destinations))
            self.assertEqual(channel.native_send_calls, 1)

    async def test_public_local_path_descriptor_binds_trust_digest_and_send(self) -> None:
        with TemporaryDirectory() as cwd:
            root = Path(cwd) / "trusted"
            root.mkdir()
            inside = root / "payload.txt"
            original = b"trusted bytes"
            outside = Path(cwd) / "outside.txt"
            outside.write_bytes(b"outside bytes")
            inside.write_bytes(original)
            channel = ReferenceChannel(trusted_attachment_root=root)
            conversation = channel.conversation("descriptor-race")
            authorizer = ScopedDeliveryAuthorizer()
            credential = await authorizer.issue(
                DeliveryPrincipal(
                    principal_id="descriptor-principal",
                    allowed_conversations=(conversation.ref,),
                )
            )
            intent = DeliveryIntent(
                delivery_id="descriptor-race",
                target=ConversationDeliveryTarget(conversation.ref),
                content=(
                    AttachmentContent(
                        "descriptor-artifact",
                        "text/plain",
                        LocalPath(str(inside)),
                        size_bytes=len(original),
                        metadata={"sha256": hashlib.sha256(original).hexdigest()},
                    ),
                ),
                created_at=datetime.now(UTC),
            )
            real_open = os.open
            swapped = False

            def racing_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                if path == inside.name and dir_fd is not None and not swapped:
                    inside.unlink()
                    inside.symlink_to(outside)
                    swapped = True
                return descriptor

            consumer = build_reference_consumer(
                channel=channel,
                delivery_authorizer=authorizer,
            )
            with patch(
                "imagent.interaction.media.os.open",
                side_effect=racing_open,
            ):
                async with consumer.gateway:
                    result = await consumer.gateway.deliver_proactively(
                        intent,
                        credential=credential,
                    )
            self.assertTrue(swapped)
            self.assertTrue(inside.is_symlink())
            self.assertIs(
                result.state,
                DeliverySubmissionState.ACCEPTED,
                repr(result),
            )
            self.assertEqual(channel.native_send_calls, 1)

    async def test_public_media_scalars_and_metadata_are_frozen_before_io(self) -> None:
        with TemporaryDirectory() as cwd:
            root = Path(cwd)
            payload = b"x"
            path = root / "payload.txt"
            path.write_bytes(payload)
            conversation_ref = ConversationRef("reference-channel", "metadata-snapshot")
            authorizer = ScopedDeliveryAuthorizer()
            credential = await authorizer.issue(
                DeliveryPrincipal(
                    principal_id="snapshot-principal",
                    allowed_conversations=(conversation_ref,),
                )
            )
            digest = hashlib.sha256(payload).hexdigest()
            for declared_size in (True, 1.0):
                with self.subTest(declared_size=declared_size):
                    channel = ReferenceChannel(trusted_attachment_root=root)
                    consumer = build_reference_consumer(
                        channel=channel,
                        delivery_authorizer=authorizer,
                    )
                    intent = DeliveryIntent(
                        delivery_id=f"bad-size:{declared_size!r}",
                        target=ConversationDeliveryTarget(conversation_ref),
                        content=(
                            AttachmentContent(
                                "bad-size",
                                "text/plain",
                                LocalPath(str(path)),
                                size_bytes=declared_size,  # type: ignore[arg-type]
                                metadata={"sha256": digest},
                            ),
                        ),
                        created_at=datetime.now(UTC),
                    )
                    async with consumer.gateway:
                        with self.assertRaisesRegex(ValueError, "size_bytes"):
                            await consumer.gateway.deliver_proactively(
                                intent,
                                credential=credential,
                            )
                    self.assertEqual(channel.native_send_calls, 0)

            metadata = {"sha256": digest}
            channel = _PausingReferenceChannel(trusted_attachment_root=root)
            consumer = build_reference_consumer(
                channel=channel,
                delivery_authorizer=authorizer,
            )
            intent = DeliveryIntent(
                delivery_id="metadata-snapshot",
                target=ConversationDeliveryTarget(conversation_ref),
                content=(
                    AttachmentContent(
                        "snapshotted",
                        "text/plain",
                        LocalPath(str(path)),
                        size_bytes=1,
                        metadata=metadata,
                    ),
                ),
                created_at=datetime.now(UTC),
            )
            async with consumer.gateway:
                delivery = asyncio.create_task(
                    consumer.gateway.deliver_proactively(intent, credential=credential)
                )
                await channel.entered.wait()
                metadata["sha256"] = "0" * 64
                channel.release.set()
                result = await delivery
                metadata["sha256"] = digest
                replay = await consumer.gateway.deliver_proactively(
                    intent,
                    credential=credential,
                )
            self.assertIs(
                result.state,
                DeliverySubmissionState.ACCEPTED,
                repr(result),
            )
            self.assertTrue(all(item.replayed for item in replay.destinations))
            self.assertEqual(channel.native_send_calls, 1)

    async def test_retryable_artifact_lease_survives_until_explicit_retry(self) -> None:
        with TemporaryDirectory() as cwd:
            ledger = ReferenceArtifactLedger(cwd, max_leases=1)
            attachment = ledger.stage(
                "retryable-artifact",
                b"retry me",
                expected_destinations=1,
            )
            assert isinstance(attachment.source, LocalPath)
            artifact_path = Path(attachment.source.path)
            channel = ReferenceChannel(trusted_attachment_root=cwd)
            conversation = channel.conversation("artifact-retry")
            channel.set_next_delivery_status(
                conversation.ref,
                DeliveryReceiptStatus.RETRYABLE_FAILURE,
            )
            authorizer = ScopedDeliveryAuthorizer()
            credential = await authorizer.issue(
                DeliveryPrincipal(
                    principal_id="artifact-principal",
                    allowed_conversations=(conversation.ref,),
                )
            )
            consumer = build_reference_consumer(
                channel=channel,
                delivery_authorizer=authorizer,
                delivery_outcome_observer=ledger,
                trusted_attachment_root=cwd,
            )
            intent = DeliveryIntent(
                delivery_id="artifact-retry",
                target=ConversationDeliveryTarget(conversation.ref),
                content=(attachment,),
                created_at=datetime.now(UTC),
            )
            async with consumer.gateway:
                first = await consumer.gateway.deliver_proactively(
                    intent,
                    credential=credential,
                )
                await asyncio.sleep(0.05)
                self.assertIs(first.state, DeliverySubmissionState.RETRYABLE)
                self.assertEqual(ledger.active_leases, 1)
                self.assertTrue(artifact_path.exists())
                resumed = await consumer.gateway.deliver_proactively(
                    intent,
                    credential=credential,
                )
                await asyncio.sleep(0.05)
            self.assertIs(resumed.state, DeliverySubmissionState.ACCEPTED)
            self.assertEqual(channel.native_send_calls, 2)
            self.assertEqual(ledger.active_leases, 0)
            self.assertFalse(artifact_path.exists())

    def test_consumer_artifact_sweep_fails_before_unbounded_enumeration(self) -> None:
        with TemporaryDirectory() as cwd:
            root = Path(cwd)
            ledger = ReferenceArtifactLedger(root, max_leases=1)
            orphans = [root / f"artifact-orphan-{index}.txt" for index in range(1_000)]
            for orphan in orphans:
                orphan.touch()
            with self.assertRaisesRegex(RuntimeError, "directory-entry bound"):
                ledger.sweep()
            self.assertTrue(all(orphan.exists() for orphan in orphans))

    def test_sqlite_inspection_rejects_encoded_and_fragmented_authority_state(self) -> None:
        marker = "authority-owned-transcript-marker"
        cases: tuple[tuple[str, tuple[object, ...]], ...] = (
            ("utf16_blob", (sqlite3.Binary(marker.encode("utf-16-le")),)),
            ("hex_blob", (sqlite3.Binary(marker.encode().hex().encode("ascii")),)),
            ("compressed_blob", (sqlite3.Binary(zlib.compress(marker.encode())),)),
            ("fragmented_text", (marker[:16], marker[16:])),
        )
        for name, payloads in cases:
            with self.subTest(name=name), TemporaryDirectory() as cwd:
                database_path = Path(cwd) / "adversarial.sqlite3"
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute("CREATE TABLE authority_leak (payload)")
                    connection.executemany(
                        "INSERT INTO authority_leak (payload) VALUES (?)",
                        ((payload,) for payload in payloads),
                    )
                    connection.commit()
                finally:
                    connection.close()

                with self.assertRaisesRegex(
                    AssertionError,
                    "authority-owned|schema objects or definitions",
                ):
                    _inspect_sqlite_bridge_state(
                        database_path,
                        forbidden_values=(marker,),
                    )

    def test_fragment_inspection_uses_each_persisted_value_at_most_once(self) -> None:
        self.assertFalse(_assembled_from_fragments("abab", ("ab",)))
        self.assertTrue(_assembled_from_fragments("abab", ("ab", "ab")))
        self.assertTrue(_assembled_from_fragments("abcd", ("cd", "ab")))

    async def test_sqlite_inspection_rejects_current_schema_blob_fragments_and_extra_rows(
        self,
    ) -> None:
        marker = "fragmented-current-schema-marker"
        for case in ("blob_type", "fragmented_rows", "extra_row"):
            with self.subTest(case=case), TemporaryDirectory() as cwd:
                await run_reference_consumer(cwd)
                database_path = Path(cwd) / "reference-gateway.sqlite3"
                connection = sqlite3.connect(database_path)
                try:
                    connection.create_function("imagent_store_maintenance", 0, lambda: 1)
                    connection.create_function("imagent_runtime_gateway_id", 0, lambda: None)
                    connection.create_function("imagent_runtime_owner_token", 0, lambda: None)
                    connection.create_function("imagent_runtime_epoch", 0, lambda: None)
                    if case == "blob_type":
                        connection.execute(
                            "UPDATE idempotency_records SET owner_token = ? "
                            "WHERE rowid = (SELECT MIN(rowid) FROM idempotency_records)",
                            (sqlite3.Binary(b"benign-non-text-value"),),
                        )
                    elif case == "fragmented_rows":
                        connection.executemany(
                            "UPDATE idempotency_records SET owner_token = ? WHERE rowid = ?",
                            (
                                (marker[:16], 1),
                                (marker[16:], 2),
                            ),
                        )
                    else:
                        connection.execute(
                            "INSERT INTO idempotency_records "
                            "(scope, record_key, status, owner_token, updated_at) "
                            "VALUES ('unexpected', 'extra-row', 'completed', NULL, ?)",
                            (datetime.now(UTC).isoformat(),),
                        )
                    connection.commit()
                finally:
                    connection.close()

                with self.assertRaisesRegex(
                    AssertionError,
                    "non-text value|fragmented authority-owned|row cardinality",
                ):
                    _inspect_sqlite_bridge_state(
                        database_path,
                        forbidden_values=(marker,),
                    )

    async def test_sqlite_inspection_rejects_extra_or_changed_schema_objects(self) -> None:
        cases = ("index", "view", "trigger", "index_definition", "trigger_definition")
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as cwd:
                await run_reference_consumer(cwd)
                database_path = Path(cwd) / "reference-gateway.sqlite3"
                connection = sqlite3.connect(database_path)
                try:
                    if case == "index":
                        connection.execute(
                            "CREATE INDEX unexpected_index ON idempotency_records(status)"
                        )
                    elif case == "view":
                        connection.execute(
                            "CREATE VIEW unexpected_view AS SELECT status FROM idempotency_records"
                        )
                    elif case == "trigger":
                        connection.execute(
                            "CREATE TRIGGER unexpected_trigger AFTER UPDATE ON idempotency_records "
                            "BEGIN SELECT 1; END"
                        )
                    elif case == "index_definition":
                        connection.execute("DROP INDEX delivery_destinations_root")
                        connection.execute(
                            "CREATE INDEX delivery_destinations_root "
                            "ON delivery_submission_destinations(state)"
                        )
                    else:
                        trigger = "imagent_runtime_fence_idempotency_records_insert"
                        connection.execute(f"DROP TRIGGER {trigger}")
                        connection.execute(
                            f"CREATE TRIGGER {trigger} BEFORE INSERT ON idempotency_records "
                            "BEGIN SELECT 1; END"
                        )
                    connection.commit()
                finally:
                    connection.close()

                with self.assertRaisesRegex(
                    AssertionError,
                    "schema objects or definitions",
                ):
                    _inspect_sqlite_bridge_state(
                        database_path,
                        forbidden_values=("not-present",),
                    )

    async def test_sqlite_inspection_includes_uncheckpointed_wal_schema_and_values(self) -> None:
        for case in ("unexpected_table", "blob_value"):
            with self.subTest(case=case), TemporaryDirectory() as cwd:
                await run_reference_consumer(cwd)
                database_path = Path(cwd) / "reference-gateway.sqlite3"
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("PRAGMA wal_autocheckpoint = 0")
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    if case == "unexpected_table":
                        connection.execute("CREATE TABLE unexpected_wal_object(payload TEXT)")
                        connection.execute(
                            "INSERT INTO unexpected_wal_object(payload) VALUES ('benign')"
                        )
                    else:
                        connection.create_function("imagent_store_maintenance", 0, lambda: 1)
                        connection.create_function("imagent_runtime_gateway_id", 0, lambda: None)
                        connection.create_function("imagent_runtime_owner_token", 0, lambda: None)
                        connection.create_function("imagent_runtime_epoch", 0, lambda: None)
                        connection.execute(
                            "UPDATE idempotency_records SET owner_token = ? "
                            "WHERE rowid = (SELECT MIN(rowid) FROM idempotency_records)",
                            (sqlite3.Binary(b"benign-non-text-value"),),
                        )
                    connection.commit()
                    self.assertGreater(Path(f"{database_path}-wal").stat().st_size, 0)

                    with self.assertRaisesRegex(
                        AssertionError,
                        "schema objects or definitions|non-text value",
                    ):
                        _inspect_sqlite_bridge_state(
                            database_path,
                            forbidden_values=("not-present",),
                        )
                finally:
                    connection.close()

    def test_sqlite_file_inspection_rejects_sidecar_only_encoded_state(self) -> None:
        marker = "sidecar-only-native-payload-marker"
        with TemporaryDirectory() as cwd:
            database_path = Path(cwd) / "sidecar.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("CREATE TABLE benign (value TEXT)")
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute(
                    "INSERT INTO benign (value) VALUES (?)",
                    (marker.encode().hex(),),
                )
                connection.commit()
                self.assertTrue(Path(f"{database_path}-wal").is_file())
                with self.assertRaisesRegex(AssertionError, "authority-owned"):
                    _inspect_sqlite_files(
                        database_path,
                        forbidden_values=(marker,),
                    )
            finally:
                connection.close()

    def test_sqlite_file_inspection_rejects_growth_during_bounded_read(self) -> None:
        with TemporaryDirectory() as cwd:
            database_path = Path(cwd) / "growing.sqlite3"
            database_path.write_bytes(b"SQLite format 3\x00")
            original_read = os.read
            mutated = False

            def grow_after_read(descriptor: int, size: int) -> bytes:
                nonlocal mutated
                chunk = original_read(descriptor, size)
                if not mutated:
                    mutated = True
                    with database_path.open("ab") as stream:
                        stream.write(b"x")
                return chunk

            with (
                patch(
                    "examples.reference_consumer.main.os.read",
                    side_effect=grow_after_read,
                ),
                self.assertRaisesRegex(AssertionError, "changed during bounded read"),
            ):
                _inspect_sqlite_files(
                    database_path,
                    forbidden_values=("not-present",),
                )

    def test_sqlite_file_inspection_rejects_sidecar_appearance(self) -> None:
        with TemporaryDirectory() as cwd:
            database_path = Path(cwd) / "appearing.sqlite3"
            database_path.write_bytes(b"SQLite format 3\x00")
            sidecar_path = Path(f"{database_path}-wal")
            original_open = os.open
            created = False

            def create_sidecar_before_open(path: os.PathLike[str] | str, flags: int) -> int:
                nonlocal created
                if not created:
                    created = True
                    sidecar_path.write_bytes(b"new-sidecar")
                return original_open(path, flags)

            with (
                patch(
                    "examples.reference_consumer.main.os.open",
                    side_effect=create_sidecar_before_open,
                ),
                self.assertRaisesRegex(AssertionError, "sidecar set or identity changed"),
            ):
                _inspect_sqlite_files(
                    database_path,
                    forbidden_values=("not-present",),
                )

    def test_sqlite_file_inspection_rejects_sidecar_disappearance(self) -> None:
        with TemporaryDirectory() as cwd:
            database_path = Path(cwd) / "disappearing.sqlite3"
            database_path.write_bytes(b"SQLite format 3\x00")
            sidecar_path = Path(f"{database_path}-wal")
            sidecar_path.write_bytes(b"existing-sidecar")
            original_open = os.open
            removed = False

            def remove_sidecar_before_open(path: os.PathLike[str] | str, flags: int) -> int:
                nonlocal removed
                if not removed:
                    removed = True
                    sidecar_path.unlink()
                return original_open(path, flags)

            with (
                patch(
                    "examples.reference_consumer.main.os.open",
                    side_effect=remove_sidecar_before_open,
                ),
                self.assertRaisesRegex(AssertionError, "path changed before bounded read"),
            ):
                _inspect_sqlite_files(
                    database_path,
                    forbidden_values=("not-present",),
                )

    def test_sqlite_file_inspection_rejects_path_replacement_after_final_stat(self) -> None:
        marker = "replacement-native-payload-marker"
        with TemporaryDirectory() as cwd:
            database_path = Path(cwd) / "replaced.sqlite3"
            database_path.write_bytes(b"SQLite format 3\x00")
            replacement_path = Path(cwd) / "replacement"
            original_fstat = os.fstat
            calls = 0

            def replace_after_final_stat(descriptor: int) -> os.stat_result:
                nonlocal calls
                result = original_fstat(descriptor)
                calls += 1
                if calls == 2:
                    replacement_path.write_bytes(marker.encode())
                    replacement_path.replace(database_path)
                return result

            with (
                patch(
                    "examples.reference_consumer.main.os.fstat",
                    side_effect=replace_after_final_stat,
                ),
                self.assertRaisesRegex(AssertionError, "path was replaced"),
            ):
                _inspect_sqlite_files(
                    database_path,
                    forbidden_values=(marker,),
                )

    def test_sqlite_file_inspection_rejects_non_file_sidecar(self) -> None:
        with TemporaryDirectory() as cwd:
            database_path = Path(cwd) / "non-file.sqlite3"
            database_path.write_bytes(b"SQLite format 3\x00")
            Path(f"{database_path}-wal").mkdir()

            with self.assertRaisesRegex(AssertionError, "not a regular file"):
                _inspect_sqlite_files(
                    database_path,
                    forbidden_values=("not-present",),
                )

    async def test_local_registry_is_frozen_and_rejects_duplicate_registration(self) -> None:
        consumer = build_reference_consumer()

        self.assertIsInstance(consumer.registry, CommandRegistry)
        self.assertTrue(consumer.registry.frozen)

        async def late_command(invocation, actions) -> CommandResult:
            del invocation, actions
            return CommandResult.text("late")

        with self.assertRaises(CommandRegistryFrozenError):
            consumer.registry.register(CommandDefinition(name="late", handler=late_command))

        registry = CommandRegistry()
        registry.register(CommandDefinition(name="duplicate", handler=late_command))
        with self.assertRaises(ValueError):
            registry.register(CommandDefinition(name="duplicate", handler=late_command))

    async def test_unfrozen_registry_fails_before_gateway_accepts_input(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _CountingMemoryGatewayStore()
        registry = CommandRegistry()

        async def retry_command(invocation, actions) -> CommandResult:
            del invocation, actions
            return CommandResult.text("retry")

        registry.register(CommandDefinition(name="retry", handler=retry_command))
        gateway = Gateway(
            gateway_id="reference-unfrozen",
            channels=[channel],
            applications=[application],
            store=store,
            controller=registry,
        )

        with self.assertRaisesRegex(ValueError, "frozen"):
            await gateway.start()
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertFalse(gateway.running)
        self.assertEqual(store.acquire_count, 0)

        registry.freeze()
        await gateway.start()
        self.assertTrue(gateway.running)
        self.assertEqual(store.acquire_count, 1)
        await gateway.stop()
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_gateway_acquires_one_coherent_store_and_closes_it_once(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-coherent-store",
            channels=[channel],
            applications=[application],
            store=store,
        )

        with self.assertRaisesRegex(RuntimeError, "running"):
            gateway.actions(
                channel.conversation("before-start").ref,
                actor="reference-user",
            )
        async with gateway:
            self.assertTrue(gateway.running)
            self.assertEqual(store.acquire_count, 1)
        self.assertFalse(gateway.running)
        self.assertEqual(store.acquire_count, 1)
        self.assertEqual(store.close_count, 1)

    async def test_scoped_create_and_bind_activates_observation_before_input(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-action-route")
        gateway = Gateway(
            gateway_id="reference-action-route",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:action-route:project",
                )
                project_ref = project.value.ref if isinstance(project, Succeeded) else None
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)
                thread = await actions.create_and_bind_thread(
                    project_ref,
                    action_id="reference:action-route:thread",
                )
                thread_ref = thread.value.ref if isinstance(thread, Succeeded) else None
                self.assertIsInstance(thread_ref, ThreadRef)
                assert isinstance(thread_ref, ThreadRef)
                self.assertEqual(application.active_observation_workers(thread_ref), 1)

                await application.send_input(
                    thread_ref,
                    AgentInput(
                        client_message_id="reference:action-route:native-output",
                        content=(TextContent("native-output-without-inbound"),),
                    ),
                )
                delivered = await conversation.wait_for_text(
                    "Neutral response: native-output-without-inbound"
                )
                self.assertEqual(len(delivered), 1)
                self.assertEqual(application.subscription_calls(thread_ref), 1)

    async def test_scoped_workflow_hands_one_worker_slot_to_the_new_thread(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-workflow-handoff")
        gateway = Gateway(
            gateway_id="reference-workflow-handoff",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            limits=GatewayLimits(projection_max_active_threads=1),
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:workflow-handoff:project",
                )
                self.assertIsInstance(project, Succeeded)
                assert isinstance(project, Succeeded)
                project_ref = project.value.ref
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)

                first = await actions.create_and_bind_thread(
                    project_ref,
                    action_id="reference:workflow-handoff:first",
                )
                second = await actions.create_and_bind_thread(
                    project_ref,
                    action_id="reference:workflow-handoff:second",
                )
                self.assertIsInstance(first, Succeeded)
                self.assertIsInstance(second, Succeeded)
                assert isinstance(first, Succeeded)
                assert isinstance(second, Succeeded)
                first_ref = first.value.ref
                second_ref = second.value.ref
                self.assertIsInstance(first_ref, ThreadRef)
                self.assertIsInstance(second_ref, ThreadRef)
                assert isinstance(first_ref, ThreadRef)
                assert isinstance(second_ref, ThreadRef)

                self.assertEqual(application.active_observation_workers(first_ref), 0)
                self.assertEqual(application.active_observation_workers(second_ref), 1)
                binding = await actions.get_binding()
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(binding.thread_ref, second_ref)

    async def test_cancellation_after_bind_commit_finishes_worker_handoff(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-cancel-handoff")
        gateway = Gateway(
            gateway_id="reference-cancel-handoff",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            limits=GatewayLimits(projection_max_active_threads=1),
        )

        reconciliation_started = asyncio.Event()
        allow_reconciliation = asyncio.Event()
        reconciliation_finished = asyncio.Event()
        block_reconciliation = asyncio.Event()
        original_reconcile = ThreadProjectionRuntime.reconcile_action_route

        async def blocking_reconcile(
            runtime: ThreadProjectionRuntime,
            route_id,
            action_lease=None,
        ):
            if block_reconciliation.is_set():
                reconciliation_started.set()
                await allow_reconciliation.wait()
            result = await original_reconcile(runtime, route_id, action_lease)
            if block_reconciliation.is_set():
                reconciliation_finished.set()
            return result

        with (
            TemporaryDirectory() as cwd,
            patch.object(
                ThreadProjectionRuntime,
                "reconcile_action_route",
                blocking_reconcile,
            ),
        ):
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:cancel-handoff:project",
                )
                self.assertIsInstance(project, Succeeded)
                assert isinstance(project, Succeeded)
                project_ref = project.value.ref
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)
                first = await actions.create_and_bind_thread(
                    project_ref,
                    action_id="reference:cancel-handoff:first",
                )
                self.assertIsInstance(first, Succeeded)
                assert isinstance(first, Succeeded)
                first_ref = first.value.ref
                self.assertIsInstance(first_ref, ThreadRef)
                assert isinstance(first_ref, ThreadRef)

                block_reconciliation.set()
                bind = asyncio.create_task(
                    actions.create_and_bind_thread(
                        project_ref,
                        action_id="reference:cancel-handoff:second",
                    )
                )
                await asyncio.wait_for(reconciliation_started.wait(), timeout=1.0)
                bind.cancel()
                await asyncio.sleep(0)
                self.assertFalse(bind.done())
                allow_reconciliation.set()
                result = await asyncio.wait_for(bind, timeout=1.0)
                self.assertIsInstance(result, Succeeded)

                self.assertTrue(reconciliation_finished.is_set())
                binding = await actions.get_binding()
                self.assertIsNotNone(binding)
                assert binding is not None
                second_ref = binding.thread_ref
                self.assertIsNotNone(second_ref)
                assert second_ref is not None
                self.assertNotEqual(second_ref, first_ref)
                self.assertEqual(application.active_observation_workers(first_ref), 0)
                self.assertEqual(application.active_observation_workers(second_ref), 1)

    async def test_scoped_observe_activates_an_unbound_remembered_route(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-observe-route")
        gateway = Gateway(
            gateway_id="reference-observe-route",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:observe-route:project",
                )
                project_ref = project.value.ref if isinstance(project, Succeeded) else None
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)
                thread = await actions.create_thread(
                    project_ref,
                    action_id="reference:observe-route:thread",
                )
                thread_ref = thread.value.ref if isinstance(thread, Succeeded) else None
                self.assertIsInstance(thread_ref, ThreadRef)
                assert isinstance(thread_ref, ThreadRef)
                observed = await actions.observe_thread(
                    thread_ref,
                    action_id="reference:observe-route:observe",
                )
                self.assertIsInstance(observed, Succeeded)
                self.assertEqual(application.active_observation_workers(thread_ref), 1)

                await application.send_input(
                    thread_ref,
                    AgentInput(
                        client_message_id="reference:observe-route:native-output",
                        content=(TextContent("observed-without-binding"),),
                    ),
                )
                delivered = await conversation.wait_for_text(
                    "Neutral response: observed-without-binding"
                )
                self.assertEqual(len(delivered), 1)
                self.assertEqual(application.subscription_calls(thread_ref), 1)

    async def test_terminal_bind_replay_ignores_current_worker_capacity(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-terminal-replay")
        gateway = Gateway(
            gateway_id="reference-terminal-replay",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            limits=GatewayLimits(projection_max_active_threads=1),
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:terminal-replay:project",
                )
                self.assertIsInstance(project, Succeeded)
                assert isinstance(project, Succeeded)
                project_ref = project.value.ref
                self.assertIsInstance(project_ref, ProjectRef)
                assert isinstance(project_ref, ProjectRef)
                first = await actions.create_thread(
                    project_ref,
                    action_id="reference:terminal-replay:first-thread",
                )
                second = await actions.create_thread(
                    project_ref,
                    action_id="reference:terminal-replay:second-thread",
                )
                self.assertIsInstance(first, Succeeded)
                self.assertIsInstance(second, Succeeded)
                assert isinstance(first, Succeeded)
                assert isinstance(second, Succeeded)
                first_ref = first.value.ref
                second_ref = second.value.ref
                self.assertIsInstance(first_ref, ThreadRef)
                self.assertIsInstance(second_ref, ThreadRef)
                assert isinstance(first_ref, ThreadRef)
                assert isinstance(second_ref, ThreadRef)

                first_binding = await actions.bind_thread(
                    first_ref,
                    action_id="reference:terminal-replay:first-bind",
                    expected_generation=project.value.binding_generation,
                )
                self.assertIsInstance(first_binding, Succeeded)
                assert isinstance(first_binding, Succeeded)
                cleared = await actions.clear_thread(
                    action_id="reference:terminal-replay:clear",
                    expected_generation=first_binding.value.binding_generation,
                )
                self.assertIsInstance(cleared, Succeeded)
                assert isinstance(cleared, Succeeded)
                second_binding = await actions.bind_thread(
                    second_ref,
                    action_id="reference:terminal-replay:second-bind",
                    expected_generation=cleared.value.binding_generation,
                )
                self.assertIsInstance(second_binding, Succeeded)
                self.assertEqual(application.active_observation_workers(first_ref), 0)
                self.assertEqual(application.active_observation_workers(second_ref), 1)

                replay = await actions.bind_thread(
                    first_ref,
                    action_id="reference:terminal-replay:first-bind",
                    expected_generation=project.value.binding_generation,
                )
                self.assertEqual(replay, first_binding)
                current = await actions.get_binding()
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.thread_ref, second_ref)
                self.assertEqual(application.active_observation_workers(first_ref), 0)
                self.assertEqual(application.active_observation_workers(second_ref), 1)

    async def test_invalid_limit_fails_before_store_acquisition(self) -> None:
        store = _CountingMemoryGatewayStore()
        with self.assertRaisesRegex(ValueError, "startup_buffer_max_pending"):
            Gateway(
                gateway_id="reference-invalid-limit",
                channels=[ReferenceChannel()],
                applications=[ReferenceApplication()],
                store=store,
                limits=GatewayLimits(startup_buffer_max_pending=0),
            )
        self.assertEqual(store.acquire_count, 0)
        self.assertEqual(store.close_count, 0)

    async def test_post_acquisition_runtime_construction_failure_closes_store(self) -> None:
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-construction-failure",
            channels=[ReferenceChannel()],
            applications=[ReferenceApplication()],
            store=store,
        )

        with patch(
            "imagent.gateway.runtime._GatewayRuntime",
            side_effect=RuntimeError("simulated runtime construction failure"),
        ):
            with self.assertRaises(GatewayLifecycleFailure) as raised:
                await gateway.start()
        self.assertNotIn("construction failure", str(raised.exception))
        evidence = repr(
            (
                raised.exception,
                vars(raised.exception),
                raised.exception.__cause__,
                raised.exception.__context__,
                gateway._terminal_error,
            )
        )
        self.assertNotIn("simulated runtime construction failure", evidence)
        self.assertIsNone(raised.exception.__context__)
        self.assertFalse(gateway.running)
        self.assertEqual(store.acquire_count, 1)
        self.assertEqual(store.close_count, 1)
        with self.assertRaisesRegex(RuntimeError, "cannot restart"):
            await gateway.start()

    async def test_post_acquisition_workspace_failure_has_no_raw_exception_graph(self) -> None:
        store = _WorkspaceIdentityFailureStore()
        gateway = Gateway(
            gateway_id="reference-workspace-failure",
            channels=[ReferenceChannel()],
            applications=[ReferenceApplication()],
            store=store,
        )

        with self.assertRaises(GatewayLifecycleFailure) as raised:
            await gateway.start()

        evidence = repr(
            (
                raised.exception,
                vars(raised.exception),
                raised.exception.__cause__,
                raised.exception.__context__,
                gateway._terminal_error,
            )
        )
        self.assertNotIn("RAW_SECRET_AFTER_ACQUISITION", evidence)
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(store.close_count, 1)

    async def test_partial_start_cleanup_continues_after_channel_stop_failure(self) -> None:
        first = _CleanupFailingChannel(
            "reference-cleanup-first",
            fail_stop_before_cleanup=True,
        )
        second = _CleanupFailingChannel(
            "reference-cleanup-second",
            fail_start=True,
            fail_stop_before_cleanup=True,
        )
        application = _CleanupFailingApplication()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-partial-cleanup",
            channels=[first, second],
            applications=[application],
            store=store,
        )

        with self.assertRaises(GatewayLifecycleFailure) as caught:
            await gateway.start()
        self.assertNotIn("start failed", str(caught.exception))

        self.assertTrue(first.started)
        self.assertTrue(second.started)
        self.assertFalse(application.started)
        self.assertEqual(first.stop_count, 1)
        self.assertEqual(second.stop_count, 1)
        self.assertEqual(application.stop_count, 1)
        self.assertEqual(store.close_count, 1)
        self.assertTrue(
            any("reference-cleanup-first" in note for note in caught.exception.__notes__)
        )
        self.assertTrue(
            any("reference-cleanup-second" in note for note in caught.exception.__notes__)
        )

    async def test_partial_application_start_failure_cleans_current_and_prior_once(
        self,
    ) -> None:
        first = _CleanupFailingApplication("reference-application-first")
        second = _CleanupFailingApplication(
            "reference-application-second",
            fail_start=True,
            fail_stop=True,
        )
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-partial-application-cleanup",
            channels=[ReferenceChannel()],
            applications=[first, second],
            store=store,
        )

        with self.assertRaises(GatewayLifecycleFailure) as caught:
            await gateway.start()
        self.assertNotIn("start failed", str(caught.exception))

        self.assertFalse(first.started)
        self.assertFalse(second.started)
        self.assertEqual(first.stop_count, 1)
        self.assertEqual(second.stop_count, 1)
        self.assertEqual(store.close_count, 1)
        self.assertTrue(
            any(
                "Application 'reference-application-second'" in note
                for note in caught.exception.__notes__
            )
        )

    async def test_normal_stop_continues_after_channel_controller_and_application_failures(
        self,
    ) -> None:
        channel = _CleanupFailingChannel(
            "reference-normal-cleanup",
            fail_stop_after_cleanup=True,
        )
        application = _CleanupFailingApplication(fail_stop=True)
        registry = _CleanupFailingRegistry()

        async def status(invocation, actions) -> CommandResult:
            del invocation, actions
            return CommandResult.text("status")

        registry.register(CommandDefinition(name="status", handler=status))
        registry.freeze()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-normal-cleanup",
            channels=[channel],
            applications=[application],
            store=store,
            controller=registry,
        )
        await gateway.start()

        with self.assertRaises(GatewayLifecycleFailure) as caught:
            await gateway.stop()
        self.assertNotIn("stop failed", str(caught.exception))

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(channel.stop_count, 1)
        self.assertEqual(registry.close_count, 1)
        self.assertEqual(application.stop_count, 1)
        self.assertEqual(store.close_count, 1)
        notes = "\n".join(caught.exception.__notes__)
        self.assertIn("Gateway cleanup also failed: Controller", notes)
        self.assertIn("Application", notes)

    async def test_cleanup_notes_and_logs_are_bounded_for_large_owner_errors(self) -> None:
        channel = _CleanupFailingChannel(
            "reference-bounded-cleanup",
            fail_stop_after_cleanup=True,
        )
        application = _HugeCleanupApplication()
        store = _HugeCloseFailureStore()
        gateway = Gateway(
            gateway_id="reference-bounded-cleanup",
            channels=[channel],
            applications=[application],
            store=store,
        )
        await gateway.start()

        with self.assertLogs("imagent.gateway", level="ERROR") as logged:
            with self.assertRaises(GatewayLifecycleFailure) as caught:
                await gateway.stop()

        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(channel.stop_count, 1)
        self.assertEqual(application.stop_count, 1)
        self.assertEqual(store.close_count, 1)
        self.assertTrue(caught.exception.__notes__)
        self.assertTrue(all(len(note) <= 450 for note in caught.exception.__notes__))
        self.assertTrue(all(len(entry) <= 500 for entry in logged.output))
        evidence = "\n".join((*caught.exception.__notes__, *logged.output))
        self.assertNotIn("x" * 193, evidence)
        self.assertNotIn("\x1b", evidence)
        self.assertNotIn("\u202e", evidence)
        self.assertNotIn("secret", evidence)

    async def test_inner_gateway_projects_first_cleanup_failure(self) -> None:
        channel = _CleanupFailingChannel(
            "reference-inner-first-cleanup",
            fail_stop_after_cleanup=True,
            stop_error_detail=_UNSAFE_HUGE_CLEANUP_DETAIL,
        )
        application = _CleanupFailingApplication("reference-inner-application")
        gateway = _GatewayRuntime(
            channels=[channel],
            applications=[application],
            repositories=_GatewayRuntimeDependencies(bindings=InMemoryBindingRepository()),
        )
        await gateway.start()

        with self.assertLogs("imagent.gateway", level="ERROR") as logged:
            with self.assertRaisesRegex(
                RuntimeError,
                "reference-inner-first-cleanup",
            ) as caught:
                await gateway.stop()

        error = caught.exception
        notes = getattr(error, "__notes__", ())
        evidence = "\n".join((str(error), repr(error), *notes, *logged.output))
        self.assertLessEqual(len(str(error)), 450)
        self.assertNotIn("x" * 193, evidence)
        self.assertNotIn("\x1b", evidence)
        self.assertNotIn("\u202e", evidence)
        self.assertIsInstance(error.__cause__, RuntimeError)
        self.assertFalse(hasattr(error, "original_error"))
        self.assertEqual(error.original_type, "RuntimeError")  # type: ignore[attr-defined]
        self.assertLessEqual(len(str(error.__cause__)), 450)
        self.assertIsNone(error.__context__)
        encoded = pickle.dumps(error)
        self.assertLessEqual(len(encoded), 2_048)
        self.assertNotIn(b"x" * 193, encoded)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(channel.stop_count, 1)
        self.assertEqual(application.stop_count, 1)

    async def test_public_gateway_projects_first_outer_cleanup_failure(self) -> None:
        channel = _CleanupFailingChannel("reference-outer-channel")
        application = _CleanupFailingApplication("reference-outer-application")
        store = _HugeCloseFailureStore()
        gateway = Gateway(
            gateway_id="reference-outer-first-cleanup",
            channels=[channel],
            applications=[application],
            store=store,
        )
        await gateway.start()

        with self.assertRaisesRegex(RuntimeError, "Gateway store") as caught:
            await gateway.stop()

        error = caught.exception
        notes = getattr(error, "__notes__", ())
        evidence = "\n".join((str(error), repr(error), *notes))
        self.assertLessEqual(len(str(error)), 450)
        self.assertNotIn("x" * 193, evidence)
        self.assertNotIn("\x1b", evidence)
        self.assertNotIn("\u202e", evidence)
        self.assertIsInstance(error.__cause__, RuntimeError)
        self.assertFalse(hasattr(error, "original_error"))
        self.assertEqual(error.original_type, "RuntimeError")  # type: ignore[attr-defined]
        self.assertLessEqual(len(str(error.__cause__)), 450)
        self.assertIsNone(error.__context__)
        encoded = pickle.dumps(error)
        self.assertLessEqual(len(encoded), 2_048)
        self.assertNotIn(b"x" * 193, encoded)
        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(channel.stop_count, 1)
        self.assertEqual(application.stop_count, 1)
        self.assertEqual(store.close_count, 1)

    async def test_public_startup_projects_huge_partial_channel_failure(self) -> None:
        channel = _CleanupFailingChannel(
            "reference-huge-startup",
            fail_start=True,
            start_error_detail=_UNSAFE_HUGE_CLEANUP_DETAIL,
        )
        application = _CleanupFailingApplication("reference-huge-startup-application")
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-huge-startup",
            channels=[channel],
            applications=[application],
            store=store,
        )

        with self.assertRaisesRegex(RuntimeError, "Gateway lifecycle failure") as caught:
            await gateway.start()

        error = caught.exception
        notes = getattr(error, "__notes__", ())
        evidence = "\n".join((str(error), repr(error), *notes))
        self.assertLessEqual(len(str(error)), 450)
        self.assertNotIn("x" * 193, evidence)
        self.assertNotIn("\x1b", evidence)
        self.assertNotIn("\u202e", evidence)
        self.assertIsInstance(error.__cause__, RuntimeError)
        self.assertFalse(hasattr(error, "original_error"))
        self.assertEqual(error.original_type, "RuntimeError")  # type: ignore[attr-defined]
        self.assertLessEqual(len(str(error.__cause__)), 450)
        self.assertIsNone(error.__context__)
        encoded = pickle.dumps(error)
        self.assertLessEqual(len(encoded), 2_048)
        self.assertNotIn(b"x" * 193, encoded)
        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(channel.stop_count, 1)
        self.assertEqual(application.stop_count, 1)
        self.assertEqual(store.close_count, 1)

    def test_lifecycle_failure_object_graph_and_pickle_are_bounded(self) -> None:
        raw = RuntimeError(_UNSAFE_HUGE_CLEANUP_DETAIL)
        public = GatewayLifecycleFailure("Gateway store", raw)

        encoded = pickle.dumps(public)
        restored = pickle.loads(encoded)
        evidence = "\n".join(
            (
                str(public),
                repr(public),
                repr(vars(public)),
                str(public.__cause__),
                repr(vars(public.__cause__)),
                str(restored),
                repr(vars(restored)),
                str(restored.__cause__),
            )
        )

        self.assertLessEqual(len(str(public)), 450)
        self.assertLessEqual(len(encoded), 2_048)
        self.assertNotIn(b"x" * 193, encoded)
        self.assertNotIn(b"SECRET", encoded)
        self.assertNotIn("x" * 193, evidence)
        self.assertNotIn("\x1b", evidence)
        self.assertNotIn("\u202e", evidence)
        self.assertFalse(hasattr(public, "original_error"))
        self.assertFalse(hasattr(restored, "original_error"))
        self.assertIsNot(public.__cause__, raw)
        self.assertIsNone(public.__context__)
        self.assertIsNone(restored.__context__)
        self.assertEqual(restored.original_type, "RuntimeError")

    def test_short_hostile_lifecycle_error_is_always_projected(self) -> None:
        raw = _HostileShortLifecycleError()
        public = _public_lifecycle_error(raw, "Channel")

        self.assertIsInstance(public, GatewayLifecycleFailure)
        self.assertIsNot(public, raw)
        self.assertFalse(hasattr(public, "secret"))
        self.assertFalse(hasattr(public, "nested"))
        self.assertIsNone(public.__context__)
        self.assertIsNot(public.__cause__, raw.__cause__)
        encoded = pickle.dumps(public)
        restored = pickle.loads(encoded)
        evidence = "\n".join(
            (
                str(public),
                repr(public),
                repr(vars(public)),
                str(public.__cause__),
                repr(vars(public.__cause__)),
                str(restored),
                repr(vars(restored)),
            )
        )
        for secret in (
            "SECRET_PAYLOAD",
            "NESTED_SECRET_PAYLOAD",
            "CAUSE_SECRET_PAYLOAD",
            "REPR_SECRET_PAYLOAD",
            "raw-argument-secret",
        ):
            self.assertNotIn(secret, evidence)
            self.assertNotIn(secret.encode(), encoded)
        self.assertLessEqual(len(encoded), 2_048)

    def test_lifecycle_sentinel_subtype_impostors_are_projected(self) -> None:
        impostors = (
            _HostileNotRunning("benign"),
            _HostileOverflow(max_pending=1),
            _HostileLifecycleFailure("Channel", RuntimeError("benign")),
        )
        secrets = (
            "NOT_RUNNING_SUBTYPE_SECRET",
            "OVERFLOW_SUBTYPE_SECRET",
            "FAILURE_SUBTYPE_SECRET",
        )

        for raw, secret in zip(impostors, secrets, strict=True):
            raw.subtype_secret = secret  # type: ignore[attr-defined]
            public = _public_lifecycle_error(raw, "Channel")

            self.assertIs(type(public), GatewayLifecycleFailure)
            self.assertIsNot(public, raw)
            self.assertNotIn("subtype_secret", vars(public))
            encoded = pickle.dumps(public)
            self.assertNotIn(secret, str(public))
            self.assertNotIn(secret.encode(), encoded)
            self.assertLessEqual(len(encoded), 2_048)

        authoritative = (
            GatewayNotRunning("not running"),
            GatewayStartupOverflow(max_pending=1),
            GatewayLifecycleFailure("Channel", RuntimeError("failed")),
        )
        for sentinel in authoritative:
            self.assertIs(_public_lifecycle_error(sentinel, "Channel"), sentinel)

    async def test_hostile_sentinel_subtypes_are_projected_on_start_and_stop(self) -> None:
        class HostileStartupChannel(ReferenceChannel):
            async def start(
                self,
                on_message: MessageHandler,
                on_admission: InboundAdmissionHandler | None = None,
            ) -> None:
                await super().start(on_message, on_admission)
                error = _HostileNotRunning("benign startup")
                error.secret = "STARTUP_SUBTYPE_SECRET"  # type: ignore[attr-defined]
                raise error

        class HostileStopChannel(ReferenceChannel):
            async def stop(self) -> None:
                await super().stop()
                error = _HostileOverflow(max_pending=1)
                error.secret = "STOP_SUBTYPE_SECRET"  # type: ignore[attr-defined]
                raise error

        startup_channel = HostileStartupChannel("hostile-startup-subtype")
        startup_application = ReferenceApplication("hostile-startup-application")
        startup_gateway = _GatewayRuntime(
            channels=[startup_channel],
            applications=[startup_application],
            repositories=_GatewayRuntimeDependencies(bindings=InMemoryBindingRepository()),
        )
        with self.assertRaises(GatewayLifecycleFailure) as startup_raised:
            await startup_gateway.start()

        stop_channel = HostileStopChannel("hostile-stop-subtype")
        stop_application = ReferenceApplication("hostile-stop-application")
        stop_gateway = _GatewayRuntime(
            channels=[stop_channel],
            applications=[stop_application],
            repositories=_GatewayRuntimeDependencies(bindings=InMemoryBindingRepository()),
        )
        await stop_gateway.start()
        with self.assertRaises(GatewayLifecycleFailure) as stop_raised:
            await stop_gateway.stop()

        for error, secret in (
            (startup_raised.exception, "STARTUP_SUBTYPE_SECRET"),
            (stop_raised.exception, "STOP_SUBTYPE_SECRET"),
        ):
            self.assertIs(type(error), GatewayLifecycleFailure)
            self.assertNotIn("secret", vars(error))
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret.encode(), pickle.dumps(error))
        self.assertFalse(startup_channel.started)
        self.assertFalse(startup_application.started)
        self.assertFalse(stop_channel.started)
        self.assertFalse(stop_application.started)

    async def test_startup_claim_release_evidence_is_bounded_and_sanitized(self) -> None:
        channel = _StartupClaimReleaseFailureChannel()
        application = _CleanupFailingApplication("reference-startup-claim-release-application")
        idempotency = _HugeReleaseIdempotencyRepository()
        gateway = _GatewayRuntime(
            channels=[channel],
            applications=[application],
            repositories=_GatewayRuntimeDependencies(
                bindings=InMemoryBindingRepository(),
                idempotency=cast(Any, idempotency),
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "Gateway lifecycle failure") as caught:
            await gateway.start()

        error = caught.exception
        evidence = "\n".join((str(error), repr(error), *getattr(error, "__notes__", ())))
        self.assertLessEqual(len(str(error)), 450)
        self.assertNotIn("x" * 193, evidence)
        self.assertNotIn("\x1b", evidence)
        self.assertNotIn("\u202e", evidence)
        self.assertNotIn(_UNSAFE_HUGE_CLEANUP_DETAIL.encode(), pickle.dumps(error))
        self.assertTrue(any("inbound claim release" in note for note in error.__notes__))
        self.assertEqual(idempotency.release_count, 1)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(application.stop_count, 1)

    async def test_invalid_application_capability_fails_before_memory_or_sqlite_io(
        self,
    ) -> None:
        application = ReferenceApplication()
        summary = application.summary
        malformed_projects = replace(
            summary.capabilities.projects,
            discovery=SupportLevel.FALLBACK,
        )
        application._summary = replace(  # type: ignore[reportPrivateUsage]
            summary,
            capabilities=replace(summary.capabilities, projects=malformed_projects),
        )

        memory = _CountingMemoryGatewayStore()
        with self.assertRaisesRegex(ValueError, "native project discovery"):
            Gateway(
                gateway_id="reference-invalid-capability-memory",
                channels=[ReferenceChannel()],
                applications=[application],
                store=memory,
            )
        self.assertEqual(memory.acquire_count, 0)
        self.assertEqual(memory.close_count, 0)

        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-capability.sqlite3"
            sqlite = _CountingSQLiteGatewayStore(path)
            with self.assertRaisesRegex(ValueError, "native project discovery"):
                Gateway(
                    gateway_id="reference-invalid-capability-sqlite",
                    channels=[ReferenceChannel("reference-sqlite")],
                    applications=[application],
                    store=sqlite,
                )
            self.assertEqual(sqlite.acquire_count, 0)
            self.assertEqual(sqlite.close_count, 0)
            await sqlite.close()

        for owner, malformed_capabilities in (
            (
                "Thread",
                replace(
                    summary.capabilities,
                    threads=replace(
                        summary.capabilities.threads,
                        listing=cast(Any, "bogus"),
                    ),
                ),
            ),
            (
                "runtime",
                replace(
                    summary.capabilities,
                    runtime=replace(
                        summary.capabilities.runtime,
                        history=cast(Any, "bogus"),
                    ),
                ),
            ),
        ):
            with self.subTest(owner=owner):
                malformed = ReferenceApplication(
                    application_instance_id=f"reference-invalid-{owner.lower()}"
                )
                malformed._summary = replace(  # type: ignore[reportPrivateUsage]
                    malformed.summary,
                    capabilities=malformed_capabilities,
                )
                store = _CountingMemoryGatewayStore()
                with self.assertRaisesRegex(ValueError, owner):
                    Gateway(
                        gateway_id=f"reference-invalid-{owner.lower()}",
                        channels=[ReferenceChannel(f"reference-{owner.lower()}")],
                        applications=[malformed],
                        store=store,
                    )
                self.assertEqual(store.acquire_count, 0)
                self.assertEqual(store.close_count, 0)

    async def test_malformed_store_and_acquired_session_fail_explicitly(self) -> None:
        channel_store = _CountingMemoryGatewayStore()
        with self.assertRaisesRegex(TypeError, "instance ID"):
            Gateway(
                gateway_id="reference-malformed-channel",
                channels=[cast(Any, object())],
                applications=[ReferenceApplication()],
                store=channel_store,
            )
        self.assertEqual(channel_store.acquire_count, 0)

        malformed_store = cast(Any, object())
        with self.assertRaisesRegex(TypeError, "max_effect_receipts"):
            Gateway(
                gateway_id="reference-malformed-store",
                channels=[ReferenceChannel()],
                applications=[ReferenceApplication()],
                store=malformed_store,
            )

        store = _MalformedSessionStore()
        gateway = Gateway(
            gateway_id="reference-malformed-session",
            channels=[ReferenceChannel()],
            applications=[ReferenceApplication()],
            store=cast(Any, store),
        )
        with self.assertRaises(GatewayLifecycleFailure) as raised:
            await gateway.start()
        self.assertEqual(raised.exception.original_type, "TypeError")
        self.assertEqual(store.acquire_count, 1)
        self.assertEqual(store.session.close_count, 1)
        self.assertEqual(store.close_count, 1)

    async def test_protocol_stub_session_fails_before_runtime_construction(self) -> None:
        store = _ProtocolStubSessionStore()
        gateway = Gateway(
            gateway_id="reference-protocol-stub-session",
            channels=[ReferenceChannel()],
            applications=[ReferenceApplication()],
            store=cast(Any, store),
        )

        with patch("imagent.gateway.runtime._GatewayRuntime") as runtime_factory:
            with self.assertRaises(GatewayLifecycleFailure) as raised:
                await gateway.start()
        self.assertEqual(raised.exception.original_type, "TypeError")

        runtime_factory.assert_not_called()
        self.assertEqual(store.acquire_count, 1)
        self.assertIsNotNone(store.session)
        assert store.session is not None
        self.assertEqual(store.session.close_count, 1)
        self.assertEqual(store.close_count, 1)

    async def test_malformed_session_cleanup_has_a_hard_deadline(self) -> None:
        store = _CancellationResistantMalformedSessionStore()
        gateway = Gateway(
            gateway_id="reference-resistant-malformed-session",
            channels=[ReferenceChannel()],
            applications=[ReferenceApplication()],
            store=cast(Any, store),
            limits=GatewayLimits(lifecycle_owner_timeout_seconds=0.01),
        )

        with self.assertRaises(GatewayLifecycleFailure):
            await asyncio.wait_for(gateway.start(), timeout=0.1)

        self.assertTrue(store.session.cancelled)
        self.assertEqual(store.session.close_count, 1)
        self.assertEqual(store.close_count, 1)
        store.session.release.set()
        await asyncio.sleep(0)

    async def test_valid_summary_capability_drift_fails_before_runtime_memory_and_sqlite(
        self,
    ) -> None:
        delegates: list[tuple[str, Any]] = [("memory", MemoryGatewayStore())]
        with TemporaryDirectory() as directory:
            delegates.append(("sqlite", SQLiteGatewayStore(Path(directory) / "drift.sqlite3")))
            for name, delegate in delegates:
                with self.subTest(store=name):
                    channel = ReferenceChannel(f"reference-drift-{name}")
                    application = ReferenceApplication(
                        application_instance_id=f"reference-drift-{name}"
                    )
                    store = _DriftingCompositionStore(delegate, channel, application)
                    gateway = Gateway(
                        gateway_id=f"reference-drift-{name}",
                        channels=[channel],
                        applications=[application],
                        store=cast(Any, store),
                    )

                    with patch("imagent.gateway.runtime._GatewayRuntime") as runtime_factory:
                        with self.assertRaises(GatewayLifecycleFailure) as raised:
                            await gateway.start()
                    self.assertEqual(raised.exception.original_type, "ValueError")

                    runtime_factory.assert_not_called()
                    self.assertEqual(store.acquire_count, 1)
                    self.assertEqual(store.close_count, 1)

    async def test_mismatched_or_malformed_acquired_lease_is_closed(self) -> None:
        cases = (
            ({"gateway_id": "another-gateway"}, "another Gateway"),
            ({"owner_token": "another-owner"}, "owner token"),
            ({"epoch": cast(Any, 1.5)}, "positive integer"),
        )
        for index, (changed_fields, _message) in enumerate(cases):
            with self.subTest(changed_fields=changed_fields):
                store = _ChangedLeaseStore(changed_fields)
                gateway = Gateway(
                    gateway_id=f"reference-invalid-lease-{index}",
                    channels=[ReferenceChannel(f"reference-invalid-lease-{index}")],
                    applications=[
                        ReferenceApplication(
                            application_instance_id=f"reference-invalid-lease-{index}"
                        )
                    ],
                    store=store,
                )
                with self.assertRaises(GatewayLifecycleFailure) as raised:
                    await gateway.start()
                self.assertEqual(
                    raised.exception.original_type,
                    "ValueError" if index < 2 else "ContractViolation",
                )
                self.assertEqual(store.acquire_count, 1)
                self.assertIsNotNone(store.session)
                assert store.session is not None
                self.assertEqual(store.session.close_count, 1)
                self.assertEqual(store.close_count, 1)

    async def test_identity_drift_and_collision_fail_before_acquisition(self) -> None:
        first_channel = ReferenceChannel("reference-drift-a")
        second_channel = ReferenceChannel("reference-drift-b")
        first_application = ReferenceApplication(application_instance_id="reference-app-a")
        second_application = ReferenceApplication(application_instance_id="reference-app-b")
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-identity-drift",
            channels=[first_channel, second_channel],
            applications=[first_application, second_application],
            store=store,
        )
        second_channel._channel_instance_id = first_channel.channel_instance_id  # type: ignore[reportPrivateUsage]

        with self.assertRaisesRegex(ValueError, "Channel instance IDs must be unique"):
            await gateway.start()
        self.assertEqual(store.acquire_count, 0)
        self.assertEqual(store.close_count, 0)

        application_store = _CountingMemoryGatewayStore()
        application_gateway = Gateway(
            gateway_id="reference-application-identity-drift",
            channels=[ReferenceChannel("reference-application-drift")],
            applications=[first_application, second_application],
            store=application_store,
        )
        second_application._summary = replace(  # type: ignore[reportPrivateUsage]
            second_application.summary,
            ref=first_application.ref,
        )
        with self.assertRaisesRegex(ValueError, "Application instance IDs must be unique"):
            await application_gateway.start()
        self.assertEqual(application_store.acquire_count, 0)
        self.assertEqual(application_store.close_count, 0)

    async def test_valid_noncolliding_identity_drift_fails_before_acquisition(self) -> None:
        channel = ReferenceChannel("reference-original-channel")
        channel_store = _CountingMemoryGatewayStore()
        channel_gateway = Gateway(
            gateway_id="reference-channel-noncollision-drift",
            channels=[channel],
            applications=[ReferenceApplication()],
            store=channel_store,
        )
        channel._channel_instance_id = "reference-changed-channel"  # type: ignore[reportPrivateUsage]
        with self.assertRaisesRegex(ValueError, "identity changed"):
            await channel_gateway.start()
        self.assertEqual(channel_store.acquire_count, 0)

        application = ReferenceApplication(application_instance_id="reference-original-application")
        application_store = _CountingMemoryGatewayStore()
        application_gateway = Gateway(
            gateway_id="reference-application-noncollision-drift",
            channels=[ReferenceChannel("reference-application-channel")],
            applications=[application],
            store=application_store,
        )
        application._summary = replace(  # type: ignore[reportPrivateUsage]
            application.summary,
            ref=ApplicationRef("reference-changed-application"),
        )
        with self.assertRaisesRegex(ValueError, "identity changed"):
            await application_gateway.start()
        self.assertEqual(application_store.acquire_count, 0)

    async def test_lease_renewal_loss_stops_admission_adapters_and_store(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _FailingRenewMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-lease-loss",
            channels=[channel],
            applications=[application],
            store=store,
        )

        with patch("imagent.gateway.runtime._LEASE_RENEWAL_SECONDS", 0.001):
            await gateway.start()
            store.allow_renew_failure()
            await asyncio.wait_for(store.renew_failed.wait(), timeout=1.0)
            with self.assertRaises(GatewayLifecycleFailure) as raised:
                await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)
            self.assertNotIn("simulated lease renewal loss", str(raised.exception))
            evidence = repr(
                (
                    raised.exception,
                    vars(raised.exception),
                    raised.exception.__cause__,
                    raised.exception.__context__,
                    gateway._terminal_error,
                )
            )
            self.assertNotIn("simulated lease renewal loss", evidence)
            self.assertIsNone(raised.exception.__context__)

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_lease_renewal_loss_racing_stop_closes_every_owner_once(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _FailingRenewMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-lease-loss-stop-race",
            channels=[channel],
            applications=[application],
            store=store,
        )

        with patch("imagent.gateway.runtime._LEASE_RENEWAL_SECONDS", 0.001):
            await gateway.start()
            store.allow_renew_failure()
            await asyncio.wait_for(store.renew_failed.wait(), timeout=1.0)
            explicit_stop = asyncio.create_task(gateway.stop())
            with self.assertRaises(GatewayLifecycleFailure):
                await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)
            await asyncio.wait_for(explicit_stop, timeout=1.0)

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_lease_loss_cancels_blocked_startup_and_rolls_back(self) -> None:
        store = _FailingRenewMemoryGatewayStore()
        channel = _BlockingStartChannel(store.allow_renew_failure)
        application = ReferenceApplication()
        gateway = Gateway(
            gateway_id="reference-startup-lease-loss",
            channels=[channel],
            applications=[application],
            store=store,
        )

        with patch("imagent.gateway.runtime._LEASE_RENEWAL_SECONDS", 0.001):
            startup = asyncio.create_task(gateway.start())
            await asyncio.wait_for(channel.start_blocked.wait(), timeout=1.0)
            await asyncio.wait_for(store.renew_failed.wait(), timeout=1.0)
            with self.assertRaises(GatewayLifecycleFailure):
                await asyncio.wait_for(startup, timeout=1.0)
            with self.assertRaises(GatewayLifecycleFailure):
                await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_stop_cancels_and_joins_blocked_startup_rollback(self) -> None:
        channel = _BlockingStartChannel(lambda: None)
        application = ReferenceApplication()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-stop-during-start",
            channels=[channel],
            applications=[application],
            store=store,
        )

        startup = asyncio.create_task(gateway.start())
        await asyncio.wait_for(channel.start_blocked.wait(), timeout=1.0)
        await asyncio.wait_for(gateway.stop(), timeout=1.0)
        with self.assertRaises(asyncio.CancelledError):
            await startup

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.acquire_count, 1)
        self.assertEqual(store.close_count, 1)
        with self.assertRaisesRegex(RuntimeError, "cannot restart"):
            await gateway.start()

    async def test_concurrent_starts_share_one_serialized_transition(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        store = _BlockingAcquireMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-concurrent-start",
            channels=[channel],
            applications=[application],
            store=store,
        )

        first = asyncio.create_task(gateway.start())
        await asyncio.wait_for(store.first_acquired.wait(), timeout=1.0)
        second = asyncio.create_task(gateway.start())
        await asyncio.sleep(0)
        self.assertFalse(second.done())
        store.allow_first_return.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1.0)

        self.assertTrue(gateway.running)
        self.assertTrue(channel.started)
        self.assertTrue(application.started)
        self.assertEqual(store.acquire_count, 1)
        await gateway.stop()
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)

    async def test_cancelled_async_context_start_rolls_back_once(self) -> None:
        channel = _BlockingStartChannel(lambda: None)
        application = ReferenceApplication()
        store = _CountingMemoryGatewayStore()
        gateway = Gateway(
            gateway_id="reference-cancelled-context-start",
            channels=[channel],
            applications=[application],
            store=store,
        )

        async def enter_context() -> None:
            async with gateway:
                self.fail("a blocked Gateway context must not enter")

        context_task = asyncio.create_task(enter_context())
        await asyncio.wait_for(channel.start_blocked.wait(), timeout=1.0)
        context_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await context_task

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        self.assertFalse(application.started)
        self.assertEqual(store.close_count, 1)
        await gateway.stop()
        self.assertEqual(store.close_count, 1)

    async def test_retained_scoped_actions_are_invalid_after_shutdown(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        gateway = Gateway(
            gateway_id="reference-retained-actions",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
        )
        with TemporaryDirectory() as cwd:
            await gateway.start()
            actions = gateway.actions(
                channel.conversation("retained").ref,
                actor="reference-user",
            )
            application_actions = gateway.application(
                application.ref,
                principal="reference-user",
            )
            self.assertEqual(application_actions.principal, "reference-user")
            self.assertFalse(hasattr(application_actions, "conversation_ref"))
            application_read = await application_actions.get_application()
            self.assertIsInstance(application_read, Succeeded)
            created = await actions.create_project(
                application.ref,
                cwd=cwd,
                action_id="reference:retained:project",
            )
            self.assertIsInstance(created, Succeeded)
            await gateway.stop()

            with self.assertRaisesRegex(RuntimeError, "not active"):
                await actions.list_applications()
            with self.assertRaisesRegex(RuntimeError, "not active"):
                await application_actions.get_application()
            with self.assertRaisesRegex(RuntimeError, "not active"):
                await actions.create_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:retained:project",
                )
        self.assertFalse(application.started)

    async def test_no_controller_treats_slash_text_as_ordinary_agent_input(self) -> None:
        channel = ReferenceChannel()
        application = ReferenceApplication()
        conversation = channel.conversation("conversation-no-controller")
        gateway = Gateway(
            gateway_id="reference-no-controller",
            channels=[channel],
            applications=[application],
            store=MemoryGatewayStore(),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            limits=GatewayLimits(projection_max_active_threads=2),
        )

        with TemporaryDirectory() as cwd:
            async with gateway:
                actions = gateway.actions(
                    conversation.ref,
                    actor=conversation.authenticated_actor,
                )
                project = await actions.create_and_select_project(
                    application.ref,
                    cwd=cwd,
                    action_id="reference:no-controller:project",
                )
                self.assertIsInstance(project, Succeeded)
                assert isinstance(project, Succeeded)
                self.assertIsInstance(project.value.ref, ProjectRef)
                assert isinstance(project.value.ref, ProjectRef)
                thread = await actions.create_and_bind_thread(
                    project.value.ref,
                    action_id="reference:no-controller:thread",
                )
                self.assertIsInstance(thread, Succeeded)
                await conversation.receive_text(
                    message_id="reference:no-controller:message",
                    text="/about",
                )
                delivered = await conversation.wait_for_text("Neutral response: /about")
                self.assertEqual(len(delivered), 1)

        self.assertFalse(channel.started)
        self.assertFalse(application.started)

    def test_example_imports_only_clean_installed_public_sdk_modules(self) -> None:
        root = Path(__file__).resolve().parents[2]
        example = root / "examples" / "reference_consumer"
        forbidden = (
            "imagent.gateway.composition",
            "imagent.gateway.effect_execution",
            "imagent.gateway.persistence",
            "imagent.gateway.runtime",
        )
        for path in sorted(example.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = tuple(
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            )
            with self.subTest(path=path.name):
                self.assertFalse(any(name.startswith(forbidden) for name in imports))
                self.assertNotIn("_GatewayRuntime", path.read_text(encoding="utf-8"))
                self.assertNotIn("_GatewayRuntimeDependencies", path.read_text(encoding="utf-8"))

        self.assertTrue(build_command_registry(ReferenceStatusService()).frozen)

    async def test_channel_capacity_fails_before_append(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceChannel(max_outbound_records=0)
        with self.assertRaises(ValueError):
            ReferenceChannel(max_outbound_records=True)

        channel = ReferenceChannel(max_outbound_records=1)

        async def on_message(message) -> None:
            del message

        await channel.start(on_message)
        message = OutboundMessage(
            delivery_id="delivery-1",
            conversation_ref=ConversationRef("reference-channel", "conversation-a"),
            content=(TextContent("one"),),
            created_at=datetime.now(UTC),
        )
        await channel.send(message)
        with self.assertRaises(RuntimeError):
            await channel.send(message)
        self.assertEqual(channel.sent, (message,))

    async def test_channel_conversation_uses_native_ingress_and_bounded_wait(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceChannel("")
        channel = ReferenceChannel()
        with self.assertRaises(ValueError):
            channel.conversation("")
        with self.assertRaises(ValueError):
            channel.conversation("conversation-a", authenticated_actor="")
        conversation = channel.conversation(
            "conversation-a",
            authenticated_actor="actor-a",
        )
        received = []

        async def on_message(message) -> None:
            received.append(message)

        await channel.start(on_message)
        try:
            with self.assertRaises(ValueError):
                conversation.text_message(message_id="", text="ordinary input")
            await conversation.receive_text(
                message_id="reference:message:ingress",
                text="ordinary input",
            )
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].conversation_ref, conversation.ref)
            self.assertEqual(received[0].sender, conversation.authenticated_actor)

            outbound = OutboundMessage(
                delivery_id="reference:delivery:one",
                conversation_ref=conversation.ref,
                content=(TextContent("ordinary output"),),
                created_at=datetime.now(UTC),
            )
            await channel.send(outbound)
            self.assertEqual(
                await conversation.wait_for_text("ordinary output"),
                (outbound,),
            )
            with self.assertRaises(ValueError):
                await channel.wait_for_text("never", count=True)
        finally:
            await channel.stop()

        self.assertFalse(channel.started)

    async def test_managed_resources_are_stable_through_the_application_port(self) -> None:
        application = ReferenceApplication()
        with TemporaryDirectory() as cwd:
            foreign = await application.execute(
                ListProjects(
                    operation_id="reference:project:list-foreign",
                    application_ref=ApplicationRef("different-application"),
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(foreign, ApplicationOperationFailed)

            empty = await application.execute(
                ListProjects(
                    operation_id="reference:project:list-empty",
                    application_ref=application.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(empty, ProjectsListed)
            assert isinstance(empty, ProjectsListed)
            self.assertEqual(empty.projects.items, ())

            create_project = CreateProject(
                operation_id="reference:project:stable",
                application_ref=application.ref,
                cwd=cwd,
                created_at=datetime.now(UTC),
            )
            first_project = await application.execute(create_project)
            repeated_project = await application.execute(create_project)
            self.assertIsInstance(first_project, ProjectCreated)
            self.assertIsInstance(repeated_project, ProjectCreated)
            assert isinstance(first_project, ProjectCreated)
            assert isinstance(repeated_project, ProjectCreated)
            self.assertEqual(repeated_project, first_project)

            conflicting_project = await application.execute(
                CreateProject(
                    operation_id=create_project.operation_id,
                    application_ref=application.ref,
                    cwd=f"{cwd}/different",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(conflicting_project, ApplicationOperationFailed)
            assert isinstance(conflicting_project, ApplicationOperationFailed)
            self.assertEqual(conflicting_project.error.code, "conflict")

            listed = await application.execute(
                ListProjects(
                    operation_id="reference:project:list-created",
                    application_ref=application.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(listed, ProjectsListed)
            assert isinstance(listed, ProjectsListed)
            self.assertEqual(
                tuple(project.ref for project in listed.projects.items),
                (first_project.project.ref,),
            )

            create_thread = CreateThread(
                operation_id="reference:thread:stable",
                application_ref=application.ref,
                project_ref=first_project.project.ref,
                created_at=datetime.now(UTC),
            )
            first_thread = await application.execute(create_thread)
            repeated_thread = await application.execute(create_thread)
            self.assertIsInstance(first_thread, ThreadCreated)
            self.assertIsInstance(repeated_thread, ThreadCreated)
            assert isinstance(first_thread, ThreadCreated)
            assert isinstance(repeated_thread, ThreadCreated)
            self.assertEqual(repeated_thread, first_thread)

            conflicting_thread = await application.execute(
                CreateThread(
                    operation_id=create_thread.operation_id,
                    application_ref=application.ref,
                    project_ref=first_project.project.ref,
                    title="different intent",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(conflicting_thread, ApplicationOperationFailed)
            assert isinstance(conflicting_thread, ApplicationOperationFailed)
            self.assertEqual(conflicting_thread.error.code, "conflict")

    async def test_adapter_diagnostics_are_bounded_redacted_and_cleanup_is_explicit(self) -> None:
        application = ReferenceApplication()
        channel = ReferenceChannel()
        secret_content = "content-that-must-not-enter-diagnostics"
        secret_conversation = "conversation-that-must-not-enter-diagnostics"
        secret_message = "message-that-must-not-enter-diagnostics"

        with TemporaryDirectory(prefix="path-that-must-not-enter-diagnostics-") as cwd:
            project = await application.create_project(
                cwd,
                operation_id="reference:diagnostics:project",
            )
            thread = await application.create_thread(
                project.ref,
                operation_id="reference:diagnostics:thread",
            )
            conversation = channel.conversation(secret_conversation)
            message = conversation.text_message(
                message_id=secret_message,
                text=secret_content,
            )

            async def discard(received) -> None:
                del received

            await application.start()
            await channel.start(discard)
            subscription = application.subscribe_thread(thread.ref)
            self.assertEqual(application.active_observation_workers(thread.ref), 1)
            try:
                facts = repr(
                    (
                        application.diagnostic_facts(),
                        channel.diagnostic_facts(),
                    )
                )
                self.assertLess(len(facts), 2_048)
                for secret in (
                    cwd,
                    project.ref.project_id,
                    thread.ref.thread_id,
                    secret_conversation,
                    secret_message,
                    secret_content,
                ):
                    self.assertNotIn(secret, facts)
                self.assertNotIn(message.message_id, facts)
            finally:
                await subscription.aclose()
                await channel.stop()
                await application.stop()

            self.assertEqual(application.active_observation_workers(thread.ref), 0)
            self.assertFalse(channel.started)
            self.assertFalse(application.started)

    async def test_application_limits_are_positive_and_capacity_is_pre_dispatch(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceApplication(max_projects=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_threads=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_turns_per_thread=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_events_per_thread=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_threads=True)

        with TemporaryDirectory() as cwd:
            application = ReferenceApplication(max_projects=1, max_threads=1)
            project = await application.create_project(
                cwd,
                operation_id="reference-project-one",
            )
            self.assertIs(
                await application.create_project(
                    cwd,
                    operation_id="reference-project-one",
                ),
                project,
            )
            with self.assertRaises(ValueError):
                await application.create_project(
                    f"{cwd}/different",
                    operation_id="reference-project-one",
                )
            with self.assertRaises(ValueError):
                await application.create_project(
                    f"{cwd}/second",
                    operation_id="reference-project-two",
                )
            project_capacity = await application.execute(
                CreateProject(
                    operation_id="reference-project-three",
                    application_ref=application.ref,
                    cwd=f"{cwd}/third",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(project_capacity, ApplicationOperationFailed)
            assert isinstance(project_capacity, ApplicationOperationFailed)
            self.assertEqual(project_capacity.error.code, "capacity_exhausted")

            await application.create_thread(
                project.ref,
                operation_id="reference-thread-one",
            )
            with self.assertRaises(ValueError):
                await application.create_thread(
                    project.ref,
                    operation_id="reference-thread-two",
                )
            with self.assertRaises(KeyError):
                await application.get_thread(
                    ThreadRef(
                        project_ref=ProjectRef("reference-agent", "unknown-project"),
                        thread_id="reference-thread-2",
                    )
                )

            turn_limited = ReferenceApplication(
                max_turns_per_thread=1,
                max_events_per_thread=6,
            )
            turn_project = await turn_limited.create_project(
                cwd,
                operation_id="reference-turn-project",
            )
            turn_thread = await turn_limited.create_thread(
                turn_project.ref,
                operation_id="reference-turn-thread",
            )
            await turn_limited.send_input(
                turn_thread.ref,
                AgentInput(
                    client_message_id="accepted-turn",
                    content=(TextContent("first"),),
                ),
            )
            callback_calls: list[str] = []

            async def before_dispatch(dispatch) -> None:
                callback_calls.append(dispatch.client_message_id)

            with self.assertRaises(ValueError):
                await turn_limited.send_input(
                    turn_thread.ref,
                    AgentInput(
                        client_message_id="rejected-turn",
                        content=(TextContent("second"),),
                    ),
                    before_dispatch=before_dispatch,
                )
            self.assertEqual(callback_calls, [])
            self.assertEqual(len((await _history(turn_limited, turn_thread.ref)).turns), 1)

            event_limited = ReferenceApplication(
                max_turns_per_thread=2,
                max_events_per_thread=3,
            )
            event_project = await event_limited.create_project(
                cwd,
                operation_id="reference-event-project",
            )
            event_thread = await event_limited.create_thread(
                event_project.ref,
                operation_id="reference-event-thread",
            )
            await event_limited.send_input(
                event_thread.ref,
                AgentInput(
                    client_message_id="accepted-event",
                    content=(TextContent("first"),),
                ),
            )
            event_callback_calls: list[str] = []

            async def before_event_dispatch(dispatch) -> None:
                event_callback_calls.append(dispatch.client_message_id)

            with self.assertRaises(ValueError):
                await event_limited.send_input(
                    event_thread.ref,
                    AgentInput(
                        client_message_id="rejected-event",
                        content=(TextContent("rejected"),),
                    ),
                    before_dispatch=before_event_dispatch,
                )
            self.assertEqual(event_callback_calls, [])
            self.assertEqual(len((await _history(event_limited, event_thread.ref)).turns), 1)


async def _history(
    application: ReferenceApplication,
    thread_ref: ThreadRef,
) -> ThreadHistory:
    result = await application.execute(
        GetThreadHistory(
            operation_id="reference-test-history",
            application_ref=ApplicationRef("reference-agent"),
            thread_ref=thread_ref,
            created_at=datetime.now(UTC),
        )
    )
    if not isinstance(result, ThreadHistoryRead):
        raise AssertionError(f"history read failed: {result!r}")
    return result.history

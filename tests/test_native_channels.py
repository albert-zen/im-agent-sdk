from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from imagent.channels import NativeTransportChannelAdapter, channel_from_config
from imagent.channels.native.artifacts import (
    delivered_artifact_message_ids,
    record_artifact_delivery,
    record_artifact_failure,
    stable_artifact_identity,
)
from imagent.channels.native.base import BaseChannelAdapter
from imagent.channels.native.diagnostics import (
    NativeChannelDiagnosticState,
    NativeConnectionDiagnosticSnapshot,
)
from imagent.channels.native.weixin_state import WeixinCredentials, WeixinStateStore
from imagent.contracts import (
    AttachmentContent,
    ConversationRef,
    DeliveryItemStatus,
    DeliverySupportLevel,
    LocalPath,
    OutboundMessage,
    RemoteUrl,
    TextContent,
)
from imagent.diagnostics import ConnectionDiagnosticState, QueueDiagnosticName
from imagent.interaction.channels import (
    ChannelStartupConfigurationValidator,
    InboundAdmission,
)
from imagent.interaction.channels.ingress import ChannelAccessPolicy, InboundMessage
from imagent.interaction.channels.outbound_delivery import (
    NativeDeliveryResult,
    OutboundArtifact,
    split_text,
)
from imagent.interaction.channels.outbound_delivery import (
    OutboundMessage as NativeOutboundMessage,
)
from imagent.testing import verify_channel_adapter


class NativeProductionChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_native_stop_does_not_cache_stale_ready_facts(self) -> None:
        class Native:
            channel_id = "qq"

            def __init__(self, middleware) -> None:
                self.middleware = middleware

            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                raise RuntimeError("stop failed")

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

            def diagnostic_connection_facts(self) -> NativeConnectionDiagnosticSnapshot:
                return NativeConnectionDiagnosticSnapshot(
                    state="ready",
                    connection_epoch=1,
                    reconnect_count=0,
                    worker_running=True,
                    worker_degraded=False,
                )

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            native_factory=Native,
            startup_validator=lambda: None,
        )

        async def ignore(_item) -> None:
            return None

        await adapter.start(ignore, ignore)
        self.assertIsNotNone(adapter.diagnostic_facts().connection)
        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            await adapter.stop()
        self.assertIsNone(adapter.diagnostic_facts().connection)

    async def test_native_channel_diagnostics_track_lifecycle_without_io(self) -> None:
        class Native(BaseChannelAdapter):
            channel_id = "qq"

            @classmethod
            def from_config(cls, *, config, middleware):
                del config
                return cls(middleware=middleware)

            async def start(self) -> None:
                self.mark_health(connected=False, status="connecting")
                self.mark_health(connected=True, status="connected")

            async def stop(self) -> None:
                self.mark_health(connected=False, status="stopped")

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            native_factory=lambda middleware: Native(middleware=middleware),
            startup_validator=lambda: None,
        )

        async def ignore(_item) -> None:
            return None

        await adapter.start(ignore, ignore)
        ready = adapter.diagnostic_facts()
        ready_connection = ready.connection
        self.assertIsNotNone(ready_connection)
        assert ready_connection is not None
        self.assertEqual(ready_connection.state, ConnectionDiagnosticState.READY)
        self.assertEqual(ready_connection.connection_epoch, 1)
        self.assertIsNone(adapter._last_connection_facts)
        self.assertEqual(adapter.diagnostic_facts(), ready)
        self.assertIsNone(adapter._last_connection_facts)
        native = cast(Any, adapter._native)
        native.mark_health(connected=False, status="reconnecting")
        native.mark_health(connected=False, status="reconnecting")
        reconnecting = adapter.diagnostic_facts()
        reconnecting_connection = reconnecting.connection
        self.assertIsNotNone(reconnecting_connection)
        assert reconnecting_connection is not None
        self.assertEqual(reconnecting_connection.reconnect_count, 1)
        self.assertTrue(reconnecting_connection.worker_degraded)
        native.mark_health(connected=True, status="connected")
        reconnected = adapter.diagnostic_facts().connection
        self.assertIsNotNone(reconnected)
        assert reconnected is not None
        self.assertEqual(reconnected.connection_epoch, 2)
        await adapter.stop()
        stopped = adapter.diagnostic_facts()
        stopped_connection = stopped.connection
        self.assertIsNotNone(stopped_connection)
        assert stopped_connection is not None
        self.assertEqual(stopped_connection.state, ConnectionDiagnosticState.DISCONNECTED)
        self.assertFalse(stopped_connection.worker_running)

    async def test_four_native_channels_report_only_owned_queue_facts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapters = (
                channel_from_config("qq", config={"enabled": False}),
                channel_from_config("telegram", config={"enabled": False}),
                channel_from_config("feishu", config={"enabled": False}),
                channel_from_config(
                    "weixin",
                    config={"enabled": False, "state_dir": directory},
                ),
            )

            async def ignore(_item) -> None:
                return None

            for adapter in adapters:
                await adapter.start(ignore, ignore)
            try:
                adapter_by_kind = {adapter.kind: adapter for adapter in adapters}
                facts = {adapter.kind: adapter.diagnostic_facts() for adapter in adapters}
                native = {adapter.kind: cast(Any, adapter._native) for adapter in adapters}

                def connection_for(kind: str):
                    connection = facts[kind].connection
                    self.assertIsNotNone(connection)
                    assert connection is not None
                    return connection

                for kind in ("qq", "feishu"):
                    connection = connection_for(kind)
                    queues = connection.queues
                    self.assertEqual(len(queues), 1)
                    self.assertEqual(queues[0].name, QueueDiagnosticName.CHANNEL_INBOUND)
                    self.assertGreater(queues[0].capacity, 0)
                    self.assertEqual(queues[0].depth, 0)
                for kind in ("telegram", "weixin"):
                    connection = connection_for(kind)
                    self.assertEqual(connection.queues, ())

                for kind, concrete in native.items():
                    concrete.mark_health(connected=False, status="connecting")
                    connecting = adapter_by_kind[kind].diagnostic_facts().connection
                    self.assertIsNotNone(connecting)
                    assert connecting is not None
                    self.assertEqual(
                        connecting.state,
                        ConnectionDiagnosticState.CONNECTING,
                    )
                    concrete.mark_health(connected=True, status="connected")
                    ready = adapter_by_kind[kind].diagnostic_facts().connection
                    self.assertIsNotNone(ready)
                    assert ready is not None
                    self.assertEqual(
                        ready.state,
                        ConnectionDiagnosticState.READY,
                    )

                qq = native["qq"]
                qq._ensure_inbound_worker = lambda: None
                qq_capacity = connection_for("qq").queues[0].capacity
                queued = InboundMessage(
                    channel_id="qq",
                    conversation_id="c2c:queued",
                    user_id="queued",
                    message_id="queued",
                    text="queued",
                )
                for _ in range(qq_capacity):
                    qq._inbound_queue.put_nowait((queued, (), (), None, 0))
                self.assertEqual(
                    qq.diagnostic_connection_facts().queues[0].depth,
                    qq_capacity,
                )
                with self.assertRaisesRegex(RuntimeError, "queue is full"):
                    qq._queue_dispatch_event(
                        "C2C_MESSAGE_CREATE",
                        {
                            "id": "overflow",
                            "content": "overflow",
                            "author": {"user_openid": "overflow-user"},
                        },
                        None,
                    )
                self.assertEqual(
                    qq.diagnostic_connection_facts().queues[0].overflow_count,
                    1,
                )

                feishu = native["feishu"]
                feishu_capacity = connection_for("feishu").queues[0].capacity
                feishu._main_loop = asyncio.get_running_loop()
                feishu._inbound_queue = asyncio.Queue(maxsize=feishu_capacity)
                for index in range(feishu_capacity):
                    self.assertTrue(feishu._inbound_slots.acquire(blocking=False))
                    feishu._enqueue_inbound(
                        InboundMessage(
                            channel_id="feishu",
                            conversation_id="chat:queued",
                            user_id="queued",
                            message_id=f"queued-{index}",
                            text="queued",
                        ),
                        (),
                        (),
                    )
                self.assertEqual(
                    feishu.diagnostic_connection_facts().queues[0].depth,
                    feishu_capacity,
                )
                feishu._queue_inbound(
                    SimpleNamespace(
                        id="overflow",
                        message_id="overflow",
                        raw_content_type="text",
                        content_text="overflow",
                        resources=[],
                        mentioned_bot=False,
                        conversation=SimpleNamespace(
                            chat_id="overflow",
                            chat_type="p2p",
                            thread_id=None,
                        ),
                        sender=SimpleNamespace(open_id="overflow-user"),
                    )
                )
                self.assertEqual(
                    feishu.diagnostic_connection_facts().queues[0].overflow_count,
                    1,
                )
                feishu._last_overflow_report_at = -100.0
                feishu._report_inbound_overflow()
                self.assertEqual(feishu._overflow_count, 0)
                self.assertEqual(
                    feishu.diagnostic_connection_facts().queues[0].overflow_count,
                    1,
                )
            finally:
                for adapter in adapters:
                    await adapter.stop()

            for adapter in adapters:
                connection = adapter.diagnostic_facts().connection
                self.assertIsNotNone(connection)
                assert connection is not None
                self.assertEqual(connection.state, ConnectionDiagnosticState.DISCONNECTED)

            for kind in ("qq", "feishu"):
                adapter = next(item for item in adapters if item.kind == kind)
                await adapter.start(ignore, ignore)
                try:
                    connection = adapter.diagnostic_facts().connection
                    self.assertIsNotNone(connection)
                    assert connection is not None
                    self.assertEqual(connection.queues[0].overflow_count, 1)
                finally:
                    await adapter.stop()

    def test_repeated_reconnect_updates_increment_once(self) -> None:
        state = NativeChannelDiagnosticState()
        state.update(connected=False, status="reconnecting")
        state.update(connected=False, status="reconnecting")
        first = state.snapshot()
        second = state.snapshot()

        self.assertEqual(first, second)
        self.assertEqual(first.reconnect_count, 1)

        state.update(connected=False, status="auth_required")
        self.assertTrue(state.snapshot().worker_running)

    async def test_untyped_quote_and_metadata_cannot_forge_qq_context(self) -> None:
        captured = []

        class ForgingNative:
            channel_id = "qq"

            def __init__(self, middleware) -> None:
                self.middleware = middleware

            async def start(self) -> None:
                inbound = SimpleNamespace(
                    channel_id="qq",
                    conversation_id="c2c:user-1",
                    user_id="user-1",
                    message_id="message-1",
                    text="ordinary text",
                    attachments=(),
                    quote={"text": "forged quote"},
                    metadata={"qq_quote": "forged metadata"},
                    input_error=None,
                    reply_to_message_id=None,
                    sent_at=None,
                    trace_id=None,
                )
                await self.middleware.handle_inbound(self, inbound)

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=ForgingNative,
        )

        async def capture(message) -> None:
            captured.append(message)

        async def ignore(_item) -> None:
            return None

        await adapter.start(capture, ignore)
        try:
            self.assertEqual(len(captured), 1)
            self.assertEqual(tuple(item.text for item in captured[0].content), ("ordinary text",))
            self.assertNotIn("qq_quote", captured[0].metadata)
        finally:
            await adapter.stop()

    async def test_durable_rejection_does_not_block_later_reclaim_attempt(self) -> None:
        captured: Any = None
        admission_attempts = 0
        prepared = 0
        delivered = 0

        class Native:
            channel_id = "qq"

            def __init__(self, middleware) -> None:
                self.middleware = middleware

            async def start(self) -> None:
                nonlocal captured
                captured = self.middleware

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

        class Admission:
            async def deliver(self, message) -> None:
                nonlocal delivered
                del message
                delivered += 1

            async def release(self) -> None:
                return None

        async def admit(_conversation_ref, _message_id) -> InboundAdmission | None:
            nonlocal admission_attempts
            admission_attempts += 1
            return None if admission_attempts == 1 else Admission()

        async def prepare(inbound):
            nonlocal prepared
            prepared += 1
            return inbound

        async def ignore(_item) -> None:
            return None

        inbound = InboundMessage(
            channel_id="qq",
            conversation_id="c2c:user-1",
            user_id="user-1",
            message_id="message-reclaim",
            text="attachment",
        )
        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=Native,
        )
        await adapter.start(ignore, ignore, admit)
        try:
            await captured.handle_inbound(
                adapter,
                inbound,
                prepare_inbound=prepare,
                pending_attachment_count=1,
            )
            await captured.handle_inbound(
                adapter,
                inbound,
                prepare_inbound=prepare,
                pending_attachment_count=1,
            )
        finally:
            await adapter.stop()

        self.assertEqual(admission_attempts, 2)
        self.assertEqual(prepared, 1)
        self.assertEqual(delivered, 1)

    async def test_preparation_failure_releases_untransferred_admission(self) -> None:
        captured: Any = None
        released = False

        class Native:
            channel_id = "qq"

            def __init__(self, middleware) -> None:
                self.middleware = middleware

            async def start(self) -> None:
                nonlocal captured
                captured = self.middleware

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

        class Admission:
            async def deliver(self, message) -> None:
                del message
                raise AssertionError("failed preparation must not be delivered")

            async def release(self) -> None:
                nonlocal released
                released = True

        async def admit(_conversation_ref, _message_id) -> InboundAdmission | None:
            return Admission()

        async def ignore(_item) -> None:
            return None

        async def fail_preparation(_inbound):
            raise RuntimeError("media download failed")

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=Native,
        )
        await adapter.start(ignore, ignore, admit)
        try:
            with self.assertRaisesRegex(RuntimeError, "media download failed"):
                await captured.handle_inbound(
                    adapter,
                    InboundMessage(
                        channel_id="qq",
                        conversation_id="c2c:user-1",
                        user_id="user-1",
                        message_id="message-prepare-failure",
                        text="attachment",
                    ),
                    prepare_inbound=fail_preparation,
                    pending_attachment_count=1,
                )
        finally:
            await adapter.stop()

        self.assertTrue(released)

    async def test_release_failure_does_not_mask_preparation_failure(self) -> None:
        captured: Any = None
        preparation_error = RuntimeError("media download failed")

        class Native:
            channel_id = "qq"

            def __init__(self, middleware) -> None:
                self.middleware = middleware

            async def start(self) -> None:
                nonlocal captured
                captured = self.middleware

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

        class Admission:
            async def deliver(self, message) -> None:
                del message
                raise AssertionError("failed preparation must not be delivered")

            async def release(self) -> None:
                raise RuntimeError("release failed")

        async def admit(_conversation_ref, _message_id) -> InboundAdmission | None:
            return Admission()

        async def ignore(_item) -> None:
            return None

        async def fail_preparation(_inbound):
            raise preparation_error

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=Native,
        )
        await adapter.start(ignore, ignore, admit)
        try:
            with self.assertRaises(RuntimeError) as raised:
                await captured.handle_inbound(
                    adapter,
                    InboundMessage(
                        channel_id="qq",
                        conversation_id="c2c:user-1",
                        user_id="user-1",
                        message_id="message-release-failure",
                        text="attachment",
                    ),
                    prepare_inbound=fail_preparation,
                    pending_attachment_count=1,
                )
        finally:
            await adapter.stop()

        self.assertIs(raised.exception, preparation_error)
        self.assertTrue(any("release failed" in note for note in preparation_error.__notes__))

    async def test_gateway_handoff_failure_is_not_released_by_channel(self) -> None:
        captured: Any = None
        released = False

        class Native:
            channel_id = "qq"

            def __init__(self, middleware) -> None:
                self.middleware = middleware

            async def start(self) -> None:
                nonlocal captured
                captured = self.middleware

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

        class Admission:
            async def deliver(self, message) -> None:
                del message
                raise RuntimeError("gateway processing failed")

            async def release(self) -> None:
                nonlocal released
                released = True

        async def admit(_conversation_ref, _message_id) -> InboundAdmission | None:
            return Admission()

        async def ignore(_item) -> None:
            return None

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=Native,
        )
        await adapter.start(ignore, ignore, admit)
        try:
            with self.assertRaisesRegex(RuntimeError, "gateway processing failed"):
                await captured.handle_inbound(
                    adapter,
                    InboundMessage(
                        channel_id="qq",
                        conversation_id="c2c:user-1",
                        user_id="user-1",
                        message_id="message-handoff-failure",
                        text="hello",
                    ),
                )
        finally:
            await adapter.stop()

        self.assertFalse(released)

    async def test_outbound_attachments_and_receipt_preserve_common_contract(self) -> None:
        sent = []

        class CapturingNative:
            channel_id = "qq"
            middleware = object()

            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                sent.append(message)
                return NativeDeliveryResult(("native-message-1",))

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=lambda _middleware: CapturingNative(),
        )

        async def ignore(_item) -> None:
            return None

        await adapter.start(ignore, ignore)
        try:
            receipt = await adapter.send(
                OutboundMessage(
                    delivery_id="delivery-1",
                    conversation_ref=ConversationRef("qq-main", "user:user-1"),
                    content=(
                        TextContent("artifact"),
                        AttachmentContent(
                            attachment_id="attachment-1",
                            media_type="image/png",
                            source=LocalPath("D:/spool/image.png"),
                            filename="image.png",
                            size_bytes=3,
                            metadata={"sha256": "abc"},
                        ),
                    ),
                    created_at=datetime.now(UTC),
                )
            )

            self.assertEqual(receipt.status, "accepted_by_platform")
            self.assertEqual(receipt.native_message_id, "native-message-1")
            self.assertIn("accepted one native message", receipt.detail or "")
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0].artifacts[0].kind, "image")
            self.assertEqual(sent[0].artifacts[0].local_path, "D:/spool/image.png")
            self.assertEqual(sent[0].artifacts[0].sha256, "abc")
            self.assertEqual(sent[0].artifacts[0].attachment_id, "attachment-1")
        finally:
            await adapter.stop()

    async def test_native_artifact_results_become_typed_item_receipts(self) -> None:
        class PartiallySuccessfulNative:
            channel_id = "qq"
            middleware = object()

            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                record_artifact_delivery(
                    message,
                    message.artifacts[0],
                    platform_message_id="native-image-1",
                )
                record_artifact_failure(
                    message,
                    message.artifacts[1],
                    error="unsupported document",
                )
                return NativeDeliveryResult(("native-image-1",))

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=lambda _middleware: PartiallySuccessfulNative(),
        )

        async def ignore(_item) -> None:
            return None

        await adapter.start(ignore, ignore)
        try:
            receipt = await adapter.send(
                OutboundMessage(
                    delivery_id="delivery-partial",
                    conversation_ref=ConversationRef("qq-main", "user:user-1"),
                    content=(
                        TextContent("artifacts"),
                        AttachmentContent(
                            attachment_id="image-1",
                            media_type="image/png",
                            source=LocalPath("D:/spool/image.png"),
                            filename="image.png",
                            size_bytes=3,
                        ),
                        AttachmentContent(
                            attachment_id="document-1",
                            media_type="application/pdf",
                            source=LocalPath("D:/spool/document.pdf"),
                            filename="document.pdf",
                            size_bytes=4,
                        ),
                    ),
                    created_at=datetime.now(UTC),
                )
            )

            self.assertEqual(
                tuple(item.content_index for item in receipt.items),
                (1, 2),
            )
            self.assertEqual(receipt.items[0].attachment_id, "image-1")
            self.assertEqual(receipt.items[0].status, DeliveryItemStatus.ACCEPTED)
            self.assertEqual(receipt.items[0].native_message_id, "native-image-1")
            self.assertEqual(receipt.items[1].attachment_id, "document-1")
            self.assertEqual(receipt.items[1].status, DeliveryItemStatus.REJECTED)
            self.assertEqual(receipt.items[1].detail, "unsupported document")
        finally:
            await adapter.stop()

    def test_artifact_identity_prefers_stable_attachment_id(self) -> None:
        message = NativeOutboundMessage(
            channel_id="qq",
            conversation_id="user:user-1",
            message_type="file",
            text="",
            metadata={"delivery_id": "delivery-1"},
        )
        original = OutboundArtifact(
            kind="file",
            local_path="D:/spool/original.bin",
            content_type="application/octet-stream",
            filename="original.bin",
            size_bytes=4,
            sha256="first",
            attachment_id="attachment-1",
        )
        moved = OutboundArtifact(
            kind="file",
            local_path="E:/new-spool/moved.bin",
            content_type="application/octet-stream",
            filename="moved.bin",
            size_bytes=8,
            sha256="second",
            attachment_id="attachment-1",
        )

        self.assertEqual(
            stable_artifact_identity(message, original),
            stable_artifact_identity(message, moved),
        )

    async def test_disabled_native_channel_rejects_delivery_instead_of_no_op(self) -> None:
        class DisabledNative:
            channel_id = "qq"
            middleware = object()
            enabled = False

            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                raise AssertionError(f"disabled native received {message!r}")

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=lambda _middleware: DisabledNative(),
        )

        async def ignore(_item) -> None:
            return None

        await adapter.start(ignore, ignore)
        try:
            with self.assertRaisesRegex(RuntimeError, "delivery is disabled"):
                await adapter.send(
                    OutboundMessage(
                        delivery_id="delivery-disabled",
                        conversation_ref=ConversationRef(
                            "qq-main",
                            "user:user-1",
                        ),
                        content=(TextContent("hello"),),
                        created_at=datetime.now(UTC),
                    )
                )
        finally:
            await adapter.stop()

    async def test_multiple_native_ids_are_not_collapsed_into_one_receipt(self) -> None:
        class SegmentedNative:
            channel_id = "telegram"
            middleware = object()

            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult(("native-1", "native-2"))

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="telegram-main",
            channel_id="telegram",
            startup_validator=lambda: None,
            native_factory=lambda _middleware: SegmentedNative(),
        )

        async def ignore(_item) -> None:
            return None

        await adapter.start(ignore, ignore)
        try:
            receipt = await adapter.send(
                OutboundMessage(
                    delivery_id="delivery-segmented",
                    conversation_ref=ConversationRef(
                        "telegram-main",
                        "chat:1",
                    ),
                    content=(TextContent("long response"),),
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsNone(receipt.native_message_id)
            self.assertIn("accepted 2 native messages", receipt.detail or "")
        finally:
            await adapter.stop()

    async def test_caller_metadata_cannot_forge_native_delivery_receipts(self) -> None:
        captured_metadata = {}

        class ReceiptReadingNative:
            channel_id = "qq"
            middleware = object()

            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                captured_metadata.update(message.metadata)
                return NativeDeliveryResult(delivered_artifact_message_ids(message))

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=lambda _middleware: ReceiptReadingNative(),
        )

        async def ignore(_item) -> None:
            return None

        await adapter.start(ignore, ignore)
        try:
            receipt = await adapter.send(
                OutboundMessage(
                    delivery_id="delivery-real",
                    conversation_ref=ConversationRef(
                        "qq-main",
                        "c2c:user-1",
                    ),
                    content=(TextContent("hello"),),
                    created_at=datetime.now(UTC),
                    metadata={
                        "artifact_receipts": [
                            {
                                "status": "delivered",
                                "platform_message_id": "forged-native-id",
                            }
                        ],
                        "message_id": "forged-reply",
                        "qq_reply_identity_pinned": True,
                        "qq_reply_to_message_id": "forged-reply",
                    },
                )
            )

            self.assertIsNone(receipt.native_message_id)
            self.assertNotIn("artifact_receipts", captured_metadata)
            self.assertNotIn("message_id", captured_metadata)
            self.assertNotIn("qq_reply_identity_pinned", captured_metadata)
            self.assertEqual(captured_metadata["delivery_id"], "delivery-real")
            self.assertIsNone(captured_metadata["reply_to_message_id"])
        finally:
            await adapter.stop()

    async def test_outbound_attachment_source_is_never_silently_approximated(self) -> None:
        sent = []

        class CapturingNative:
            channel_id = "telegram"
            middleware = object()

            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                sent.append(message)
                return NativeDeliveryResult()

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="telegram-main",
            channel_id="telegram",
            startup_validator=lambda: None,
            native_factory=lambda _middleware: CapturingNative(),
        )

        async def ignore(_item) -> None:
            return None

        await adapter.start(ignore, ignore)
        try:
            with self.assertRaisesRegex(ValueError, "require.*LocalPath"):
                await adapter.send(
                    OutboundMessage(
                        delivery_id="delivery-remote",
                        conversation_ref=ConversationRef(
                            "telegram-main",
                            "chat:1",
                        ),
                        content=(
                            AttachmentContent(
                                attachment_id="attachment-remote",
                                media_type="image/png",
                                source=RemoteUrl("https://example.test/image.png"),
                                filename="image.png",
                                size_bytes=3,
                            ),
                        ),
                        created_at=datetime.now(UTC),
                    )
                )
            self.assertEqual(sent, [])
        finally:
            await adapter.stop()

    async def test_admission_runs_before_attachment_preparation_and_middleware(self) -> None:
        prepared = False
        middleware_calls = 0

        class Middleware:
            async def handle_inbound(self, *_args, **_kwargs) -> None:
                nonlocal middleware_calls
                middleware_calls += 1

        class RestrictedNative(BaseChannelAdapter):
            channel_id = "test"

            @classmethod
            def from_config(cls, *, config, middleware):
                del config
                return cls(middleware=middleware)

            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

        adapter = RestrictedNative(
            middleware=Middleware(),
            access_policy=ChannelAccessPolicy(
                allowed_user_ids=frozenset({"allowed-user"}),
            ),
        )

        async def prepare(message):
            nonlocal prepared
            prepared = True
            return message

        with self.assertLogs("imagent.channels.native.base", level="WARNING"):
            await adapter.dispatch_inbound(
                InboundMessage(
                    channel_id="test",
                    conversation_id="chat-1",
                    user_id="blocked-user",
                    message_id="message-1",
                    text="blocked",
                ),
                prepare_inbound=prepare,
                pending_attachment_count=1,
            )

        self.assertFalse(prepared)
        self.assertEqual(middleware_calls, 0)

    async def test_failed_native_start_can_be_retried_without_a_stale_instance(self) -> None:
        attempts = 0

        class FailingNative:
            channel_id = "qq"
            middleware = object()

            async def start(self) -> None:
                nonlocal attempts
                attempts += 1
                raise RuntimeError("startup failed")

            async def stop(self) -> None:
                return None

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            startup_validator=lambda: None,
            native_factory=lambda _middleware: FailingNative(),
        )

        async def ignore(_item) -> None:
            return None

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await adapter.start(ignore, ignore)
        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await adapter.start(ignore, ignore)
        self.assertEqual(attempts, 2)

    def test_shared_admission_policy_and_chunking_keep_native_semantics(self) -> None:
        any_match = ChannelAccessPolicy(
            allowed_user_ids=frozenset({"user-1"}),
            allowed_conversation_ids=frozenset({"chat-1"}),
        )
        all_match = ChannelAccessPolicy(
            allowed_user_ids=frozenset({"user-1"}),
            allowed_conversation_ids=frozenset({"chat-1"}),
            access_match="all",
        )

        self.assertTrue(any_match.allows(user_id="user-2", conversation_id="chat-1"))
        self.assertFalse(any_match.allows(user_id="user-2", conversation_id="chat-2"))
        self.assertTrue(all_match.allows(user_id="user-1", conversation_id="chat-1"))
        self.assertFalse(all_match.allows(user_id="user-2", conversation_id="chat-1"))
        self.assertEqual(split_text("alpha beta gamma", limit=10), ["alpha beta", "gamma"])
        self.assertEqual(split_text("abcdefghijk", limit=5), ["abcde", "fghij", "k"])

    async def test_four_sdk_owned_channels_load_through_one_seam(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            configurations = {
                "qq": {"enabled": False},
                "telegram": {"enabled": False},
                "feishu": {"enabled": False},
                "weixin": {"enabled": False, "state_dir": state_dir},
            }
            adapters = [
                channel_from_config(
                    channel_id,
                    config=config,
                    channel_instance_id=f"{channel_id}-main",
                )
                for channel_id, config in configurations.items()
            ]

            for adapter in adapters:
                self.assertIsInstance(
                    adapter,
                    ChannelStartupConfigurationValidator,
                )
                adapter.validate_startup_configuration()
                adapter.validate_startup_configuration()
                self.assertIsNone(adapter._native)

            async def ignore(_item) -> None:
                return None

            await adapters[0].start(ignore, ignore)
            try:
                self.assertTrue(getattr(adapters[0]._native, "markdown_enabled", False))
            finally:
                await adapters[0].stop()

            reports = [await verify_channel_adapter(adapter) for adapter in adapters]

            for adapter in adapters:
                adapter.validate_startup_configuration()
                self.assertIsNone(adapter._native)

        self.assertEqual(
            [adapter.channel_instance_id for adapter in adapters],
            ["qq-main", "telegram-main", "feishu-main", "weixin-main"],
        )
        self.assertTrue(all("start and stop lifecycle" in report.check_names for report in reports))
        self.assertEqual(
            adapters[0].capabilities.delivery.markdown,
            DeliverySupportLevel.NATIVE,
        )
        self.assertTrue(
            all(
                adapter.capabilities.delivery.markdown is not DeliverySupportLevel.UNSUPPORTED
                for adapter in adapters
            )
        )

    def test_sdk_channel_preflight_accepts_each_valid_resolved_configuration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            weixin_state_dir = Path(directory) / "weixin"
            WeixinStateStore(weixin_state_dir).save_credentials(
                WeixinCredentials(
                    account_id="bot@im.bot",
                    bot_token="bot-secret",
                    base_url="https://ilinkai.weixin.qq.com",
                    owner_user_id="owner@im.wechat",
                )
            )
            qq_config: dict[str, object] = {
                "enabled": True,
                "app_id": "app",
                "client_secret": "secret",
            }
            adapters = (
                channel_from_config("qq", config=qq_config),
                channel_from_config(
                    "telegram",
                    config={"enabled": True, "bot_token": "token"},
                ),
                channel_from_config(
                    "feishu",
                    config={
                        "enabled": True,
                        "app_id": "app",
                        "app_secret": "secret",
                    },
                ),
                channel_from_config(
                    "weixin",
                    config={"enabled": True, "state_dir": str(weixin_state_dir)},
                ),
            )
            qq_config["app_id"] = ""
            files_before = tuple(sorted(weixin_state_dir.iterdir()))

            for adapter in adapters:
                adapter.validate_startup_configuration()
                adapter.validate_startup_configuration()
                self.assertIsNone(adapter._native)

            self.assertEqual(tuple(sorted(weixin_state_dir.iterdir())), files_before)

    def test_sdk_channel_preflight_does_not_construct_http_clients(self) -> None:
        adapters = (
            channel_from_config("qq", config={"enabled": False}),
            channel_from_config("telegram", config={"enabled": False}),
        )

        with (
            patch("imagent.channels.native.qq.httpx.AsyncClient") as qq_client,
            patch("imagent.channels.native.telegram.httpx.AsyncClient") as telegram_client,
        ):
            for adapter in adapters:
                adapter.validate_startup_configuration()
                adapter.validate_startup_configuration()

        qq_client.assert_not_called()
        telegram_client.assert_not_called()
        self.assertTrue(all(adapter._native is None for adapter in adapters))

    def test_native_transport_requires_an_honest_startup_validator(self) -> None:
        with self.assertRaises(TypeError):
            cast(Any, NativeTransportChannelAdapter)(
                channel_instance_id="qq-main",
                channel_id="qq",
                native_factory=lambda _middleware: object(),
            )
        for invalid_validator in (None, "not-callable"):
            with self.subTest(startup_validator=invalid_validator):
                with self.assertRaisesRegex(TypeError, "must be callable"):
                    cast(Any, NativeTransportChannelAdapter)(
                        channel_instance_id="qq-main",
                        channel_id="qq",
                        native_factory=lambda _middleware: object(),
                        startup_validator=invalid_validator,
                    )

    def test_sdk_channel_preflight_rejects_each_invalid_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid: tuple[tuple[str, dict[str, object], str], ...] = (
                ("qq", {"enabled": True}, "app_id and client_secret"),
                ("telegram", {"enabled": True}, "bot_token or bot_token_file"),
                ("feishu", {"enabled": True}, "app_id and app_secret"),
                (
                    "weixin",
                    {"enabled": True, "state_dir": directory},
                    "credentials are missing",
                ),
            )
            for channel_id, config, message in invalid:
                with self.subTest(channel_id=channel_id):
                    adapter = channel_from_config(channel_id, config=config)
                    with self.assertRaisesRegex(RuntimeError, message):
                        adapter.validate_startup_configuration()
                    self.assertIsNone(adapter._native)

    def test_telegram_preflight_rejects_unsafe_offset_without_client_creation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corrupt_dir = root / "corrupt"
            corrupt_dir.mkdir()
            (corrupt_dir / "polling-offset.json").write_text(
                "not-json",
                encoding="utf-8",
            )
            symlink_dir = root / "symlink"
            symlink_dir.mkdir()
            target = root / "offset-target.json"
            target.write_text("{}", encoding="utf-8")
            (symlink_dir / "polling-offset.json").symlink_to(target)

            for state_dir in (corrupt_dir, symlink_dir):
                with self.subTest(state_dir=state_dir.name):
                    adapter = channel_from_config(
                        "telegram",
                        config={
                            "enabled": True,
                            "bot_token": "token",
                            "state_dir": str(state_dir),
                        },
                    )
                    with (
                        patch("imagent.channels.native.telegram.httpx.AsyncClient") as client,
                        self.assertRaisesRegex(RuntimeError, "polling offset"),
                    ):
                        adapter.validate_startup_configuration()
                    client.assert_not_called()
                    self.assertIsNone(adapter._native)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from imagent.adapters import InboundAdmission
from imagent.channels import NativeTransportChannelAdapter, channel_from_config
from imagent.channels.native.access import ChannelAccessPolicy
from imagent.channels.native.artifacts import (
    delivered_artifact_message_ids,
    record_artifact_delivery,
    record_artifact_failure,
    stable_artifact_identity,
)
from imagent.channels.native.base import BaseChannelAdapter
from imagent.channels.native.models import (
    InboundMessage,
    NativeDeliveryResult,
    OutboundArtifact,
)
from imagent.channels.native.models import (
    OutboundMessage as NativeOutboundMessage,
)
from imagent.channels.native.text import split_text
from imagent.contracts import (
    AttachmentContent,
    ConversationRef,
    DeliveryItemStatus,
    LocalPath,
    OutboundMessage,
    RemoteUrl,
    SupportLevel,
    TextContent,
)
from imagent.testing import verify_channel_adapter


class NativeProductionChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_channel_exposes_native_diagnostics_after_shutdown(self) -> None:
        class Native(BaseChannelAdapter):
            channel_id = "qq"

            @classmethod
            def from_config(cls, *, config, middleware):
                del config
                return cls(middleware=middleware)

            async def start(self) -> None:
                self.mark_health(status="connected", connected=True)

            async def stop(self) -> None:
                self.mark_health(status="stopped", connected=False)

            async def send_message(self, message) -> NativeDeliveryResult:
                del message
                return NativeDeliveryResult()

        adapter = NativeTransportChannelAdapter(
            channel_instance_id="qq-main",
            channel_id="qq",
            native_factory=lambda middleware: Native(middleware=middleware),
        )

        async def ignore(_item) -> None:
            return None

        await adapter.start(ignore, ignore)
        ready = adapter.diagnostic_facts()
        await adapter.stop()
        stopped = adapter.diagnostic_facts()

        self.assertEqual(ready.channel_instance_id, "qq-main")
        self.assertEqual(ready.kind, "qq")
        self.assertIsNotNone(ready.connection)
        self.assertIsNotNone(stopped.connection)
        assert ready.connection is not None
        assert stopped.connection is not None
        self.assertEqual(ready.connection.state, "ready")
        self.assertEqual(stopped.connection.state, "disconnected")
        self.assertFalse(stopped.connection.worker_running)

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
                adapter.validate_startup_configuration()

            async def ignore(_item) -> None:
                return None

            await adapters[0].start(ignore, ignore)
            try:
                self.assertTrue(getattr(adapters[0]._native, "markdown_enabled", False))
            finally:
                await adapters[0].stop()

            reports = [await verify_channel_adapter(adapter) for adapter in adapters]

        self.assertEqual(
            [adapter.channel_instance_id for adapter in adapters],
            ["qq-main", "telegram-main", "feishu-main", "weixin-main"],
        )
        self.assertTrue(all("start and stop lifecycle" in report.check_names for report in reports))
        self.assertEqual(
            adapters[0].capabilities.delivery.markdown,
            SupportLevel.NATIVE,
        )
        self.assertTrue(
            all(
                adapter.capabilities.delivery.markdown is not SupportLevel.UNSUPPORTED
                for adapter in adapters
            )
        )

    def test_sdk_channel_preflight_uses_resolved_native_configuration(self) -> None:
        adapter = channel_from_config(
            "telegram",
            config={"enabled": True, "bot_token": ""},
        )

        with self.assertRaisesRegex(RuntimeError, "token"):
            adapter.validate_startup_configuration()


if __name__ == "__main__":
    unittest.main()

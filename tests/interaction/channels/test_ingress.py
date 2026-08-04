from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from imagent.interaction.channels import InboundAdmission
from imagent.interaction.channels.adapters.runtime import _InboundMiddleware
from imagent.interaction.channels.ingress import (
    _TRANSIENT_ADMISSION_LIMIT,
    ChannelAccessPolicy,
    InboundAttachment,
    InboundMessage,
    _InboundAdmissionTransaction,
    _normalize_inbound_message,
    _parse_datetime,
    parse_id_set,
)
from imagent.interaction.media import AttachmentContent, LocalPath
from imagent.interaction.messages import (
    ConversationRef,
    TextContent,
)
from imagent.interaction.messages import (
    InboundMessage as InteractionInboundMessage,
)


class ChannelAccessPolicyTests(unittest.TestCase):
    def test_inbound_attachments_are_an_immutable_tuple(self) -> None:
        attachment = InboundAttachment(
            kind="image",
            content_type="image/png",
            local_path="/staged/image.png",
            size_bytes=3,
        )
        message = InboundMessage(
            channel_id="qq",
            conversation_id="chat-1",
            user_id="user-1",
            message_id="message-1",
            text="",
            attachments=(attachment,),
        )

        self.assertEqual(message.attachments, (attachment,))

    def test_configuration_parses_id_dimensions_and_match_mode(self) -> None:
        policy = ChannelAccessPolicy.from_config(
            {
                "allowed_user_ids": " user-1, user-2\nuser-1 ",
                "allowed_conversation_ids": ["chat-1", " chat-2 "],
                "access_match": " ALL ",
            }
        )

        self.assertEqual(policy.allowed_user_ids, frozenset({"user-1", "user-2"}))
        self.assertEqual(
            policy.allowed_conversation_ids,
            frozenset({"chat-1", "chat-2"}),
        )
        self.assertEqual(policy.access_match, "all")
        self.assertEqual(parse_id_set(None), frozenset())

    def test_empty_and_unrestricted_dimensions_allow_platform_scope(self) -> None:
        for policy in (
            ChannelAccessPolicy.allow_all(),
            ChannelAccessPolicy(allowed_user_ids=frozenset({"*"})),
            ChannelAccessPolicy(allowed_conversation_ids=frozenset({"*"})),
        ):
            with self.subTest(policy=policy):
                self.assertEqual(policy.mode, "platform")
                self.assertTrue(policy.allows(user_id="user-1", conversation_id="chat-1"))

    def test_deny_all_rejects_every_identity(self) -> None:
        policy = ChannelAccessPolicy(allowed_user_ids=frozenset({"none"}))

        self.assertTrue(policy.denies_all)
        self.assertEqual(policy.mode, "deny_all")
        self.assertFalse(policy.allows(user_id="user-1", conversation_id="chat-1"))

    def test_any_and_all_apply_only_configured_dimensions(self) -> None:
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

    def test_invalid_mode_and_mixed_deny_all_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "access_match"):
            ChannelAccessPolicy.from_config({"access_match": "sometimes"})
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            ChannelAccessPolicy(
                allowed_user_ids=frozenset({"none", "user-1"}),
            )


class InboundNormalizationTests(unittest.TestCase):
    def test_ingress_owns_public_identity_time_reply_and_selected_metadata(self) -> None:
        inbound = cast(
            InboundMessage,
            SimpleNamespace(
                channel_id="qq",
                conversation_id="c2c:user-1",
                user_id="user-1",
                message_id="native-message-1",
                text="hello",
                attachments=(),
                reply_to_message_id=42,
                sent_at="2026-08-04T12:34:56Z",
                input_error="attachment warning",
                trace_id="trace-1",
                metadata={"forged": "ignored"},
            ),
        )
        message = _normalize_inbound_message(
            channel_instance_id="qq-main",
            inbound=inbound,
            reply_to_message_id="override-message",
        )

        self.assertEqual(message.message_id, "native-message-1")
        self.assertEqual(
            message.conversation_ref,
            ConversationRef("qq-main", "c2c:user-1"),
        )
        self.assertEqual(message.sender, "user-1")
        self.assertEqual(message.content, (TextContent("hello"),))
        self.assertEqual(
            message.created_at,
            datetime(2026, 8, 4, 12, 34, 56, tzinfo=UTC),
        )
        self.assertEqual(message.reply_to, "override-message")
        self.assertEqual(
            message.metadata,
            {
                "channel_id": "qq",
                "input_error": "attachment warning",
                "trace_id": "trace-1",
            },
        )

        fallback_reply = _normalize_inbound_message(
            channel_instance_id="qq-main",
            inbound=inbound,
            reply_to_message_id=None,
        )
        self.assertEqual(fallback_reply.reply_to, 42)

    def test_ingress_assembles_text_and_attachments_with_stable_metadata(self) -> None:
        inbound = InboundMessage(
            channel_id="qq",
            conversation_id="c2c:user-1",
            user_id="user-1",
            message_id="native-message-3",
            text="hello",
            attachments=(
                InboundAttachment(
                    kind="image",
                    content_type="image/png",
                    local_path="/staged/image.png",
                    size_bytes=3,
                    source_message_id="",
                ),
                InboundAttachment(
                    kind="file",
                    content_type="text/plain",
                    local_path="/staged/report.txt",
                    size_bytes=7,
                    filename="report.txt",
                    source_message_id="native-attachment-2",
                ),
            ),
        )

        message = _normalize_inbound_message(
            channel_instance_id="qq-main",
            inbound=inbound,
            reply_to_message_id=None,
        )

        self.assertEqual(
            message.content,
            (
                TextContent("hello"),
                AttachmentContent(
                    attachment_id="native-message-3:attachment:0",
                    media_type="image/png",
                    filename=None,
                    size_bytes=3,
                    source=LocalPath("/staged/image.png"),
                    metadata={"kind": "image"},
                ),
                AttachmentContent(
                    attachment_id="native-attachment-2",
                    media_type="text/plain",
                    filename="report.txt",
                    size_bytes=7,
                    source=LocalPath("/staged/report.txt"),
                    metadata={"kind": "file"},
                ),
            ),
        )

    def test_runtime_keeps_text_then_attachment_order_and_route_context(self) -> None:
        async def ignore(_message) -> None:
            return None

        middleware = _InboundMiddleware(
            channel_instance_id="qq-main",
            on_message=ignore,
            on_admission=None,
        )
        inbound = InboundMessage(
            channel_id="qq",
            conversation_id="c2c:user-1",
            user_id="user-1",
            message_id="native-message-2",
            text="hello",
            attachments=(
                InboundAttachment(
                    kind="image",
                    content_type="image/png",
                    local_path="/staged/image.png",
                    size_bytes=3,
                    source_message_id="native-attachment-1",
                ),
            ),
            sent_at="2026-08-04T12:34:56Z",
        )

        message = middleware._normalize_inbound(
            inbound,
            reply_to_message_id=None,
        )

        self.assertEqual(
            message.content,
            (
                TextContent("hello"),
                AttachmentContent(
                    attachment_id="native-attachment-1",
                    media_type="image/png",
                    filename=None,
                    size_bytes=3,
                    source=LocalPath("/staged/image.png"),
                    metadata={"kind": "image"},
                ),
            ),
        )
        self.assertEqual(
            message,
            _normalize_inbound_message(
                channel_instance_id="qq-main",
                inbound=inbound,
                reply_to_message_id=None,
            ),
        )
        self.assertEqual(
            getattr(
                middleware.get_route_context("qq", "c2c:user-1"),
                "last_inbound_message_id",
            ),
            "native-message-2",
        )

    def test_datetime_parser_preserves_iso_and_current_time_fallback(self) -> None:
        self.assertEqual(
            _parse_datetime("2026-08-04T12:34:56+08:00"),
            datetime.fromisoformat("2026-08-04T12:34:56+08:00"),
        )
        for value in (None, "", "not-a-date"):
            with self.subTest(value=value):
                before = datetime.now(UTC)
                parsed = _parse_datetime(value)
                after = datetime.now(UTC)
                self.assertGreaterEqual(parsed, before)
                self.assertLessEqual(parsed, after)

    def test_moved_helper_keeps_required_identity_failures_and_runtime_ownership(self) -> None:
        with self.assertRaises(AttributeError):
            _normalize_inbound_message(
                channel_instance_id="qq-main",
                inbound=cast(
                    InboundMessage,
                    SimpleNamespace(
                        channel_id="qq",
                        conversation_id="c2c:user-1",
                        user_id="user-1",
                    ),
                ),
                reply_to_message_id=None,
            )

        from imagent.interaction.channels.adapters import runtime

        self.assertFalse(hasattr(runtime, "_normalize_inbound_message"))
        self.assertFalse(hasattr(runtime, "_parse_datetime"))
        self.assertFalse(hasattr(runtime, "AttachmentContent"))
        self.assertFalse(hasattr(runtime, "LocalPath"))
        self.assertFalse(hasattr(runtime, "TextContent"))
        self.assertFalse(hasattr(runtime, "TextFormat"))


class InboundAdmissionTransactionTests(unittest.IsolatedAsyncioTestCase):
    def _native(self, message_id: str) -> InboundMessage:
        return InboundMessage(
            channel_id="qq",
            conversation_id="c2c:user-1",
            user_id="user-1",
            message_id=message_id,
            text=message_id,
        )

    @staticmethod
    def _normalize(
        inbound: InboundMessage,
        *,
        reply_to_message_id: str | None,
    ) -> InteractionInboundMessage:
        return _normalize_inbound_message(
            channel_instance_id="qq-main",
            inbound=inbound,
            reply_to_message_id=reply_to_message_id,
        )

    async def test_admission_precedes_preparation_and_transfer_fences_delivery(self) -> None:
        events: list[str] = []
        delivered: list[InteractionInboundMessage] = []

        class Admission:
            async def deliver(self, message: InteractionInboundMessage) -> None:
                events.append("deliver")
                delivered.append(message)

            async def release(self) -> None:
                events.append("release")

        admission = Admission()

        async def on_admission(
            _conversation_ref: ConversationRef,
            _message_id: str,
        ) -> InboundAdmission:
            events.append("admission")
            return cast(InboundAdmission, admission)

        async def prepare(inbound: InboundMessage) -> InboundMessage:
            events.append("prepare")
            return inbound

        async def on_message(_message: InteractionInboundMessage) -> None:
            raise AssertionError("admitted messages must use the opaque lease")

        transaction = _InboundAdmissionTransaction(
            channel_instance_id="qq-main",
            on_message=on_message,
            on_admission=on_admission,
        )
        await transaction.run(
            self._native("message-order"),
            normalize_inbound=self._normalize,
            prepare_inbound=prepare,
        )
        await transaction.run(
            self._native("message-order"),
            normalize_inbound=self._normalize,
            prepare_inbound=prepare,
        )

        self.assertEqual(events, ["admission", "prepare", "deliver"])
        self.assertEqual([message.message_id for message in delivered], ["message-order"])

    async def test_no_lease_discards_key_and_allows_later_reclaim(self) -> None:
        attempts = 0
        prepared = 0
        delivered: list[InteractionInboundMessage] = []

        class Admission:
            async def deliver(self, message: InteractionInboundMessage) -> None:
                delivered.append(message)

            async def release(self) -> None:
                raise AssertionError("a delivered lease must not be released")

        async def on_admission(
            _conversation_ref: ConversationRef,
            _message_id: str,
        ) -> InboundAdmission | None:
            nonlocal attempts
            attempts += 1
            return None if attempts == 1 else cast(InboundAdmission, Admission())

        async def prepare(inbound: InboundMessage) -> InboundMessage:
            nonlocal prepared
            prepared += 1
            return inbound

        async def on_message(_message: InteractionInboundMessage) -> None:
            raise AssertionError("admission callback is configured")

        transaction = _InboundAdmissionTransaction(
            channel_instance_id="qq-main",
            on_message=on_message,
            on_admission=on_admission,
        )
        inbound = self._native("message-reclaim")
        await transaction.run(inbound, normalize_inbound=self._normalize, prepare_inbound=prepare)
        await transaction.run(inbound, normalize_inbound=self._normalize, prepare_inbound=prepare)

        self.assertEqual(attempts, 2)
        self.assertEqual(prepared, 1)
        self.assertEqual([message.message_id for message in delivered], ["message-reclaim"])

    async def test_async_preparation_and_pre_handoff_failure_release_clean_up_key(self) -> None:
        released = 0
        attempts = 0
        preparation_error = RuntimeError("media preparation failed")

        class Admission:
            async def deliver(self, _message: InteractionInboundMessage) -> None:
                return None

            async def release(self) -> None:
                nonlocal released
                released += 1

        async def on_admission(
            _conversation_ref: ConversationRef,
            _message_id: str,
        ) -> InboundAdmission:
            nonlocal attempts
            attempts += 1
            return cast(InboundAdmission, Admission())

        async def fail_preparation(inbound: InboundMessage) -> InboundMessage:
            del inbound
            raise preparation_error

        async def on_message(_message: InteractionInboundMessage) -> None:
            raise AssertionError("admission callback is configured")

        transaction = _InboundAdmissionTransaction(
            channel_instance_id="qq-main",
            on_message=on_message,
            on_admission=on_admission,
        )
        inbound = self._native("message-preparation-failure")
        with self.assertRaises(RuntimeError) as raised:
            await transaction.run(
                inbound,
                normalize_inbound=self._normalize,
                prepare_inbound=fail_preparation,
            )
        self.assertIs(raised.exception, preparation_error)
        self.assertEqual(released, 1)

        async def recover(inbound: InboundMessage) -> InboundMessage:
            return inbound

        await transaction.run(
            inbound,
            normalize_inbound=self._normalize,
            prepare_inbound=recover,
        )
        self.assertEqual(attempts, 2)

    async def test_release_failure_notes_original_and_handoff_failure_is_not_released(self) -> None:
        preparation_error = RuntimeError("preparation failed")
        handoff_error = RuntimeError("handoff failed")
        released = 0

        class FailingReleaseAdmission:
            async def deliver(self, _message: InteractionInboundMessage) -> None:
                raise AssertionError("failed preparation must not be delivered")

            async def release(self) -> None:
                raise RuntimeError("release failed")

        async def fail_admission(
            _conversation_ref: ConversationRef,
            _message_id: str,
        ) -> InboundAdmission:
            return cast(InboundAdmission, FailingReleaseAdmission())

        async def fail_preparation(inbound: InboundMessage) -> InboundMessage:
            del inbound
            raise preparation_error

        async def on_message(_message: InteractionInboundMessage) -> None:
            raise AssertionError("admission callback is configured")

        transaction = _InboundAdmissionTransaction(
            channel_instance_id="qq-main",
            on_message=on_message,
            on_admission=fail_admission,
        )
        with self.assertRaises(RuntimeError) as raised:
            await transaction.run(
                self._native("message-release-failure"),
                normalize_inbound=self._normalize,
                prepare_inbound=fail_preparation,
            )
        self.assertIs(raised.exception, preparation_error)
        self.assertTrue(any("release failed" in note for note in preparation_error.__notes__))

        class FailingDeliveryAdmission:
            async def deliver(self, _message: InteractionInboundMessage) -> None:
                raise handoff_error

            async def release(self) -> None:
                nonlocal released
                released += 1

        async def handoff_admission(
            _conversation_ref: ConversationRef,
            _message_id: str,
        ) -> InboundAdmission:
            return cast(InboundAdmission, FailingDeliveryAdmission())

        handoff_transaction = _InboundAdmissionTransaction(
            channel_instance_id="qq-main",
            on_message=on_message,
            on_admission=handoff_admission,
        )
        with self.assertRaises(RuntimeError) as raised:
            await handoff_transaction.run(
                self._native("message-handoff-failure"),
                normalize_inbound=self._normalize,
            )
        self.assertIs(raised.exception, handoff_error)
        self.assertEqual(released, 0)

    async def test_cancellation_during_preparation_releases_and_allows_reclaim(self) -> None:
        preparation_started = asyncio.Event()
        preparation_blocker = asyncio.Event()
        released = 0
        attempts = 0
        delivered = 0

        class Admission:
            async def deliver(self, _message: InteractionInboundMessage) -> None:
                nonlocal delivered
                delivered += 1

            async def release(self) -> None:
                nonlocal released
                released += 1

        async def on_admission(
            _conversation_ref: ConversationRef,
            _message_id: str,
        ) -> InboundAdmission:
            nonlocal attempts
            attempts += 1
            return cast(InboundAdmission, Admission())

        async def blocked_preparation(inbound: InboundMessage) -> InboundMessage:
            preparation_started.set()
            await preparation_blocker.wait()
            return inbound

        async def on_message(_message: InteractionInboundMessage) -> None:
            raise AssertionError("admission callback is configured")

        transaction = _InboundAdmissionTransaction(
            channel_instance_id="qq-main",
            on_message=on_message,
            on_admission=on_admission,
        )
        inbound = self._native("message-cancelled-pre-handoff")
        task = asyncio.create_task(
            transaction.run(
                inbound,
                normalize_inbound=self._normalize,
                prepare_inbound=blocked_preparation,
            )
        )
        await preparation_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(released, 1)
        self.assertEqual(delivered, 0)
        await transaction.run(inbound, normalize_inbound=self._normalize)
        self.assertEqual(attempts, 2)
        self.assertEqual(delivered, 1)

    async def test_cancellation_after_handoff_never_releases_or_reauthorizes(self) -> None:
        delivery_started = asyncio.Event()
        delivery_blocker = asyncio.Event()
        attempts = 0
        deliveries = 0
        released = 0

        class Admission:
            async def deliver(self, _message: InteractionInboundMessage) -> None:
                nonlocal deliveries
                deliveries += 1
                delivery_started.set()
                await delivery_blocker.wait()

            async def release(self) -> None:
                nonlocal released
                released += 1

        async def on_admission(
            _conversation_ref: ConversationRef,
            _message_id: str,
        ) -> InboundAdmission | None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return cast(InboundAdmission, Admission())
            # The transient key is cleaned after cancellation, but only the
            # Gateway-owned durable claim may authorize a later redelivery.
            return None

        async def on_message(_message: InteractionInboundMessage) -> None:
            raise AssertionError("admission callback is configured")

        transaction = _InboundAdmissionTransaction(
            channel_instance_id="qq-main",
            on_message=on_message,
            on_admission=on_admission,
        )
        inbound = self._native("message-cancelled-post-handoff")
        task = asyncio.create_task(transaction.run(inbound, normalize_inbound=self._normalize))
        await delivery_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(released, 0)
        self.assertEqual(deliveries, 1)
        await transaction.run(inbound, normalize_inbound=self._normalize)
        self.assertEqual(attempts, 2)
        self.assertEqual(deliveries, 1)

    async def test_without_admission_callback_delivers_directly_and_keeps_bound(self) -> None:
        delivered: list[InteractionInboundMessage] = []

        async def on_message(message: InteractionInboundMessage) -> None:
            delivered.append(message)

        transaction = _InboundAdmissionTransaction(
            channel_instance_id="qq-main",
            on_message=on_message,
            on_admission=None,
        )

        def prepare(inbound: InboundMessage) -> InboundMessage:
            return inbound

        await transaction.run(
            self._native("message-direct"),
            normalize_inbound=self._normalize,
            prepare_inbound=prepare,
        )
        await transaction.run(
            self._native("message-direct"),
            normalize_inbound=self._normalize,
        )

        self.assertEqual([message.message_id for message in delivered], ["message-direct"])
        self.assertEqual(_TRANSIENT_ADMISSION_LIMIT, 16_384)

        async def ignore(_message: InteractionInboundMessage) -> None:
            return None

        bounded_transaction = _InboundAdmissionTransaction(
            channel_instance_id="qq-main",
            on_message=ignore,
            on_admission=None,
        )
        for index in range(_TRANSIENT_ADMISSION_LIMIT + 1):
            await bounded_transaction.run(
                self._native(f"message-bound-{index}"),
                normalize_inbound=self._normalize,
            )
        self.assertEqual(len(bounded_transaction._admitted_inbound), _TRANSIENT_ADMISSION_LIMIT)


if __name__ == "__main__":
    unittest.main()

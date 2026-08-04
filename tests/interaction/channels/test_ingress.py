from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from imagent.interaction.channels.adapters.runtime import _InboundMiddleware
from imagent.interaction.channels.ingress import (
    ChannelAccessPolicy,
    InboundAttachment,
    InboundMessage,
    _normalize_inbound_message,
    _parse_datetime,
    parse_id_set,
)
from imagent.interaction.media import AttachmentContent, LocalPath
from imagent.interaction.messages import ConversationRef, TextContent


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
        inbound = SimpleNamespace(
            channel_id="qq",
            conversation_id="c2c:user-1",
            user_id="user-1",
            message_id="native-message-1",
            reply_to_message_id=42,
            sent_at="2026-08-04T12:34:56Z",
            input_error="attachment warning",
            trace_id="trace-1",
            metadata={"forged": "ignored"},
        )
        content = (TextContent("hello"),)

        message = _normalize_inbound_message(
            channel_instance_id="qq-main",
            inbound=inbound,
            content=content,
            reply_to_message_id="override-message",
        )

        self.assertEqual(message.message_id, "native-message-1")
        self.assertEqual(
            message.conversation_ref,
            ConversationRef("qq-main", "c2c:user-1"),
        )
        self.assertEqual(message.sender, "user-1")
        self.assertEqual(message.content, content)
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
            content=(),
            reply_to_message_id=None,
        )
        self.assertEqual(fallback_reply.reply_to, 42)

    def test_runtime_keeps_text_then_attachment_order_and_route_context(self) -> None:
        async def ignore(_message) -> None:
            return None

        middleware = _InboundMiddleware(
            channel_instance_id="qq-main",
            on_message=ignore,
            on_admission=None,
        )
        inbound = SimpleNamespace(
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
            sent_at=None,
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
                inbound=SimpleNamespace(
                    channel_id="qq",
                    conversation_id="c2c:user-1",
                    user_id="user-1",
                ),
                content=(),
                reply_to_message_id=None,
            )

        from imagent.interaction.channels.adapters import runtime

        self.assertFalse(hasattr(runtime, "_normalize_inbound_message"))
        self.assertFalse(hasattr(runtime, "_parse_datetime"))


if __name__ == "__main__":
    unittest.main()

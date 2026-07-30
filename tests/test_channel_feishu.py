from __future__ import annotations

import unittest
import warnings
from types import SimpleNamespace

from imagent.channels.native.access import ChannelAccessPolicy
from imagent.channels.native.feishu import (
    FEISHU_DOMAIN,
    LARK_DOMAIN,
    FeishuChannelAdapter,
)
from imagent.channels.native.models import OutboundMessage


def _message(
    *,
    text: str = "inspect repo",
    chat_type: str = "p2p",
    thread_id: str | None = None,
    mentioned_bot: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="message-1",
        message_id="message-1",
        raw_content_type="text",
        content_text=text,
        resources=[],
        mentioned_bot=mentioned_bot,
        conversation=SimpleNamespace(
            chat_id="chat-1",
            chat_type=chat_type,
            thread_id=thread_id,
        ),
        sender=SimpleNamespace(open_id="user-1"),
    )


class FeishuChannelTests(unittest.IsolatedAsyncioTestCase):
    def _adapter(self, **overrides) -> FeishuChannelAdapter:
        values = {
            "enabled": True,
            "app_id": "app",
            "app_secret": "secret",
            "middleware": object(),
            "access_policy": ChannelAccessPolicy.allow_all(),
        }
        values.update(overrides)
        return FeishuChannelAdapter(**values)

    def test_domains_are_explicit(self) -> None:
        self.assertEqual(self._adapter(domain="feishu").domain, FEISHU_DOMAIN)
        self.assertEqual(self._adapter(domain="lark").domain, LARK_DOMAIN)
        with self.assertRaisesRegex(ValueError, "must be 'feishu' or 'lark'"):
            self._adapter(domain="example.com")

    def test_direct_and_topic_messages_preserve_native_thread_identity(self) -> None:
        adapter = self._adapter()
        direct = adapter.parse_inbound_message(_message())
        topic = adapter.parse_inbound_message(
            _message(
                text="@Agent inspect repo",
                chat_type="topic",
                thread_id="thread-1",
                mentioned_bot=True,
            )
        )

        self.assertIsNotNone(direct)
        self.assertIsNotNone(topic)
        assert direct is not None and topic is not None
        self.assertEqual(direct.conversation_id, "chat:chat-1")
        self.assertEqual(direct.user_id, "user-1")
        self.assertEqual(topic.conversation_id, "chat:chat-1:thread:thread-1")

    def test_optional_sdk_uses_strict_bounded_security_configuration(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            sdk = self._adapter()._create_sdk()
            try:
                self.assertEqual(sdk.config.security.mode, "strict")
                self.assertFalse(sdk.config.security.allow_insecure_ws)
                self.assertFalse(sdk.config.security.allow_local_insecure_ws)
                self.assertTrue(sdk.config.inbound.media_capabilities.image)
                self.assertTrue(sdk.config.inbound.media_capabilities.file)
                self.assertFalse(sdk.config.safety.dedup.enabled)
                self.assertEqual(sdk.config.safety.dedup.max_entries, 0)
            finally:
                sdk.stop(join_timeout=0.1)

    async def test_single_text_delivery_preserves_platform_message_id(self) -> None:
        class FakeSDK:
            async def send(self, *_args, **_kwargs):
                return SimpleNamespace(success=True, data=SimpleNamespace(message_id="om_1"))

        adapter = self._adapter()
        adapter._sdk = FakeSDK()

        result = await adapter.send_message(
            OutboundMessage(
                channel_id="feishu",
                conversation_id="chat:chat-1",
                message_type="plain",
                text="hello",
            )
        )

        self.assertEqual(result.native_message_ids, ("om_1",))


if __name__ == "__main__":
    unittest.main()

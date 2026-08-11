from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from imagent.interaction.channels.adapters.feishu import (
    FEISHU_DOMAIN,
    LARK_DOMAIN,
    FeishuChannelAdapter,
)
from imagent.interaction.channels.ingress import ChannelAccessPolicy
from imagent.interaction.channels.outbound_delivery import OutboundArtifact, OutboundMessage


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

    def test_direct_and_topic_messages_preserve_thread_identity(self) -> None:
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

    async def test_native_upload_uses_descriptor_bound_bytes_after_swap(self) -> None:
        class FakeSDK:
            uploaded: bytes | None = None

            async def send(self, _chat_id, outbound, _opts):
                self.uploaded = outbound["file"]["source"]
                return SimpleNamespace(success=True)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "trusted"
            root.mkdir()
            source = root / "result.bin"
            source.write_bytes(b"trusted")
            outside = Path(directory) / "outside.bin"
            outside.write_bytes(b"attacker")
            adapter = self._adapter(outbound_media_dir=root)
            artifact = OutboundArtifact(
                kind="file",
                local_path=str(source),
                content_type="application/octet-stream",
                filename="result.bin",
                size_bytes=7,
                sha256=hashlib.sha256(b"trusted").hexdigest(),
            )
            message = OutboundMessage(
                channel_id="feishu",
                conversation_id="chat:chat-1",
                message_type="file",
                text="",
                metadata={"delivery_id": "delivery-safe"},
            )
            original_read = os.read
            swapped = False

            def replace_after_open(descriptor: int, size: int) -> bytes:
                nonlocal swapped
                if not swapped:
                    source.unlink()
                    source.symlink_to(outside)
                    swapped = True
                return original_read(descriptor, size)

            sdk = FakeSDK()
            with patch("imagent.interaction.media.os.read", replace_after_open):
                await adapter._send_artifact(
                    sdk,
                    chat_id="chat-1",
                    artifact=artifact,
                    message=message,
                    reply_to="",
                    reply_in_thread=False,
                )

            self.assertTrue(swapped)
            self.assertEqual(sdk.uploaded, b"trusted")


if __name__ == "__main__":
    unittest.main()

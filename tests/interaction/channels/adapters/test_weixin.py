from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imagent import __version__
from imagent.interaction.channels.adapters.weixin import WeixinChannelAdapter
from imagent.interaction.channels.adapters.weixin_ilink import (
    BASE_INFO,
    WeixinILinkTransport,
)
from imagent.interaction.channels.adapters.weixin_state import (
    WeixinCredentials,
    WeixinStateStore,
    WeixinTransportState,
)
from imagent.interaction.channels.ingress import ChannelAccessPolicy
from imagent.interaction.channels.outbound_delivery import OutboundArtifact, OutboundMessage


def _raw_message(*, text: str = "inspect repo") -> dict[str, object]:
    return {
        "message_id": 123,
        "from_user_id": "owner@im.wechat",
        "message_type": 1,
        "message_state": 2,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
        "context_token": "context-secret",
    }


class WeixinChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_upload_uses_descriptor_bound_bytes_after_swap(self) -> None:
        class FakeTransport:
            uploaded: bytes | None = None

            async def send_artifact(self, **kwargs: object) -> None:
                self.uploaded = kwargs["content"]  # type: ignore[assignment]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "trusted"
            root.mkdir()
            source = root / "result.bin"
            source.write_bytes(b"trusted")
            outside = Path(directory) / "outside.bin"
            outside.write_bytes(b"attacker")
            adapter = WeixinChannelAdapter(
                enabled=True,
                middleware=object(),
                state_dir=Path(directory) / "state",
                outbound_media_dir=root,
                access_policy=ChannelAccessPolicy.allow_all(),
            )
            transport = FakeTransport()
            adapter._transport = transport
            adapter._state.set_context_token("owner@im.wechat", "context-secret")
            message = OutboundMessage(
                channel_id="weixin",
                conversation_id="user:owner@im.wechat",
                message_type="file",
                text="",
                metadata={"delivery_id": "delivery-safe"},
                artifacts=[
                    OutboundArtifact(
                        kind="file",
                        local_path=str(source),
                        content_type="application/octet-stream",
                        filename="result.bin",
                        size_bytes=7,
                        sha256=hashlib.sha256(b"trusted").hexdigest(),
                    )
                ],
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

            with patch("imagent.interaction.media.os.read", replace_after_open):
                await adapter.send_message(message)

            self.assertTrue(swapped)
            self.assertEqual(transport.uploaded, b"trusted")

    def test_protocol_identity_uses_the_sdk_package_version(self) -> None:
        self.assertEqual(BASE_INFO["channel_version"], __version__)
        self.assertEqual(BASE_INFO["bot_agent"], f"im-agent-sdk/{__version__}")

    def test_direct_text_is_normalized_and_group_messages_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = WeixinChannelAdapter(
                enabled=True,
                middleware=object(),
                state_dir=Path(directory),
            )
            direct = adapter.parse_inbound_message(_raw_message())
            group = adapter.parse_inbound_message(
                {**_raw_message(), "group_id": "group-1"},
            )

        self.assertIsNotNone(direct)
        assert direct is not None
        self.assertEqual(direct.conversation_id, "user:owner@im.wechat")
        self.assertEqual(direct.message_id, "123")
        self.assertEqual(direct.text, "inspect repo")
        self.assertIsNone(group)

    def test_state_store_round_trips_channel_owned_reconnect_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WeixinStateStore(Path(directory))
            credentials = WeixinCredentials(
                account_id="bot@im.bot",
                bot_token="bot-secret",
                base_url="https://ilinkai.weixin.qq.com",
                owner_user_id="owner@im.wechat",
            )
            state = WeixinTransportState(get_updates_buf="cursor")
            state.set_context_token("owner@im.wechat", "context-secret")

            store.save_credentials(credentials)
            store.save_transport_state(state)

            loaded_credentials = store.load_credentials()
            self.assertIsNotNone(loaded_credentials)
            assert loaded_credentials is not None
            self.assertEqual(loaded_credentials.bot_token, "bot-secret")
            self.assertEqual(store.load_transport_state(), state)

    def test_ilink_transport_rejects_nonofficial_or_insecure_origins(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS origin"):
            WeixinILinkTransport(base_url="http://ilinkai.weixin.qq.com")
        with self.assertRaisesRegex(ValueError, "official weixin.qq.com host"):
            WeixinILinkTransport(base_url="https://attacker.example")

    async def test_client_delivery_identity_is_not_reported_as_platform_message_id(
        self,
    ) -> None:
        class FakeTransport:
            async def send_text(self, **_kwargs) -> str:
                return "sdk-generated-client-id"

        with tempfile.TemporaryDirectory() as directory:
            adapter = WeixinChannelAdapter(
                enabled=True,
                middleware=object(),
                state_dir=Path(directory),
                access_policy=ChannelAccessPolicy.allow_all(),
            )
            adapter._transport = FakeTransport()
            adapter._state.set_context_token(
                "owner@im.wechat",
                "context-secret",
            )

            result = await adapter.send_message(
                OutboundMessage(
                    channel_id="weixin",
                    conversation_id="user:owner@im.wechat",
                    message_type="plain",
                    text="hello",
                )
            )

        self.assertEqual(result.native_message_ids, ())


if __name__ == "__main__":
    unittest.main()

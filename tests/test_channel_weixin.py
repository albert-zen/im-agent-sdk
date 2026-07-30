from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from imagent import __version__
from imagent.channels.native.access import ChannelAccessPolicy
from imagent.channels.native.models import OutboundMessage
from imagent.channels.native.weixin import WeixinChannelAdapter
from imagent.channels.native.weixin_ilink import BASE_INFO, WeixinILinkTransport
from imagent.channels.native.weixin_state import (
    WeixinCredentials,
    WeixinStateStore,
    WeixinTransportState,
)


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

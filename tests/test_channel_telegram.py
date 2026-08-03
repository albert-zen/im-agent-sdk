from __future__ import annotations

import unittest
from typing import Any, cast

from imagent.channels.native.telegram import TelegramChannelAdapter
from imagent.interaction.channels.ingress import ChannelAccessPolicy
from imagent.interaction.channels.outbound_delivery import OutboundMessage


class TelegramChannelTests(unittest.IsolatedAsyncioTestCase):
    def _adapter(self, **overrides) -> TelegramChannelAdapter:
        values = {
            "enabled": True,
            "bot_token": "test-token",
            "middleware": object(),
            "access_policy": ChannelAccessPolicy.allow_all(),
            "http_client": object(),
        }
        values.update(overrides)
        return TelegramChannelAdapter(**values)

    def test_private_and_forum_messages_keep_distinct_native_routes(self) -> None:
        private = self._adapter().parse_inbound_update(
            {
                "update_id": 10,
                "message": {
                    "message_id": 7,
                    "from": {"id": 42, "is_bot": False},
                    "chat": {"id": 42, "type": "private"},
                    "text": "inspect repo",
                },
            }
        )
        topic = self._adapter(require_mention=True).parse_inbound_update(
            {
                "message": {
                    "message_id": 9,
                    "message_thread_id": 77,
                    "is_topic_message": True,
                    "from": {"id": 42, "is_bot": False},
                    "chat": {"id": -1001, "type": "supergroup"},
                    "text": "/status",
                }
            }
        )

        self.assertIsNotNone(private)
        self.assertIsNotNone(topic)
        assert private is not None and topic is not None
        self.assertEqual(private[0].conversation_id, "chat:42")
        self.assertEqual(private[0].message_id, "42:7")
        self.assertEqual(private[1], "7")
        self.assertEqual(topic[0].conversation_id, "chat:-1001:topic:77")

    def test_api_endpoint_rejects_embedded_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain userinfo"):
            self._adapter(
                api_base="https://user:password@api.telegram.org",
            ).validate_startup_configuration()

    async def test_single_text_delivery_preserves_platform_message_id(self) -> None:
        class SendingTelegram(TelegramChannelAdapter):
            async def _api_call(
                self,
                method: str,
                body: dict[str, object],
                **_options: Any,
            ) -> object:
                self.last_call = (method, body)
                return {"message_id": 42}

        adapter = SendingTelegram(
            enabled=True,
            bot_token="test-token",
            middleware=object(),
            access_policy=ChannelAccessPolicy.allow_all(),
            http_client=cast(Any, object()),
        )

        result = await adapter.send_message(
            OutboundMessage(
                channel_id="telegram",
                conversation_id="chat:7",
                message_type="plain",
                text="hello",
            )
        )

        self.assertEqual(result.native_message_ids, ("42",))
        self.assertEqual(adapter.last_call[0], "sendMessage")


if __name__ == "__main__":
    unittest.main()

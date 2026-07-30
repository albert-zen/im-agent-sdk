from __future__ import annotations

import time
import unittest
from typing import Any, cast

from imagent.channels.native.models import OutboundArtifact, OutboundMessage
from imagent.channels.native.qq import QQ_TEXT_LIMIT, QQChannelAdapter


class QQChannelTests(unittest.IsolatedAsyncioTestCase):
    def _adapter(self, **overrides) -> QQChannelAdapter:
        values = {
            "enabled": True,
            "app_id": "app",
            "client_secret": "secret",
            "middleware": object(),
            "http_client": object(),
        }
        values.update(overrides)
        return QQChannelAdapter(**values)

    def test_group_mention_is_normalized_to_stable_route_ids(self) -> None:
        inbound = self._adapter().parse_inbound_event(
            "GROUP_AT_MESSAGE_CREATE",
            {
                "id": "message-1",
                "content": "<@123>  inspect repo",
                "group_openid": "group-1",
                "author": {"member_openid": "user-1"},
            },
        )

        self.assertIsNotNone(inbound)
        assert inbound is not None
        self.assertEqual(inbound.conversation_id, "group:group-1")
        self.assertEqual(inbound.user_id, "user-1")
        self.assertEqual(inbound.text, "inspect repo")

    def test_artifact_delivery_identity_uses_attachment_id_not_staging_path(
        self,
    ) -> None:
        message = OutboundMessage(
            channel_id="qq",
            conversation_id="c2c:user",
            message_type="file",
            text="",
            metadata={"delivery_id": "delivery-1"},
        )
        original = OutboundArtifact(
            kind="file",
            local_path="D:/first/result.bin",
            content_type="application/octet-stream",
            filename="result.bin",
            size_bytes=3,
            sha256="first",
            attachment_id="artifact-1",
        )
        moved = OutboundArtifact(
            kind="file",
            local_path="E:/second/result.bin",
            content_type="application/octet-stream",
            filename="renamed.bin",
            size_bytes=4,
            sha256="second",
            attachment_id="artifact-1",
        )

        self.assertEqual(
            QQChannelAdapter._artifact_delivery_id(message, original),
            QQChannelAdapter._artifact_delivery_id(message, moved),
        )

    def test_startup_validation_normalizes_and_rejects_unsafe_configuration(self) -> None:
        normalized = self._adapter(
            app_id="  app-id  ",
            client_secret="  client-secret  ",
            api_base="  https://api.sgroup.qq.com/  ",
        )
        normalized.validate_startup_configuration()
        self.assertEqual(normalized.app_id, "app-id")
        self.assertEqual(normalized.client_secret, "client-secret")
        self.assertEqual(normalized.api_base, "https://api.sgroup.qq.com")

        with self.assertRaisesRegex(RuntimeError, "requires app_id and client_secret"):
            self._adapter(app_id=" ").validate_startup_configuration()
        with self.assertRaisesRegex(ValueError, r"HTTP\(S\) URL"):
            self._adapter(api_base="ftp://api.sgroup.qq.com").validate_startup_configuration()

    async def test_long_outbound_text_is_segmented_in_order_with_stable_ids(self) -> None:
        class CapturingQQ(QQChannelAdapter):
            def __init__(self) -> None:
                super().__init__(
                    enabled=True,
                    app_id="app",
                    client_secret="secret",
                    middleware=object(),
                    http_client=cast(Any, object()),
                    markdown_enabled=False,
                )
                self.bodies: list[dict[str, Any]] = []

            async def _get_access_token(self) -> str:
                return "token"

            async def _post_message(
                self,
                *,
                path: str,
                token: str,
                body: dict[str, Any],
            ) -> None:
                self.assert_request(path=path, token=token)
                self.bodies.append(body)

            @staticmethod
            def assert_request(*, path: str, token: str) -> None:
                if path != "/v2/users/user-1/messages" or token != "token":
                    raise AssertionError((path, token))

        adapter = CapturingQQ()
        text = "a" * (QQ_TEXT_LIMIT * 2 + 1)
        message = OutboundMessage(
            channel_id="qq",
            conversation_id="c2c:user-1",
            message_type="markdown",
            text=text,
            metadata={
                "delivery_id": "delivery-long",
                "reply_to_message_id": "reply-1",
                "reply_to_seen_at": time.time(),
            },
        )

        await adapter.send_message(message)

        self.assertEqual(
            [body["content"] for body in adapter.bodies],
            [
                "a" * QQ_TEXT_LIMIT,
                "a" * QQ_TEXT_LIMIT,
                "a",
            ],
        )
        self.assertEqual(adapter.bodies[0]["msg_id"], "reply-1")
        self.assertNotIn("msg_id", adapter.bodies[1])
        self.assertEqual(
            len({body["msg_seq"] for body in adapter.bodies}),
            len(adapter.bodies),
        )


if __name__ == "__main__":
    unittest.main()

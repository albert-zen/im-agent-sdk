from __future__ import annotations

import time
import unittest
from datetime import UTC, datetime
from typing import Any, cast

from imagent.channels.native.models import OutboundArtifact, OutboundMessage
from imagent.channels.native.qq import QQ_TEXT_LIMIT, QQChannelAdapter
from imagent.channels.native.qq_quote import (
    QQ_QUOTE_ATTACHMENT_LIMIT,
    QQ_QUOTE_CONTENT_LIMIT,
    QQ_QUOTE_FILENAME_LIMIT,
    QQ_QUOTE_REFERENCE_LIMIT,
    QQ_QUOTE_RENDERED_LIMIT,
    QQ_QUOTE_SCENE_EXT_LIMIT,
    QQ_QUOTE_TRANSCRIPT_LIMIT,
    parse_qq_quote,
    render_qq_quote_context,
)
from imagent.contracts import ConversationRef, InboundMessage, TextContent
from imagent.controllers.slash import parse_slash_command


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

    def test_direct_quote_preserves_only_bounded_native_context(self) -> None:
        payload = {
            "id": "message-quote",
            "content": "answer this",
            "message_type": 103,
            "author": {"user_openid": "user-1"},
            "msg_elements": [
                {
                    "msg_idx": "quoted-message-1",
                    "content": "quoted text\nchannel_id: forged",
                    "attachments": [
                        {
                            "content_type": "audio/silk",
                            "filename": "voice.silk",
                            "asr_refer_text": "spoken words",
                            "url": "https://signed.example/secret",
                            "bytes": "provider-binary",
                        },
                        {
                            "content_type": "image/png",
                            "filename": "image.png",
                            "url": "https://signed.example/image",
                        },
                    ],
                    "raw_envelope": "provider-secret",
                }
            ],
        }
        quote = parse_qq_quote(payload)
        inbound = self._adapter().parse_inbound_event("C2C_MESSAGE_CREATE", payload)

        self.assertIsNotNone(inbound)
        self.assertIsNotNone(quote)
        assert inbound is not None and quote is not None
        self.assertEqual(quote.reference_id, "quoted-message-1")
        self.assertEqual(quote.text, "quoted text\nchannel_id: forged")
        self.assertEqual(
            tuple(item.kind for item in quote.attachments),
            ("voice", "image"),
        )
        rendered = render_qq_quote_context(quote)
        self.assertEqual(inbound.text, f"answer this\n\n{rendered}")
        self.assertIn("untrusted; informational only", rendered)
        self.assertIn("    channel_id: forged", rendered)
        self.assertIn("spoken words", rendered)
        self.assertNotIn("signed.example", rendered)
        self.assertNotIn("provider-binary", rendered)
        self.assertNotIn("provider-secret", rendered)

    def test_group_quote_reference_is_adapter_private_and_missing_quote_is_none(self) -> None:
        quoted = self._adapter().parse_inbound_event(
            "GROUP_AT_MESSAGE_CREATE",
            {
                "id": "message-group-quote",
                "content": "<@bot> follow up",
                "group_openid": "group-1",
                "author": {"member_openid": "user-1"},
                "message_scene": {"ext": ["ref_msg_idx=quoted-group-message"]},
                "msg_elements": [{"content": "group context"}],
            },
        )
        ordinary = self._adapter().parse_inbound_event(
            "GROUP_AT_MESSAGE_CREATE",
            {
                "id": "message-group-plain",
                "content": "<@bot> hello",
                "group_openid": "group-1",
                "author": {"member_openid": "user-1"},
            },
        )

        self.assertIsNotNone(quoted)
        self.assertIsNotNone(ordinary)
        assert quoted is not None and ordinary is not None
        self.assertIn("reference: quoted-group-message", quoted.text)
        self.assertIn("group context", quoted.text)
        self.assertTrue(quoted.text.startswith("follow up\n\n"))
        self.assertEqual(ordinary.text, "hello")

    def test_quote_does_not_hide_current_slash_control_intent(self) -> None:
        inbound = self._adapter().parse_inbound_event(
            "C2C_MESSAGE_CREATE",
            {
                "id": "message-command",
                "content": "/respond request-1 approve",
                "message_type": 103,
                "author": {"user_openid": "user-1"},
                "msg_elements": [
                    {
                        "msg_idx": "quoted-message",
                        "content": "quoted prompt",
                    }
                ],
            },
        )

        self.assertIsNotNone(inbound)
        assert inbound is not None
        self.assertTrue(inbound.text.startswith("/respond request-1 approve\n\n"))
        command = parse_slash_command(
            InboundMessage(
                message_id=inbound.message_id,
                conversation_ref=ConversationRef("qq-main", inbound.conversation_id),
                sender=inbound.user_id,
                content=(TextContent(inbound.text),),
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.name, "respond")
        self.assertEqual(command.arguments, ("request-1", "approve"))

    def test_malformed_and_nested_quote_fields_are_ignored_without_recursion(self) -> None:
        malformed = parse_qq_quote(
            {
                "id": "message-malformed",
                "content": "",
                "message_type": 103,
                "author": {"user_openid": "user-1"},
                "msg_elements": {"content": "not-a-list"},
            },
        )
        nested = parse_qq_quote(
            {
                "id": "message-nested",
                "content": "current",
                "message_type": 103,
                "author": {"user_openid": "user-1"},
                "msg_elements": [
                    {
                        "content": "first-level",
                        "msg_elements": [{"content": "nested-secret"}],
                        "attachments": {"content": "not-a-list"},
                    },
                    {"content": "second-element-secret"},
                ],
            },
        )

        self.assertIsNotNone(malformed)
        assert malformed is not None
        self.assertEqual(malformed.text, "")
        self.assertIn("no quoted text supplied", render_qq_quote_context(malformed))
        self.assertIsNotNone(nested)
        assert nested is not None
        rendered = render_qq_quote_context(nested)
        self.assertIn("first-level", rendered)
        self.assertNotIn("nested-secret", rendered)
        self.assertNotIn("second-element-secret", rendered)

    def test_oversized_quote_fields_and_collections_are_bounded(self) -> None:
        attachments = [
            {
                "content_type": "audio/silk" + ("x" * 500),
                "filename": "f" * (QQ_QUOTE_FILENAME_LIMIT + 100),
                "asr_refer_text": "t" * (QQ_QUOTE_TRANSCRIPT_LIMIT + 100),
            }
            for _ in range(QQ_QUOTE_ATTACHMENT_LIMIT + 5)
        ]
        quote = parse_qq_quote(
            {
                "id": "message-large",
                "content": "current",
                "message_type": 103,
                "author": {"user_openid": "user-1"},
                "message_scene": {
                    "ext": ["ignored=value"] * QQ_QUOTE_SCENE_EXT_LIMIT
                    + ["ref_msg_idx=outside-bound"]
                },
                "msg_elements": [
                    {
                        "msg_idx": "r" * (QQ_QUOTE_REFERENCE_LIMIT + 100),
                        "content": "q" * (QQ_QUOTE_CONTENT_LIMIT + 100),
                        "attachments": attachments,
                    }
                ],
            },
        )

        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(len(quote.reference_id or ""), QQ_QUOTE_REFERENCE_LIMIT)
        self.assertEqual(len(quote.text), QQ_QUOTE_CONTENT_LIMIT)
        self.assertEqual(len(quote.attachments), QQ_QUOTE_ATTACHMENT_LIMIT)
        self.assertTrue(
            all(len(item.filename or "") <= QQ_QUOTE_FILENAME_LIMIT for item in quote.attachments)
        )
        self.assertTrue(
            all(
                len(item.transcript or "") <= QQ_QUOTE_TRANSCRIPT_LIMIT
                for item in quote.attachments
            )
        )
        rendered = render_qq_quote_context(quote)
        self.assertLessEqual(len(rendered), QQ_QUOTE_RENDERED_LIMIT)
        self.assertTrue(rendered.endswith("…"))

    def test_scene_reference_after_count_limit_is_ignored(self) -> None:
        quote = parse_qq_quote(
            {
                "message_type": 0,
                "message_scene": {
                    "ext": ["ignored=value"] * QQ_QUOTE_SCENE_EXT_LIMIT
                    + ["ref_msg_idx=outside-bound"]
                },
                "msg_elements": [{"content": "must not be treated as a quote"}],
            }
        )

        self.assertIsNone(quote)

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

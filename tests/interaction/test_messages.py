from __future__ import annotations

import importlib.util
import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import cast, get_args

from imagent.interaction.media import AttachmentContent, LocalPath
from imagent.interaction.messages import (
    Content,
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
    TextFormat,
)


class InteractionMessageFoundationTests(unittest.TestCase):
    def test_text_format_discriminants_remain_stable(self) -> None:
        self.assertEqual(TextFormat.PLAIN.value, "plain")
        self.assertEqual(TextFormat.MARKDOWN.value, "markdown")

    def test_historical_cross_layer_contract_facade_is_absent(self) -> None:
        self.assertIsNone(importlib.util.find_spec("imagent.contracts"))

    def test_content_union_is_closed_ordered_text_and_attachment(self) -> None:
        self.assertEqual(get_args(Content), (TextContent, AttachmentContent))
        content = (
            TextContent("before", TextFormat.MARKDOWN),
            AttachmentContent(
                attachment_id="attachment-1",
                media_type="image/png",
                source=LocalPath("/trusted/image.png"),
            ),
            TextContent("after"),
        )
        conversation = ConversationRef("channel-1", "conversation-1")
        message = OutboundMessage(
            delivery_id="delivery-1",
            conversation_ref=conversation,
            content=content,
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
        )

        self.assertEqual(message.content, content)
        first_text = cast(TextContent, message.content[0])
        last_text = cast(TextContent, message.content[2])
        self.assertIsInstance(first_text, TextContent)
        self.assertIsInstance(last_text, TextContent)
        self.assertIs(first_text.format, TextFormat.MARKDOWN)
        self.assertIs(last_text.format, TextFormat.PLAIN)

    def test_inbound_and_outbound_identities_remain_distinct_and_frozen(self) -> None:
        conversation = ConversationRef("channel-1", "conversation-1")
        created_at = datetime(2026, 8, 3, tzinfo=UTC)
        inbound = InboundMessage(
            message_id="native-message-1",
            conversation_ref=conversation,
            sender="sender-1",
            content=(TextContent("hello"),),
            created_at=created_at,
            reply_to="native-message-0",
            metadata={"native_kind": "text"},
        )
        outbound = OutboundMessage(
            delivery_id="delivery-1",
            conversation_ref=conversation,
            content=inbound.content,
            created_at=created_at,
            reply_to=inbound.message_id,
            metadata=inbound.metadata,
        )

        self.assertEqual(inbound.message_id, "native-message-1")
        self.assertEqual(outbound.delivery_id, "delivery-1")
        self.assertFalse(hasattr(inbound, "delivery_id"))
        self.assertFalse(hasattr(outbound, "message_id"))
        with self.assertRaises(FrozenInstanceError):
            inbound.sender = "other"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()

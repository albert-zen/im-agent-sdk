from __future__ import annotations

import unittest
from typing import Any, cast

from imagent.interaction.channels.outbound_delivery import (
    NativeDeliveryResult,
    OutboundArtifact,
    OutboundMessage,
    split_text,
)


class ChannelOutboundTextTests(unittest.TestCase):
    def test_native_delivery_result_defaults_to_no_platform_identity(self) -> None:
        self.assertEqual(NativeDeliveryResult().native_message_ids, ())

    def test_outbound_artifact_mappings_are_coerced_to_mutable_model_list(self) -> None:
        message = OutboundMessage(
            channel_id="qq",
            conversation_id="chat-1",
            message_type="agent",
            text="result",
            artifacts=cast(
                Any,
                [
                    {
                        "kind": "file",
                        "local_path": "/staged/result.txt",
                        "content_type": "text/plain",
                        "filename": "result.txt",
                        "size_bytes": 6,
                    }
                ],
            ),
        )

        self.assertEqual(len(message.artifacts), 1)
        self.assertIsInstance(message.artifacts[0], OutboundArtifact)
        message.artifacts.clear()
        self.assertEqual(message.artifacts, [])

    def test_limit_must_be_positive(self) -> None:
        for limit in (0, -1):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "positive"):
                    split_text("text", limit=limit)

    def test_empty_and_surrounding_whitespace_are_normalized(self) -> None:
        self.assertEqual(split_text(" \n ", limit=10), [])
        self.assertEqual(split_text("  short text  ", limit=20), ["short text"])

    def test_uses_paragraph_newline_and_space_soft_breaks(self) -> None:
        self.assertEqual(split_text("alpha1\n\nbeta", limit=10), ["alpha1", "beta"])
        self.assertEqual(split_text("alpha1\nbeta", limit=10), ["alpha1", "beta"])
        self.assertEqual(split_text("alpha beta gamma", limit=10), ["alpha beta", "gamma"])

    def test_hard_breaks_preserve_order(self) -> None:
        self.assertEqual(
            split_text("abcdefghijk", limit=5),
            ["abcde", "fghij", "k"],
        )

    def test_soft_break_before_half_limit_is_ignored(self) -> None:
        self.assertEqual(
            split_text("a bcdefghij", limit=10),
            ["a bcdefghi", "j"],
        )

    def test_unicode_code_points_are_not_split_into_encoded_units(self) -> None:
        self.assertEqual(split_text("你好世界🙂完成", limit=3), ["你好世", "界🙂完", "成"])


if __name__ == "__main__":
    unittest.main()

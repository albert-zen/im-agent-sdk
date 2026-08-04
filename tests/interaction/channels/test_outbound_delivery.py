from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from imagent.interaction.channels import DeliveryItemStatus
from imagent.interaction.channels.outbound_delivery import (
    ArtifactDeliveryReceipt,
    NativeDeliveryResult,
    OutboundArtifact,
    OutboundMessage,
    PermanentArtifactDeliveryError,
    _artifact_item_receipts,
    _to_native_artifact,
    _to_native_outbound,
    deliver_artifact_batch,
    read_managed_artifact,
    split_text,
    stable_artifact_identity,
)
from imagent.interaction.media import AttachmentContent, LocalPath, RemoteUrl
from imagent.interaction.messages import (
    ConversationRef,
    TextContent,
    TextFormat,
)
from imagent.interaction.messages import (
    OutboundMessage as PublicOutboundMessage,
)


class ChannelOutboundTextTests(unittest.TestCase):
    def test_historical_native_artifact_module_is_absent(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import importlib.util; "
                    "assert importlib.util.find_spec("
                    "'imagent.channels.native') is None"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_native_delivery_result_defaults_to_no_platform_identity(self) -> None:
        self.assertEqual(NativeDeliveryResult().native_message_ids, ())

    def test_native_artifact_receipts_have_one_outbound_owner(self) -> None:
        from imagent.interaction.channels.adapters import runtime

        self.assertFalse(hasattr(runtime, "_artifact_item_receipts"))

    def test_native_artifact_conversion_has_one_outbound_owner(self) -> None:
        from imagent.interaction.channels.adapters import runtime

        self.assertFalse(hasattr(runtime, "_to_native_artifact"))
        artifact = _to_native_artifact(
            AttachmentContent(
                attachment_id="attachment-1",
                media_type="image/png",
                source=LocalPath("/staged/preview.png"),
                size_bytes=7,
                metadata={"sha256": "digest"},
            )
        )

        self.assertEqual(
            (
                artifact.kind,
                artifact.local_path,
                artifact.content_type,
                artifact.filename,
                artifact.size_bytes,
                artifact.sha256,
                artifact.attachment_id,
            ),
            (
                "image",
                "/staged/preview.png",
                "image/png",
                "preview.png",
                7,
                "digest",
                "attachment-1",
            ),
        )
        for source in (RemoteUrl("https://media.example/preview.png"),):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "LocalPath"):
                    _to_native_artifact(
                        AttachmentContent(
                            attachment_id="attachment-2",
                            media_type="image/png",
                            source=source,
                            size_bytes=7,
                        )
                    )
        for size_bytes in (None, -1):
            with self.subTest(size_bytes=size_bytes):
                with self.assertRaisesRegex(ValueError, "non-negative"):
                    _to_native_artifact(
                        AttachmentContent(
                            attachment_id="attachment-3",
                            media_type="application/octet-stream",
                            source=LocalPath("/staged/data.bin"),
                            size_bytes=size_bytes,
                        )
                    )
        with self.assertRaisesRegex(ValueError, "filename"):
            _to_native_artifact(
                AttachmentContent(
                    attachment_id="attachment-4",
                    media_type="text/plain",
                    source=LocalPath("/staged/data.txt"),
                    filename="   ",
                    size_bytes=4,
                )
            )

    def test_native_message_conversion_has_one_outbound_owner(self) -> None:
        from imagent.interaction.channels.adapters import runtime

        self.assertFalse(hasattr(runtime, "_NATIVE_OWNED_METADATA_KEYS"))
        self.assertFalse(hasattr(runtime, "_to_native_outbound"))
        native = _to_native_outbound(
            channel_id="qq",
            message=PublicOutboundMessage(
                delivery_id="delivery-1",
                conversation_ref=ConversationRef("qq-main", "group:1"),
                content=(
                    TextContent("first"),
                    AttachmentContent(
                        attachment_id="attachment-1",
                        media_type="image/png",
                        source=LocalPath("/staged/image.png"),
                        filename="image.png",
                        size_bytes=3,
                    ),
                    TextContent("second", format=TextFormat.MARKDOWN),
                ),
                created_at=datetime.now(UTC),
                reply_to="reply-1",
                metadata={
                    "custom": "kept",
                    "delivery_id": "forged",
                    "message_id": "forged",
                    "artifact_receipts": [],
                    "reply_to_message_id": "forged",
                    "qq_reply_identity_pinned": True,
                },
            ),
        )

        self.assertEqual(native.channel_id, "qq")
        self.assertEqual(native.conversation_id, "group:1")
        self.assertEqual(native.message_type, "markdown")
        self.assertEqual(native.text, "first\nsecond")
        self.assertEqual(
            native.metadata,
            {
                "custom": "kept",
                "delivery_id": "delivery-1",
                "reply_to_message_id": "reply-1",
            },
        )
        self.assertEqual(tuple(item.attachment_id for item in native.artifacts), ("attachment-1",))

    def test_native_artifact_receipts_are_sorted_and_ignore_unknown_entries(self) -> None:
        receipts = _artifact_item_receipts(
            {
                "artifact_receipts": [
                    {
                        "attachment_id": "second",
                        "status": "delivered",
                        "platform_message_id": "native-2",
                    },
                    "malformed",
                    {
                        "attachment_id": "unknown",
                        "status": "delivered",
                        "platform_message_id": "native-unknown",
                    },
                    {
                        "attachment_id": "first",
                        "status": "failed",
                        "error": "unsupported",
                    },
                ]
            },
            item_indexes={"first": 2, "second": 0},
        )

        self.assertEqual(
            tuple(
                (
                    receipt.content_index,
                    receipt.attachment_id,
                    receipt.status,
                    receipt.native_message_id,
                    receipt.detail,
                )
                for receipt in receipts
            ),
            (
                (0, "second", DeliveryItemStatus.ACCEPTED, "native-2", None),
                (2, "first", DeliveryItemStatus.REJECTED, None, "unsupported"),
            ),
        )

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

    def test_artifact_identity_prefers_stable_attachment_id_over_path(self) -> None:
        message = OutboundMessage(
            channel_id="qq",
            conversation_id="chat-1",
            message_type="agent",
            text="",
            metadata={"delivery_id": "delivery-1"},
        )
        first = _artifact(
            attachment_id="attachment-1",
            local_path="/first/staging/result.bin",
        )
        moved = _artifact(
            attachment_id="attachment-1",
            local_path="/second/staging/result.bin",
        )

        self.assertEqual(
            stable_artifact_identity(message, first),
            stable_artifact_identity(message, moved),
        )


class ChannelArtifactDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_permanent_failure_is_recorded_and_does_not_block_suffix(self) -> None:
        first = _artifact(attachment_id="first", filename="first.bin")
        second = _artifact(attachment_id="second", filename="second.bin")
        message = _message(first, second)
        attempts: list[str] = []

        async def send_one(artifact: OutboundArtifact) -> ArtifactDeliveryReceipt:
            attempts.append(artifact.attachment_id)
            if artifact is first:
                raise PermanentArtifactDeliveryError("unsupported")
            return ArtifactDeliveryReceipt(platform_message_id="native-2")

        await deliver_artifact_batch(message, send_one)

        self.assertEqual(attempts, ["first", "second"])
        self.assertEqual(message.artifacts, [])
        self.assertEqual(
            [receipt["status"] for receipt in message.metadata["artifact_receipts"]],
            ["failed", "delivered"],
        )
        self.assertIn("first.bin: unsupported", message.text)

    async def test_unclassified_failure_retains_failed_and_unattempted_suffix(self) -> None:
        first = _artifact(attachment_id="first")
        second = _artifact(attachment_id="second")
        third = _artifact(attachment_id="third")
        message = _message(first, second, third)

        async def send_one(artifact: OutboundArtifact) -> ArtifactDeliveryReceipt:
            if artifact is second:
                raise RuntimeError("outcome unknown")
            return ArtifactDeliveryReceipt(platform_message_id="native-1")

        with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            await deliver_artifact_batch(message, send_one)

        self.assertEqual(message.artifacts, [second, third])
        self.assertEqual(
            [receipt["attachment_id"] for receipt in message.metadata["artifact_receipts"]],
            ["first"],
        )

    async def test_cancellation_retains_failed_and_unattempted_suffix(self) -> None:
        first = _artifact(attachment_id="first")
        second = _artifact(attachment_id="second")
        message = _message(first, second)

        async def send_one(artifact: OutboundArtifact) -> ArtifactDeliveryReceipt:
            if artifact is first:
                raise asyncio.CancelledError
            raise AssertionError("unattempted artifact was submitted")

        with self.assertRaises(asyncio.CancelledError):
            await deliver_artifact_batch(message, send_one)

        self.assertEqual(message.artifacts, [first, second])

    async def test_trusted_root_read_verifies_containment_size_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "trusted"
            root.mkdir()
            source = root / "result.bin"
            source.write_bytes(b"safe")
            digest = hashlib.sha256(b"safe").hexdigest()
            artifact = _artifact(
                local_path=str(source),
                size_bytes=4,
                sha256=digest,
            )

            resolved, content = await read_managed_artifact(artifact, root=root)
            self.assertEqual(resolved, source.resolve())
            self.assertEqual(content, b"safe")

            outside = Path(directory) / "outside.bin"
            outside.write_bytes(b"safe")
            with self.assertRaisesRegex(PermanentArtifactDeliveryError, "trusted root"):
                await read_managed_artifact(
                    _artifact(local_path=str(outside), size_bytes=4, sha256=digest),
                    root=root,
                )
            with self.assertRaisesRegex(PermanentArtifactDeliveryError, "no longer exists"):
                await read_managed_artifact(
                    _artifact(local_path=str(root / "missing.bin")),
                    root=root,
                )
            directory_source = root / "directory.bin"
            directory_source.mkdir()
            with self.assertRaisesRegex(PermanentArtifactDeliveryError, "regular file"):
                await read_managed_artifact(
                    _artifact(local_path=str(directory_source)),
                    root=root,
                )
            with self.assertRaisesRegex(PermanentArtifactDeliveryError, "changed"):
                await read_managed_artifact(
                    _artifact(local_path=str(source), size_bytes=5, sha256=digest),
                    root=root,
                )
            with self.assertRaisesRegex(PermanentArtifactDeliveryError, "changed"):
                await read_managed_artifact(
                    _artifact(local_path=str(source), size_bytes=4, sha256="0" * 64),
                    root=root,
                )


def _artifact(
    *,
    attachment_id: str = "attachment-1",
    local_path: str = "/trusted/result.bin",
    filename: str = "result.bin",
    size_bytes: int = 4,
    sha256: str = "digest",
) -> OutboundArtifact:
    return OutboundArtifact(
        kind="file",
        local_path=local_path,
        content_type="application/octet-stream",
        filename=filename,
        size_bytes=size_bytes,
        sha256=sha256,
        attachment_id=attachment_id,
    )


def _message(*artifacts: OutboundArtifact) -> OutboundMessage:
    return OutboundMessage(
        channel_id="qq",
        conversation_id="chat-1",
        message_type="agent",
        text="result",
        metadata={"delivery_id": "delivery-1"},
        artifacts=list(artifacts),
    )


if __name__ == "__main__":
    unittest.main()

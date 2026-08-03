from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

from imagent.interaction.media import LocalPath
from imagent.interaction.media_staging import (
    InlineArtifactStagingInput,
    create_inline_staging_directory,
    decoded_inline_artifact_size,
    stage_inline_artifacts,
    validated_inline_artifact_size,
)


class InteractionMediaStagingTests(unittest.TestCase):
    def test_decoded_size_rejects_invalid_or_oversized_base64(self) -> None:
        self.assertEqual(
            decoded_inline_artifact_size(
                base64.b64encode(b"one").decode(),
                max_bytes=3,
            ),
            3,
        )
        with self.assertRaisesRegex(ValueError, "byte limit"):
            decoded_inline_artifact_size(
                base64.b64encode(b"four").decode(),
                max_bytes=3,
            )
        with self.assertRaisesRegex(ValueError, "invalid"):
            decoded_inline_artifact_size("not-base64", max_bytes=64)

        with self.assertRaisesRegex(ValueError, "declared size"):
            validated_inline_artifact_size(
                InlineArtifactStagingInput(
                    attachment_id="mismatch",
                    filename="one.bin",
                    media_type="application/octet-stream",
                    encoded_content=base64.b64encode(b"one").decode(),
                    declared_size=4,
                ),
                max_bytes=64,
            )

    def test_staging_confines_sdk_names_and_preserves_order_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "staging")
            staging_directory = create_inline_staging_directory(root)
            artifacts = (
                InlineArtifactStagingInput(
                    attachment_id="../first",
                    filename="one.bin",
                    media_type="application/octet-stream",
                    encoded_content=base64.b64encode(b"one").decode(),
                    declared_size=3,
                ),
                InlineArtifactStagingInput(
                    attachment_id="second",
                    filename="two.bin",
                    media_type="application/octet-stream",
                    encoded_content=base64.b64encode(b"two").decode(),
                    declared_size=None,
                ),
            )

            staged = stage_inline_artifacts(staging_directory, artifacts)

            self.assertEqual(
                tuple(item.attachment_id for item in staged),
                ("../first", "second"),
            )
            for item, expected in zip(staged, (b"one", b"two"), strict=True):
                self.assertIsInstance(item.source, LocalPath)
                assert isinstance(item.source, LocalPath)
                path = Path(item.source.path)
                self.assertEqual(path.parent, staging_directory)
                self.assertNotIn(item.attachment_id, path.name)
                self.assertEqual(path.read_bytes(), expected)
                self.assertEqual(item.metadata["sha256"], hashlib.sha256(expected).hexdigest())


if __name__ == "__main__":
    unittest.main()

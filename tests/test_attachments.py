from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from imagent.attachments import (
    configure_shared_filesystem_root,
    resolve_local_attachment,
)
from imagent.contracts import LocalPath, RemoteUrl


class AttachmentTrustTests(unittest.TestCase):
    def test_local_path_must_stay_inside_configured_shared_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            shared = base / "shared"
            shared.mkdir()
            allowed = shared / "image.png"
            allowed.write_bytes(b"png")
            outside = base / "secret.png"
            outside.write_bytes(b"secret")
            root = configure_shared_filesystem_root(shared)

            self.assertEqual(
                resolve_local_attachment(
                    LocalPath(str(allowed)),
                    shared_filesystem_root=root,
                    consumer="test",
                ),
                allowed.resolve(),
            )
            with self.assertRaisesRegex(ValueError, "outside"):
                resolve_local_attachment(
                    LocalPath(str(outside)),
                    shared_filesystem_root=root,
                    consumer="test",
                )

    def test_local_path_requires_explicit_trust_and_absolute_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "shared_filesystem_root"):
            resolve_local_attachment(
                LocalPath(str(Path.cwd() / "image.png")),
                shared_filesystem_root=None,
                consumer="test",
            )
        with tempfile.TemporaryDirectory() as directory:
            root = configure_shared_filesystem_root(directory)
            with self.assertRaisesRegex(ValueError, "absolute"):
                resolve_local_attachment(
                    LocalPath("relative.png"),
                    shared_filesystem_root=root,
                    consumer="test",
                )

    def test_remote_url_is_rejected_without_adapter_support(self) -> None:
        with self.assertRaisesRegex(NotImplementedError, "remote_url"):
            resolve_local_attachment(
                RemoteUrl("https://media.example/image.png"),
                shared_filesystem_root=None,
                consumer="test",
            )

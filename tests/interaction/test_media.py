from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from imagent import contracts as contracts_facade
from imagent.interaction.media import (
    AttachmentContent,
    AttachmentGrouping,
    AttachmentHandle,
    AttachmentSource,
    AttachmentSourceKind,
    InvalidGenericFileError,
    LocalPath,
    RemoteUrl,
    UnsupportedGenericFileError,
    configure_shared_filesystem_root,
    detect_generic_file,
    resolve_local_attachment,
)


class InteractionMediaTests(unittest.TestCase):
    def test_generic_utf8_text_uses_the_explicit_extension_media_type(self) -> None:
        self.assertEqual(
            detect_generic_file("notes.MD", b"# Notes\n"),
            ("text/markdown", ".md"),
        )

    def test_generic_pdf_requires_structural_markers_in_actual_bytes(self) -> None:
        content = (
            b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
            b"xref\n0 1\n0000000000 65535 f \ntrailer\n<<>>\n"
            b"startxref\n0\n%%EOF"
        )

        self.assertEqual(
            detect_generic_file("document.pdf", content),
            ("application/pdf", ".pdf"),
        )
        with self.assertRaises(InvalidGenericFileError):
            detect_generic_file("document.pdf", b"%PDF-1.7\nnot a complete pdf\n%%EOF")

    def test_generic_file_rejects_unsupported_or_invalid_text_bytes(self) -> None:
        with self.assertRaises(UnsupportedGenericFileError):
            detect_generic_file("archive.zip", b"PK")
        for content in (b"hello\x00world", b"\xff"):
            with self.subTest(content=content):
                with self.assertRaises(InvalidGenericFileError):
                    detect_generic_file("notes.txt", content)

    def test_contract_facade_reexports_exact_media_objects(self) -> None:
        expected = {
            "AttachmentContent": AttachmentContent,
            "AttachmentGrouping": AttachmentGrouping,
            "AttachmentHandle": AttachmentHandle,
            "AttachmentSource": AttachmentSource,
            "AttachmentSourceKind": AttachmentSourceKind,
            "LocalPath": LocalPath,
            "RemoteUrl": RemoteUrl,
        }
        for name, owner_object in expected.items():
            with self.subTest(name=name):
                self.assertIs(getattr(contracts_facade, name), owner_object)

    def test_attachment_sources_keep_stable_discriminants(self) -> None:
        sources = (
            (LocalPath("/tmp/example"), AttachmentSourceKind.LOCAL_PATH),
            (RemoteUrl("https://media.example/image.png"), AttachmentSourceKind.REMOTE_URL),
            (AttachmentHandle("handle-1"), AttachmentSourceKind.ATTACHMENT_HANDLE),
        )
        for source, expected_kind in sources:
            with self.subTest(kind=expected_kind):
                self.assertIs(source.kind, expected_kind)

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


if __name__ == "__main__":
    unittest.main()

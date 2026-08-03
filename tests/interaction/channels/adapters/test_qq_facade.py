from __future__ import annotations

import subprocess
import sys
import unittest

import imagent.interaction.channels.adapters as adapter_facade
from imagent.channels.native import qq as current_qq


class QQAdapterFacadeTests(unittest.TestCase):
    def test_approved_exports_preserve_exact_object_identity(self) -> None:
        approved = {
            "QQChannelAdapter",
            "QQ_QUOTE_ATTACHMENT_LIMIT",
            "QQ_QUOTE_CONTENT_LIMIT",
            "QQ_QUOTE_FILENAME_LIMIT",
            "QQ_QUOTE_MESSAGE_TYPE",
            "QQ_QUOTE_REFERENCE_LIMIT",
            "QQ_QUOTE_TRANSCRIPT_LIMIT",
        }
        self.assertEqual(set(adapter_facade.__all__), approved)
        for name in approved:
            with self.subTest(name=name):
                self.assertIs(getattr(adapter_facade, name), getattr(current_qq, name))

    def test_unknown_export_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(AttributeError, "has no attribute 'QQ_INTERNAL'"):
            getattr(adapter_facade, "QQ_INTERNAL")

    def test_package_and_other_provider_imports_do_not_eagerly_load_qq(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "import imagent.interaction.channels.adapters; "
                    "import imagent.interaction.channels.adapters.feishu; "
                    "import imagent.interaction.channels.adapters.telegram; "
                    "import imagent.interaction.channels.adapters.weixin; "
                    "assert 'imagent.channels.native.qq' not in sys.modules"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

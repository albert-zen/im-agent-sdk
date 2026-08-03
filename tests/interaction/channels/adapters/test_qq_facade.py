from __future__ import annotations

import subprocess
import sys
import unittest

import imagent.interaction.channels.adapters as adapter_facade
from imagent.interaction.channels.adapters import qq as target_qq
from imagent.interaction.channels.adapters import qq_quote as target_qq_quote


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
        self.assertIs(adapter_facade.QQChannelAdapter, target_qq.QQChannelAdapter)
        for name in approved - {"QQChannelAdapter"}:
            with self.subTest(name=name):
                self.assertIs(getattr(adapter_facade, name), getattr(target_qq_quote, name))

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
                    "from importlib.util import find_spec; "
                    "import imagent.interaction.channels.adapters; "
                    "import imagent.interaction.channels.adapters.feishu; "
                    "import imagent.interaction.channels.adapters.telegram; "
                    "import imagent.interaction.channels.adapters.weixin; "
                    "assert 'imagent.interaction.channels.adapters.qq' not in sys.modules; "
                    "assert find_spec('imagent.channels.native.qq') is None; "
                    "assert find_spec('imagent.channels.native.qq_media') is None; "
                    "assert find_spec('imagent.channels.native.qq_quote') is None"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

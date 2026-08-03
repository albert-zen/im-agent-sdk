from __future__ import annotations

import subprocess
import sys
import unittest

import imagent.channels as formal_facade
import imagent.interaction.channels.adapters as target_facade
from imagent.interaction.channels.adapters.runtime import (
    NativeTransportChannelAdapter,
    channel_from_config,
)


class NativeRuntimeFacadeTests(unittest.TestCase):
    def test_formal_and_target_facades_preserve_exact_owner_identity(self) -> None:
        self.assertIs(formal_facade.NativeTransportChannelAdapter, NativeTransportChannelAdapter)
        self.assertIs(formal_facade.channel_from_config, channel_from_config)
        self.assertIs(target_facade.NativeTransportChannelAdapter, NativeTransportChannelAdapter)
        self.assertIs(target_facade.channel_from_config, channel_from_config)

    def test_facade_imports_are_provider_lazy_and_old_runtime_is_absent(self) -> None:
        import_orders = (
            "from imagent.interaction.channels.adapters.runtime import "
            "NativeTransportChannelAdapter, channel_from_config; ",
            "from imagent.interaction.channels.adapters import "
            "NativeTransportChannelAdapter, channel_from_config; ",
            "from imagent.channels import NativeTransportChannelAdapter, channel_from_config; ",
        )
        for import_order in import_orders:
            with self.subTest(import_order=import_order):
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import sys; "
                            "from importlib.util import find_spec; "
                            f"{import_order}"
                            "providers = ('qq', 'telegram', 'feishu', 'weixin'); "
                            "assert all("
                            "f'imagent.interaction.channels.adapters.{name}' not in sys.modules "
                            "for name in providers); "
                            "assert find_spec('imagent.channels.runtime') is None"
                        ),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

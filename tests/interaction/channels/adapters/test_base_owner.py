from __future__ import annotations

import subprocess
import sys
import unittest

from imagent.interaction.channels.adapters import feishu, qq, runtime, telegram, weixin
from imagent.interaction.channels.adapters.base import (
    BaseChannelAdapter,
    ChannelRouteContext,
)


class NativeChannelBaseOwnershipTests(unittest.TestCase):
    def test_providers_and_runtime_use_exact_base_owner_objects(self) -> None:
        for adapter_type in (
            feishu.FeishuChannelAdapter,
            qq.QQChannelAdapter,
            telegram.TelegramChannelAdapter,
            weixin.WeixinChannelAdapter,
        ):
            with self.subTest(adapter_type=adapter_type.__name__):
                self.assertIs(adapter_type.__mro__[1], BaseChannelAdapter)
        self.assertIs(runtime.ChannelRouteContext, ChannelRouteContext)
        self.assertEqual(
            BaseChannelAdapter.__module__,
            "imagent.interaction.channels.adapters.base",
        )
        self.assertEqual(
            ChannelRouteContext.__module__,
            "imagent.interaction.channels.adapters.base",
        )

    def test_historical_native_base_path_is_absent(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from importlib.util import find_spec; "
                    "from imagent.interaction.channels.adapters.base import "
                    "BaseChannelAdapter, ChannelRouteContext; "
                    "assert find_spec('imagent.channels.native.base') is None"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

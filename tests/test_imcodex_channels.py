from __future__ import annotations

import tempfile
import unittest

from imagent.channels import imcodex_channel
from imagent.contracts import SupportLevel
from imagent.testing import verify_channel_adapter


class ImcodexProductionChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_proven_channels_load_through_one_seam(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            configurations = {
                "qq": {"enabled": False},
                "telegram": {"enabled": False},
                "feishu": {"enabled": False},
                "weixin": {"enabled": False, "state_dir": state_dir},
            }
            adapters = [
                imcodex_channel(
                    channel_id,
                    config=config,
                    channel_instance_id=f"{channel_id}-main",
                )
                for channel_id, config in configurations.items()
            ]

            async def ignore(_item) -> None:
                return None

            await adapters[0].start(ignore, ignore)
            try:
                self.assertTrue(getattr(adapters[0]._native, "markdown_enabled", False))
            finally:
                await adapters[0].stop()

            reports = [await verify_channel_adapter(adapter) for adapter in adapters]

        self.assertEqual(
            [adapter.channel_instance_id for adapter in adapters],
            ["qq-main", "telegram-main", "feishu-main", "weixin-main"],
        )
        self.assertTrue(all("start and stop lifecycle" in report.check_names for report in reports))
        self.assertEqual(adapters[0].capabilities.markdown, SupportLevel.NATIVE)
        self.assertTrue(
            all(
                adapter.capabilities.markdown is not SupportLevel.UNSUPPORTED
                for adapter in adapters
            )
        )


if __name__ == "__main__":
    unittest.main()

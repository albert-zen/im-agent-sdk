from __future__ import annotations

import subprocess
import sys
import unittest

from imagent.interaction.channels.ingress_media import (
    FileMediaMaterializer,
    FileMediaResult,
    ImageMediaMaterializer,
    MaterializedFile,
    MaterializedImage,
    MediaResult,
    MediaSpoolError,
    materialize_inbound_media,
)


class IngressMediaOwnershipTests(unittest.TestCase):
    def test_shared_media_values_and_services_have_one_ingress_owner(self) -> None:
        for value in (
            FileMediaMaterializer,
            FileMediaResult,
            ImageMediaMaterializer,
            MaterializedFile,
            MaterializedImage,
            MediaResult,
            MediaSpoolError,
            materialize_inbound_media,
        ):
            with self.subTest(value=value.__name__):
                self.assertEqual(
                    value.__module__,
                    "imagent.interaction.channels.ingress_media",
                )

    def test_historical_native_media_package_is_absent(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from importlib.util import find_spec; "
                    "from imagent.interaction.channels.ingress_media import "
                    "ImageMediaMaterializer, FileMediaMaterializer; "
                    "assert find_spec('imagent.channels.native') is None"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

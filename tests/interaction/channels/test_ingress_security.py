from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from imagent.interaction.channels.ingress_security import secure_windows_path


class IngressSecurityOwnershipTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "non-Windows no-op contract")
    def test_non_windows_path_security_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "not-created"
            secure_windows_path(missing)
            self.assertFalse(missing.exists())

    def test_historical_native_security_path_is_absent(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from importlib.util import find_spec; "
                    "from imagent.interaction.channels.ingress_security import "
                    "secure_windows_path; "
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

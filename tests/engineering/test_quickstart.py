from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUICKSTART_DOC = ROOT / "docs" / "onboarding" / "quickstart.md"
RELEASE_TAG = "gh release download v0.1.0a1 --repo albert-zen/im-agent-sdk"
RELEASE_WHEEL = ".quickstart-download/im_agent_sdk-0.1.0a1-py3-none-any.whl"
SUCCESS_LINE = (
    "reference consumer OK: projects=1 threads=2 conversations=2 "
    "max_workers=1 diagnostics=bounded sqlite_recovery=true shutdown=true\n"
)


class QuickstartTests(unittest.TestCase):
    def test_documented_install_is_authenticated_checksummed_github_release(self) -> None:
        document = QUICKSTART_DOC.read_text(encoding="utf-8")
        self.assertIn(RELEASE_TAG, document)
        self.assertIn(RELEASE_WHEEL, document)
        self.assertIn("--pattern '*.whl' --pattern SHA256SUMS", document)
        self.assertIn("Get-FileHash", document)
        self.assertIn("(cd .quickstart-download && sha256sum -c SHA256SUMS)", document)
        self.assertIn("repository read access", document)
        self.assertNotIn("pypi.org", document.casefold())
        self.assertNotIn("file://", document.casefold())
        self.assertNotRegex(document, r"[A-Za-z]:\\[^\n]*\.whl")

    def test_documented_command_uses_only_canonical_packaged_consumer(self) -> None:
        document = QUICKSTART_DOC.read_text(encoding="utf-8")
        self.assertIn("-m examples.reference_consumer.main", document)
        self.assertNotIn("examples.quickstart", document)
        self.assertFalse((ROOT / "examples" / "quickstart").exists())
        self.assertEqual(
            set((ROOT / "examples").rglob("main.py")),
            {ROOT / "examples" / "reference_consumer" / "main.py"},
        )

    def test_built_wheel_uses_canonical_consumer_without_repository_pythonpath(self) -> None:
        uv = shutil.which("uv")
        if uv is None:
            self.skipTest("uv is required for the clean-wheel smoke")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            build = subprocess.run(
                [uv, "build", "--wheel", "--out-dir", str(dist)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            wheels = tuple(dist.glob("*.whl"))
            self.assertEqual(len(wheels), 1)

            venv = root / "venv"
            create = subprocess.run(
                [uv, "venv", "--python", sys.executable, str(venv)],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            install = subprocess.run(
                [uv, "pip", "install", "--python", str(python), str(wheels[0])],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            import_smoke = subprocess.run(
                [
                    str(python),
                    "-c",
                    "import pathlib, examples.reference_consumer.main as m; "
                    "print(pathlib.Path(m.__file__).resolve())",
                ],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(import_smoke.returncode, 0, import_smoke.stderr)
            imported_path = Path(import_smoke.stdout.strip())
            self.assertTrue(imported_path.is_relative_to(venv.resolve()))
            self.assertFalse(imported_path.is_relative_to(ROOT.resolve()))

            # The canonical artifact's descriptor-pinned ledger requires the
            # POSIX no-follow directory flags. Its full installed execution is
            # exercised here wherever that existing consumer is runnable.
            if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
                return
            run = subprocess.run(
                [str(python), "-m", "examples.reference_consumer.main"],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout, SUCCESS_LINE)
            self.assertEqual(run.stderr, "")


if __name__ == "__main__":
    unittest.main()

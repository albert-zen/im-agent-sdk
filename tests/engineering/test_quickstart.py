from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from examples.quickstart.main import run_quickstart

ROOT = Path(__file__).resolve().parents[2]
QUICKSTART_DOC = ROOT / "docs" / "onboarding" / "quickstart.md"
EXAMPLE_FILES = tuple((ROOT / "examples" / "quickstart").glob("*.py"))
RELEASE_TAG = "gh release download v0.1.0a1 --repo albert-zen/im-agent-sdk"
RELEASE_WHEEL = ".quickstart-download/im_agent_sdk-0.1.0a1-py3-none-any.whl"


class QuickstartTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_vertical_and_persistence_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            result = await run_quickstart(workspace=workspace, state_dir=state)

            self.assertEqual(result.response, "Echo: hello from IM")
            self.assertTrue(result.database_path.is_file())
            persisted = result.database_path.read_bytes()
            self.assertNotIn(b"hello from IM", persisted)
            self.assertNotIn(b"Echo: hello from IM", persisted)
            self.assertNotIn(str(workspace.resolve()).encode(), persisted)

            with self.assertRaisesRegex(RuntimeError, "process-local"):
                await run_quickstart(workspace=workspace, state_dir=state)

    def test_example_imports_only_public_sdk_modules(self) -> None:
        for path in EXAMPLE_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for module in modules:
                    if module == "imagent" or module.startswith("imagent."):
                        self.assertFalse(
                            any(part.startswith("_") for part in module.split(".")),
                            f"private SDK import in {path}: {module}",
                        )

    def test_documented_install_is_the_github_release_asset(self) -> None:
        document = QUICKSTART_DOC.read_text(encoding="utf-8")
        self.assertIn(RELEASE_TAG, document)
        self.assertIn(RELEASE_WHEEL, document)
        self.assertIn("--pattern '*.whl' --pattern SHA256SUMS", document)
        self.assertIn("Get-FileHash", document)
        self.assertIn("repository read access", document)
        self.assertNotIn("pypi.org", document.casefold())
        self.assertNotIn("file://", document.casefold())
        self.assertNotRegex(document, r"[A-Za-z]:\\[^\n]*\.whl")

    def test_module_smoke_from_a_clean_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "examples.quickstart.main",
                    "--workspace",
                    str(workspace),
                    "--state-dir",
                    str(root / "state"),
                ],
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Echo: hello from IM", completed.stdout)
            self.assertIn("Agent transcript/state owner", completed.stdout)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPENDENCY_BOUNDARY_PATHS = (
    ROOT / "pyproject.toml",
    ROOT / "uv.lock",
)
DEPENDENCY_BOUNDARY_TREES = (
    ROOT / ".github" / "workflows",
    ROOT / "src" / "imagent",
)
DEPENDENCY_BOUNDARY_TEXT_SUFFIXES = {".py", ".yaml", ".yml"}


def _is_dependency_boundary_text(path: Path) -> bool:
    return path.suffix in DEPENDENCY_BOUNDARY_TEXT_SUFFIXES or path.name == "py.typed"


class PackageIndependenceTests(unittest.TestCase):
    def test_release_mirror_has_one_owner_and_no_historical_module(self) -> None:
        expected = ROOT / "tests" / "engineering" / "test_release.py"
        self.assertTrue(expected.is_file())
        self.assertFalse((ROOT / "tests" / "test_package_independence.py").exists())
        self.assertEqual(
            set((ROOT / "tests").rglob("test_release.py")),
            {expected},
        )

    def test_sdk_dependency_graph_has_no_consumer_package_reference(self) -> None:
        paths = list(DEPENDENCY_BOUNDARY_PATHS)
        for root in DEPENDENCY_BOUNDARY_TREES:
            paths.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and _is_dependency_boundary_text(path)
            )

        violations = []
        for path in paths:
            if "__pycache__" in path.parts:
                continue
            content = path.read_text(encoding="utf-8")
            if "imcodex" in content.casefold():
                violations.append(path.relative_to(ROOT).as_posix())

        self.assertEqual(violations, [])

    def test_dependency_boundary_scan_ignores_non_source_metadata(self) -> None:
        self.assertFalse(_is_dependency_boundary_text(Path(".DS_Store")))
        self.assertTrue(_is_dependency_boundary_text(Path("adapter.py")))
        self.assertTrue(_is_dependency_boundary_text(Path("ci.yml")))
        self.assertTrue(_is_dependency_boundary_text(Path("py.typed")))

    def test_top_level_facade_is_finite_lazy_and_exact_in_a_clean_process(self) -> None:
        environment = os.environ.copy()
        source_root = str(ROOT / "src")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_root
            if not existing_pythonpath
            else os.pathsep.join((source_root, existing_pythonpath))
        )
        code = r"""
import importlib
import sys
import typing

import imagent

owner_modules = {
    "adapters": "imagent.adapters",
    "contracts": "imagent.contracts",
    "delivery_coordination": "imagent.gateway.delivery.coordination",
    "delivery_planning": "imagent.gateway.delivery.planning",
    "diagnostics": "imagent.diagnostics",
    "events": "imagent.events",
    "projections": "imagent.projections",
}
assert imagent.__all__ == list(owner_modules)
assert typing.get_type_hints(imagent.__getattr__) == {"name": str, "return": object}
assert all(name not in imagent.__dict__ for name in owner_modules)
assert not any(
    name == "imagent.gateway" or name.startswith("imagent.gateway.")
    for name in sys.modules
)
assert not any(
    name in sys.modules
    for name in ("websockets", "PIL", "Crypto", "lark_oapi", "lark")
)
assert not hasattr(imagent, "unsupported_root_export")

for name, module_name in owner_modules.items():
    first = getattr(imagent, name)
    owner = importlib.import_module(module_name)
    assert first is owner
    assert getattr(imagent, name) is owner
    assert imagent.__dict__[name] is owner
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

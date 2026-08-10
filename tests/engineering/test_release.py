from __future__ import annotations

import os
import subprocess
import sys
import tomllib
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

    def test_console_script_resolves_to_client_tool_owner_without_historical_package(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            metadata["project"]["scripts"]["imagent-send"],
            "imagent.interaction.client_tools.send:main",
        )
        self.assertTrue((ROOT / "src" / "imagent" / "interaction" / "client_tools").is_dir())
        self.assertFalse((ROOT / "src" / "imagent" / "cli").exists())

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
import importlib.util
import sys

assert importlib.util.find_spec("imagent.cli") is None
try:
    importlib.import_module("imagent.cli")
except ModuleNotFoundError:
    pass
else:
    raise AssertionError("historical imagent.cli package is importable")

from imagent.interaction.client_tools.send import main

assert main.__module__ == "imagent.interaction.client_tools.send"
assert not any(
    name == "imagent.gateway" or name.startswith("imagent.gateway.")
    for name in sys.modules
)
assert not any(
    name == "imagent.applications" or name.startswith("imagent.applications.")
    for name in sys.modules
)
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

    def test_wheel_includes_the_one_reference_consumer_entry_point(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        wheel = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]
        self.assertEqual(
            wheel["force-include"],
            {"examples/reference_consumer": "examples/reference_consumer"},
        )
        self.assertEqual(
            {path.name for path in (ROOT / "examples" / "reference_consumer").glob("*.py")},
            {"application.py", "gateway.py", "interaction.py", "main.py"},
        )
        self.assertEqual(
            set((ROOT / "examples").rglob("main.py")),
            {ROOT / "examples" / "reference_consumer" / "main.py"},
        )

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

module_exports = {
    "adapters": "imagent.adapters",
    "contracts": "imagent.contracts",
    "delivery_coordination": "imagent.gateway.delivery.coordination",
    "delivery_planning": "imagent.gateway.delivery.planning",
    "diagnostics": "imagent.diagnostics",
    "events": "imagent.events",
}
value_exports = {
    "ActionResult": "imagent.gateway.actions",
    "ActionValue": "imagent.gateway.actions",
    "ApplicationActions": "imagent.gateway.actions",
    "ConversationActions": "imagent.gateway.actions",
    "ReadOutcome": "imagent.gateway.actions",
    "Failed": "imagent.gateway.outcomes",
    "Gateway": "imagent.gateway.runtime",
    "GatewayExtensions": "imagent.gateway.composition",
    "GatewayLimits": "imagent.gateway.composition",
    "GatewayStore": "imagent.gateway.persistence",
    "MemoryGatewayStore": "imagent.gateway.persistence",
    "OutcomeUnknown": "imagent.gateway.outcomes",
    "Partial": "imagent.gateway.outcomes",
    "ProjectionPolicy": "imagent.gateway.routing",
    "SQLiteGatewayStore": "imagent.gateway.persistence",
    "Succeeded": "imagent.gateway.outcomes",
    "CommandArgumentContract": "imagent.interaction.controllers",
    "CommandDefinition": "imagent.interaction.controllers",
    "CommandExecutionSafety": "imagent.interaction.controllers",
    "CommandHandler": "imagent.interaction.controllers",
    "CommandLimits": "imagent.interaction.controllers",
    "CommandRegistry": "imagent.interaction.controllers",
    "CommandResult": "imagent.interaction.controllers",
    "include_common_commands": "imagent.interaction.controllers",
}
assert imagent.__all__ == [
    "ActionResult",
    "ActionValue",
    "ApplicationActions",
    "CommandArgumentContract",
    "CommandDefinition",
    "CommandExecutionSafety",
    "CommandHandler",
    "CommandLimits",
    "CommandRegistry",
    "CommandResult",
    "ConversationActions",
    "Failed",
    "Gateway",
    "GatewayExtensions",
    "GatewayLimits",
    "GatewayStore",
    "MemoryGatewayStore",
    "OutcomeUnknown",
    "Partial",
    "ProjectionPolicy",
    "ReadOutcome",
    "SQLiteGatewayStore",
    "Succeeded",
    "adapters",
    "contracts",
    "delivery_coordination",
    "delivery_planning",
    "diagnostics",
    "events",
    "include_common_commands",
]
assert not hasattr(imagent, "projections")
assert typing.get_type_hints(imagent.__getattr__) == {"name": str, "return": object}
assert all(name not in imagent.__dict__ for name in (*module_exports, *value_exports))
assert not any(
    name == "imagent.gateway" or name.startswith("imagent.gateway.")
    for name in sys.modules
)
assert not any(
    name in sys.modules
    for name in ("websockets", "PIL", "Crypto", "lark_oapi", "lark")
)
assert not hasattr(imagent, "unsupported_root_export")

for name, module_name in module_exports.items():
    first = getattr(imagent, name)
    owner = importlib.import_module(module_name)
    assert first is owner
    assert getattr(imagent, name) is owner
    assert imagent.__dict__[name] is owner

for name, module_name in value_exports.items():
    first = getattr(imagent, name)
    owner = getattr(importlib.import_module(module_name), name)
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

from __future__ import annotations

import ast
import importlib.util
import inspect
import unittest
from dataclasses import FrozenInstanceError
from importlib.util import resolve_name
from pathlib import Path
from typing import cast

from imagent.interaction.diagnostics import (
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
    DiagnosticFailureCode,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
)

ROOT = Path(__file__).resolve().parents[2]
MOVED_NAMES = (
    "ConnectionDiagnosticState",
    "DiagnosticFailureCode",
    "QueueDiagnosticName",
    "QueueDiagnosticFacts",
    "ConnectionDiagnosticFacts",
)


def _resolved_import_targets(path: Path) -> tuple[str, ...]:
    relative = path.relative_to(ROOT / "src").with_suffix("")
    parts = relative.parts[:-1] if path.name == "__init__.py" else relative.parts
    module = ".".join(parts)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.append(
                resolve_name(
                    "." * node.level + (node.module or ""),
                    package,
                )
                if node.level
                else (node.module or "")
            )
    return tuple(targets)


class InteractionDiagnosticsOwnershipTests(unittest.TestCase):
    def test_owner_exports_are_explicit_and_transition_facade_is_absent(self) -> None:
        import imagent.interaction.diagnostics as owner

        self.assertEqual(list(owner.__all__), list(MOVED_NAMES))
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                value = getattr(owner, name)
                self.assertEqual(value.__module__, "imagent.interaction.diagnostics")
        self.assertIsNone(importlib.util.find_spec("imagent.diagnostics"))

    def test_validation_messages_and_immutability_remain_unchanged(self) -> None:
        with self.assertRaisesRegex(ValueError, "fixed vocabulary"):
            QueueDiagnosticFacts(
                cast(QueueDiagnosticName, "consumer-controlled-name"),
                capacity=1,
                depth=0,
            )
        with self.assertRaisesRegex(TypeError, "must be integers"):
            QueueDiagnosticFacts(QueueDiagnosticName.NOTIFICATION, capacity=True, depth=0)
        facts = QueueDiagnosticFacts(QueueDiagnosticName.NOTIFICATION, capacity=2, depth=1)
        with self.assertRaises(FrozenInstanceError):
            setattr(facts, "depth", 2)

        self.assertIsNotNone(inspect.signature(QueueDiagnosticFacts))
        self.assertIsNotNone(inspect.signature(ConnectionDiagnosticFacts))
        self.assertEqual(
            ConnectionDiagnosticState.READY.value,
            "ready",
        )
        self.assertEqual(
            DiagnosticFailureCode.OTHER.value,
            "other",
        )

    def test_canonical_owner_modules_have_no_top_facade_or_higher_layer_import(self) -> None:
        forbidden_prefixes = (
            "imagent.diagnostics",
            "imagent.applications",
            "imagent.gateway",
        )
        paths = (
            ROOT / "src/imagent/interaction/diagnostics.py",
            ROOT / "src/imagent/interaction/channels/diagnostics.py",
        )
        for path in paths:
            with self.subTest(path=path):
                for target in _resolved_import_targets(path):
                    self.assertFalse(
                        any(
                            target == forbidden or target.startswith(f"{forbidden}.")
                            for forbidden in forbidden_prefixes
                        ),
                        f"{path}: forbidden import {target}",
                    )

        channel_targets = _resolved_import_targets(paths[1])
        self.assertIn("imagent.interaction.diagnostics", channel_targets)

    def test_changed_diagnostics_callsites_use_canonical_contract_imports(self) -> None:
        expected = {
            "src/imagent/interaction/channels/adapters/diagnostics.py": {
                "imagent.interaction.diagnostics",
                "imagent.interaction.channels.diagnostics",
            },
            "src/imagent/interaction/channels/adapters/runtime.py": {
                "imagent.interaction.channels.diagnostics",
            },
            "src/imagent/applications/adapters/appserver/diagnostics.py": {
                "imagent.interaction.diagnostics",
            },
            "src/imagent/applications/adapters/appserver/client/client.py": {
                "imagent.interaction.diagnostics",
            },
            "src/imagent/applications/adapters/appserver/_base.py": {
                "imagent.interaction.diagnostics",
            },
            "src/imagent/gateway/orchestration.py": {"imagent.interaction.diagnostics"},
        }
        for relative, required_targets in expected.items():
            path = ROOT / relative
            with self.subTest(path=relative):
                targets = set(_resolved_import_targets(path))
                self.assertTrue(required_targets.issubset(targets))
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("imagent.diagnostics", source)


if __name__ == "__main__":
    unittest.main()

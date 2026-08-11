from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError
from importlib.util import resolve_name
from pathlib import Path
from typing import cast, get_type_hints

import imagent.applications.diagnostics as application_owner
from imagent.applications.adapters.appserver._base import _AppServerApplicationAdapter
from imagent.applications.adapters.t3 import T3ApplicationAdapter
from imagent.applications.diagnostics import (
    ApplicationArtifactMaterializationDiagnosticFacts,
    ApplicationArtifactMaterializationFailureCode,
    ApplicationDiagnosticFacts,
    ApplicationDiagnosticsProvider,
    ApplicationPresentationDiagnosticFacts,
    ApplicationPresentationFailureCode,
    DiagnosticsProvider,
)
from imagent.applications.presentation.artifact_materialization import (
    AppServerArtifactMaterializationRuntime,
)
from imagent.applications.presentation.live_activity import ApplicationPresentationRuntime
from imagent.interaction.diagnostics import (
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
    DiagnosticFailureCode,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
)

ROOT = Path(__file__).resolve().parents[2]
MOVED_NAMES = (
    "ApplicationPresentationFailureCode",
    "ApplicationArtifactMaterializationFailureCode",
    "ApplicationDiagnosticFacts",
    "ApplicationPresentationDiagnosticFacts",
    "ApplicationArtifactMaterializationDiagnosticFacts",
    "ApplicationDiagnosticsProvider",
    "DiagnosticsProvider",
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


class ApplicationDiagnosticsOwnershipTests(unittest.TestCase):
    def test_owner_exports_are_finite_and_transition_facade_is_absent(self) -> None:
        self.assertEqual(list(application_owner.__all__), list(MOVED_NAMES))
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                owner_value = getattr(application_owner, name)
                self.assertEqual(owner_value.__module__, application_owner.__name__)
        self.assertIsNone(importlib.util.find_spec("imagent.diagnostics"))
        self.assertIs(ApplicationDiagnosticsProvider, DiagnosticsProvider)
        self.assertIs(
            application_owner.ApplicationDiagnosticsProvider,
            application_owner.DiagnosticsProvider,
        )

    def test_signatures_validation_and_immutability_are_unchanged(self) -> None:
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertIsNotNone(inspect.signature(getattr(application_owner, name)))

        with self.assertRaisesRegex(ValueError, "fixed vocabulary"):
            ApplicationPresentationDiagnosticFacts(
                invocation_count=1,
                failure_count=1,
                last_failure_code=cast(ApplicationPresentationFailureCode, "consumer-value"),
            )
        with self.assertRaisesRegex(ValueError, "fixed vocabulary"):
            ApplicationArtifactMaterializationDiagnosticFacts(
                invocation_count=1,
                failure_count=1,
                last_failure_code=cast(
                    ApplicationArtifactMaterializationFailureCode,
                    "consumer-value",
                ),
            )
        facts = ApplicationDiagnosticFacts("application-1", "codex")
        with self.assertRaises(FrozenInstanceError):
            facts.kind = "changed"  # type: ignore[misc]

    def test_runtime_diagnostic_type_hints_use_application_owner(self) -> None:
        runtime_methods = (
            (_AppServerApplicationAdapter.diagnostic_facts, ApplicationDiagnosticFacts),
            (T3ApplicationAdapter.diagnostic_facts, ApplicationDiagnosticFacts),
            (
                ApplicationPresentationRuntime.diagnostic_facts,
                ApplicationPresentationDiagnosticFacts,
            ),
            (
                AppServerArtifactMaterializationRuntime.diagnostic_facts,
                ApplicationArtifactMaterializationDiagnosticFacts,
            ),
        )
        for method, expected in runtime_methods:
            with self.subTest(method=method):
                self.assertIs(get_type_hints(method)["return"], expected)
        for provider in (ApplicationDiagnosticsProvider, DiagnosticsProvider):
            with self.subTest(provider=provider):
                self.assertIs(
                    get_type_hints(provider.diagnostic_facts)["return"],
                    ApplicationDiagnosticFacts,
                )

    def test_application_owner_is_lower_layer_and_has_no_gateway_import(self) -> None:
        targets = _resolved_import_targets(ROOT / "src/imagent/applications/diagnostics.py")
        forbidden = ("imagent.gateway", "imagent.diagnostics")
        self.assertFalse(
            any(
                target == prefix or target.startswith(f"{prefix}.")
                for target in targets
                for prefix in forbidden
            )
        )
        self.assertEqual(
            targets,
            ("__future__", "dataclasses", "enum", "typing", "imagent.interaction.diagnostics"),
        )

    def test_application_call_sites_use_the_canonical_owner(self) -> None:
        expected = (
            "src/imagent/applications/adapters/appserver/_base.py",
            "src/imagent/applications/adapters/t3.py",
            "src/imagent/applications/presentation/live_activity.py",
            "src/imagent/applications/presentation/artifact_materialization.py",
        )
        for relative in expected:
            path = ROOT / relative
            with self.subTest(path=relative):
                self.assertIn("imagent.applications.diagnostics", _resolved_import_targets(path))
                self.assertNotIn("imagent.diagnostics", path.read_text(encoding="utf-8"))

    def test_clean_owner_import_does_not_restore_transition_facade(self) -> None:
        source = (
            "import importlib.util; "
            "import imagent.applications.diagnostics; "
            "assert importlib.util.find_spec('imagent.diagnostics') is None"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src")
        subprocess.run([sys.executable, "-c", source], cwd=ROOT, env=environment, check=True)

    def test_application_scope_rejects_channel_queue(self) -> None:
        channel_queue = QueueDiagnosticFacts(QueueDiagnosticName.CHANNEL_INBOUND, 1, 0)
        connection = ConnectionDiagnosticFacts(
            state=ConnectionDiagnosticState.READY,
            connection_epoch=1,
            reconnect_count=0,
            worker_running=True,
            worker_degraded=False,
            last_failure_code=DiagnosticFailureCode.OTHER,
            queues=(channel_queue,),
        )
        with self.assertRaisesRegex(ValueError, "not Application-scoped"):
            ApplicationDiagnosticFacts("application-1", "codex", connection)


if __name__ == "__main__":
    unittest.main()

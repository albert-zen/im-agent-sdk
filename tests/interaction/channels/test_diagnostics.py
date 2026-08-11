from __future__ import annotations

import ast
import importlib.util
import inspect
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from imagent.interaction.channels.diagnostics import (
    ChannelDiagnosticFacts,
)
from imagent.interaction.diagnostics import (
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
)

ROOT = Path(__file__).resolve().parents[3]


class ChannelDiagnosticsOwnershipTests(unittest.TestCase):
    def test_owner_exports_and_transition_facade_is_absent(self) -> None:
        import imagent.interaction.channels.diagnostics as owner

        self.assertEqual(
            list(owner.__all__), ["ChannelDiagnosticFacts", "ChannelDiagnosticsProvider"]
        )
        self.assertIsNone(importlib.util.find_spec("imagent.diagnostics"))
        self.assertEqual(owner.ChannelDiagnosticFacts.__module__, owner.__name__)
        self.assertEqual(owner.ChannelDiagnosticsProvider.__module__, owner.__name__)

    def test_channel_scope_validation_and_signatures_are_preserved(self) -> None:
        connection = ConnectionDiagnosticFacts(
            state=ConnectionDiagnosticState.READY,
            connection_epoch=1,
            reconnect_count=0,
            worker_running=True,
            worker_degraded=False,
            queues=(
                QueueDiagnosticFacts(
                    QueueDiagnosticName.CHANNEL_INBOUND,
                    capacity=4,
                    depth=1,
                ),
            ),
        )
        facts = ChannelDiagnosticFacts("qq-main", "qq", connection)
        self.assertEqual(facts.channel_instance_id, "qq-main")
        with self.assertRaises(FrozenInstanceError):
            setattr(facts, "kind", "other")
        with self.assertRaisesRegex(ValueError, "not Channel-scoped"):
            ChannelDiagnosticFacts(
                "qq-main",
                "qq",
                ConnectionDiagnosticFacts(
                    state=ConnectionDiagnosticState.READY,
                    connection_epoch=1,
                    reconnect_count=0,
                    worker_running=True,
                    worker_degraded=False,
                    queues=(
                        QueueDiagnosticFacts(
                            QueueDiagnosticName.NOTIFICATION,
                            capacity=1,
                            depth=0,
                        ),
                    ),
                ),
            )
        self.assertIsNotNone(inspect.signature(ChannelDiagnosticFacts))

    def test_canonical_channel_module_imports_only_parent_interaction_contracts(self) -> None:
        path = ROOT / "src/imagent/interaction/channels/diagnostics.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertTrue(
            any(
                isinstance(node, ast.ImportFrom)
                and node.level == 2
                and node.module == "diagnostics"
                for node in imports
            )
        )
        source = path.read_text(encoding="utf-8")
        for forbidden in ("imagent.diagnostics", "imagent.applications", "imagent.gateway"):
            self.assertNotIn(forbidden, source)

        native_path = ROOT / "src/imagent/interaction/channels/adapters/diagnostics.py"
        native_source = native_path.read_text(encoding="utf-8")
        self.assertIn("from ...diagnostics import", native_source)
        self.assertIn("from ..diagnostics import ChannelDiagnosticFacts", native_source)


if __name__ == "__main__":
    unittest.main()

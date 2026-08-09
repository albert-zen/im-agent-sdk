from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import get_type_hints

import imagent.contracts as contract_facade
import imagent.gateway as gateway_facade
import imagent.gateway.delivery as delivery_facade
import imagent.gateway.persistence as persistence_facade
from imagent.gateway.delivery import proactive as contract_seam
from imagent.gateway.delivery import proactive_runtime as runtime_owner
from imagent.gateway.persistence import state_contracts as state_owner

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_NAMES = {
    "ConversationDeliveryTarget",
    "DeliveryIntent",
    "DeliveryTarget",
    "DeliveryTargetKind",
    "DestinationDeliveryResult",
    "ProactiveDeliveryResult",
    "ThreadRouteDeliveryTarget",
    "validate_delivery_intent",
}
RUNTIME_NAMES = {
    "ResolveThreadRoutes",
    "DeliveryRouteError",
    "ProactiveDeliveryService",
    "authorize_delivery_target",
    "_state_from_receipt",
    "_destination_ids",
    "_preflight_rejection",
    "_result_from_record",
    "_redact_receipt",
    "_durable_delivery_receipt",
    "_require_external_local_digests",
    "_ensure_submission_identity",
}


def _top_level_named_nodes(tree: ast.Module) -> dict[str, ast.AST]:
    nodes: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if isinstance(target, ast.Name):
                    nodes[target.id] = node
    return nodes


class ProactiveRuntimeOwnershipTests(unittest.TestCase):
    def test_contract_seam_has_only_contract_vocabulary_and_validator(self) -> None:
        public_names = {name for name in vars(contract_seam) if not name.startswith("_")}
        self.assertEqual(set(contract_seam.__all__), CONTRACT_NAMES)
        self.assertEqual(len(contract_seam.__all__), len(CONTRACT_NAMES))
        self.assertTrue(CONTRACT_NAMES.issubset(public_names))
        self.assertTrue(RUNTIME_NAMES.isdisjoint(vars(contract_seam)))

        source = (REPOSITORY_ROOT / "src/imagent/gateway/delivery/proactive.py").read_text()
        tree = ast.parse(source)
        named_nodes = _top_level_named_nodes(tree)
        self.assertTrue(RUNTIME_NAMES.isdisjoint(named_nodes))
        imported_modules = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_names = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertNotIn("imagent.gateway.delivery.proactive_runtime", imported_modules)
        self.assertNotIn("proactive_runtime", imported_modules)
        self.assertNotIn("ProactiveDeliveryService", imported_names)
        self.assertNotIn("DeliveryRouteError", imported_names)

    def test_runtime_and_formal_facades_have_exact_owner_identity(self) -> None:
        for name in RUNTIME_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(runtime_owner, name))
        self.assertEqual(
            runtime_owner.ProactiveDeliveryService.__module__,
            runtime_owner.__name__,
        )
        self.assertEqual(runtime_owner.DeliveryRouteError.__module__, runtime_owner.__name__)
        self.assertIs(
            delivery_facade.ProactiveDeliveryService,
            runtime_owner.ProactiveDeliveryService,
        )
        self.assertIs(
            gateway_facade.ProactiveDeliveryService,
            runtime_owner.ProactiveDeliveryService,
        )
        for name in CONTRACT_NAMES:
            with self.subTest(contract=name):
                self.assertFalse(hasattr(state_owner, name))
                self.assertIs(getattr(delivery_facade, name), getattr(contract_seam, name))
                self.assertNotIn(name, contract_facade.__all__)
                self.assertFalse(hasattr(contract_facade, name))
        self.assertIs(
            delivery_facade.DeliverySubmissionOrigin,
            state_owner.DeliverySubmissionOrigin,
        )
        self.assertFalse(hasattr(persistence_facade, "DeliverySubmissionOrigin"))

    def test_runtime_source_has_the_exact_finite_owner_set(self) -> None:
        current_source = (
            REPOSITORY_ROOT / "src/imagent/gateway/delivery/proactive_runtime.py"
        ).read_text()
        current_nodes = _top_level_named_nodes(ast.parse(current_source))
        self.assertEqual(set(current_nodes), RUNTIME_NAMES)
        self.assertTrue(CONTRACT_NAMES.isdisjoint(current_nodes))

    def test_vocabulary_source_has_the_exact_finite_owner_set(self) -> None:
        proactive_source = (
            REPOSITORY_ROOT / "src/imagent/gateway/delivery/proactive.py"
        ).read_text()
        proactive_nodes = _top_level_named_nodes(ast.parse(proactive_source))
        proactive_nodes.pop("__all__")
        self.assertEqual(set(proactive_nodes), CONTRACT_NAMES)

        historical_path = REPOSITORY_ROOT / "src/imagent/contracts/delivery.py"
        self.assertFalse(historical_path.exists())

    def test_runtime_signatures_and_type_hints_resolve(self) -> None:
        methods = (
            "__init__",
            "deliver",
            "authorize",
            "deliver_internal",
            "_submit",
            "_resume_retryable_destinations",
            "_resolve_snapshots",
            "_preflight",
            "_send_destination",
        )
        functions = tuple(
            RUNTIME_NAMES
            - {"ResolveThreadRoutes", "DeliveryRouteError", "ProactiveDeliveryService"}
        )
        for name in methods:
            with self.subTest(method=name):
                method = getattr(runtime_owner.ProactiveDeliveryService, name)
                self.assertIsNotNone(inspect.signature(method))
                self.assertTrue(get_type_hints(method))
        for name in functions:
            with self.subTest(function=name):
                function = getattr(runtime_owner, name)
                self.assertIsNotNone(inspect.signature(function))
                self.assertTrue(get_type_hints(function))

    def test_clean_process_import_orders_are_cycle_free(self) -> None:
        environment = os.environ.copy()
        source_path = str(REPOSITORY_ROOT / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            path for path in (source_path, environment.get("PYTHONPATH")) if path
        )
        scripts = (
            "import imagent.gateway.delivery.proactive as seam\n"
            "import imagent.gateway.delivery.proactive_runtime as runtime\n"
            "import imagent.gateway.delivery as delivery\n"
            "import imagent.gateway as gateway\n"
            "import imagent.contracts as contracts\n"
            "removed = ('DeliveryTargetKind', 'ConversationDeliveryTarget',\n"
            "'ThreadRouteDeliveryTarget', 'DeliveryTarget', 'DeliveryIntent',\n"
            "'DestinationDeliveryResult', 'ProactiveDeliveryResult',\n"
            "'validate_delivery_intent', 'derive_delivery_target_fingerprint',\n"
            "'derive_delivery_payload_fingerprint', 'derive_delivery_submission_id',\n"
            "'derive_destination_delivery_id')\n"
            "assert all(not hasattr(contracts, name) and name not in contracts.__all__\n"
            "for name in removed)\n"
            "from imagent.gateway.delivery import DeliverySubmissionOrigin\n"
            "assert DeliverySubmissionOrigin.EXTERNAL.value == 'external'\n"
            "assert not hasattr(seam, 'ProactiveDeliveryService')\n"
            "assert not hasattr(seam, 'DeliveryRouteError')\n"
            "assert delivery.ProactiveDeliveryService is runtime.ProactiveDeliveryService\n"
            "assert gateway.ProactiveDeliveryService is runtime.ProactiveDeliveryService\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", scripts],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

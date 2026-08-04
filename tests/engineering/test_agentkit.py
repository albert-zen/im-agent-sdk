from __future__ import annotations

import fnmatch
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = yaml.safe_load((ROOT / "agentkit.yml").read_text(encoding="utf-8"))
COMPONENTS = CONFIG["components"]

PRODUCT_COMPONENTS = {
    "contracts",
    "ports",
    "attachments-and-media",
    "gateway",
    "delivery-planning-and-coordination",
    "diagnostics",
    "projections-and-recovery",
    "persistence",
    "controllers",
    "channel-adapters",
    "application-adapters-appserver",
    "application-adapters-t3",
    "testing-and-conformance",
}

# This namespace initializer is intentionally shared because AgentKit v1 has
# no parent application-adapters component: the Codex/Zen and T3 mappings both
# need to notice public exports from the common package.
SHARED_PATH_OWNERS = {
    "src/imagent/adapters.py": {
        "ports",
        "channel-adapters",
    },
    "src/imagent/contracts/__init__.py": {
        "release",
        "channel-adapters",
    },
    "src/imagent/applications/__init__.py": {
        "application-adapters-appserver",
        "application-adapters-t3",
    },
    "src/imagent/gateway/delivery/submissions.py": {
        "delivery-planning-and-coordination",
        "persistence",
    },
}

ROOT_MAINTENANCE_PATHS = {
    ".gitignore",
    "AGENTS.md",
    "README.md",
    "agentkit.yml",
    "pyproject.toml",
    "uv.lock",
}


def _matches(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-3])
    if "/**/" in pattern:
        prefix, suffix = pattern.split("/**/", 1)
        return path.startswith(prefix) and path.endswith(suffix)
    return False


def _components_for(path: str) -> set[str]:
    return {
        name
        for name, component in COMPONENTS.items()
        if any(_matches(path, pattern) for pattern in component.get("code", []))
        or any(_matches(path, pattern) for pattern in component.get("docs", []))
    }


def _relative_files(root: Path, pattern: str = "*") -> set[str]:
    return {
        path.relative_to(ROOT).as_posix()
        for path in root.rglob(pattern)
        if path.is_file() and "__pycache__" not in path.parts
    }


class AgentKitMappingTests(unittest.TestCase):
    def test_agentkit_mirror_has_one_owner_and_no_historical_module(self) -> None:
        expected = ROOT / "tests" / "engineering" / "test_agentkit.py"
        self.assertTrue(expected.is_file())
        self.assertFalse((ROOT / "tests" / "test_agentkit_mapping.py").exists())
        self.assertEqual(
            set((ROOT / "tests").rglob("test_agentkit.py")),
            {expected},
        )

    def test_representative_runtime_paths_have_precise_owners(self) -> None:
        expected = {
            "src/imagent/interaction/messages.py": {"contracts"},
            "src/imagent/interaction/operations.py": {"contracts"},
            "src/imagent/contracts/operations.py": {"contracts"},
            "src/imagent/adapters.py": {"ports", "channel-adapters"},
            "src/imagent/contracts/__init__.py": {"release", "channel-adapters"},
            "src/imagent/interaction/media.py": {"attachments-and-media"},
            "src/imagent/gateway/__init__.py": {"gateway"},
            "src/imagent/gateway/composition.py": {"gateway"},
            "src/imagent/gateway/input/content_transformation.py": {"gateway"},
            "src/imagent/gateway/input/failure_presentation.py": {"gateway"},
            "src/imagent/keyed_locks.py": {"delivery-planning-and-coordination"},
            "src/imagent/gateway/delivery/coordination.py": {"delivery-planning-and-coordination"},
            "src/imagent/gateway/delivery/planning.py": {"delivery-planning-and-coordination"},
            "src/imagent/gateway/delivery/outcome_observation.py": {
                "delivery-planning-and-coordination"
            },
            "src/imagent/gateway/delivery/proactive_authorization.py": {
                "delivery-planning-and-coordination"
            },
            "src/imagent/gateway/delivery/proactive.py": {"delivery-planning-and-coordination"},
            "src/imagent/gateway/delivery/submissions.py": {
                "delivery-planning-and-coordination",
                "persistence",
            },
            "src/imagent/gateway/presentation.py": {"delivery-planning-and-coordination"},
            "src/imagent/events.py": {"projections-and-recovery"},
            "src/imagent/projection_routes.py": {"projections-and-recovery"},
            "src/imagent/projection_runtime.py": {"projections-and-recovery"},
            "src/imagent/request_projection_runtime.py": {"projections-and-recovery"},
            "src/imagent/gateway/projection/checkpoints.py": {"projections-and-recovery"},
            "src/imagent/gateway/projection/recovery.py": {"projections-and-recovery"},
            "src/imagent/gateway/persistence/idempotency.py": {"persistence"},
            "src/imagent/gateway/persistence/memory.py": {"persistence"},
            "src/imagent/gateway/persistence/repository_contracts.py": {"persistence"},
            "src/imagent/request_correlations.py": {"persistence"},
            "src/imagent/gateway/persistence/row_mapping.py": {"persistence"},
            "src/imagent/sqlite_rows.py": {"persistence"},
            "src/imagent/interaction/controllers/contract.py": {"controllers"},
            "src/imagent/interaction/controllers/common.py": {"controllers"},
            "src/imagent/interaction/controllers/common_presentation.py": {"controllers"},
            "src/imagent/interaction/controllers/registry.py": {"controllers"},
            "src/imagent/interaction/controllers/request_presentation.py": {"controllers"},
            "src/imagent/interaction/channels/outbound_delivery.py": {"channel-adapters"},
            "src/imagent/interaction/channels/adapters/runtime.py": {"channel-adapters"},
            "src/imagent/interaction/channels/adapters/qq.py": {"channel-adapters"},
            "src/imagent/applications/adapters/appserver/_base.py": {
                "application-adapters-appserver"
            },
            "src/imagent/applications/adapters/codex.py": {"application-adapters-appserver"},
            "src/imagent/applications/adapters/zen.py": {"application-adapters-appserver"},
            "src/imagent/applications/presentation/live_activity.py": {"applications-common"},
            "src/imagent/applications/adapters/appserver/mapping.py": {
                "application-adapters-appserver"
            },
            "src/imagent/applications/adapters/appserver/transport.py": {
                "application-adapters-appserver"
            },
            "src/imagent/applications/adapters/appserver/diagnostics.py": {
                "application-adapters-appserver"
            },
            "src/imagent/applications/adapters/appserver/client/client.py": {
                "application-adapters-appserver"
            },
            "src/imagent/applications/adapters/appserver/requests.py": {
                "application-adapters-appserver"
            },
            "src/imagent/applications/adapters/t3.py": {"application-adapters-t3"},
            "src/imagent/interaction/testing/__init__.py": {"testing-and-conformance"},
            "src/imagent/interaction/testing/contracts.py": {"testing-and-conformance"},
            "src/imagent/interaction/testing/fakes.py": {"testing-and-conformance"},
            "src/imagent/testing/__init__.py": {"testing-and-conformance"},
        }
        for path, owners in expected.items():
            with self.subTest(path=path):
                self.assertEqual(_components_for(path), owners)

    def test_representative_tests_route_to_their_component(self) -> None:
        expected = {
            "tests/interaction/test_messages.py": {"contracts"},
            "tests/interaction/test_operations.py": {"contracts"},
            "tests/test_contracts.py": {"contracts"},
            "tests/interaction/test_media.py": {"attachments-and-media"},
            "tests/gateway/test_package_root.py": {"gateway"},
            "tests/test_gateway_operations.py": {"gateway"},
            "tests/gateway/input/test_content_transformation.py": {"gateway"},
            "tests/gateway/input/test_failure_presentation.py": {"gateway"},
            "tests/gateway/delivery/test_planning.py": {"delivery-planning-and-coordination"},
            "tests/gateway/delivery/test_coordination.py": {"delivery-planning-and-coordination"},
            "tests/gateway/delivery/test_outcome_observation.py": {
                "delivery-planning-and-coordination"
            },
            "tests/gateway/delivery/test_proactive_authorization.py": {
                "delivery-planning-and-coordination"
            },
            "tests/gateway/delivery/test_proactive_delivery.py": {
                "delivery-planning-and-coordination"
            },
            "tests/gateway/delivery/test_submissions.py": {"delivery-planning-and-coordination"},
            "tests/gateway/test_presentation.py": {"delivery-planning-and-coordination"},
            "tests/test_event_fanout.py": {"projections-and-recovery"},
            "tests/gateway/projection/test_checkpoints.py": {"projections-and-recovery"},
            "tests/gateway/projection/test_recovery.py": {"projections-and-recovery"},
            "tests/gateway/persistence/test_idempotency.py": {"persistence"},
            "tests/gateway/persistence/test_row_mapping.py": {"persistence"},
            "tests/test_storage.py": {"persistence"},
            "tests/interaction/controllers/test_contract.py": {"controllers"},
            "tests/interaction/controllers/test_registry.py": {"controllers"},
            "tests/interaction/controllers/test_common_commands.py": {"controllers"},
            "tests/interaction/controllers/test_request_presentation.py": {"controllers"},
            "tests/interaction/controllers/test_optional_controller.py": {"controllers"},
            "tests/interaction/channels/test_outbound_delivery.py": {"channel-adapters"},
            "tests/test_native_channels.py": {"channel-adapters"},
            "tests/interaction/channels/adapters/test_qq.py": {"channel-adapters"},
            "tests/interaction/channels/adapters/test_telegram.py": {"channel-adapters"},
            "tests/interaction/channels/adapters/test_feishu.py": {"channel-adapters"},
            "tests/interaction/channels/adapters/test_weixin.py": {"channel-adapters"},
            "tests/applications/adapters/appserver/test_client.py": {
                "application-adapters-appserver"
            },
            "tests/test_appserver_input.py": {"application-adapters-appserver"},
            "tests/applications/adapters/test_codex.py": {"application-adapters-appserver"},
            "tests/applications/adapters/test_zen.py": {"application-adapters-appserver"},
            "tests/applications/adapters/appserver/test_mapping.py": {
                "application-adapters-appserver"
            },
            "tests/applications/adapters/appserver/test_requests.py": {
                "application-adapters-appserver"
            },
            "tests/test_appserver_requests.py": {"application-adapters-appserver"},
            "tests/test_appserver_transport.py": {"application-adapters-appserver"},
            "tests/applications/adapters/appserver/test_transport.py": {
                "application-adapters-appserver"
            },
            "tests/applications/adapters/appserver/test_diagnostics.py": {
                "application-adapters-appserver"
            },
            "tests/applications/presentation/test_live_activity.py": {"applications-common"},
            "tests/applications/adapters/test_t3.py": {"application-adapters-t3"},
            "tests/conformance/test_adapter_contracts.py": {"testing-and-conformance"},
            "tests/engineering/test_schema_conformance.py": {"schema-conformance"},
            "tests/engineering/test_repository_maintainability.py": {"repository-maintainability"},
            "tests/engineering/test_release.py": {"release"},
        }
        for path, owners in expected.items():
            with self.subTest(path=path):
                self.assertEqual(_components_for(path), owners)

    def test_engineering_leaf_docs_route_to_their_owner(self) -> None:
        expected = {
            "docs/engineering/README.md": {"repository-maintainability"},
            "docs/engineering/testing-and-conformance/design.md": {"testing-and-conformance"},
            "docs/engineering/schema-conformance/design.md": {"schema-conformance"},
            "docs/engineering/repository-maintainability/design.md": {"repository-maintainability"},
            "docs/engineering/agentkit/design.md": {"agentkit"},
            "docs/engineering/release/design.md": {"release"},
        }
        for path, owners in expected.items():
            with self.subTest(path=path):
                self.assertEqual(_components_for(path), owners)

    def test_global_intent_changes_have_cross_component_impact(self) -> None:
        self.assertEqual(_components_for("docs/VISION.md"), PRODUCT_COMPONENTS)
        self.assertTrue(
            {"schema-conformance", "agentkit", "release"} <= _components_for("docs/ARCHITECTURE.md")
        )
        self.assertEqual(
            _components_for("docs/migrations/imcodex-followup-blockers.md"),
            {
                "application-adapters-appserver",
                "application-adapters-t3",
                "attachments-and-media",
                "channel-adapters",
                "gateway",
                "projections-and-recovery",
                "repository-maintainability",
            },
        )
        self.assertEqual(
            _components_for("docs/decisions/0002-design-authority-and-control-boundaries.md"),
            {
                "application-adapters-appserver",
                "application-adapters-t3",
                "contracts",
                "controllers",
                "gateway",
                "ports",
                "repository-maintainability",
                "testing-and-conformance",
            },
        )
        self.assertEqual(
            _components_for("docs/decisions/0004-event-fanout-and-recovery.md"),
            {
                "contracts",
                "gateway",
                "projections-and-recovery",
                "persistence",
                "application-adapters-appserver",
                "application-adapters-t3",
                "testing-and-conformance",
                "repository-maintainability",
            },
        )
        self.assertEqual(
            _components_for("docs/decisions/0007-projection-lifecycle-and-delivery-boundaries.md"),
            {
                "application-adapters-appserver",
                "application-adapters-t3",
                "channel-adapters",
                "contracts",
                "delivery-planning-and-coordination",
                "gateway",
                "persistence",
                "ports",
                "projections-and-recovery",
                "repository-maintainability",
                "testing-and-conformance",
            },
        )
        self.assertEqual(
            _components_for("docs/decisions/0008-interactive-request-routing.md"),
            {
                "application-adapters-appserver",
                "application-adapters-t3",
                "channel-adapters",
                "contracts",
                "controllers",
                "gateway",
                "persistence",
                "ports",
                "projections-and-recovery",
                "repository-maintainability",
                "testing-and-conformance",
            },
        )

    def test_authoritative_paths_and_component_docs_exist(self) -> None:
        self.assertEqual(CONFIG["docs"]["design"], "docs/VISION.md")
        self.assertEqual(CONFIG["docs"]["workflow"], "docs/ARCHITECTURE.md")
        self.assertEqual(CONFIG["docs"]["decisions"], "docs/decisions")
        for component in COMPONENTS.values():
            for path in component.get("docs", []):
                self.assertTrue((ROOT / path).is_file(), path)

    def test_every_configured_path_matches_a_durable_file(self) -> None:
        for component_name, component in COMPONENTS.items():
            for field in ("code", "docs"):
                for pattern in component.get(field, []):
                    with self.subTest(
                        component=component_name,
                        field=field,
                        pattern=pattern,
                    ):
                        if any(character in pattern for character in "*?["):
                            matches = [path for path in ROOT.glob(pattern) if path.is_file()]
                            self.assertTrue(matches, pattern)
                        else:
                            self.assertTrue((ROOT / pattern).exists(), pattern)

    def test_durable_repository_inventory_has_explicit_owners(self) -> None:
        runtime_paths = _relative_files(ROOT / "src" / "imagent", "*.py")
        runtime_paths.add("src/imagent/py.typed")
        test_paths = _relative_files(ROOT / "tests", "test_*.py")
        schema_paths = _relative_files(ROOT / "schemas" / "v1")
        maintenance_paths = set(ROOT_MAINTENANCE_PATHS)
        maintenance_paths.update(_relative_files(ROOT / ".agents"))
        maintenance_paths.update(_relative_files(ROOT / ".github" / "workflows"))
        maintenance_paths.update(_relative_files(ROOT / "docs", "*.md"))
        maintenance_paths.update(_relative_files(ROOT / "plugins" / "agentkit"))
        maintenance_paths.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "scripts").iterdir()
            if path.is_file()
        )

        uniquely_owned = runtime_paths | test_paths | schema_paths
        for path in sorted(uniquely_owned):
            with self.subTest(path=path):
                owners = _components_for(path)
                expected_shared = SHARED_PATH_OWNERS.get(path)
                if expected_shared is not None:
                    self.assertEqual(owners, expected_shared)
                else:
                    self.assertEqual(len(owners), 1, f"{path}: {sorted(owners)}")

        for path in sorted(maintenance_paths):
            with self.subTest(path=path):
                self.assertTrue(_components_for(path), path)

    def test_removed_broad_docs_do_not_remain_as_duplicate_authority(self) -> None:
        for path in (
            "THIRD_PARTY_NOTICES.md",
            "docs/design.md",
            "docs/workflow.md",
            "docs/ADAPTERS.md",
            "docs/DECISIONS.md",
            "docs/DOMAIN_MODEL.md",
            "docs/PROTOCOL.md",
        ):
            self.assertFalse((ROOT / path).exists(), path)

    def test_mapping_does_not_use_broad_source_catchall(self) -> None:
        patterns = {
            pattern for component in COMPONENTS.values() for pattern in component.get("code", [])
        }
        self.assertNotIn("src/**", patterns)


if __name__ == "__main__":
    unittest.main()

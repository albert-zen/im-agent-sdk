from __future__ import annotations

import ast
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agentkit import _validate_component_map
from scripts.check_doc_links import ROOT, main, markdown_files
from scripts.validate_component_map import (
    ComponentMapError,
    _facade_reexport_is_allowed,
    _load_component_map_text,
    _resolve_internal_module,
    _resolved_imports,
    load_component_map,
    validate_component_map,
)


class ComponentMapTests(unittest.TestCase):
    def test_complete_current_source_inventory_and_leaf_schema(self) -> None:
        summary = validate_component_map(load_component_map())

        self.assertGreaterEqual(summary["components"], 50)
        self.assertGreaterEqual(summary["test_paths"], 38)
        self.assertEqual(summary["orphans"], 0)
        self.assertGreater(summary["split_candidates"], 0)
        self.assertGreaterEqual(summary["public_facade_exports"], 240)
        self.assertGreaterEqual(summary["internal_imports"], 1400)

    def test_split_candidate_import_requires_an_explicit_owner_edge(self) -> None:
        component_map = copy.deepcopy(load_component_map())
        for component_id in (
            "applications.adapters.codex",
            "applications.adapters.zen",
        ):
            component_map["components"][component_id]["dependencies"].remove("interaction.media")

        with self.assertRaisesRegex(
            ComponentMapError,
            "(?s)forbidden internal component imports.*_base.py",
        ):
            validate_component_map(component_map)

    def test_formal_facade_reexports_must_be_declared(self) -> None:
        component_map = copy.deepcopy(load_component_map())
        component_map["structural_status"]["formal_facades"] = [
            facade
            for facade in component_map["structural_status"]["formal_facades"]
            if facade["path"] != "src/imagent/contracts/__init__.py"
        ]

        with self.assertRaisesRegex(
            ComponentMapError,
            "(?s)forbidden internal component imports.*contracts/__init__.py",
        ):
            validate_component_map(component_map)

    def test_facade_reexport_requires_exact_imported_symbol_owner(self) -> None:
        public_owners = {"imagent.facade:Public": ["component.public"]}

        self.assertFalse(
            _facade_reexport_is_allowed(
                source_path="src/imagent/facade.py",
                facade_reference="imagent.facade:Public",
                target_owners=["component.other"],
                public_export_owners=public_owners,
                formal_facade_paths={"src/imagent/facade.py"},
            )
        )
        self.assertTrue(
            _facade_reexport_is_allowed(
                source_path="src/imagent/facade.py",
                facade_reference="imagent.facade:Public",
                target_owners=["component.public"],
                public_export_owners=public_owners,
                formal_facade_paths={"src/imagent/facade.py"},
            )
        )

    def test_multi_owner_split_candidate_cannot_be_a_formal_facade(self) -> None:
        component_map = copy.deepcopy(load_component_map())
        component_map["structural_status"]["formal_facades"].append(
            {
                "path": "src/imagent/diagnostics.py",
                "owner": "gateway.diagnostics",
                "rationale": "Invalid mixed transition declaration for test coverage.",
            }
        )

        with self.assertRaisesRegex(ComponentMapError, "invalid formal facade declaration"):
            validate_component_map(component_map)

    def test_facade_alias_preserves_imported_and_bound_symbol_names(self) -> None:
        node = ast.parse("from .model import Internal as Public").body[0]

        self.assertEqual(
            _resolved_imports(node, package="imagent.facade"),
            [("imagent.facade.model", "Internal", "Public")],
        )

    def test_current_import_exceptions_must_match_a_real_import(self) -> None:
        component_map = copy.deepcopy(load_component_map())
        component_map["architecture_lint"]["current_import_exceptions"][0]["symbol"] = (
            "MissingGatewayOperation"
        )

        with self.assertRaisesRegex(ComponentMapError, "unused current import exceptions"):
            validate_component_map(component_map)

    def test_unknown_internal_import_target_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ComponentMapError, "unknown internal import target"):
            _resolve_internal_module(
                "imagent.missing.module",
                internal_package="imagent",
                module_paths={"imagent": "src/imagent/__init__.py"},
            )

    def test_agentkit_has_no_second_import_graph(self) -> None:
        config = Path("agentkit.yml").read_text(encoding="utf-8")

        self.assertNotIn("\nlayers:", config)

    def test_dunder_all_exports_must_have_component_ownership(self) -> None:
        component_map = copy.deepcopy(load_component_map())
        component = component_map["components"]["applications.adapters.appserver.client"]
        component["public_exports"]["current"].remove(
            "imagent.applications.adapters.appserver.client:AppServerClient"
        )

        with self.assertRaisesRegex(ComponentMapError, "unmapped __all__ public exports"):
            validate_component_map(component_map)

    def test_duplicate_yaml_keys_fail_explicitly(self) -> None:
        with self.assertRaisesRegex(ComponentMapError, "duplicate YAML key 'version'"):
            _load_component_map_text("version: 1\nversion: 2\n")

    def test_component_leaf_schema_and_direct_parent_are_enforced(self) -> None:
        component_map = load_component_map()
        mutations = (
            ("parent", "gateway", "invalid layer/parent"),
            ("purpose", [], "purpose must be non-empty text"),
            ("owns", [], "owns must be non-empty"),
        )

        for field, value, expected_error in mutations:
            with self.subTest(field=field):
                mutated = copy.deepcopy(component_map)
                mutated["components"]["gateway.routing.bindings"][field] = value
                with self.assertRaisesRegex(ComponentMapError, expected_error):
                    validate_component_map(mutated)

    def test_component_dependency_graph_must_be_acyclic(self) -> None:
        component_map = copy.deepcopy(load_component_map())
        component_map["components"]["interaction.messages"]["dependencies"] = [
            "interaction.operations"
        ]

        with self.assertRaisesRegex(ComponentMapError, "component dependency cycle"):
            validate_component_map(component_map)

    def test_forbidden_reverse_layer_edge_fails_explicitly(self) -> None:
        component_map = copy.deepcopy(load_component_map())
        component_map["components"]["interaction.channels.channel-contract"]["dependencies"].append(
            "gateway.composition"
        )

        with self.assertRaisesRegex(ComponentMapError, "violates layer dependencies"):
            validate_component_map(component_map)

    def test_port_and_composition_contract_edges_are_explicit(self) -> None:
        component_map = load_component_map()
        components = component_map["components"]

        composition = components["gateway.composition"]
        self.assertEqual(
            composition["public_exports"]["current"],
            [
                "imagent.gateway.composition:GatewayRepositories",
                "imagent.gateway.composition:GatewayLimits",
                "imagent.gateway.composition:GatewayExtensions",
            ],
        )
        self.assertEqual(
            composition["current_code"],
            [
                "src/imagent/gateway/composition.py",
                "src/imagent/gateway/__init__.py",
            ],
        )
        self.assertNotIn("src/imagent/gateway_composition.py", composition["current_code"])

        controller_action_edges = {
            "applications.operations",
            "gateway.routing.gateway-operations",
            "gateway.persistence.state-contracts",
        }
        self.assertGreaterEqual(
            set(components["interaction.controllers.controller-contract"]["dependencies"]),
            controller_action_edges,
        )
        common_command_edges = {
            "applications.application-contract",
            "applications.capabilities",
            "applications.operations",
            "applications.requests",
            "gateway.routing.bindings",
            "gateway.routing.gateway-operations",
            "gateway.routing.projection-routes",
            "gateway.projection.request-correlation",
            "gateway.persistence.state-contracts",
        }
        self.assertGreaterEqual(
            set(components["interaction.controllers.common-commands"]["dependencies"]),
            common_command_edges,
        )
        exceptions = {(item["from"], item["to"]) for item in component_map["dependency_exceptions"]}
        self.assertEqual(
            exceptions,
            {
                *(
                    ("interaction.controllers.controller-contract", target)
                    for target in controller_action_edges
                ),
                *(
                    ("interaction.controllers.common-commands", target)
                    for target in common_command_edges
                ),
                (
                    "interaction.controllers.request-presentation",
                    "applications.requests",
                ),
            },
        )

        self.assertGreaterEqual(
            set(components["applications.application-contract"]["dependencies"]),
            {
                "applications.capabilities",
                "applications.events",
                "applications.operations",
                "applications.requests",
            },
        )
        self.assertNotIn(
            "applications.application-contract",
            components["applications.operations"]["dependencies"],
        )
        self.assertIn(
            "interaction.channels.channel-contract",
            components["interaction.channels.outbound-delivery"]["dependencies"],
        )
        self.assertNotIn(
            "interaction.channels.outbound-delivery",
            components["interaction.channels.channel-contract"]["dependencies"],
        )
        self.assertGreaterEqual(
            set(components["gateway.composition"]["dependencies"]),
            {
                "interaction.controllers.controller-contract",
                "interaction.controllers.request-presentation",
                "gateway.admission",
                "gateway.input.content-transformation",
                "gateway.input.failure-presentation",
                "gateway.projection.observation",
                "gateway.presentation",
                "gateway.delivery.coordination",
                "gateway.delivery.proactive-authorization",
                "gateway.delivery.proactive-delivery",
                "gateway.delivery.outcome-observation",
                "gateway.persistence.state-contracts",
                "gateway.persistence.repository-contracts",
                "gateway.persistence.memory",
                "gateway.persistence.idempotency",
            },
        )
        self.assertEqual(
            components["gateway.delivery.proactive-authorization"]["public_contracts"],
            [
                "DeliveryAuthorizer",
                "DeliveryPrincipal",
                "ScopedDeliveryAuthorizer",
                "validate_delivery_principal",
            ],
        )
        self.assertGreaterEqual(
            set(components["gateway.admission"]["dependencies"]),
            {"interaction.channels.channel-contract", "interaction.messages"},
        )
        self.assertNotIn(
            "interaction.channels.ingress",
            components["gateway.admission"]["dependencies"],
        )
        self.assertGreaterEqual(
            set(components["interaction.channels.channel-contract"]["public_contracts"]),
            {"InboundAdmission", "InboundAdmissionHandler"},
        )
        channel_contract = components["interaction.channels.channel-contract"]
        self.assertEqual(
            channel_contract["public_exports"]["current"],
            channel_contract["public_exports"]["target"],
        )
        self.assertTrue(
            all(
                export.startswith(
                    (
                        "imagent.interaction.channels:",
                        "imagent.interaction.channels.contract:",
                    )
                )
                for export in channel_contract["public_exports"]["current"]
            )
        )
        self.assertNotIn(
            "src/imagent/adapters.py",
            channel_contract["current_code"],
        )
        native_facade = next(
            facade
            for facade in component_map["structural_status"]["formal_facades"]
            if facade["path"] == "src/imagent/channels/__init__.py"
        )
        self.assertEqual(native_facade["owner"], "interaction.channels.adapters")

    def test_approved_runtime_layers_and_registry_owner_are_explicit(self) -> None:
        component_map = load_component_map()

        self.assertEqual(
            {layer: config["may_depend_on"] for layer, config in component_map["layers"].items()},
            {
                "interaction": [],
                "applications": ["interaction"],
                "gateway": ["interaction", "applications"],
                "engineering": ["interaction", "applications", "gateway"],
            },
        )
        registry = component_map["components"]["interaction.controllers.command-registry"]
        self.assertEqual(
            registry["current_code"],
            ["src/imagent/interaction/controllers/registry.py"],
        )
        self.assertNotIn("implementation absent", registry["gaps"])

    def test_schema_semantics_belong_to_runtime_leaves(self) -> None:
        component_map = load_component_map()
        components = component_map["components"]
        schema_support = components["engineering.schema-conformance"]

        self.assertEqual(
            schema_support["current_code"],
            ["schemas/v1/README.md", "scripts/validate_schemas.py"],
        )
        self.assertEqual(
            schema_support["current_tests"],
            ["tests/engineering/test_schema_conformance.py"],
        )
        self.assertEqual(schema_support["target_tests"], schema_support["current_tests"])
        self.assertEqual(schema_support["gaps"], [])
        self.assertIn(
            "schemas/v1/messages.schema.json",
            components["interaction.messages"]["current_code"],
        )
        self.assertIn(
            "schemas/v1/messages.schema.json",
            components["interaction.messages"]["target_code"],
        )
        self.assertIn(
            "schemas/v1/operations.schema.json",
            components["gateway.routing.gateway-operations"]["current_code"],
        )
        self.assertIn(
            "schemas/v1/resources.schema.json",
            components["applications.application-contract"]["current_code"],
        )
        self.assertIn(
            "schemas/v1/resources.schema.json",
            components["applications.application-contract"]["target_code"],
        )

    def test_conformance_implementation_mirror_and_facade_are_explicit(self) -> None:
        component = load_component_map()["components"]["engineering.testing-and-conformance"]

        expected_code = [
            "src/imagent/interaction/testing/__init__.py",
            "src/imagent/interaction/testing/contracts.py",
            "src/imagent/interaction/testing/fakes.py",
            "src/imagent/testing/__init__.py",
        ]
        self.assertEqual(component["current_code"], expected_code)
        self.assertEqual(component["target_code"], expected_code)
        self.assertEqual(
            component["current_tests"],
            ["tests/conformance/test_adapter_contracts.py"],
        )
        self.assertEqual(component["target_tests"], component["current_tests"])
        self.assertEqual(component["gaps"], [])
        for module_name in ("imagent.interaction.testing", "imagent.testing"):
            with self.subTest(module_name=module_name):
                self.assertTrue(
                    all(
                        f"{module_name}:{name}" in component["public_exports"]["current"]
                        for name in component["public_contracts"]
                    )
                )

    def test_application_input_contracts_are_not_owned_by_gateway(self) -> None:
        components = load_component_map()["components"]

        self.assertEqual(
            components["gateway.input.dispatch"]["public_contracts"],
            ["derive_client_message_id"],
        )
        self.assertGreaterEqual(
            set(components["applications.application-contract"]["public_contracts"]),
            {
                "ApplicationInputDispatch",
                "AcceptedTurn",
                "InputContinuationPreference",
            },
        )

    def test_zen_adapter_declares_shared_artifact_materialization_seam(self) -> None:
        zen = load_component_map()["components"]["applications.adapters.zen"]

        self.assertIn(
            "applications.presentation.artifact-materialization",
            zen["dependencies"],
        )
        self.assertIn(
            "tests/applications/presentation/test_artifact_materialization.py",
            zen["current_tests"],
        )
        self.assertIn(
            "docs/decisions/0015-typed-extension-seams-and-composition.md",
            zen["adrs"],
        )

    def test_agentkit_architecture_and_check_commands_consume_the_map(self) -> None:
        with patch("scripts.agentkit.subprocess.call", return_value=0) as call:
            for command in ("lint-architecture", "check"):
                with self.subTest(command=command):
                    status = _validate_component_map(
                        uv="/tools/uv",
                        repo=Path("/repository"),
                        arguments=[command],
                        environment={"PYTHONUTF8": "1"},
                    )
                    self.assertEqual(status, 0)

        self.assertEqual(call.call_count, 2)
        for invocation in call.call_args_list:
            self.assertEqual(
                invocation.args[0],
                [
                    "/tools/uv",
                    "run",
                    "--isolated",
                    "--no-project",
                    "--with",
                    "PyYAML==6.0.3",
                    "--with",
                    "httpx>=0.28,<1",
                    "python",
                    "/repository/scripts/validate_component_map.py",
                ],
            )
            self.assertEqual(
                invocation.kwargs["env"]["PYTHONPATH"],
                "/repository/src",
            )

    def test_component_map_gate_does_not_use_the_caller_python(self) -> None:
        with (
            patch("scripts.agentkit.sys.executable", "/usr/bin/python3"),
            patch("scripts.agentkit.subprocess.call", return_value=0) as call,
        ):
            status = _validate_component_map(
                uv="/tools/uv",
                repo=Path("/repository"),
                arguments=["check"],
                environment={},
            )

        self.assertEqual(status, 0)
        self.assertNotIn("/usr/bin/python3", call.call_args.args[0])

    def test_unrelated_agentkit_commands_skip_the_component_map_gate(self) -> None:
        with patch("scripts.agentkit.subprocess.call") as call:
            status = _validate_component_map(
                uv="/tools/uv",
                repo=Path("/repository"),
                arguments=["status"],
                environment={},
            )

        self.assertEqual(status, 0)
        call.assert_not_called()


ENGINEERING_LEAVES = (
    "testing-and-conformance",
    "schema-conformance",
    "repository-maintainability",
    "agentkit",
    "release",
)


class DocumentationLinkTests(unittest.TestCase):
    def test_canonical_guidance_routes_engineering_support_changes(self) -> None:
        guidance = (
            ROOT / "AGENTS.md",
            ROOT / "README.md",
            ROOT / "plugins" / "agentkit" / "skills" / "agentkit" / "SKILL.md",
        )
        for path in guidance:
            with self.subTest(path=path):
                self.assertIn("docs/engineering/", path.read_text(encoding="utf-8"))

    def test_engineering_leaves_have_meaningful_design_and_testing_pages(self) -> None:
        for leaf in ENGINEERING_LEAVES:
            for page in ("design.md", "testing.md"):
                path = ROOT / "docs" / "engineering" / leaf / page
                with self.subTest(path=path):
                    content = path.read_text(encoding="utf-8")
                    self.assertGreater(len(content), 400)
                    self.assertTrue(content.startswith("# "))
                    self.assertIn("## ", content)

    def test_transitional_broad_pages_point_to_engineering_authority(self) -> None:
        transitional = (
            ROOT / "docs" / "components" / "testing-and-conformance",
            ROOT / "docs" / "components" / "repository-maintainability",
        )
        for directory in transitional:
            for page in ("design.md", "testing.md"):
                path = directory / page
                with self.subTest(path=path):
                    self.assertIn("../../engineering/", path.read_text(encoding="utf-8"))

    def test_markdown_inventory_includes_engineering_tree(self) -> None:
        files = set(markdown_files())
        expected = {
            ROOT / "docs" / "engineering" / leaf / page
            for leaf in ENGINEERING_LEAVES
            for page in ("design.md", "testing.md")
        }
        self.assertTrue(expected <= files)

    def test_repository_maintainability_tests_have_one_engineering_owner(self) -> None:
        expected = ROOT / "tests" / "engineering" / "test_repository_maintainability.py"

        self.assertEqual(
            list((ROOT / "tests").rglob("test_repository_maintainability.py")),
            [expected],
        )
        for historical_path in (
            ROOT / "tests" / "test_component_map.py",
            ROOT / "tests" / "test_documentation_links.py",
        ):
            with self.subTest(historical_path=historical_path):
                self.assertFalse(historical_path.exists())

    def test_all_local_markdown_links_resolve(self) -> None:
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    unittest.main()

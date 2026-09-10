# ruff: noqa: E501

from __future__ import annotations

import ast
import copy
import re
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
            if facade["path"] != "src/imagent/gateway/__init__.py"
        ]

        with self.assertRaisesRegex(
            ComponentMapError,
            "(?s)forbidden internal component imports.*gateway/__init__.py",
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
                "path": "src/imagent/gateway/__init__.py",
                "owner": "gateway.diagnostics",
                "rationale": "Invalid mixed transition declaration for test coverage.",
            }
        )

        with self.assertRaisesRegex(ComponentMapError, "invalid formal facade declaration"):
            validate_component_map(component_map)

    def test_gateway_diagnostics_map_has_only_the_focused_owner_surface(self) -> None:
        component = load_component_map()["components"]["gateway.diagnostics"]

        for state in ("current", "target"):
            self.assertTrue(
                all(
                    export.startswith("imagent.gateway.diagnostics:")
                    for export in component["public_exports"][state]
                )
            )

        formal_facades = load_component_map()["structural_status"]["formal_facades"]
        self.assertNotIn(
            "src/imagent/diagnostics.py",
            {facade["path"] for facade in formal_facades},
        )

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
                "imagent:GatewayLimits",
                "imagent:GatewayExtensions",
                "imagent.gateway:GatewayLimits",
                "imagent.gateway:GatewayExtensions",
                "imagent.gateway.composition:GatewayLimits",
                "imagent.gateway.composition:GatewayExtensions",
            ],
        )
        self.assertEqual(
            composition["current_code"],
            [
                "src/imagent/gateway/composition.py",
                "src/imagent/gateway/controller_input.py",
                "src/imagent/gateway/__init__.py",
            ],
        )
        self.assertNotIn("src/imagent/gateway_composition.py", composition["current_code"])

        controller_action_edges = {"gateway.actions"}
        self.assertGreaterEqual(
            set(components["interaction.controllers.controller-contract"]["dependencies"]),
            controller_action_edges,
        )
        common_command_edges = {
            "applications.application-contract",
            "applications.capabilities",
            "applications.operations",
            "applications.requests",
            "gateway.actions",
            "gateway.outcomes",
            "gateway.persistence.state-contracts",
        }
        command_registry_edges = {"gateway.actions"}
        self.assertGreaterEqual(
            set(components["interaction.controllers.command-registry"]["dependencies"]),
            command_registry_edges,
        )
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
                    ("interaction.controllers.command-registry", target)
                    for target in command_registry_edges
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
                "DeliveryAuthorizationError",
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
            [
                "MissingBindingError",
                "StaleBindingError",
                "derive_client_message_id",
            ],
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
                    str(Path("/repository") / "scripts" / "validate_component_map.py"),
                ],
            )
            self.assertEqual(
                invocation.kwargs["env"]["PYTHONPATH"],
                str(Path("/repository") / "src"),
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


def _normalized_blocks(markdown: str) -> list[str]:
    return [" ".join(block.split()) for block in re.split(r"\n\s*\n", markdown) if block.strip()]


def _heading_section(markdown: str, heading: str) -> list[str]:
    lines = markdown.splitlines()
    start = lines.index(heading) + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("### ")),
        len(lines),
    )
    return _normalized_blocks("\n".join(lines[start:end]))


def _table_rows(markdown: str, heading: str) -> list[tuple[str, str]]:
    section = "\n".join(markdown.split(heading, 1)[1].split("\n### ", 1)[0].splitlines()[2:])
    rows = []
    for line in section.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            cells = [" ".join(cell.split()) for cell in line.strip("|").split("|")]
            if len(cells) == 2 and cells[0] != "IMCodex source":
                rows.append((cells[0], cells[1]))
    return rows


def _bullet_inventory(markdown: str, marker: str) -> list[str]:
    section = markdown.split(marker, 1)[1]
    bullets: list[str] = []
    current = ""
    for line in section.splitlines():
        if line.startswith("-"):
            if current:
                bullets.append(" ".join(current.split()))
            current = line[1:].strip()
        elif current and line.strip():
            current += " " + line.strip()
        elif current and not line.strip():
            break
    if current:
        bullets.append(" ".join(current.split()))
    return bullets


def _assert_complete_inventory(
    test: unittest.TestCase, actual: list, expected: list, label: str
) -> None:
    test.assertEqual(len(expected), len(set(expected)), label)
    test.assertEqual(actual, expected, label)
    for index, item in enumerate(expected):
        mutated = actual[:index] + actual[index + 1 :]
        test.assertNotEqual(mutated, expected, f"{label} deletion {index} must fail")
        wording_mutation = actual.copy()
        if isinstance(item, tuple):
            wording_mutation[index] = (item[0], item[1].replace(item[1].split()[0], "MUTATED", 1))
        else:
            wording_mutation[index] = item.replace(item.split()[0], "MUTATED", 1)
        test.assertNotEqual(wording_mutation, expected, f"{label} wording loss {index} must fail")


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

    def test_retired_pre_three_layer_and_consumer_status_docs_are_absent(self) -> None:
        retired_component_directories = (
            "application-adapters",
            "attachments-and-media",
            "channel-adapters",
            "controllers",
            "delivery-planning-and-coordination",
            "persistence",
            "ports",
            "projections-and-recovery",
            "repository-maintainability",
            "testing-and-conformance",
        )
        for directory in retired_component_directories:
            path = ROOT / "docs" / "components" / directory
            with self.subTest(path=path):
                self.assertFalse(any(candidate.is_file() for candidate in path.rglob("*")))

        migrations = ROOT / "docs" / "migrations"
        self.assertFalse(any(candidate.is_file() for candidate in migrations.rglob("*")))

        channel_design = (
            ROOT / "docs" / "components" / "interaction" / "channels" / "adapters" / "design.md"
        ).read_text(encoding="utf-8")
        channel_testing = (
            ROOT / "docs" / "components" / "interaction" / "channels" / "adapters" / "testing.md"
        ).read_text(encoding="utf-8")
        channel_design_inventory = {
            "### QQ": (
                "QQ owns bot authentication, Gateway WebSocket reconnect state, stable C2C and group identities, passive-reply context, media staging, and native Markdown/file capabilities. C2C and group events retain native message and sender IDs; group mentions are stripped only after native targeting succeeds. Passive-reply context is bounded and degrades to proactive delivery when the native reply window cannot be proven.",
                "QQ quote snapshots are bounded adapter-specific untrusted input. Parsing accepts only QQ evidence and bounds reference IDs, text, attachment counts, filenames, voice transcripts, and rendered text. Raw envelopes, URLs, bytes, and nested quote history are excluded. The adapter appends a labelled untrusted block after the current text before the common native boundary emits ordinary `TextFormat.PLAIN` content. The parsed shape never enters shared native models or runtime code and does not create a common quote contract, capability, or Metadata key.",
                "The descriptive quote block grants no message, delivery, idempotency, binding, reply-target, request, or approval authority. Only the authenticated native inbound path supplies a provider snapshot. A caller may mimic the label only as ordinary untrusted text; it cannot forge a trusted snapshot, and Metadata is ignored for this feature.",
                "Enabled instances require normalized `app_id` and `client_secret` values and an HTTP(S) API endpoint. Authentication, reconnect, upload, reply-window, and unsupported group-file failures remain explicit. Media is staged inside the Channel-owned bounded spool before crossing the attachment source boundary. Diagnostics expose only bounded lifecycle/worker facts and the fixed-capacity inbound queue depth/overflow count, never credentials, endpoint, identities, media paths, or exception text.",
            ),
            "### Telegram": (
                "Telegram owns Bot API polling offsets, bot identity, private/group/forum Conversation normalization, mention targeting, native reply IDs, and media transfer. Private chats, groups, and forum topics remain distinct routes; polling offsets are Channel reconnect state, not Agent cursors. Group input is admitted only after native mention/reply targeting. Enabled instances require a direct token or private token file and a credential-free HTTP(S) endpoint; corrupt offsets fail closed. Bot API descriptions may surface only without leaking tokens. Diagnostics expose bounded polling lifecycle facts without tokens, offsets, endpoints, native identities, or exception text.",
            ),
            "### Feishu/Lark": (
                "Feishu/Lark owns App credentials, the official SDK connection, named domain selection, native chat/topic identity, mention targeting, resource transfer, and reconnect health. Direct chats and topics remain distinct Conversations; private resource references become content only through the bounded Channel spool. The optional native SDK is constructed with strict transport security and bounded inbound buffering. Only the named Feishu and Lark domains are accepted, credentials are required, and subscription, reconnect, token, resource, overflow, and delivery failures remain explicit. Diagnostics expose bounded lifecycle/worker and inbound queue facts without credentials, endpoints, identities, resource keys, paths, SDK snapshots, or exception text.",
            ),
            "### Weixin iLink": (
                "Weixin owns consumer-enrolled iLink credentials, direct-message polling, native reply context tokens, bounded reconnect state, media crypto/transport, and credential-file protection. Only official direct-user identities are supported; group and bot messages are explicitly unsupported. Context tokens and update cursors are minimal Channel delivery/reconnect state. The transport accepts only the official HTTPS origin, and corrupt, overly permissive, wildcard-owner, or malformed credential state fails closed. Enrollment UX and stale-credential recovery remain consumer policy. Diagnostics expose bounded polling lifecycle facts without credentials, tokens, cursors, endpoints, identities, paths, or exception text.",
            ),
        }
        for heading, expected in channel_design_inventory.items():
            actual = _heading_section(channel_design, heading)
            _assert_complete_inventory(self, actual, list(expected), f"design {heading}")

        channel_testing_inventory = [
            "QQ direct/group targeting, authenticated-native-only quote provenance, bounded parsing, plain-text emission, shared-model exclusion, forged-label and Metadata non-authority, passive-to-proactive fallback, explicit authentication/reconnect/upload/reply-window/unsupported-group-file failures, media staging, queue facts, and lazy facade;",
            "Telegram private/group/forum routing, mention/reply targeting, private token files, corrupt-offset failure, token-safe Bot API descriptions, and credential-free diagnostics;",
            "Feishu/Lark named-domain restriction, topic identity, bounded resource staging, strict transport-security construction, bounded inbound buffering, SDK queue overflow/reconnect, and redaction; and",
            "Weixin direct-user-only support, official-origin enforcement, protected credential state, context/cursor ownership, and explicit unsupported group and bot input.",
        ]
        actual_testing = _bullet_inventory(
            channel_testing, "Provider-focused suites additionally prove:"
        )
        _assert_complete_inventory(
            self, actual_testing, channel_testing_inventory, "provider testing"
        )
        for omission in (
            "native message and sender IDs",
            "polling offsets are Channel reconnect state",
            "named domain selection",
            "Only official direct-user identities",
        ):
            self.assertNotEqual(
                " ".join(channel_design.split()).replace(omission, ""),
                " ".join(channel_design.split()),
            )

        reuse = (ROOT / "docs" / "REUSE.md").read_text(encoding="utf-8")
        normalized_reuse = " ".join(reuse.split())
        channel_rows = [
            (
                "`channels/access.py`",
                "`interaction/channels/ingress.py`; stable-ID access policy only",
            ),
            (
                "`channels/base.py`",
                "`interaction/channels/adapters/base.py`; lifecycle/access base without product telemetry or a historical shim",
            ),
            (
                "`channels/artifacts.py`",
                "`interaction/channels/outbound_delivery.py`; one native attachment attempt, while consumers retain bytes/root/quota/ledger/sweep ownership",
            ),
            (
                "`channels/media.py`",
                "`interaction/channels/ingress_media.py`; bounded staging with its shared lock/quota/secure-create/cleanup/cancellation transaction boundary intact",
            ),
            (
                "`channels/text.py`",
                "`interaction/channels/outbound_delivery.py`; defensive native text splitting",
            ),
            (
                "`channels/qq.py`, `channels/qq_media.py`",
                "`interaction/channels/adapters/qq.py`, `qq_media.py`",
            ),
            ("`channels/telegram.py`", "`interaction/channels/adapters/telegram.py`"),
            (
                "`channels/feishu.py`",
                "`interaction/channels/adapters/feishu.py`; optional SDK loading retained",
            ),
            (
                "`channels/weixin_ilink.py`, `channels/weixin_state.py`, `channels/weixin.py`",
                "matching Interaction adapter modules; credential/reconnect state stays Channel-owned and enrollment UX stays downstream",
            ),
            ("top-level `models.py`", "split into private ingress and outbound-delivery DTOs"),
            (
                "top-level `file_types.py`",
                "`interaction/media.py`; shared generic-file byte validation",
            ),
            (
                "top-level `windows_security.py`",
                "`interaction/channels/ingress_security.py`; secure staging without a historical shim",
            ),
            (
                "top-level `config.py`",
                "only neutral endpoint validation moved to `interaction/channels/adapters/endpoints.py`; product configuration did not transfer",
            ),
        ]
        appserver_rows = [
            (
                "`app_server_target.py`",
                "`applications/adapters/appserver/client/target.py`; endpoint/ownership model with neutral configuration wording",
            ),
            ("`appserver/retry.py`", "`applications/adapters/appserver/client/retry.py`"),
            ("`appserver/protocol_map.py`", "`applications/adapters/appserver/mapping.py`"),
            (
                "`appserver/diagnostics.py`",
                "`applications/adapters/appserver/diagnostics.py`; fixed bounded redacted facts/helpers",
            ),
            (
                "`appserver/client.py`",
                "`applications/adapters/appserver/client/client.py`; JSON-RPC connection-epoch state machine kept coherent",
            ),
            (
                "`appserver/supervisor.py`",
                "`applications/adapters/appserver/client/supervisor.py`; product telemetry removed",
            ),
        ]
        _assert_complete_inventory(
            self,
            _table_rows(reuse, "### Exact Channel source map"),
            channel_rows,
            "REUSE Channel map",
        )
        _assert_complete_inventory(
            self,
            _table_rows(reuse, "### Exact App Server source map"),
            appserver_rows,
            "REUSE App Server map",
        )
        provenance_blocks = (
            "repository: https://github.com/albert-zen/imcodex transferred commit: 858398226e8f76e49f8259ae686939f209e1bb36",
            "The transferred source repository and commit contained no `LICENSE` file or declared license. That historical absence is preserved as provenance. The SDK code maintained here, including these transferred portions, is now provided under the repository's [MIT License](../LICENSE). Local changes are limited to the package namespace, removal of consumer observability/configuration/store/backend dependencies, neutral caller-provided or `.imagent` state paths, standard logging and explicit adapter errors, and translation only at Channel or Application boundaries. Product commands and Agent state were not copied.",
        )
        for block in provenance_blocks:
            self.assertIn(block, normalized_reuse)
            self.assertNotEqual(normalized_reuse.replace(block, ""), normalized_reuse)
        reuse_inventories = {
            "source_identity": (
                "repository: https://github.com/albert-zen/imcodex",
                "transferred commit: 858398226e8f76e49f8259ae686939f209e1bb36",
            ),
            "source_families": (
                "src/imcodex/channels/base.py",
                "src/imcodex/channels/access.py",
                "src/imcodex/channels/media.py",
                "src/imcodex/channels/text.py",
                "src/imcodex/channels/qq.py",
                "src/imcodex/channels/qq_media.py",
                "src/imcodex/channels/telegram.py",
                "src/imcodex/channels/feishu.py",
                "src/imcodex/channels/weixin*.py",
                "src/imcodex/models.py",
                "src/imcodex/file_types.py",
                "src/imcodex/windows_security.py",
            ),
            "retained_behaviors": (
                "stable account/conversation/sender identity",
                "access and duplicate checks before attachment work",
                "Markdown conversion and plain-text fallback",
                "ordered segmentation and one sender per destination",
                (
                    "native delivery IDs when a platform response actually provides them, "
                    "stable SDK delivery IDs otherwise, and conservative retry"
                ),
                "attachment staging and platform size limits",
                "reconnect tokens that belong to the Channel adapter",
            ),
            "excluded_consumer_owners": (
                (
                    "Product middleware, registry, commands, login UX, configured allowlist "
                    "values and UX, bot policy, and deployment configuration were deliberately "
                    "excluded."
                ),
                (
                    "`channels/api.py`, `channels/middleware.py`, `channels/outbound.py`, "
                    "`channels/registry.py`, `channels/weixin_login.py`, and the product "
                    "`channels/__init__.py` were deliberately excluded"
                ),
                (
                    "`appserver/backend*.py`, `settings_backend.py`, `thread_backend.py`, "
                    "`thread_dynamic_tools.py`, and `schema_drift.py` were excluded"
                ),
                "Product commands and Agent state were not copied.",
                (
                    "Product CLI, HTTP API, registry, webhook composition, and IMCodex "
                    "configuration tests were excluded."
                ),
            ),
            "source_tests": (
                "`test_appserver_stdio.py`",
                "`test_appserver_target.py`",
                "`test_channel_foundations.py`",
                "`test_channel_files.py`",
                "`test_channels.py`",
                "`test_channel_telegram.py`",
                "`test_channel_feishu.py`",
                "`test_channel_weixin.py`",
                "`test_channel_weixin_ilink.py`",
                "`test_qq_media.py`",
            ),
        }
        for category, expected_items in reuse_inventories.items():
            self.assertEqual(len(expected_items), len(set(expected_items)))
            for item in expected_items:
                with self.subTest(category=category, item=item):
                    self.assertIn(item, normalized_reuse)

        release_design = (ROOT / "docs" / "engineering" / "release" / "design.md").read_text(
            encoding="utf-8"
        )
        release_testing = (ROOT / "docs" / "engineering" / "release" / "testing.md").read_text(
            encoding="utf-8"
        )
        release_inventories = {
            "design": (
                "`appserver` adds WebSocket support for remote Codex App Server endpoints",
                (
                    "`qq`, `telegram`, `feishu`, and `weixin` each add only that protocol's "
                    "optional native dependencies"
                ),
                "`channels` is the union for deployments using all four Channel protocols",
                "Base Contracts, Ports, and Gateway imports require none of these extras.",
                (
                    "allow its adapter to import and construct in a clean environment with "
                    "no `imcodex` package"
                ),
                (
                    "Installation and import do not connect to a network or perform real "
                    "credential validation"
                ),
            ),
            "testing": (
                (
                    "exactly one built wheel into six isolated environments: base, QQ, "
                    "Telegram, Feishu, Weixin, and App Server"
                ),
                "`appserver` supplies remote App Server WebSocket support",
                "each named Channel extra supplies only its protocol's native dependencies",
                "`channels` remains their declared union",
                (
                    "Clean import and construction must succeed without `imcodex`, network "
                    "connection, or real credential validation"
                ),
                "any installation-time I/O is a release-boundary failure",
            ),
        }
        release_text = {
            "design": " ".join(release_design.split()),
            "testing": " ".join(release_testing.split()),
        }
        for owner, expected_rules in release_inventories.items():
            self.assertEqual(len(expected_rules), len(set(expected_rules)))
            for rule in expected_rules:
                with self.subTest(owner=owner, rule=rule):
                    self.assertIn(rule, release_text[owner])

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

    def test_root_test_modules_are_absent_after_three_layer_mirror(self) -> None:
        self.assertEqual(list((ROOT / "tests").glob("test_*.py")), [])
        self.assertTrue((ROOT / "tests/gateway/routing/__init__.py").is_file())

    def test_all_local_markdown_links_resolve(self) -> None:
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
import typing
import unittest
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.util import resolve_name
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import imagent.diagnostics as transition_facade
import imagent.gateway as gateway_facade
import imagent.gateway.diagnostics as gateway_diagnostics
from imagent.applications.adapters.t3 import T3ApplicationAdapter
from imagent.applications.capabilities import ProjectMode
from imagent.applications.contract import ThreadRef
from imagent.applications.diagnostics import ApplicationDiagnosticFacts
from imagent.gateway import GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.diagnostics import (
    DiagnosticsSnapshot,
    _DiagnosticApplication,
    collect_application_diagnostics,
    collect_channel_diagnostics,
    summarize_projection_health,
)
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.gateway.projection.observation import ProjectionWorkerHealth, ProjectionWorkerState
from imagent.interaction.channels.diagnostics import ChannelDiagnosticFacts
from imagent.interaction.diagnostics import (
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
)
from imagent.interaction.testing.fakes import FakeAgentApplicationAdapter


class _UnusedT3Client:
    async def shell_snapshot(self):
        return {}

    async def thread_detail(self, thread_id: str):
        del thread_id
        return {}

    async def dispatch(self, command):
        del command
        return {}


class DiagnosticsSurfaceTests(unittest.TestCase):
    def test_gateway_owner_and_transition_facade_export_exact_objects(self) -> None:
        self.assertEqual(len(gateway_diagnostics.__all__), len(set(gateway_diagnostics.__all__)))
        self.assertNotIn("__getattr__", transition_facade.__dict__)
        for name in gateway_diagnostics.__all__:
            self.assertIs(
                getattr(transition_facade, name),
                getattr(gateway_diagnostics, name),
                name,
            )
        gateway_facade_names = (
            "GatewayDiagnosticFacts",
            "DiagnosticsSnapshot",
            "summarize_projection_health",
            "collect_application_diagnostics",
            "collect_channel_diagnostics",
            "new_diagnostics_snapshot",
        )
        for name in gateway_facade_names:
            self.assertIs(getattr(gateway_facade, name), getattr(gateway_diagnostics, name))

        transition_tree = ast.parse(
            (Path(__file__).parents[2] / "src/imagent/diagnostics.py").read_text(encoding="utf-8")
        )
        definitions = tuple(
            node.name
            for node in transition_tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        )
        self.assertEqual(definitions, ())

    def test_gateway_owner_and_call_sites_have_no_top_facade_or_higher_import(self) -> None:
        root = Path(__file__).parents[2]
        owner_path = root / "src/imagent/gateway/diagnostics.py"
        tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))
        internal_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                internal_modules.update(
                    alias.name for alias in node.names if alias.name.startswith("imagent")
                )
            elif isinstance(node, ast.ImportFrom):
                module = (
                    resolve_name("." * node.level + (node.module or ""), "imagent.gateway")
                    if node.level
                    else (node.module or "")
                )
                if module.startswith("imagent"):
                    internal_modules.add(module)
        self.assertNotIn("imagent.diagnostics", internal_modules)
        self.assertNotIn("imagent.gateway", internal_modules)
        self.assertEqual(
            internal_modules,
            {
                "imagent.applications.diagnostics",
                "imagent.interaction.channels.diagnostics",
                "imagent.interaction.diagnostics",
            },
        )

        call_sites = {
            "src/imagent/gateway/__init__.py": "from .diagnostics import",
            "src/imagent/gateway/presentation.py": "from .diagnostics import",
            "src/imagent/gateway/input/content_transformation.py": "from ..diagnostics import",
            "src/imagent/gateway/input/failure_presentation.py": "from ..diagnostics import",
            "src/imagent/gateway/delivery/outcome_observation.py": "from ..diagnostics import",
        }
        for relative_path, expected_import in call_sites.items():
            source = (root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("imagent.diagnostics", source, relative_path)
            self.assertIn(expected_import, source, relative_path)

    def test_gateway_owner_import_order_and_runtime_hints_preserve_identity(self) -> None:
        check_suffix = """
for name in owner.__all__:
    assert getattr(facade, name) is getattr(owner, name)
for name in (
    'GatewayDiagnosticFacts', 'DiagnosticsSnapshot',
    'summarize_projection_health', 'collect_application_diagnostics',
    'collect_channel_diagnostics', 'new_diagnostics_snapshot',
):
    assert getattr(gateway_facade, name) is getattr(owner, name)
"""
        for imports in (
            "import imagent.gateway.diagnostics as owner\n"
            "import imagent.diagnostics as facade\n"
            "import imagent.gateway as gateway_facade\n",
            "import imagent.diagnostics as facade\n"
            "import imagent.gateway.diagnostics as owner\n"
            "import imagent.gateway as gateway_facade\n",
        ):
            subprocess.run([sys.executable, "-c", imports + check_suffix], check=True)

        snapshot_hints = typing.get_type_hints(gateway_diagnostics.DiagnosticsSnapshot)
        self.assertIs(
            typing.get_args(snapshot_hints["applications"])[0], ApplicationDiagnosticFacts
        )
        self.assertIs(typing.get_args(snapshot_hints["channels"])[0], ChannelDiagnosticFacts)
        self.assertIs(snapshot_hints["projections"], gateway_diagnostics.ProjectionDiagnosticFacts)
        self.assertIs(snapshot_hints["gateway"], gateway_diagnostics.GatewayDiagnosticFacts)

        snapshot_factory_hints = typing.get_type_hints(gateway_diagnostics.new_diagnostics_snapshot)
        self.assertIs(snapshot_factory_hints["return"], gateway_diagnostics.DiagnosticsSnapshot)
        self.assertIs(
            typing.get_args(snapshot_factory_hints["applications"])[0],
            ApplicationDiagnosticFacts,
        )
        self.assertIs(
            typing.get_args(snapshot_factory_hints["channels"])[0],
            ChannelDiagnosticFacts,
        )
        self.assertIs(
            snapshot_factory_hints["projections"], gateway_diagnostics.ProjectionDiagnosticFacts
        )
        self.assertIs(snapshot_factory_hints["gateway"], gateway_diagnostics.GatewayDiagnosticFacts)
        self.assertIs(
            typing.get_args(
                typing.get_type_hints(gateway_diagnostics.collect_application_diagnostics)["return"]
            )[0],
            ApplicationDiagnosticFacts,
        )
        self.assertIs(
            typing.get_args(
                typing.get_type_hints(gateway_diagnostics.collect_channel_diagnostics)["return"]
            )[0],
            ChannelDiagnosticFacts,
        )
        self.assertIs(
            typing.get_type_hints(ImAgentGateway.diagnostics_snapshot)["return"],
            gateway_diagnostics.DiagnosticsSnapshot,
        )
        self.assertEqual(
            inspect.signature(transition_facade.new_diagnostics_snapshot),
            inspect.signature(gateway_diagnostics.new_diagnostics_snapshot),
        )

    def test_queue_fact_vocabulary_and_bounds_are_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "fixed vocabulary"):
            QueueDiagnosticFacts(
                cast(QueueDiagnosticName, "consumer-controlled-name"),
                capacity=1,
                depth=0,
            )
        with self.assertRaisesRegex(ValueError, "within capacity"):
            QueueDiagnosticFacts(
                QueueDiagnosticName.NOTIFICATION,
                capacity=1,
                depth=2,
            )
        queue = QueueDiagnosticFacts(
            QueueDiagnosticName.NOTIFICATION,
            capacity=1,
            depth=0,
        )
        with self.assertRaisesRegex(ValueError, "must be unique"):
            ConnectionDiagnosticFacts(
                state=ConnectionDiagnosticState.READY,
                connection_epoch=1,
                reconnect_count=0,
                worker_running=True,
                worker_degraded=False,
                queues=(queue, queue),
            )

    def test_projection_summary_is_identity_free_and_bounds_gap_codes(self) -> None:
        secret_thread = "native-thread-secret"
        health = (
            ProjectionWorkerHealth(
                thread_ref=ThreadRef("codex-a", secret_thread),
                state=ProjectionWorkerState.RETRYING,
                restart_count=2,
                delivery_failure_count=1,
                event_overflow_count=3,
                last_subscription_error="secret transport failure",
                last_event_gap="application_event_fanout_overflow",
                interactive_request_recovery_degraded=True,
                updated_at=datetime.now(UTC),
            ),
            ProjectionWorkerHealth(
                thread_ref=ThreadRef("codex-a", "another-native-thread"),
                state=ProjectionWorkerState.RUNNING,
                last_gap="secret-route:checkpoint_out_of_window",
                last_event_gap="consumer-controlled-unbounded-value",
            ),
        )

        facts = summarize_projection_health(health)

        self.assertEqual(facts.worker_count, 2)
        self.assertEqual(facts.running_count, 1)
        self.assertEqual(facts.retrying_count, 1)
        self.assertEqual(facts.degraded_count, 2)
        self.assertEqual(facts.restart_count, 2)
        self.assertEqual(facts.event_overflow_count, 3)
        self.assertEqual(facts.request_recovery_degraded_count, 1)
        self.assertEqual(facts.recovery_gap_count, 2)
        self.assertEqual(
            facts.recovery_gap_codes,
            (
                "application_event_fanout_overflow",
                "checkpoint_out_of_window",
                "other",
            ),
        )
        serialized = json.dumps(asdict(facts), default=str)
        self.assertNotIn(secret_thread, serialized)
        self.assertNotIn("secret transport failure", serialized)
        self.assertNotIn("consumer-controlled-unbounded-value", serialized)
        self.assertNotIn("secret-route", serialized)

    def test_gateway_snapshot_is_stable_read_only_and_non_authoritative(self) -> None:
        application = FakeAgentApplicationAdapter(
            application_instance_id="fake-agent",
            project_mode=ProjectMode.FLAT,
        )
        gateway = ImAgentGateway(
            channels=[],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            limits=GatewayLimits(
                startup_buffer_max_pending=7,
            ),
        )

        first = gateway.diagnostics_snapshot()
        second = gateway.diagnostics_snapshot()

        self.assertIsInstance(first, DiagnosticsSnapshot)
        self.assertFalse(first.authoritative)
        self.assertEqual(first.schema_version, 8)
        self.assertIsNone(first.gateway.inbound_content_transformer)
        self.assertIsNone(first.gateway.inbound_failure_presenter)
        self.assertIsNone(first.gateway.outbound_presentation)
        self.assertIsNone(first.gateway.delivery_outcome_observer)
        self.assertEqual(first.applications, (ApplicationDiagnosticFacts("fake-agent", "fake"),))
        self.assertEqual(first.channels, ())
        self.assertEqual(
            first.gateway.startup_queue,
            QueueDiagnosticFacts(QueueDiagnosticName.GATEWAY_STARTUP, capacity=7, depth=0),
        )
        self.assertEqual(first.applications, second.applications)
        self.assertEqual(first.projections, second.projections)
        self.assertEqual(first.gateway, second.gateway)
        self.assertLessEqual(first.generated_at, second.generated_at)

    def test_channel_diagnostics_preserve_registry_identity_and_fail_closed(self) -> None:
        connection = ConnectionDiagnosticFacts(
            state=ConnectionDiagnosticState.READY,
            connection_epoch=1,
            reconnect_count=0,
            worker_running=True,
            worker_degraded=False,
            queues=(
                QueueDiagnosticFacts(
                    QueueDiagnosticName.CHANNEL_INBOUND,
                    capacity=8,
                    depth=2,
                    overflow_count=3,
                ),
            ),
        )

        class Channel:
            channel_instance_id = "qq-main"
            kind = "qq"

            def __init__(self, facts: object = None, *, raises: bool = False) -> None:
                self._facts = facts
                self._raises = raises

            def diagnostic_facts(self) -> object:
                if self._raises:
                    raise RuntimeError("secret provider failure")
                return self._facts

        valid = Channel(ChannelDiagnosticFacts("qq-main", "qq", connection))
        mismatch = Channel(ChannelDiagnosticFacts("secret-instance", "qq", connection))
        invalid = Channel(
            SimpleNamespace(
                channel_instance_id="qq-main",
                kind="qq",
                connection=SimpleNamespace(state="consumer-value"),
            )
        )
        invalid_queue_scope = Channel(
            SimpleNamespace(
                channel_instance_id="qq-main",
                kind="qq",
                connection=ConnectionDiagnosticFacts(
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
        )

        class RaisingIdentity:
            @property
            def channel_instance_id(self) -> str:
                raise RuntimeError("secret identity failure")

        raising_identity = Channel(RaisingIdentity())

        class RaisingProviderAttribute:
            channel_instance_id = "qq-main"
            kind = "qq"

            @property
            def diagnostic_facts(self) -> object:
                raise RuntimeError("secret provider attribute failure")

        raising_provider_attribute = RaisingProviderAttribute()

        class RaisingConnectionAttribute:
            channel_instance_id = "qq-main"
            kind = "qq"

            @property
            def connection(self) -> object:
                raise RuntimeError("secret connection failure")

        raising_connection_attribute = Channel(RaisingConnectionAttribute())
        raising = Channel(raises=True)
        absent = SimpleNamespace(channel_instance_id="qq-main", kind="qq")

        self.assertEqual(collect_channel_diagnostics((valid,))[0].connection, connection)
        for channel in (
            mismatch,
            invalid,
            invalid_queue_scope,
            raising_identity,
            raising_provider_attribute,
            raising_connection_attribute,
            raising,
            absent,
        ):
            with self.subTest(channel=channel):
                facts = collect_channel_diagnostics((channel,))[0]
                self.assertEqual(facts.channel_instance_id, "qq-main")
                self.assertEqual(facts.kind, "qq")
                self.assertIsNone(facts.connection)
                serialized = json.dumps(asdict(facts), default=str)
                self.assertNotIn("secret-instance", serialized)
                self.assertNotIn("secret provider failure", serialized)
                self.assertNotIn("consumer-value", serialized)

    def test_application_diagnostics_fail_closed_for_every_optional_provider_failure(self) -> None:
        def make_summary(identifier: str, kind: str) -> SimpleNamespace:
            return SimpleNamespace(
                ref=SimpleNamespace(application_instance_id=identifier),
                kind=kind,
            )

        valid_facts = ApplicationDiagnosticFacts("z-valid", "codex")
        valid = SimpleNamespace(
            summary=make_summary("z-valid", "codex"),
            diagnostic_facts=lambda: valid_facts,
        )
        missing = SimpleNamespace(summary=make_summary("a-missing", "t3"))
        invalid = SimpleNamespace(
            summary=make_summary("b-invalid", "zen"),
            diagnostic_facts=lambda: SimpleNamespace(
                application_instance_id="native-secret",
                kind="untrusted-kind",
            ),
        )
        mismatched = SimpleNamespace(
            summary=make_summary("c-mismatch", "codex"),
            diagnostic_facts=lambda: ApplicationDiagnosticFacts("native-secret", "codex"),
        )

        class RaisingDescriptor:
            summary = make_summary("d-descriptor", "zen")

            @property
            def diagnostic_facts(self) -> object:
                raise RuntimeError("native provider secret")

        class RaisingProvider:
            summary = make_summary("e-provider", "t3")

            def diagnostic_facts(self) -> object:
                raise RuntimeError("native provider secret")

        applications = (valid, RaisingProvider(), mismatched, missing, RaisingDescriptor(), invalid)
        collected = collect_application_diagnostics(
            cast(tuple[_DiagnosticApplication, ...], applications)
        )

        self.assertEqual(
            tuple(facts.application_instance_id for facts in collected),
            ("a-missing", "b-invalid", "c-mismatch", "d-descriptor", "e-provider", "z-valid"),
        )
        self.assertEqual(len(collected), len(applications))
        self.assertIs(collected[-1], valid_facts)
        for facts in collected[:-1]:
            self.assertIsNone(facts.connection)
            self.assertIsNone(facts.presentation)
            self.assertIsNone(facts.artifact_materialization)
            serialized = json.dumps(asdict(facts), default=str)
            self.assertNotIn("native-secret", serialized)
            self.assertNotIn("native provider secret", serialized)

    def test_application_and_channel_queue_scopes_remain_distinct(self) -> None:
        channel_queue = QueueDiagnosticFacts(
            QueueDiagnosticName.CHANNEL_INBOUND,
            capacity=1,
            depth=0,
        )
        application_queue = QueueDiagnosticFacts(
            QueueDiagnosticName.NOTIFICATION,
            capacity=1,
            depth=0,
        )
        connection = {
            "state": ConnectionDiagnosticState.READY,
            "connection_epoch": 1,
            "reconnect_count": 0,
            "worker_running": True,
            "worker_degraded": False,
        }

        with self.assertRaisesRegex(ValueError, "not Application-scoped"):
            ApplicationDiagnosticFacts(
                "app",
                "appserver",
                ConnectionDiagnosticFacts(**connection, queues=(channel_queue,)),
            )
        with self.assertRaisesRegex(ValueError, "not Channel-scoped"):
            ChannelDiagnosticFacts(
                "channel",
                "qq",
                ConnectionDiagnosticFacts(**connection, queues=(application_queue,)),
            )

    def test_channel_diagnostics_are_sorted_and_reads_do_not_mutate_provider(self) -> None:
        reads = 0

        class Channel:
            kind = "telegram"

            def __init__(self, identity: str) -> None:
                self.channel_instance_id = identity

            def diagnostic_facts(self) -> ChannelDiagnosticFacts:
                nonlocal reads
                reads += 1
                return ChannelDiagnosticFacts(self.channel_instance_id, self.kind)

        channels = (Channel("z"), Channel("a"))
        first = collect_channel_diagnostics(channels)
        second = collect_channel_diagnostics(channels)

        self.assertEqual(tuple(item.channel_instance_id for item in first), ("a", "z"))
        self.assertEqual(first, second)
        self.assertEqual(reads, 4)

    def test_t3_reports_no_synthetic_long_lived_connection(self) -> None:
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-a",
            client=_UnusedT3Client(),
        )

        facts = adapter.diagnostic_facts()

        self.assertEqual(facts.application_instance_id, "t3-a")
        self.assertEqual(facts.kind, "t3")
        self.assertIsNone(facts.connection)


if __name__ == "__main__":
    unittest.main()

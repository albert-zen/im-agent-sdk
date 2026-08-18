from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime

import imagent.interaction.testing as testing_owner
import imagent.testing as testing_facade
from imagent.applications.adapters.codex import CodexApplicationAdapter
from imagent.applications.adapters.t3 import T3ApplicationAdapter
from imagent.applications.adapters.zen import ZenApplicationAdapter
from imagent.applications.capabilities import ProjectMode
from imagent.applications.operations import CreateProject, ProjectCreated
from imagent.channels import channel_from_config
from imagent.interaction.channels import ChannelStartupConfigurationValidator
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    TextContent,
)
from imagent.testing import (
    FakeAgentApplicationAdapter,
    FakeChannelAdapter,
    verify_application_adapter,
    verify_channel_adapter,
)
from tests.gateway.test_vertical_slice import NativeT3Client, NativeZenClient


class TestingPackageBoundaryTests(unittest.TestCase):
    def test_owner_and_compatibility_facade_export_exact_objects(self) -> None:
        expected = {name: getattr(testing_owner, name) for name in testing_owner.__all__}
        self.assertEqual(tuple(testing_facade.__all__), tuple(testing_owner.__all__))
        for name, owner_object in expected.items():
            with self.subTest(name=name):
                self.assertIs(getattr(testing_facade, name), owner_object)

    def test_import_orders_do_not_recreate_historical_modules(self) -> None:
        import_orders = (
            "from imagent.interaction.testing import ContractCheck; "
            "from imagent.testing import ContractCheck as FacadeContractCheck; "
            "assert ContractCheck is FacadeContractCheck;",
            "from imagent.testing import ContractCheck; "
            "from imagent.interaction.testing import ContractCheck as OwnerContractCheck; "
            "assert ContractCheck is OwnerContractCheck;",
        )
        for import_order in import_orders:
            with self.subTest(import_order=import_order):
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import sys; "
                            f"{import_order} "
                            "assert 'imagent.testing.contracts' not in sys.modules; "
                            "assert 'imagent.testing.fakes' not in sys.modules; "
                            "from importlib.util import find_spec; "
                            "assert find_spec('imagent.testing.contracts') is None; "
                            "assert find_spec('imagent.testing.fakes') is None"
                        ),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


class ChannelAdapterContractKitTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_channel_passes_contract(self) -> None:
        adapter = FakeChannelAdapter()
        report = await verify_channel_adapter(
            adapter,
            InboundMessage(
                message_id="message-1",
                conversation_ref=ConversationRef("fake-channel", "direct-1"),
                sender="user-1",
                content=(TextContent("hello"),),
                created_at=datetime.now(UTC),
            ),
        )
        self.assertIn("stable channel identity", report.check_names)
        self.assertIn("admission callback accepted at startup", report.check_names)
        self.assertIn("delivery receipt semantics", report.check_names)
        self.assertNotIsInstance(
            adapter,
            ChannelStartupConfigurationValidator,
        )

    async def test_all_shipped_channels_pass_lifecycle_and_capability_contract(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            adapters = (
                channel_from_config("qq", config={"enabled": False}),
                channel_from_config("telegram", config={"enabled": False}),
                channel_from_config("feishu", config={"enabled": False}),
                channel_from_config(
                    "weixin",
                    config={"enabled": False, "state_dir": state_dir},
                ),
            )
            for adapter in adapters:
                with self.subTest(kind=adapter.kind):
                    self.assertIsInstance(adapter, ChannelStartupConfigurationValidator)
                    adapter.validate_startup_configuration()
                    report = await verify_channel_adapter(adapter)
                    self.assertIn("valid channel capabilities", report.check_names)
                    self.assertIn("start and stop lifecycle", report.check_names)
                    facts = adapter.diagnostic_facts()
                    self.assertEqual(facts.channel_instance_id, adapter.channel_instance_id)
                    self.assertEqual(facts.kind, adapter.kind)


class ShippedChannelBehavioralLedgerTests(unittest.TestCase):
    def test_shipped_channel_owner_suites_are_executable_conformance_evidence(self) -> None:
        suites = (
            "tests.interaction.channels.adapters.test_native_channels",
            "tests.interaction.channels.adapters.test_qq",
            "tests.interaction.channels.adapters.test_telegram",
            "tests.interaction.channels.adapters.test_feishu",
            "tests.interaction.channels.adapters.test_weixin",
        )
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", *suites],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"shipped Channel behavioral ledger failed:\n{completed.stdout}\n{completed.stderr}",
        )
        self.assertIn("Ran 51 tests", completed.stderr)


class AgentApplicationContractKitTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_application_passes_all_project_modes(self) -> None:
        for mode in ProjectMode:
            with self.subTest(mode=mode):
                report = await verify_application_adapter(
                    FakeAgentApplicationAdapter(project_mode=mode)
                )
                self.assertIn(
                    "thread create, read, and list round-trip",
                    report.check_names,
                )
                self.assertIn(
                    "stable client message ID round-trip",
                    report.check_names,
                )

    async def test_fake_managed_project_creation_replays_by_stable_operation_id(self) -> None:
        adapter = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
        operation = CreateProject(
            operation_id="stable-project-operation",
            application_ref=adapter.summary.ref,
            cwd="/requested/repository",
            display_name="Repository",
            created_at=datetime.now(UTC),
        )
        first = await adapter.execute(operation)
        second = await adapter.execute(operation)

        self.assertIsInstance(first, ProjectCreated)
        self.assertEqual(first, second)

    def test_workspace_project_identity_is_independent_of_root_and_display_text(self) -> None:
        canonical = FakeAgentApplicationAdapter(
            project_mode=ProjectMode.FIXED,
            workspace_id="workspace-1",
            workspace_root="/repo/root",
        )
        equivalent = FakeAgentApplicationAdapter(
            project_mode=ProjectMode.FIXED,
            workspace_id="workspace-1",
            workspace_root="/repo/child/../root",
        )
        moved = FakeAgentApplicationAdapter(
            project_mode=ProjectMode.FIXED,
            workspace_id="workspace-1",
            workspace_root="/repo/moved",
        )
        replacement = FakeAgentApplicationAdapter(
            project_mode=ProjectMode.FIXED,
            workspace_id="workspace-2",
            workspace_root="/repo/root",
        )

        assert canonical.summary.workspace_identity is not None
        assert equivalent.summary.workspace_identity is not None
        assert moved.summary.workspace_identity is not None
        assert replacement.summary.workspace_identity is not None
        self.assertEqual(
            canonical.summary.workspace_identity,
            equivalent.summary.workspace_identity,
        )
        self.assertEqual(
            canonical.summary.workspace_identity.project_ref,
            moved.summary.workspace_identity.project_ref,
        )
        self.assertNotEqual(
            canonical.summary.workspace_identity.root_fingerprint,
            moved.summary.workspace_identity.root_fingerprint,
        )
        self.assertNotEqual(
            canonical.summary.workspace_identity.project_ref,
            replacement.summary.workspace_identity.project_ref,
        )

    async def test_codex_and_zen_adapters_pass_contract_kit(self) -> None:
        for adapter in (
            CodexApplicationAdapter(
                application_instance_id="codex-contract",
                client=NativeZenClient(),
                workspace_id="workspace",
                cwd="/repo",
            ),
            ZenApplicationAdapter(
                application_instance_id="zen-contract",
                client=NativeZenClient(),
                workspace_id="workspace",
                cwd="/repo",
            ),
        ):
            with self.subTest(kind=adapter.summary.kind):
                report = await verify_application_adapter(adapter)
                self.assertIn("explicit native thread activation", report.check_names)
                self.assertIn("catch-up and history result scoping", report.check_names)

    async def test_t3_adapter_passes_contract_kit(self) -> None:
        report = await verify_application_adapter(
            T3ApplicationAdapter(
                application_instance_id="t3-contract",
                client=NativeT3Client(),
            )
        )
        self.assertIn("project list and read", report.check_names)
        self.assertIn("declared thread deletion", report.check_names)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import sys
import unittest
from datetime import UTC, datetime

import imagent.interaction.testing as testing_owner
import imagent.testing as testing_facade
from imagent.applications import (
    CodexApplicationAdapter,
    T3ApplicationAdapter,
    ZenApplicationAdapter,
)
from imagent.applications.capabilities import ProjectMode
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    TextContent,
)
from imagent.interaction.channels import ChannelStartupConfigurationValidator
from imagent.testing import (
    FakeAgentApplicationAdapter,
    FakeChannelAdapter,
    verify_application_adapter,
    verify_channel_adapter,
)
from tests.test_gateway_vertical_slice import NativeT3Client, NativeZenClient


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

    async def test_codex_and_zen_adapters_pass_contract_kit(self) -> None:
        for adapter in (
            CodexApplicationAdapter(
                application_instance_id="codex-contract",
                client=NativeZenClient(),
                cwd="/repo",
            ),
            ZenApplicationAdapter(
                application_instance_id="zen-contract",
                client=NativeZenClient(),
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
        self.assertIn("managed project list and read", report.check_names)
        self.assertIn("declared thread deletion", report.check_names)


if __name__ == "__main__":
    unittest.main()

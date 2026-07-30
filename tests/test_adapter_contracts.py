from __future__ import annotations

import unittest
from datetime import UTC, datetime

from test_gateway_vertical_slice import NativeT3Client, NativeZenClient

from imagent.applications import (
    CodexApplicationAdapter,
    T3ApplicationAdapter,
    ZenApplicationAdapter,
)
from imagent.contracts import (
    ChannelMessage,
    ConversationRef,
    ProjectMode,
    TextContent,
)
from imagent.testing import (
    FakeAgentApplicationAdapter,
    FakeChannelAdapter,
    verify_application_adapter,
    verify_channel_adapter,
)


class ChannelAdapterContractKitTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_channel_passes_contract(self) -> None:
        adapter = FakeChannelAdapter()
        report = await verify_channel_adapter(
            adapter,
            ChannelMessage(
                message_id="message-1",
                conversation_ref=ConversationRef("fake-channel", "direct-1"),
                sender="user-1",
                content=(TextContent("hello"),),
                created_at=datetime.now(UTC),
            ),
        )
        self.assertIn("stable channel identity", report.check_names)
        self.assertIn("delivery receipt semantics", report.check_names)


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

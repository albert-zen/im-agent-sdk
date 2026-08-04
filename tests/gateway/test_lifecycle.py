from __future__ import annotations

import asyncio
import importlib
import unittest
from datetime import UTC, datetime

from imagent.applications.capabilities import ProjectMode
from imagent.contracts import ConversationRef, InboundMessage, TextContent
from imagent.gateway import GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.lifecycle import (
    GatewayNotRunning,
    GatewayStartupAdmission,
    GatewayStartupOverflow,
)
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class GatewayLifecycleHelperTests(unittest.TestCase):
    def test_helpers_have_one_lifecycle_owner_and_old_module_is_absent(self) -> None:
        self.assertEqual(GatewayStartupAdmission.__module__, "imagent.gateway.lifecycle")
        self.assertEqual(GatewayStartupOverflow.__module__, "imagent.gateway.lifecycle")
        self.assertEqual(GatewayNotRunning.__module__, "imagent.gateway.lifecycle")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("imagent.gateway_startup")

    def test_admission_preserves_fifo_and_sticky_overflow(self) -> None:
        admission = GatewayStartupAdmission[str](max_pending=2)
        admission.admit("first")
        admission.admit("second")

        with self.assertRaises(GatewayStartupOverflow) as overflow:
            admission.admit("third")
        with self.assertRaises(GatewayStartupOverflow) as repeated:
            admission.admit("later")

        self.assertIs(repeated.exception, overflow.exception)
        self.assertEqual(admission.popleft(), "first")
        self.assertEqual(admission.popleft(), "second")
        self.assertEqual(admission.overflow_count, 1)


class GatewayStartupAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_claimed_messages_drain_in_fifo_order(self) -> None:
        conversation = ConversationRef("startup-channel", "conversation")
        channel = _StartupEntriesChannel(
            (
                _inbound(conversation, "first"),
                _inbound(conversation, "second"),
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            limits=GatewayLimits(
                startup_buffer_max_pending=3,
            ),
        )
        drained: list[str] = []

        async def record_message(
            message: InboundMessage,
            *,
            idempotency_owner_token: str,
            before_application_send,
        ) -> None:
            del idempotency_owner_token, before_application_send
            drained.append(f"message:{message.message_id}")

        gateway._process_message = record_message
        await gateway.start()
        try:
            self.assertEqual(
                drained,
                ["message:first", "message:second"],
            )
        finally:
            await gateway.stop()

    async def test_startup_overflow_fails_and_stops_started_channel(self) -> None:
        conversation = ConversationRef("startup-channel", "conversation")
        channel = _StartupEntriesChannel(
            tuple(_inbound(conversation, f"message-{index}") for index in range(3))
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
            limits=GatewayLimits(
                startup_buffer_max_pending=2,
            ),
        )

        with self.assertRaises(GatewayStartupOverflow) as raised:
            await gateway.start()
        self.assertEqual(raised.exception.max_pending, 2)
        self.assertFalse(channel.started)
        startup = gateway.diagnostics_snapshot().gateway.startup_queue
        self.assertEqual(startup.capacity, 2)
        self.assertEqual(startup.depth, 0)
        self.assertEqual(startup.overflow_count, 1)

    async def test_startup_failure_rejects_callbacks_during_teardown(self) -> None:
        active_channel = _BlockingStopStartupChannel()
        failing_channel = _FailingStartupChannel()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        gateway = ImAgentGateway(
            channels=[active_channel, failing_channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )
        starting = asyncio.create_task(gateway.start())
        await active_channel.stopping.wait()
        try:
            with self.assertRaises(GatewayNotRunning):
                await active_channel.on_message(
                    _inbound(
                        ConversationRef("active-startup-channel", "conversation"),
                        "during-failed-start",
                    )
                )
            self.assertEqual(application._inputs, [])
        finally:
            active_channel.release_stop.set()
        with self.assertRaisesRegex(RuntimeError, "simulated channel startup failure"):
            await starting


class _StartupEntriesChannel(FakeChannelAdapter):
    def __init__(self, entries) -> None:
        super().__init__("startup-channel")
        self._entries = entries

    async def start(self, on_message, on_admission=None) -> None:
        await super().start(on_message, on_admission)
        for entry in self._entries:
            await on_message(entry)


class _BlockingStopStartupChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__("active-startup-channel")
        self.stopping = asyncio.Event()
        self.release_stop = asyncio.Event()

    async def stop(self) -> None:
        self.stopping.set()
        await self.release_stop.wait()
        await super().stop()


class _FailingStartupChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__("failing-startup-channel")

    async def start(self, on_message, on_admission=None) -> None:
        await super().start(on_message, on_admission)
        raise RuntimeError("simulated channel startup failure")


def _inbound(conversation: ConversationRef, message_id: str) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation,
        sender="user-1",
        content=(TextContent("run"),),
        created_at=datetime.now(UTC),
    )


if __name__ == "__main__":
    unittest.main()

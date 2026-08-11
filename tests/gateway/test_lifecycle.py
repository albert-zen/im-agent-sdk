from __future__ import annotations

import asyncio
import importlib
import unittest
from datetime import UTC, datetime

from imagent import Gateway, MemoryGatewayStore
from imagent.applications.capabilities import ProjectMode
from imagent.contracts import ConversationRef, InboundMessage, TextContent
from imagent.gateway import GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.delivery.coordination import DeliveryCoordinator
from imagent.gateway.lifecycle import (
    GatewayLifecycleFailure,
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

    def test_lifecycle_owner_timeout_is_positive_and_finite(self) -> None:
        self.assertEqual(GatewayLimits().lifecycle_owner_timeout_seconds, 30.0)
        for value in (0, -1, float("inf"), float("nan"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                GatewayLimits(lifecycle_owner_timeout_seconds=value)


class PublicGatewayLifecycleMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_uses_same_lifecycle_and_explicit_stop_wakes_waiter(self) -> None:
        gateway, channel, _ = _public_gateway("run-stop")
        running = asyncio.create_task(gateway.run())
        while not gateway.running:
            await asyncio.sleep(0)

        await gateway.stop()
        await asyncio.wait_for(running, timeout=1.0)
        await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)

    async def test_run_caller_cancellation_joins_cleanup_before_propagating(self) -> None:
        gateway, channel, _ = _public_gateway("run-cancel")
        running = asyncio.create_task(gateway.run())
        while not gateway.running:
            await asyncio.sleep(0)

        running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await running

        self.assertFalse(gateway.running)
        self.assertFalse(channel.started)
        await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)

    async def test_run_cancellation_stays_primary_when_cleanup_fails(self) -> None:
        gateway, _, _ = _public_gateway(
            "run-cancel-primary",
            channel=_FailingStopChannel("run-cancel-primary-channel"),
        )
        running = asyncio.create_task(gateway.run())
        while not gateway.running:
            await asyncio.sleep(0)

        running.cancel()
        with self.assertRaises(asyncio.CancelledError) as raised:
            await running

        notes = tuple(getattr(raised.exception, "__notes__", ()))
        self.assertTrue(any("run cleanup also failed" in note for note in notes))
        with self.assertRaises(GatewayLifecycleFailure):
            await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)

    async def test_context_body_failure_stays_primary_when_cleanup_fails(self) -> None:
        class BodyFailure(RuntimeError):
            pass

        gateway, _, _ = _public_gateway(
            "body-primary",
            channel=_FailingStopChannel("body-primary-channel"),
        )

        with self.assertRaises(BodyFailure) as raised:
            async with gateway:
                raise BodyFailure("body is primary")

        notes = tuple(getattr(raised.exception, "__notes__", ()))
        self.assertTrue(any("context cleanup also failed" in note for note in notes))
        self.assertIsNone(raised.exception.__context__)
        with self.assertRaises(GatewayLifecycleFailure):
            await asyncio.wait_for(gateway.wait_closed(), timeout=1.0)

    async def test_concurrent_and_repeated_transitions_are_terminal(self) -> None:
        gateway, channel, _ = _public_gateway("transition-matrix")
        await asyncio.gather(gateway.start(), gateway.start())
        await gateway.start()
        self.assertTrue(gateway.running)

        await asyncio.gather(gateway.stop(), gateway.stop())
        await gateway.stop()
        self.assertFalse(channel.started)
        with self.assertRaisesRegex(RuntimeError, "cannot restart"):
            await gateway.start()

    async def test_owner_timeout_continues_cleanup_through_later_owners(self) -> None:
        channel = _BlockingStopChannel()
        application = _CountingStopApplication()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            limits=GatewayLimits(lifecycle_owner_timeout_seconds=0.01),
        )
        await gateway.start()

        with self.assertRaisesRegex(RuntimeError, "lifecycle failure"):
            await asyncio.wait_for(gateway.stop(), timeout=1.0)

        self.assertTrue(channel.stop_cancelled)
        self.assertEqual(application.stop_count, 1)

    async def test_cancellation_resistant_owner_cannot_extend_cleanup_deadline(self) -> None:
        channel = _CancellationResistantStopChannel()
        application = _CountingStopApplication()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            limits=GatewayLimits(lifecycle_owner_timeout_seconds=0.01),
        )
        await gateway.start()

        with self.assertRaises(GatewayLifecycleFailure):
            await asyncio.wait_for(gateway.stop(), timeout=0.1)

        self.assertTrue(channel.stop_cancelled.is_set())
        self.assertEqual(application.stop_count, 1)
        channel.release_stop.set()
        await asyncio.sleep(0)

    async def test_repeated_cancellation_cannot_abort_later_owner_cleanup(self) -> None:
        channel = _CancellationResistantStopChannel()
        application = _CountingStopApplication()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            limits=GatewayLimits(lifecycle_owner_timeout_seconds=0.02),
        )
        await gateway.start()

        stopping = asyncio.create_task(gateway.stop())
        await channel.stop_started.wait()
        stopping.cancel()
        await channel.stop_cancelled.wait()
        stopping.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(stopping, timeout=0.2)

        self.assertEqual(application.stop_count, 1)
        channel.release_stop.set()
        await asyncio.wait_for(channel.stop_finished.wait(), timeout=0.2)

    async def test_scoped_action_fence_failure_is_projected_and_cleanup_continues(self) -> None:
        gateway, channel, _ = _public_gateway("fence-failure")
        await gateway.start()
        runtime = gateway._runtime  # type: ignore[reportPrivateUsage]
        self.assertIsNotNone(runtime)

        def fail_deactivation() -> None:
            raise RuntimeError("DEACTIVATE_SECRET")

        runtime._deactivate_scoped_actions = fail_deactivation  # type: ignore[union-attr,method-assign,reportPrivateUsage]

        with self.assertRaises(GatewayLifecycleFailure) as raised:
            await gateway.stop()

        evidence = "\n".join(
            (
                str(raised.exception),
                repr(raised.exception),
                repr(raised.exception.__cause__),
                repr(raised.exception.__context__),
                *getattr(raised.exception, "__notes__", ()),
            )
        )
        self.assertNotIn("DEACTIVATE_SECRET", evidence)
        self.assertFalse(channel.started)
        self.assertIsNone(gateway._runtime)  # type: ignore[reportPrivateUsage]
        self.assertIsNone(gateway._session)  # type: ignore[reportPrivateUsage]
        self.assertTrue(gateway._store._closed)  # type: ignore[reportPrivateUsage]
        await gateway.stop()

    async def test_partial_delivery_owner_start_is_inside_rollback_boundary(self) -> None:
        coordinator = _PartiallyStartingCoordinator()
        gateway = ImAgentGateway(
            channels=[FakeChannelAdapter("partial-coordinator-channel")],
            applications=[FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            delivery_coordinator=coordinator,
        )

        with self.assertRaises(GatewayLifecycleFailure) as raised:
            await gateway.start()

        self.assertNotIn("RAW_SECRET_COORDINATOR_START", str(raised.exception))
        self.assertTrue(coordinator.start_attempted)
        self.assertTrue(coordinator.closed_after_failure)


class _FailingStopChannel(FakeChannelAdapter):
    async def stop(self) -> None:
        await super().stop()
        raise RuntimeError("channel cleanup failed")


class _BlockingStopChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__("blocking-stop-channel")
        self.stop_cancelled = False

    async def stop(self) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.stop_cancelled = True
            raise


class _CancellationResistantStopChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__("cancellation-resistant-stop-channel")
        self.stop_started = asyncio.Event()
        self.stop_cancelled = asyncio.Event()
        self.stop_finished = asyncio.Event()
        self.release_stop = asyncio.Event()

    async def stop(self) -> None:
        self.stop_started.set()
        try:
            await self.release_stop.wait()
        except asyncio.CancelledError:
            self.stop_cancelled.set()
            await self.release_stop.wait()
        finally:
            self.stop_finished.set()


class _PartiallyStartingCoordinator(DeliveryCoordinator):
    def __init__(self) -> None:
        super().__init__()
        self.start_attempted = False
        self.closed_after_failure = False

    def start(self) -> None:
        super().start()
        self.start_attempted = True
        raise RuntimeError("RAW_SECRET_COORDINATOR_START")

    async def close(self) -> None:
        self.closed_after_failure = True
        await super().close()


class _CountingStopApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(
            application_instance_id="counting-stop-application",
            project_mode=ProjectMode.FLAT,
        )
        self.stop_count = 0

    async def stop(self) -> None:
        self.stop_count += 1


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
        self.assertIs(type(raised.exception), GatewayStartupOverflow)
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
            with self.assertRaises(GatewayNotRunning) as not_running:
                await active_channel.on_message(
                    _inbound(
                        ConversationRef("active-startup-channel", "conversation"),
                        "during-failed-start",
                    )
                )
            self.assertIs(type(not_running.exception), GatewayNotRunning)
            self.assertEqual(application._inputs, [])
        finally:
            active_channel.release_stop.set()
        with self.assertRaises(GatewayLifecycleFailure) as raised:
            await starting
        self.assertNotIn("simulated channel startup failure", str(raised.exception))
        self.assertEqual(active_channel.start_attempts, 1)
        self.assertEqual(active_channel.stop_attempts, 1)
        self.assertEqual(failing_channel.start_attempts, 1)
        self.assertEqual(failing_channel.stop_attempts, 1)
        self.assertFalse(active_channel.started)
        self.assertFalse(failing_channel.started)
        self.assertEqual(application._inputs, [])


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
        self.start_attempts = 0
        self.stop_attempts = 0

    async def start(self, on_message, on_admission=None) -> None:
        self.start_attempts += 1
        await super().start(on_message, on_admission)

    async def stop(self) -> None:
        self.stop_attempts += 1
        self.stopping.set()
        await self.release_stop.wait()
        await super().stop()


class _FailingStartupChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__("failing-startup-channel")
        self.start_attempts = 0
        self.stop_attempts = 0

    async def start(self, on_message, on_admission=None) -> None:
        self.start_attempts += 1
        await super().start(on_message, on_admission)
        raise RuntimeError("simulated channel startup failure")

    async def stop(self) -> None:
        self.stop_attempts += 1
        await super().stop()


def _inbound(conversation: ConversationRef, message_id: str) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation,
        sender="user-1",
        content=(TextContent("run"),),
        created_at=datetime.now(UTC),
    )


def _public_gateway(
    gateway_id: str,
    *,
    channel: FakeChannelAdapter | None = None,
) -> tuple[Gateway, FakeChannelAdapter, FakeAgentApplicationAdapter]:
    configured_channel = channel or FakeChannelAdapter(f"{gateway_id}-channel")
    application = FakeAgentApplicationAdapter(
        application_instance_id=f"{gateway_id}-application",
        project_mode=ProjectMode.FLAT,
        workspace_id=f"{gateway_id}-workspace",
        workspace_root="/reference",
    )
    gateway = Gateway(
        gateway_id=gateway_id,
        channels=[configured_channel],
        applications=[application],
        store=MemoryGatewayStore(),
    )
    return gateway, configured_channel, application


if __name__ == "__main__":
    unittest.main()

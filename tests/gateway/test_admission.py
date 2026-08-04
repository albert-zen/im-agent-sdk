from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import patch

from imagent.applications.capabilities import ProjectMode
from imagent.contracts import ConversationRef, InboundMessage, TextContent
from imagent.gateway import GatewayRepositories, ImAgentGateway
from imagent.gateway.admission import (
    ClaimedInbound,
    InboundAdmissionService,
    start_channel_with_admission,
)
from imagent.gateway.lifecycle import GatewayNotRunning
from imagent.gateway.persistence import IdempotencyCapacityError, InMemoryIdempotencyRepository
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.gateway.persistence.sqlite import SQLiteGatewayState
from imagent.interaction.channels import ChannelAdapter
from imagent.interaction.channels.adapters import channel_from_config
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class InboundAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_rejects_a_new_identity_before_inbound_handoff(self) -> None:
        repository = InMemoryIdempotencyRepository(max_records=1)
        handed_off: list[ClaimedInbound] = []

        async def handoff(claimed: ClaimedInbound) -> None:
            handed_off.append(claimed)

        service = InboundAdmissionService(repository, handoff)
        conversation = ConversationRef("qq-main", "c2c:user-1")
        first = await service.begin("qq-main", conversation, "message-1")
        assert first is not None
        try:
            with self.assertRaises(IdempotencyCapacityError):
                await service.begin("qq-main", conversation, "message-2")
            self.assertEqual(handed_off, [])
        finally:
            await first.release()

    async def test_cancellation_during_handoff_fencing_releases_claim(self) -> None:
        class BlockingRefreshRepository(InMemoryIdempotencyRepository):
            def __init__(self) -> None:
                super().__init__()
                self.started = asyncio.Event()

            async def refresh(self, scope, key, *, owner_token=None) -> None:
                del scope, key, owner_token
                self.started.set()
                await asyncio.Event().wait()

        repository = BlockingRefreshRepository()

        async def handoff(_claimed: ClaimedInbound) -> None:
            raise AssertionError("cancelled fencing must not reach handoff")

        service = InboundAdmissionService(repository, handoff)
        conversation = ConversationRef("qq-main", "c2c:user-1")
        admission = await service.begin("qq-main", conversation, "message-cancelled")
        assert admission is not None
        delivery = asyncio.create_task(
            admission.deliver(
                InboundMessage(
                    message_id="message-cancelled",
                    conversation_ref=conversation,
                    sender="user-1",
                    content=(TextContent("hello"),),
                    created_at=datetime.now(UTC),
                )
            )
        )
        await repository.started.wait()
        delivery.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await delivery

        retry = await service.begin("qq-main", conversation, "message-cancelled")
        self.assertIsNotNone(retry)
        if retry is not None:
            await retry.release()

    async def test_concurrent_deliver_calls_transfer_one_shot_lease_once(self) -> None:
        class BlockingRefreshRepository(InMemoryIdempotencyRepository):
            def __init__(self) -> None:
                super().__init__()
                self.started = asyncio.Event()
                self.resume = asyncio.Event()

            async def refresh(self, scope, key, *, owner_token=None) -> None:
                self.started.set()
                await self.resume.wait()
                await super().refresh(scope, key, owner_token=owner_token)

        repository = BlockingRefreshRepository()
        handed_off: list[ClaimedInbound] = []

        async def handoff(claimed: ClaimedInbound) -> None:
            handed_off.append(claimed)

        service = InboundAdmissionService(repository, handoff)
        conversation = ConversationRef("qq-main", "c2c:user-1")
        admission = await service.begin("qq-main", conversation, "message-1")
        assert admission is not None
        message = InboundMessage(
            message_id="message-1",
            conversation_ref=conversation,
            sender="user-1",
            content=(TextContent("hello"),),
            created_at=datetime.now(UTC),
        )

        first = asyncio.create_task(admission.deliver(message))
        await repository.started.wait()
        with self.assertRaisesRegex(RuntimeError, "no longer open"):
            await admission.deliver(message)
        repository.resume.set()
        await first

        self.assertEqual([item.message for item in handed_off], [message])

    async def test_sdk_channels_receive_the_exact_admission_handler(self) -> None:
        async def on_message(_message: InboundMessage) -> None:
            return None

        async def on_admission(
            _conversation_ref: ConversationRef,
            _message_id: str,
        ) -> None:
            return None

        with tempfile.TemporaryDirectory() as directory:
            channels = (
                channel_from_config("qq", config={"enabled": False}),
                channel_from_config("telegram", config={"enabled": False}),
                channel_from_config("feishu", config={"enabled": False}),
                channel_from_config(
                    "weixin",
                    config={"enabled": False, "state_dir": directory},
                ),
            )

            for channel in channels:
                with patch.object(channel, "start", wraps=channel.start) as start:
                    await start_channel_with_admission(
                        channel,
                        on_message,
                        on_admission,
                    )
                    start.assert_awaited_once()
                    awaited = start.await_args
                    self.assertIsNotNone(awaited)
                    assert awaited is not None
                    self.assertIs(awaited.args[0], on_message)
                    self.assertIs(awaited.args[1], on_admission)
                await channel.stop()

    async def test_legacy_start_fails_before_body_without_retry_and_can_restart(self) -> None:
        class LegacyChannel:
            def __init__(self) -> None:
                self.delegate = FakeChannelAdapter("legacy-channel")
                self.start_body_calls = 0
                self.stop_attempts = 0

            @property
            def channel_instance_id(self):
                return self.delegate.channel_instance_id

            @property
            def capabilities(self):
                return self.delegate.capabilities

            async def start(self, on_message) -> None:
                self.start_body_calls += 1
                await self.delegate.start(on_message)

            async def stop(self) -> None:
                self.stop_attempts += 1
                await self.delegate.stop()

            async def send(self, message):
                return await self.delegate.send(message)

        active = _CountingChannel("active-channel")
        legacy = LegacyChannel()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        gateway = ImAgentGateway(
            channels=[active, cast(ChannelAdapter, legacy)],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )

        for attempt in (1, 2):
            with self.assertRaises(TypeError):
                await gateway.start()

            self.assertEqual(active.start_attempts, attempt)
            self.assertEqual(active.stop_attempts, attempt)
            self.assertEqual(legacy.start_body_calls, 0)
            self.assertEqual(legacy.stop_attempts, attempt)
            self.assertFalse(active.started)
            self.assertFalse(legacy.delegate.started)
            self.assertEqual(application._inputs, [])
            startup = gateway.diagnostics_snapshot().gateway.startup_queue
            self.assertEqual(startup.depth, 0)
            with self.assertRaises(GatewayNotRunning):
                await active.on_message(
                    _inbound(
                        ConversationRef("active-channel", "conversation"),
                        f"after-failure-{attempt}",
                    )
                )

    async def test_modern_start_type_error_after_effect_is_not_retried(self) -> None:
        class FailingModernChannel:
            def __init__(self) -> None:
                self.delegate = FakeChannelAdapter("modern-channel")
                self.start_attempts = 0
                self.stop_attempts = 0

            @property
            def channel_instance_id(self):
                return self.delegate.channel_instance_id

            @property
            def capabilities(self):
                return self.delegate.capabilities

            async def start(self, on_message, on_admission=None) -> None:
                self.start_attempts += 1
                await self.delegate.start(on_message, on_admission)
                raise TypeError("failure after modern startup effect")

            async def stop(self) -> None:
                self.stop_attempts += 1
                await self.delegate.stop()

            async def send(self, message):
                return await self.delegate.send(message)

        active = _CountingChannel("active-channel")
        channel = FailingModernChannel()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        gateway = ImAgentGateway(
            channels=[active, cast(ChannelAdapter, channel)],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )

        with self.assertRaisesRegex(TypeError, "after modern startup effect"):
            await gateway.start()

        self.assertEqual(active.start_attempts, 1)
        self.assertEqual(active.stop_attempts, 1)
        self.assertEqual(channel.start_attempts, 1)
        self.assertEqual(channel.stop_attempts, 1)
        self.assertFalse(active.started)
        self.assertFalse(channel.delegate.started)
        self.assertEqual(application._inputs, [])
        with self.assertRaises(GatewayNotRunning):
            await active.on_message(
                _inbound(
                    ConversationRef("active-channel", "conversation"),
                    "after-modern-failure",
                )
            )

    async def test_reclaimed_preparation_worker_is_fenced_before_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = SQLiteGatewayState(
                Path(directory) / "gateway.sqlite3",
                stale_claim_after_seconds=0,
            )
            handed_off: list[ClaimedInbound] = []

            async def handoff(claimed: ClaimedInbound) -> None:
                handed_off.append(claimed)

            service = InboundAdmissionService(state, handoff)
            conversation = ConversationRef("qq-main", "c2c:user-1")
            first = await service.begin("qq-main", conversation, "message-1")
            second = await service.begin("qq-main", conversation, "message-1")
            assert first is not None
            assert second is not None
            message = InboundMessage(
                message_id="message-1",
                conversation_ref=conversation,
                sender="user-1",
                content=(TextContent("hello"),),
                created_at=datetime.now(UTC),
            )
            try:
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    await first.deliver(message)
                await second.deliver(message)
            finally:
                await state.close()

            self.assertEqual([item.message for item in handed_off], [message])

    async def test_identity_mismatch_releases_pre_handoff_claim(self) -> None:
        state = SQLiteGatewayState(":memory:")

        async def handoff(_claimed: ClaimedInbound) -> None:
            raise AssertionError("identity mismatch must not reach handoff")

        service = InboundAdmissionService(state, handoff)
        conversation = ConversationRef("qq-main", "c2c:user-1")
        admission = await service.begin("qq-main", conversation, "message-1")
        assert admission is not None
        try:
            with self.assertRaisesRegex(ValueError, "does not match"):
                await admission.deliver(
                    InboundMessage(
                        message_id="message-2",
                        conversation_ref=conversation,
                        sender="user-1",
                        content=(TextContent("wrong"),),
                        created_at=datetime.now(UTC),
                    )
                )
            retry = await service.begin("qq-main", conversation, "message-1")
            self.assertIsNotNone(retry)
            if retry is not None:
                await retry.release()
        finally:
            await state.close()


class _CountingChannel(FakeChannelAdapter):
    def __init__(self, channel_instance_id: str) -> None:
        super().__init__(channel_instance_id)
        self.start_attempts = 0
        self.stop_attempts = 0

    async def start(self, on_message, on_admission=None) -> None:
        self.start_attempts += 1
        await super().start(on_message, on_admission)

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


if __name__ == "__main__":
    unittest.main()

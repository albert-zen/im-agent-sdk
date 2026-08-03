from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import ConversationRef, InboundMessage, TextContent
from imagent.gateway import GatewayRepositories, ImAgentGateway
from imagent.inbound_admission import ClaimedInbound, InboundAdmissionService
from imagent.interaction.channels import ChannelAdapter
from imagent.storage import InMemoryIdempotencyRepository, SQLiteGatewayState
from imagent.testing import FakeChannelAdapter


class InboundAdmissionTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_gateway_starts_legacy_message_only_channel(self) -> None:
        class LegacyChannel:
            def __init__(self) -> None:
                self.delegate = FakeChannelAdapter("legacy-channel")

            @property
            def channel_instance_id(self):
                return self.delegate.channel_instance_id

            @property
            def capabilities(self):
                return self.delegate.capabilities

            async def start(self, on_message) -> None:
                await self.delegate.start(on_message)

            async def stop(self) -> None:
                await self.delegate.stop()

            async def send(self, message):
                return await self.delegate.send(message)

        legacy = LegacyChannel()
        gateway = ImAgentGateway(
            channels=[cast(ChannelAdapter, legacy)],
            applications=[],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )

        await gateway.start()
        try:
            self.assertTrue(legacy.delegate.started)
        finally:
            await gateway.stop()

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

        channel = FailingModernChannel()
        gateway = ImAgentGateway(
            channels=[cast(ChannelAdapter, channel)],
            applications=[],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )

        with self.assertRaisesRegex(TypeError, "after modern startup effect"):
            await gateway.start()

        self.assertEqual(channel.start_attempts, 1)
        self.assertEqual(channel.stop_attempts, 1)
        self.assertFalse(channel.delegate.started)

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


if __name__ == "__main__":
    unittest.main()

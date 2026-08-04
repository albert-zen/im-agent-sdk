from __future__ import annotations

import asyncio
import sys
import unittest
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from unittest.mock import patch

import imagent
from imagent.gateway.delivery import (
    DeliveryCoordinator,
    DeliveryCoordinatorConfig,
    DeliveryPlanner,
    DeliveryPlanningError,
)
from imagent.gateway.delivery import coordination as coordination_owner
from imagent.interaction.channels import (
    ChannelCapabilities,
    DeliveryItemStatus,
    DeliveryProfile,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySegmentStatus,
    DeliverySupportLevel,
)
from imagent.interaction.media import (
    AttachmentContent,
    AttachmentGrouping,
    AttachmentSourceKind,
    RemoteUrl,
)
from imagent.interaction.messages import (
    ConversationRef,
    OutboundMessage,
    TextContent,
)


class DeliveryCoordinationFacadeTests(unittest.TestCase):
    def test_root_attribute_and_gateway_facade_use_exact_owner_objects(self) -> None:
        self.assertIs(imagent.delivery_coordination, coordination_owner)
        self.assertIs(DeliveryCoordinator, coordination_owner.DeliveryCoordinator)
        self.assertIs(
            DeliveryCoordinatorConfig,
            coordination_owner.DeliveryCoordinatorConfig,
        )
        self.assertNotIn("imagent.delivery_coordination", sys.modules)


class _Channel:
    def __init__(
        self,
        profile: DeliveryProfile,
        sender: Callable[[OutboundMessage], Awaitable[DeliveryReceipt]] | None = None,
    ) -> None:
        self._capabilities = ChannelCapabilities(
            plain_text=profile.plain_text,
            markdown=profile.markdown,
            attachments=profile.attachments,
            attachment_sources=profile.attachment_sources,
            attachment_media_types=profile.attachment_media_types,
            attachment_grouping=profile.attachment_grouping,
            reply_references=profile.reply_references,
            reply_reference_scope=profile.reply_reference_scope,
            text_length_unit=profile.text_length_unit,
            max_text_length=profile.max_text_length,
            max_attachment_size=profile.max_attachment_size,
            max_attachment_count=profile.max_attachment_count,
            max_attachment_group_size=profile.max_attachment_group_size,
        )
        self.sent: list[OutboundMessage] = []
        self._sender = sender

    @property
    def channel_instance_id(self) -> str:
        return "test-channel"

    @property
    def capabilities(self) -> ChannelCapabilities:
        return self._capabilities

    async def start(self, on_message, on_admission=None) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        self.sent.append(message)
        if self._sender is not None:
            return await self._sender(message)
        return DeliveryReceipt(
            status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            native_message_id=f"native-{len(self.sent)}",
        )


class DeliveryCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_is_reserved_before_planning(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            started.set()
            await release.wait()
            return DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)

        planner = DeliveryPlanner()
        channel = _Channel(DeliveryProfile(), sender)
        coordinator = DeliveryCoordinator(
            planner=planner,
            config=DeliveryCoordinatorConfig(
                max_pending=1,
                max_source_items_per_delivery=1,
            ),
        )
        with patch.object(planner, "plan", wraps=planner.plan) as plan:
            first = coordinator.submit(channel, self._message("a", "first"))
            await started.wait()
            second = coordinator.submit(
                channel,
                OutboundMessage(
                    delivery_id="oversized-while-full",
                    conversation_ref=ConversationRef("test-channel", "b"),
                    content=tuple(TextContent(str(index)) for index in range(1_000)),
                    created_at=datetime.now(UTC),
                ),
            )

            self.assertFalse(second.admitted)
            backpressure = await second.result()
            self.assertIs(backpressure.status, DeliveryReceiptStatus.RETRYABLE_FAILURE)
            self.assertEqual(backpressure.items, ())
            self.assertEqual(backpressure.segments, ())
            self.assertEqual(plan.call_count, 1)

            release.set()
            await first.result()
        await coordinator.close()

    async def test_oversized_plan_fails_before_native_side_effect_and_releases_slot(self) -> None:
        channel = _Channel(DeliveryProfile(max_text_length=1))
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(
                max_pending=1,
                max_segments_per_delivery=2,
            )
        )

        with self.assertRaisesRegex(DeliveryPlanningError, "segment limit"):
            await coordinator.deliver(channel, self._message("a", "abc"))

        self.assertEqual(channel.sent, [])
        receipt = await coordinator.deliver(channel, self._message("a", "ok"))
        self.assertIs(receipt.status, DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)
        await coordinator.close()

    async def test_unrepresentable_later_attachment_fails_before_any_send(self) -> None:
        profile = DeliveryProfile(
            attachments=DeliverySupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.REMOTE_URL,),
            attachment_grouping=AttachmentGrouping.NONE,
            max_attachment_group_size=10,
        )
        channel = _Channel(profile)
        coordinator = DeliveryCoordinator()
        message = OutboundMessage(
            delivery_id="delivery-preflight",
            conversation_ref=ConversationRef("test-channel", "conversation"),
            content=(
                TextContent("must not be sent"),
                AttachmentContent(
                    attachment_id="oversize",
                    media_type="image/png",
                    source=RemoteUrl("https://example.test/oversize"),
                    size_bytes=11,
                ),
            ),
            created_at=datetime(2026, 7, 31, tzinfo=UTC),
        )

        with self.assertRaisesRegex(DeliveryPlanningError, "group size limit"):
            await coordinator.deliver(channel, message)

        self.assertEqual(channel.sent, [])
        await coordinator.close()

    async def test_same_destination_is_fifo_and_other_destinations_can_progress(self) -> None:
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls: list[tuple[str, str]] = []

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            text = message.content[0]
            assert isinstance(text, TextContent)
            calls.append((message.conversation_ref.native_conversation_id, text.text))
            if text.text == "first":
                first_started.set()
                await release_first.wait()
            return DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)

        channel = _Channel(DeliveryProfile(), sender)
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(max_concurrent_destinations=2)
        )
        first = coordinator.submit(channel, self._message("a", "first"))
        await first_started.wait()
        second = coordinator.submit(channel, self._message("a", "second"))
        other = coordinator.submit(channel, self._message("b", "other"))
        await other.result()

        self.assertEqual(calls, [("a", "first"), ("b", "other")])
        release_first.set()
        await asyncio.gather(first.result(), second.result())
        self.assertEqual(calls, [("a", "first"), ("b", "other"), ("a", "second")])
        await coordinator.close()

    async def test_submit_applies_bounded_nonblocking_backpressure(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            started.set()
            await release.wait()
            return DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)

        channel = _Channel(DeliveryProfile(), sender)
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(
                max_pending=1,
                max_pending_per_destination=1,
            )
        )
        admitted = coordinator.submit(channel, self._message("a", "first"))
        await started.wait()
        self.assertEqual(coordinator._destination_locks.active_key_count, 1)
        self.assertLessEqual(
            coordinator._destination_locks.active_key_count,
            coordinator._config.max_pending,
        )
        rejected = coordinator.submit(channel, self._message("b", "second"))

        self.assertTrue(admitted.admitted)
        self.assertFalse(rejected.admitted)
        receipt = await rejected.result()
        self.assertIs(receipt.status, DeliveryReceiptStatus.RETRYABLE_FAILURE)
        # Capacity rejection happens before planning, so neither source items
        # nor synthetic segments are allocated merely to describe pressure.
        self.assertEqual(receipt.items, ())
        self.assertEqual(receipt.segments, ())
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(coordinator._destination_locks.active_key_count, 1)
        release.set()
        await admitted.result()
        self.assertEqual(coordinator._destination_locks.active_key_count, 0)
        await coordinator.close()

    def test_retry_timing_configuration_must_be_finite(self) -> None:
        for value in (float("nan"), float("inf")):
            with self.subTest(field="retry_initial_seconds", value=value):
                with self.assertRaisesRegex(ValueError, "finite"):
                    DeliveryCoordinatorConfig(retry_initial_seconds=value)
            with self.subTest(field="retry_max_seconds", value=value):
                with self.assertRaisesRegex(ValueError, "finite"):
                    DeliveryCoordinatorConfig(retry_max_seconds=value)
            with self.subTest(field="backpressure_retry_after_seconds", value=value):
                with self.assertRaisesRegex(ValueError, "finite"):
                    DeliveryCoordinatorConfig(backpressure_retry_after_seconds=value)

    async def test_per_destination_bound_does_not_consume_other_capacity(self) -> None:
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            if message.conversation_ref.native_conversation_id == "a":
                first_started.set()
                await release_first.wait()
            return DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)

        channel = _Channel(DeliveryProfile(), sender)
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(
                max_pending=2,
                max_pending_per_destination=1,
                max_concurrent_destinations=2,
            )
        )
        first = coordinator.submit(channel, self._message("a", "first"))
        await first_started.wait()

        same_destination = coordinator.submit(channel, self._message("a", "second"))
        other_destination = coordinator.submit(channel, self._message("b", "other"))

        self.assertFalse(same_destination.admitted)
        self.assertTrue(other_destination.admitted)
        self.assertIs(
            (await same_destination.result()).status,
            DeliveryReceiptStatus.RETRYABLE_FAILURE,
        )
        await other_destination.result()
        release_first.set()
        await first.result()
        await coordinator.close()

    async def test_default_policy_does_not_retry_retryable_receipt(self) -> None:
        async def retryable(message: OutboundMessage) -> DeliveryReceipt:
            return DeliveryReceipt(
                status=DeliveryReceiptStatus.RETRYABLE_FAILURE,
                retry_after_seconds=0,
            )

        channel = _Channel(DeliveryProfile(), retryable)
        coordinator = DeliveryCoordinator()

        receipt = await coordinator.deliver(channel, self._message("a", "one"))

        self.assertIs(receipt.status, DeliveryReceiptStatus.RETRYABLE_FAILURE)
        self.assertEqual(len(channel.sent), 1)
        await coordinator.close()

    async def test_only_explicit_retryable_outcomes_retry(self) -> None:
        attempts = 0

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return DeliveryReceipt(
                    status=DeliveryReceiptStatus.RETRYABLE_FAILURE,
                    retry_after_seconds=0,
                )
            return DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)

        channel = _Channel(DeliveryProfile(), sender)
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(max_attempts=2, retry_initial_seconds=0)
        )

        receipt = await coordinator.deliver(channel, self._message("a", "one"))

        self.assertIs(receipt.status, DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)
        self.assertEqual(attempts, 2)
        await coordinator.close()

    async def test_retry_after_larger_than_policy_limit_is_not_shortened(self) -> None:
        async def retryable(message: OutboundMessage) -> DeliveryReceipt:
            return DeliveryReceipt(
                status=DeliveryReceiptStatus.RETRYABLE_FAILURE,
                retry_after_seconds=60,
            )

        channel = _Channel(DeliveryProfile(), retryable)
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(max_attempts=2, retry_max_seconds=5)
        )

        receipt = await coordinator.deliver(channel, self._message("a", "one"))

        self.assertIs(receipt.status, DeliveryReceiptStatus.RETRYABLE_FAILURE)
        self.assertEqual(receipt.retry_after_seconds, 60)
        self.assertEqual(len(channel.sent), 1)
        await coordinator.close()

    async def test_closed_coordinator_can_start_a_clean_new_lifecycle(self) -> None:
        channel = _Channel(DeliveryProfile())
        coordinator = DeliveryCoordinator()
        await coordinator.deliver(channel, self._message("a", "first"))
        await coordinator.close()

        with self.assertRaisesRegex(RuntimeError, "closed"):
            await coordinator.deliver(channel, self._message("a", "closed"))
        coordinator.start()
        receipt = await coordinator.deliver(channel, self._message("a", "second"))

        self.assertIs(receipt.status, DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)
        self.assertEqual(len(channel.sent), 2)
        await coordinator.close()

    async def test_unknown_and_exceptions_are_never_retried(self) -> None:
        unknown_channel = _Channel(
            DeliveryProfile(),
            lambda message: _receipt(DeliveryReceiptStatus.UNKNOWN),
        )
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(max_attempts=3, retry_initial_seconds=0)
        )
        unknown = await coordinator.deliver(
            unknown_channel,
            self._message("a", "unknown"),
        )
        self.assertIs(unknown.status, DeliveryReceiptStatus.UNKNOWN)
        self.assertEqual(len(unknown_channel.sent), 1)

        async def failing(message: OutboundMessage) -> DeliveryReceipt:
            raise OSError("connection outcome is ambiguous")

        failing_channel = _Channel(DeliveryProfile(), failing)
        failed = await coordinator.deliver(
            failing_channel,
            self._message("b", "exception"),
        )
        self.assertIs(failed.status, DeliveryReceiptStatus.UNKNOWN)
        self.assertEqual(len(failing_channel.sent), 1)
        await coordinator.close()

    async def test_retry_wait_does_not_hold_global_execution_slot(self) -> None:
        first_attempt = asyncio.Event()
        allow_retry = asyncio.Event()
        calls: list[str] = []

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            text = message.content[0]
            assert isinstance(text, TextContent)
            calls.append(text.text)
            if text.text == "retry" and calls.count("retry") == 1:
                first_attempt.set()
                return DeliveryReceipt(
                    status=DeliveryReceiptStatus.RETRYABLE_FAILURE,
                    retry_after_seconds=0.05,
                )
            if text.text == "retry":
                await allow_retry.wait()
            return DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)

        channel = _Channel(DeliveryProfile(), sender)
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(
                max_attempts=2,
                max_concurrent_destinations=1,
                retry_initial_seconds=0.05,
                retry_max_seconds=0.05,
            )
        )
        retrying = coordinator.submit(channel, self._message("a", "retry"))
        await first_attempt.wait()
        other = coordinator.submit(channel, self._message("b", "other"))
        await asyncio.wait_for(other.result(), timeout=0.2)

        self.assertEqual(calls[:2], ["retry", "other"])
        allow_retry.set()
        await retrying.result()
        await coordinator.close()

    async def test_partial_prefix_stops_later_segments_without_unsafe_retry(self) -> None:
        outcomes = iter(
            (
                DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM),
                DeliveryReceipt(
                    status=DeliveryReceiptStatus.RETRYABLE_FAILURE,
                    retry_after_seconds=0,
                ),
            )
        )

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            return next(outcomes)

        channel = _Channel(DeliveryProfile(max_text_length=3), sender)
        coordinator = DeliveryCoordinator()
        receipt = await coordinator.deliver(channel, self._message("a", "abcdefghi"))

        self.assertIs(receipt.status, DeliveryReceiptStatus.UNKNOWN)
        self.assertEqual(len(channel.sent), 2)
        self.assertEqual(receipt.items[0].status, DeliveryItemStatus.UNKNOWN)
        self.assertEqual(
            [segment.status for segment in receipt.segments],
            [
                DeliverySegmentStatus.ACCEPTED_BY_PLATFORM,
                DeliverySegmentStatus.RETRYABLE_FAILURE,
                DeliverySegmentStatus.SKIPPED,
            ],
        )
        await coordinator.close()

    async def test_rejected_split_item_preserves_rejection_and_skipped_suffix(self) -> None:
        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            return DeliveryReceipt(status=DeliveryReceiptStatus.REJECTED_BY_PLATFORM)

        channel = _Channel(DeliveryProfile(max_text_length=3), sender)
        coordinator = DeliveryCoordinator()

        receipt = await coordinator.deliver(channel, self._message("a", "abcdef"))

        self.assertIs(receipt.status, DeliveryReceiptStatus.REJECTED_BY_PLATFORM)
        self.assertIs(receipt.items[0].status, DeliveryItemStatus.REJECTED)
        self.assertEqual(
            [segment.status for segment in receipt.segments],
            [
                DeliverySegmentStatus.REJECTED_BY_PLATFORM,
                DeliverySegmentStatus.SKIPPED,
            ],
        )
        await coordinator.close()

    async def test_accepted_prefix_and_rejected_item_report_partial_detail(self) -> None:
        outcomes = iter(
            (
                DeliveryReceipt(status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM),
                DeliveryReceipt(status=DeliveryReceiptStatus.REJECTED_BY_PLATFORM),
            )
        )

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            return next(outcomes)

        channel = _Channel(DeliveryProfile(max_text_length=3), sender)
        coordinator = DeliveryCoordinator()

        receipt = await coordinator.deliver(channel, self._message("a", "abcdef"))

        self.assertIs(receipt.status, DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM)
        self.assertIs(receipt.items[0].status, DeliveryItemStatus.UNKNOWN)
        self.assertIn("partially", receipt.detail or "")
        await coordinator.close()

    async def test_close_cancels_awaited_delivery_before_channel_shutdown(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return DeliveryReceipt(status=DeliveryReceiptStatus.UNKNOWN)

        channel = _Channel(DeliveryProfile(), sender)
        coordinator = DeliveryCoordinator()
        delivery = asyncio.create_task(coordinator.deliver(channel, self._message("a", "waiting")))
        await started.wait()

        await coordinator.close()

        with self.assertRaises(asyncio.CancelledError):
            await delivery
        self.assertTrue(cancelled.is_set())

    async def test_cancelling_awaited_delivery_stops_its_native_send(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def sender(message: OutboundMessage) -> DeliveryReceipt:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return DeliveryReceipt(status=DeliveryReceiptStatus.UNKNOWN)

        channel = _Channel(DeliveryProfile(), sender)
        coordinator = DeliveryCoordinator()
        delivery = asyncio.create_task(
            coordinator.deliver(channel, self._message("a", "cancelled"))
        )
        await started.wait()

        delivery.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await delivery
        self.assertTrue(cancelled.is_set())
        await coordinator.close()

    @staticmethod
    def _message(conversation: str, text: str) -> OutboundMessage:
        return OutboundMessage(
            delivery_id=f"delivery-{conversation}-{text}",
            conversation_ref=ConversationRef("test-channel", conversation),
            content=(TextContent(text),),
            created_at=datetime(2026, 7, 31, tzinfo=UTC),
        )


async def _receipt(status: DeliveryReceiptStatus) -> DeliveryReceipt:
    return DeliveryReceipt(status=status)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast
from unittest.mock import patch

from imagent.channels.runtime import NativeTransportChannelAdapter
from imagent.contracts import (
    AttachmentContent,
    AttachmentGrouping,
    AttachmentSourceKind,
    ChannelCapabilities,
    ConversationRef,
    DeliveryItemStatus,
    DeliveryProfile,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySegmentStatus,
    LocalPath,
    OutboundMessage,
    RemoteUrl,
    ReplyReferenceScope,
    SupportLevel,
    TextContent,
    TextFormat,
    TextLengthUnit,
)
from imagent.delivery_coordination import (
    DeliveryCoordinator,
    DeliveryCoordinatorConfig,
)
from imagent.delivery_planning import (
    DeliveryPlanner,
    DeliveryPlanningError,
)


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

    async def start(self, on_message, on_operation) -> None:
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


class DeliveryPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = DeliveryPlanner()
        self.conversation = ConversationRef("test-channel", "conversation")

    def test_native_markdown_golden_plan_is_stable(self) -> None:
        profile = DeliveryProfile(
            markdown=SupportLevel.NATIVE,
            reply_references=SupportLevel.NATIVE,
            max_text_length=8,
        )
        message = self._message(
            TextContent("**one** two three", TextFormat.MARKDOWN),
            reply_to="native-parent",
        )

        first = self.planner.plan(message, profile)
        second = self.planner.plan(message, profile)

        self.assertEqual(first, second)
        self.assertEqual(
            [
                (
                    cast(TextContent, segment.message.content[0]).text,
                    cast(TextContent, segment.message.content[0]).format,
                )
                for segment in first.segments
            ],
            [
                ("**one**", TextFormat.MARKDOWN),
                ("two", TextFormat.MARKDOWN),
                ("three", TextFormat.MARKDOWN),
            ],
        )
        self.assertEqual(first.segments[0].message.reply_to, "native-parent")
        self.assertTrue(all(segment.message.reply_to is None for segment in first.segments[1:]))
        self.assertEqual(
            [segment.message.metadata["segment_index"] for segment in first.segments],
            [0, 1, 2],
        )

    def test_plain_fallback_and_utf8_byte_limit_golden(self) -> None:
        profile = DeliveryProfile(
            markdown=SupportLevel.FALLBACK,
            max_text_length=6,
            text_length_unit=TextLengthUnit.UTF8_BYTES,
        )
        plan = self.planner.plan(
            self._message(TextContent("**你** 好", TextFormat.MARKDOWN)),
            profile,
        )

        self.assertEqual(
            [
                (
                    cast(TextContent, segment.message.content[0]).text,
                    cast(TextContent, segment.message.content[0]).format,
                )
                for segment in plan.segments
            ],
            [("你", TextFormat.PLAIN), ("好", TextFormat.PLAIN)],
        )

    def test_qq_and_telegram_profiles_have_distinct_golden_plans(self) -> None:
        qq = NativeTransportChannelAdapter(
            channel_instance_id="qq-test",
            channel_id="qq",
            native_factory=lambda middleware: _UnusedNative(middleware),
        )
        telegram = NativeTransportChannelAdapter(
            channel_instance_id="telegram-test",
            channel_id="telegram",
            native_factory=lambda middleware: _UnusedNative(middleware),
        )
        source = TextContent("**shared** [link](https://example.test)", TextFormat.MARKDOWN)

        qq_plan = self.planner.plan(
            OutboundMessage(
                delivery_id="qq-profile",
                conversation_ref=ConversationRef("qq-test", "conversation"),
                content=(source,),
                created_at=datetime(2026, 7, 31, tzinfo=UTC),
            ),
            qq.capabilities.delivery,
        )
        telegram_plan = self.planner.plan(
            OutboundMessage(
                delivery_id="telegram-profile",
                conversation_ref=ConversationRef("telegram-test", "conversation"),
                content=(source,),
                created_at=datetime(2026, 7, 31, tzinfo=UTC),
            ),
            telegram.capabilities.delivery,
        )

        qq_text = cast(TextContent, qq_plan.segments[0].message.content[0])
        telegram_text = cast(TextContent, telegram_plan.segments[0].message.content[0])
        self.assertEqual((qq_text.text, qq_text.format), (source.text, TextFormat.MARKDOWN))
        self.assertEqual(
            (telegram_text.text, telegram_text.format),
            ("shared link (https://example.test)", TextFormat.PLAIN),
        )
        self.assertNotEqual(
            qq_plan.segments[0].message.delivery_id,
            telegram_plan.segments[0].message.delivery_id,
        )

    def test_text_attachment_order_and_mixed_grouping_are_preserved(self) -> None:
        profile = DeliveryProfile(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.REMOTE_URL,),
            attachment_grouping=AttachmentGrouping.MIXED,
            max_attachment_count=2,
            max_attachment_group_size=7,
        )
        first_attachment = self._remote_attachment("a", "image/png", 3)
        second_attachment = self._remote_attachment("b", "text/plain", 4)
        plan = self.planner.plan(
            self._message(
                TextContent("before"),
                first_attachment,
                second_attachment,
                TextContent("after"),
            ),
            profile,
        )

        self.assertEqual(
            [segment.source_content_indexes for segment in plan.segments],
            [(0,), (1, 2), (3,)],
        )
        self.assertEqual(plan.segments[1].message.content, (first_attachment, second_attachment))

    def test_same_media_family_and_count_limits_split_groups(self) -> None:
        profile = DeliveryProfile(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.REMOTE_URL,),
            attachment_grouping=AttachmentGrouping.SAME_MEDIA_FAMILY,
            max_attachment_count=2,
        )
        plan = self.planner.plan(
            self._message(
                self._remote_attachment("a", "image/png", 1),
                self._remote_attachment("b", "image/jpeg", 1),
                self._remote_attachment("c", "image/webp", 1),
                self._remote_attachment("d", "text/plain", 1),
            ),
            profile,
        )

        self.assertEqual(
            [segment.source_content_indexes for segment in plan.segments],
            [(0, 1), (2,), (3,)],
        )

    def test_none_grouping_requires_measurable_single_attachment_groups(self) -> None:
        profile = DeliveryProfile(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.REMOTE_URL,),
            attachment_grouping=AttachmentGrouping.NONE,
            max_attachment_group_size=10,
        )

        with self.assertRaisesRegex(DeliveryPlanningError, "declared attachment sizes"):
            self.planner.plan(
                self._message(
                    AttachmentContent(
                        attachment_id="unknown",
                        media_type="image/png",
                        source=RemoteUrl("https://example.test/unknown"),
                    )
                ),
                profile,
            )

        with self.assertRaisesRegex(DeliveryPlanningError, "group size limit"):
            self.planner.plan(
                self._message(self._remote_attachment("oversize", "image/png", 11)),
                profile,
            )

    def test_every_segment_reply_scope_is_explicit(self) -> None:
        profile = DeliveryProfile(
            reply_references=SupportLevel.NATIVE,
            reply_reference_scope=ReplyReferenceScope.EVERY_SEGMENT,
            max_text_length=3,
        )
        plan = self.planner.plan(
            self._message(TextContent("abcdef"), reply_to="parent"),
            profile,
        )

        self.assertEqual(
            [segment.message.reply_to for segment in plan.segments],
            ["parent", "parent"],
        )

    def test_unsupported_or_unmeasurable_attachments_fail_before_send(self) -> None:
        unsupported_profile = DeliveryProfile()
        with self.assertRaisesRegex(DeliveryPlanningError, "does not support attachments"):
            self.planner.plan(
                self._message(self._remote_attachment("a", "image/png", 1)),
                unsupported_profile,
            )

        bounded_profile = DeliveryProfile(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
            max_attachment_size=10,
        )
        with self.assertRaisesRegex(DeliveryPlanningError, "declared size"):
            self.planner.plan(
                self._message(
                    AttachmentContent(
                        attachment_id="local",
                        media_type="image/png",
                        source=LocalPath("C:/tmp/image.png"),
                    )
                ),
                bounded_profile,
            )
        with self.assertRaisesRegex(DeliveryPlanningError, "size limit"):
            self.planner.plan(
                self._message(
                    AttachmentContent(
                        attachment_id="oversize",
                        media_type="image/png",
                        source=LocalPath("C:/tmp/oversize.png"),
                        size_bytes=11,
                    )
                ),
                bounded_profile,
            )

        typed_profile = DeliveryProfile(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.REMOTE_URL,),
            attachment_media_types=("image/*",),
        )
        with self.assertRaisesRegex(DeliveryPlanningError, "media type"):
            self.planner.plan(
                self._message(self._remote_attachment("document", "text/plain", 1)),
                typed_profile,
            )

    def _message(
        self,
        *content,
        reply_to: str | None = None,
    ) -> OutboundMessage:
        return OutboundMessage(
            delivery_id="root-delivery",
            conversation_ref=self.conversation,
            content=content,
            created_at=datetime(2026, 7, 31, tzinfo=UTC),
            reply_to=reply_to,
        )

    @staticmethod
    def _remote_attachment(
        attachment_id: str,
        media_type: str,
        size_bytes: int,
    ) -> AttachmentContent:
        return AttachmentContent(
            attachment_id=attachment_id,
            media_type=media_type,
            source=RemoteUrl(f"https://example.test/{attachment_id}"),
            size_bytes=size_bytes,
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
            attachments=SupportLevel.NATIVE,
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
        release.set()
        await admitted.result()
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


class _UnusedNative:
    channel_id = "unused"

    def __init__(self, middleware: object) -> None:
        self.middleware = middleware

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_message(self, message):
        raise AssertionError("golden profile tests do not start the native Channel")


if __name__ == "__main__":
    unittest.main()

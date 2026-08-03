from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from typing import cast

import imagent
from imagent.contracts import (
    AttachmentContent,
    AttachmentGrouping,
    AttachmentSourceKind,
    ConversationRef,
    DeliveryProfile,
    DeliverySupportLevel,
    LocalPath,
    OutboundMessage,
    RemoteUrl,
    ReplyReferenceScope,
    TextContent,
    TextFormat,
    TextLengthUnit,
)
from imagent.gateway.delivery import (
    DeliveryPlanner,
    DeliveryPlanningError,
)
from imagent.gateway.delivery import planning as planning_owner
from imagent.interaction.channels.adapters import NativeTransportChannelAdapter


class DeliveryPlanningFacadeTests(unittest.TestCase):
    def test_root_attribute_and_gateway_facade_use_exact_owner_objects(self) -> None:
        self.assertIs(imagent.delivery_planning, planning_owner)
        self.assertIs(DeliveryPlanner, planning_owner.DeliveryPlanner)
        self.assertIs(DeliveryPlanningError, planning_owner.DeliveryPlanningError)
        self.assertNotIn("imagent.delivery_planning", sys.modules)


class DeliveryPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = DeliveryPlanner()
        self.conversation = ConversationRef("test-channel", "conversation")

    def test_native_markdown_golden_plan_is_stable(self) -> None:
        profile = DeliveryProfile(
            markdown=DeliverySupportLevel.NATIVE,
            reply_references=DeliverySupportLevel.NATIVE,
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
            markdown=DeliverySupportLevel.FALLBACK,
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
            startup_validator=lambda: None,
            native_factory=lambda middleware: _UnusedNative(middleware),
        )
        telegram = NativeTransportChannelAdapter(
            channel_instance_id="telegram-test",
            channel_id="telegram",
            startup_validator=lambda: None,
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
            attachments=DeliverySupportLevel.NATIVE,
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
            attachments=DeliverySupportLevel.NATIVE,
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
            attachments=DeliverySupportLevel.NATIVE,
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
            reply_references=DeliverySupportLevel.NATIVE,
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
            attachments=DeliverySupportLevel.NATIVE,
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
            attachments=DeliverySupportLevel.NATIVE,
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

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from ...interaction.channels.contract import (
    DeliveryProfile,
    DeliverySupportLevel,
    ReplyReferenceScope,
)
from ...interaction.media import AttachmentContent, AttachmentGrouping, LocalPath
from ...interaction.messages import (
    Content,
    ConversationRef,
    OutboundMessage,
    TextContent,
    TextFormat,
    TextLengthUnit,
)


class DeliveryPlanningError(ValueError):
    """The requested content cannot be represented by one Channel."""


@dataclass(frozen=True, slots=True)
class PlannedDeliverySegment:
    segment_index: int
    source_content_indexes: tuple[int, ...]
    message: OutboundMessage


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    source_delivery_id: str
    conversation_ref: ConversationRef
    source_content_count: int
    segments: tuple[PlannedDeliverySegment, ...]


class DeliveryPlanner:
    """Pure capability-driven conversion from one logical message to segments."""

    def plan(
        self,
        message: OutboundMessage,
        profile: DeliveryProfile,
        *,
        max_source_items: int | None = None,
        max_segments: int | None = None,
    ) -> DeliveryPlan:
        if not message.delivery_id:
            raise DeliveryPlanningError("delivery_id cannot be empty")
        if not message.content:
            raise DeliveryPlanningError("outbound content cannot be empty")
        if max_source_items is not None:
            if max_source_items < 1:
                raise DeliveryPlanningError("source item limit must be positive")
            if len(message.content) > max_source_items:
                raise DeliveryPlanningError(
                    f"delivery exceeds source item limit ({max_source_items})"
                )
        if max_segments is not None and max_segments < 1:
            raise DeliveryPlanningError("delivery segment limit must be positive")
        _validate_profile(profile)
        if (
            message.reply_to is not None
            and profile.reply_references is DeliverySupportLevel.UNSUPPORTED
        ):
            raise DeliveryPlanningError("destination Channel does not support reply references")

        segments: list[PlannedDeliverySegment] = []
        pending_attachments: list[tuple[int, AttachmentContent]] = []

        def append_segment(
            content: tuple[Content, ...],
            source_indexes: tuple[int, ...],
        ) -> None:
            if max_segments is not None and len(segments) >= max_segments:
                raise DeliveryPlanningError(f"delivery plan exceeds segment limit ({max_segments})")
            segment_index = len(segments)
            segment_id = derive_segment_delivery_id(
                message.conversation_ref,
                message.delivery_id,
                segment_index,
            )
            metadata = dict(message.metadata)
            metadata.update(
                {
                    "source_delivery_id": message.delivery_id,
                    "segment_index": segment_index,
                }
            )
            segments.append(
                PlannedDeliverySegment(
                    segment_index=segment_index,
                    source_content_indexes=source_indexes,
                    message=OutboundMessage(
                        delivery_id=segment_id,
                        conversation_ref=message.conversation_ref,
                        content=content,
                        created_at=message.created_at,
                        reply_to=(
                            message.reply_to
                            if (
                                segment_index == 0
                                or profile.reply_reference_scope
                                is ReplyReferenceScope.EVERY_SEGMENT
                            )
                            else None
                        ),
                        metadata=metadata,
                    ),
                )
            )

        def flush_attachments() -> None:
            if not pending_attachments:
                return
            remaining = None if max_segments is None else max_segments - len(segments)
            for group in _attachment_groups(
                pending_attachments,
                profile,
                max_groups=remaining,
            ):
                append_segment(
                    tuple(item for _, item in group),
                    tuple(index for index, _ in group),
                )
            pending_attachments.clear()

        # Planning is a complete preflight. No Channel side effect is allowed
        # until every source item and every attachment group is representable.
        for item in message.content:
            if isinstance(item, AttachmentContent):
                _validate_attachment(item, profile)

        for source_index, item in enumerate(message.content):
            if isinstance(item, AttachmentContent):
                pending_attachments.append((source_index, item))
                continue
            if not isinstance(item, TextContent):
                raise DeliveryPlanningError("outbound content type is unsupported")
            flush_attachments()
            text, text_format = _prepare_text(item, profile)
            limit = profile.max_text_length
            if limit is None or _text_length(text, profile.text_length_unit) <= limit:
                chunks = (text,)
            else:
                chunks = split_text(
                    text,
                    limit=limit,
                    unit=profile.text_length_unit,
                    max_chunks=(None if max_segments is None else max_segments - len(segments)),
                )
            for chunk in chunks:
                append_segment((TextContent(chunk, text_format),), (source_index,))
        flush_attachments()

        if not segments:
            raise DeliveryPlanningError("outbound content produced no deliverable segments")
        return DeliveryPlan(
            source_delivery_id=message.delivery_id,
            conversation_ref=message.conversation_ref,
            source_content_count=len(message.content),
            segments=tuple(segments),
        )


def derive_segment_delivery_id(
    conversation_ref: ConversationRef,
    source_delivery_id: str,
    segment_index: int,
) -> str:
    if not source_delivery_id:
        raise DeliveryPlanningError("source_delivery_id cannot be empty")
    if segment_index < 0:
        raise DeliveryPlanningError("segment_index cannot be negative")
    identity = json.dumps(
        [
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
            source_delivery_id,
            segment_index,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:delivery-segment:sha256:{digest}"


def split_text(
    text: str,
    *,
    limit: int,
    unit: TextLengthUnit = TextLengthUnit.CODE_POINTS,
    max_chunks: int | None = None,
) -> tuple[str, ...]:
    """Split on stable soft boundaries without splitting Unicode code points."""

    if limit < 1:
        raise DeliveryPlanningError("text limit must be positive")
    if max_chunks is not None and max_chunks < 1:
        raise DeliveryPlanningError(f"delivery plan exceeds segment limit ({max_chunks})")
    remaining = text.strip()
    if not remaining:
        raise DeliveryPlanningError("text content cannot be empty")
    chunks: list[str] = []
    while _text_length(remaining, unit) > limit:
        if max_chunks is not None and len(chunks) >= max_chunks:
            raise DeliveryPlanningError(f"delivery plan exceeds segment limit ({max_chunks})")
        hard_end = _prefix_within_limit(remaining, limit, unit)
        if hard_end < 1:
            raise DeliveryPlanningError("one text code point exceeds the Channel limit")
        minimum_soft_break = max(1, hard_end // 4)
        window = remaining[:hard_end]
        break_at = max(
            window.rfind("\n\n", minimum_soft_break),
            window.rfind("\n", minimum_soft_break),
            window.rfind(" ", minimum_soft_break),
        )
        if break_at < minimum_soft_break:
            break_at = hard_end
        chunk = remaining[:break_at].rstrip()
        if not chunk:
            chunk = remaining[:hard_end]
            break_at = hard_end
        chunks.append(chunk)
        remaining = remaining[break_at:].lstrip()
    if remaining:
        if max_chunks is not None and len(chunks) >= max_chunks:
            raise DeliveryPlanningError(f"delivery plan exceeds segment limit ({max_chunks})")
        chunks.append(remaining)
    return tuple(chunks)


def markdown_to_plain(text: str) -> str:
    """Deterministic conservative Markdown fallback for plain-text Channels."""

    value = re.sub(r"(?m)^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]?)", "", text)
    value = re.sub(r"(?m)^[ \t]*(```+|~~~+).*$", "", value)
    value = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1 (\2)", value)
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", value)
    value = re.sub(r"(`+)(.*?)\1", r"\2", value)
    value = re.sub(r"(\*\*|__)(.*?)\1", r"\2", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", value)
    value = re.sub(r"(?m)^[ \t]*[-*_]{3,}[ \t]*$", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or text.strip()


def _prepare_text(
    item: TextContent,
    profile: DeliveryProfile,
) -> tuple[str, TextFormat]:
    if not item.text.strip():
        raise DeliveryPlanningError("text content cannot be empty")
    if item.format is TextFormat.PLAIN:
        if profile.plain_text is DeliverySupportLevel.UNSUPPORTED:
            raise DeliveryPlanningError("destination Channel does not support plain text")
        return item.text, TextFormat.PLAIN
    if profile.markdown is DeliverySupportLevel.NATIVE:
        return item.text, TextFormat.MARKDOWN
    if profile.markdown is DeliverySupportLevel.FALLBACK:
        if profile.plain_text is DeliverySupportLevel.UNSUPPORTED:
            raise DeliveryPlanningError("Markdown fallback requires plain-text support")
        return markdown_to_plain(item.text), TextFormat.PLAIN
    raise DeliveryPlanningError("destination Channel does not support Markdown")


def _validate_attachment(
    item: AttachmentContent,
    profile: DeliveryProfile,
) -> None:
    if profile.attachments is DeliverySupportLevel.UNSUPPORTED:
        raise DeliveryPlanningError("destination Channel does not support attachments")
    if item.source.kind not in profile.attachment_sources:
        raise DeliveryPlanningError(
            f"attachment source is unsupported by destination Channel: {item.source.kind.value}"
        )
    if isinstance(item.source, LocalPath) and item.size_bytes is None:
        raise DeliveryPlanningError("LocalPath attachments require a declared size")
    if profile.max_attachment_size is not None and item.size_bytes is None:
        raise DeliveryPlanningError(
            "bounded attachment delivery requires a declared size before native side effects"
        )
    if (
        profile.max_attachment_size is not None
        and item.size_bytes is not None
        and item.size_bytes > profile.max_attachment_size
    ):
        raise DeliveryPlanningError(
            f"attachment {item.attachment_id} exceeds destination Channel size limit"
        )
    if profile.attachment_media_types and not any(
        _media_type_matches(item.media_type, pattern) for pattern in profile.attachment_media_types
    ):
        raise DeliveryPlanningError(
            f"attachment media type is unsupported by destination Channel: {item.media_type}"
        )


def _validate_profile(profile: DeliveryProfile) -> None:
    if profile.max_text_length is not None and profile.max_text_length < 1:
        raise DeliveryPlanningError("destination text limit must be positive")
    if profile.max_attachment_count is not None and profile.max_attachment_count < 1:
        raise DeliveryPlanningError("destination attachment count limit must be positive")
    if profile.max_attachment_size is not None and profile.max_attachment_size < 1:
        raise DeliveryPlanningError("destination attachment size limit must be positive")
    if profile.max_attachment_group_size is not None and profile.max_attachment_group_size < 1:
        raise DeliveryPlanningError("destination attachment group size limit must be positive")
    if len(set(profile.attachment_sources)) != len(profile.attachment_sources):
        raise DeliveryPlanningError("attachment source capabilities must be unique")
    if len(set(profile.attachment_media_types)) != len(profile.attachment_media_types):
        raise DeliveryPlanningError("attachment media types must be unique")


def _attachment_groups(
    attachments: list[tuple[int, AttachmentContent]],
    profile: DeliveryProfile,
    *,
    max_groups: int | None = None,
) -> tuple[tuple[tuple[int, AttachmentContent], ...], ...]:
    if max_groups is not None and max_groups < 1:
        raise DeliveryPlanningError(f"delivery plan exceeds segment limit ({max_groups})")

    size_limit = profile.max_attachment_group_size
    # NONE still represents one native group per attachment. Validate the
    # declared group bound before the early return.
    if profile.attachment_grouping is AttachmentGrouping.NONE:
        groups: list[tuple[tuple[int, AttachmentContent], ...]] = []
        for attachment in attachments:
            item = attachment[1]
            if size_limit is not None and item.size_bytes is None:
                raise DeliveryPlanningError(
                    "attachment group size limits require declared attachment sizes"
                )
            if (
                size_limit is not None
                and item.size_bytes is not None
                and item.size_bytes > size_limit
            ):
                raise DeliveryPlanningError(
                    f"attachment {item.attachment_id} exceeds destination group size limit"
                )
            if max_groups is not None and len(groups) >= max_groups:
                raise DeliveryPlanningError(f"delivery plan exceeds segment limit ({max_groups})")
            groups.append((attachment,))
        return tuple(groups)

    groups_list: list[list[tuple[int, AttachmentContent]]] = []
    current: list[tuple[int, AttachmentContent]] = []
    current_size = 0
    current_family: str | None = None
    count_limit = profile.max_attachment_count
    for attachment in attachments:
        item = attachment[1]
        if size_limit is not None and item.size_bytes is None:
            raise DeliveryPlanningError(
                "attachment group size limits require declared attachment sizes"
            )
        item_size = item.size_bytes or 0
        if size_limit is not None and item_size > size_limit:
            raise DeliveryPlanningError(
                f"attachment {item.attachment_id} exceeds destination group size limit"
            )
        family = item.media_type.partition("/")[0].casefold()
        family_changed = (
            profile.attachment_grouping is AttachmentGrouping.SAME_MEDIA_FAMILY
            and current_family is not None
            and family != current_family
        )
        count_full = count_limit is not None and len(current) >= count_limit
        size_full = (
            size_limit is not None and bool(current) and current_size + item_size > size_limit
        )
        if current and (family_changed or count_full or size_full):
            if max_groups is not None and len(groups_list) >= max_groups:
                raise DeliveryPlanningError(f"delivery plan exceeds segment limit ({max_groups})")
            groups_list.append(current)
            current = []
            current_size = 0
            current_family = None
        current.append(attachment)
        current_size += item_size
        current_family = family
    if current:
        if max_groups is not None and len(groups_list) >= max_groups:
            raise DeliveryPlanningError(f"delivery plan exceeds segment limit ({max_groups})")
        groups_list.append(current)
    return tuple(tuple(group) for group in groups_list)


def _text_length(text: str, unit: TextLengthUnit) -> int:
    if unit is TextLengthUnit.CODE_POINTS:
        return len(text)
    if unit is TextLengthUnit.UTF16_CODE_UNITS:
        return len(text.encode("utf-16-le")) // 2
    if unit is TextLengthUnit.UTF8_BYTES:
        return len(text.encode("utf-8"))
    raise DeliveryPlanningError("text length unit is unsupported")


def _prefix_within_limit(
    text: str,
    limit: int,
    unit: TextLengthUnit,
) -> int:
    low = 0
    high = len(text)
    while low < high:
        midpoint = (low + high + 1) // 2
        if _text_length(text[:midpoint], unit) <= limit:
            low = midpoint
        else:
            high = midpoint - 1
    return low


def _media_type_matches(media_type: str, pattern: str) -> bool:
    normalized = pattern.casefold().strip()
    candidate = media_type.casefold().strip()
    if normalized == "*/*":
        return True
    if normalized.endswith("/*"):
        return candidate.startswith(normalized[:-1])
    return candidate == normalized

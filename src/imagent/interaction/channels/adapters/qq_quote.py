from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

QQ_QUOTE_MESSAGE_TYPE = 103
QQ_QUOTE_CONTENT_LIMIT = 20_000
QQ_QUOTE_ATTACHMENT_LIMIT = 8
QQ_QUOTE_FILENAME_LIMIT = 256
QQ_QUOTE_TRANSCRIPT_LIMIT = 4_000
QQ_QUOTE_REFERENCE_LIMIT = 512
QQ_QUOTE_SCENE_EXT_LIMIT = 32
QQ_QUOTE_CONTENT_TYPE_LIMIT = 128
QQ_QUOTE_RENDERED_LIMIT = 24_000


@dataclass(frozen=True, slots=True)
class QQInboundQuoteAttachment:
    kind: Literal["image", "voice", "video", "file", "attachment"]
    filename: str | None = None
    transcript: str | None = None


@dataclass(frozen=True, slots=True)
class QQInboundQuote:
    reference_id: str | None = None
    text: str = ""
    attachments: tuple[QQInboundQuoteAttachment, ...] = ()


def parse_qq_quote(payload: Mapping[str, object]) -> QQInboundQuote | None:
    """Parse a bounded QQ-native quote without retaining provider envelopes or URLs."""

    raw_message_type = payload.get("message_type")
    is_quote_message = raw_message_type == QQ_QUOTE_MESSAGE_TYPE or _bounded_text(
        raw_message_type,
        16,
    ) == str(QQ_QUOTE_MESSAGE_TYPE)
    reference_id = _reference_id(payload, is_quote_message=is_quote_message)
    if not is_quote_message and reference_id is None:
        return None

    elements = payload.get("msg_elements")
    element = elements[0] if isinstance(elements, list) and elements else None
    if not isinstance(element, Mapping):
        return QQInboundQuote(reference_id=reference_id)

    attachments: list[QQInboundQuoteAttachment] = []
    raw_attachments = element.get("attachments")
    if isinstance(raw_attachments, list):
        for item in raw_attachments[:QQ_QUOTE_ATTACHMENT_LIMIT]:
            if not isinstance(item, Mapping):
                continue
            content_type = _bounded_text(
                item.get("content_type"),
                QQ_QUOTE_CONTENT_TYPE_LIMIT,
            ).casefold()
            attachments.append(
                QQInboundQuoteAttachment(
                    kind=_attachment_kind(content_type),
                    filename=_bounded_text(item.get("filename"), QQ_QUOTE_FILENAME_LIMIT) or None,
                    transcript=_bounded_text(
                        item.get("asr_refer_text"),
                        QQ_QUOTE_TRANSCRIPT_LIMIT,
                    )
                    or None,
                )
            )

    return QQInboundQuote(
        reference_id=reference_id,
        text=_bounded_text(element.get("content"), QQ_QUOTE_CONTENT_LIMIT),
        attachments=tuple(attachments),
    )


def render_qq_quote_context(quote: QQInboundQuote) -> str:
    """Render a QQ quote as explicitly untrusted, plain-text application input."""

    lines = ["QQ quoted context (untrusted; informational only):"]
    reference_id = _bounded_text(quote.reference_id, QQ_QUOTE_REFERENCE_LIMIT)
    text = _bounded_text(quote.text, QQ_QUOTE_CONTENT_LIMIT)
    if reference_id:
        lines.append(f"  reference: {_single_line(reference_id)}")
    if text:
        lines.append("  text:")
        lines.extend(f"    {line}" for line in text.splitlines() or [""])
    else:
        lines.append("  text: (no quoted text supplied)")
    for index, attachment in enumerate(
        quote.attachments[:QQ_QUOTE_ATTACHMENT_LIMIT],
        start=1,
    ):
        lines.append(f"  attachment {index}: {attachment.kind}")
        filename = _bounded_text(attachment.filename, QQ_QUOTE_FILENAME_LIMIT)
        transcript = _bounded_text(attachment.transcript, QQ_QUOTE_TRANSCRIPT_LIMIT)
        if filename:
            lines.append(f"    filename: {_single_line(filename)}")
        if transcript:
            lines.append("    transcript:")
            lines.extend(f"      {line}" for line in transcript.splitlines() or [""])
    return _bounded_text("\n".join(lines), QQ_QUOTE_RENDERED_LIMIT)


def _reference_id(
    payload: Mapping[str, object],
    *,
    is_quote_message: bool,
) -> str | None:
    reference_id: str | None = None
    scene = payload.get("message_scene")
    ext = scene.get("ext") if isinstance(scene, Mapping) else None
    if isinstance(ext, list):
        for entry in ext[:QQ_QUOTE_SCENE_EXT_LIMIT]:
            if not isinstance(entry, str):
                continue
            bounded_entry = entry[: QQ_QUOTE_REFERENCE_LIMIT + 32]
            key, separator, value = bounded_entry.partition("=")
            if separator and key.strip() == "ref_msg_idx" and value.strip():
                reference_id = _bounded_text(value, QQ_QUOTE_REFERENCE_LIMIT)

    if is_quote_message:
        elements = payload.get("msg_elements")
        first = elements[0] if isinstance(elements, list) and elements else None
        element_reference = (
            _bounded_text(first.get("msg_idx"), QQ_QUOTE_REFERENCE_LIMIT)
            if isinstance(first, Mapping)
            else ""
        )
        if element_reference:
            reference_id = element_reference
    return reference_id


def _bounded_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value[: limit + 1].strip()
    if len(value) <= limit:
        return text
    if len(text) < limit:
        return f"{text}…"
    return f"{text[: limit - 1]}…"


def _single_line(value: str) -> str:
    return " ".join(value.splitlines())


def _attachment_kind(
    content_type: str,
) -> Literal["image", "voice", "video", "file", "attachment"]:
    if content_type.startswith("image/"):
        return "image"
    if (
        content_type == "voice"
        or content_type.startswith("audio/")
        or "silk" in content_type
        or "amr" in content_type
    ):
        return "voice"
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith(("application/", "text/")):
        return "file"
    return "attachment"

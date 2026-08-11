from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from ..media import (
    AttachmentContent,
    LocalPath,
    configure_shared_filesystem_root,
    read_local_attachment,
)
from ..messages import (
    OutboundMessage as PublicOutboundMessage,
)
from ..messages import (
    TextContent,
    TextFormat,
)
from .contract import (
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryReceipt,
    DeliveryReceiptStatus,
)

_NATIVE_OWNED_METADATA_KEYS = frozenset(
    {
        "artifact_failures",
        "artifact_receipts",
        "delivery_id",
        "message_id",
        "qq_reply_identity_pinned",
        "qq_reply_to_message_id",
        "reply_to_message_id",
        "reply_to_seen_at",
    }
)
MAX_NATIVE_ARTIFACT_BYTES = 64 * 1024 * 1024


class _RouteContext(Protocol):
    @property
    def admitted_user_id(self) -> str: ...

    @property
    def last_inbound_message_id(self) -> str: ...


class _AccessPolicy(Protocol):
    def allows(self, *, user_id: str, conversation_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class _OutboundAccessDecision:
    allowed: bool
    user_id: str


@dataclass(frozen=True, slots=True)
class OutboundArtifact:
    kind: Literal["image", "file"]
    local_path: str
    content_type: str
    filename: str
    size_bytes: int
    sha256: str = ""
    attachment_id: str = ""


@dataclass(frozen=True, slots=True)
class NativeDeliveryResult:
    native_message_ids: tuple[str, ...] = ()


class PermanentArtifactDeliveryError(RuntimeError):
    """An artifact cannot be delivered and retrying the same bytes will not help."""


@dataclass(frozen=True, slots=True)
class ArtifactDeliveryReceipt:
    platform_message_id: str = ""
    delivery_identity: str = ""


def route_context_user_id(context: _RouteContext | None) -> str | None:
    user_id = context.admitted_user_id.strip() if context is not None else ""
    return user_id or None


def route_context_message_id(context: _RouteContext | None) -> str | None:
    message_id = context.last_inbound_message_id.strip() if context is not None else ""
    return message_id or None


def resolve_outbound_user_id(
    *,
    route_user_id: str | None,
    conversation_user_id: str | None,
) -> str:
    return str(route_user_id or conversation_user_id or "")


def ensure_outbound_allowed(
    *,
    channel_id: str,
    message: OutboundMessage,
    access_policy: _AccessPolicy,
    route_user_id: str | None,
    conversation_user_id: str | None,
) -> _OutboundAccessDecision:
    user_id = resolve_outbound_user_id(
        route_user_id=route_user_id,
        conversation_user_id=conversation_user_id,
    )
    return _OutboundAccessDecision(
        allowed=access_policy.allows(
            user_id=user_id,
            conversation_id=message.conversation_id,
        ),
        user_id=user_id,
    )


def _to_native_artifact(attachment: AttachmentContent) -> OutboundArtifact:
    source = attachment.source
    if not isinstance(source, LocalPath):
        raise ValueError("SDK-owned native Channels require outbound attachments to use LocalPath")
    if (
        not isinstance(attachment.size_bytes, int)
        or isinstance(attachment.size_bytes, bool)
        or attachment.size_bytes < 0
    ):
        raise ValueError("outbound LocalPath attachments require a non-negative size_bytes")
    path = Path(source.path)
    filename = str(attachment.filename or path.name).strip()
    if not filename:
        raise ValueError("outbound attachments require a filename")
    declared_kind = str(attachment.metadata.get("kind") or "").casefold()
    kind = (
        "image"
        if declared_kind == "image" or attachment.media_type.startswith("image/")
        else "file"
    )
    return OutboundArtifact(
        kind=kind,
        local_path=source.path,
        content_type=attachment.media_type,
        filename=filename,
        size_bytes=attachment.size_bytes,
        sha256=str(attachment.metadata.get("sha256") or ""),
        attachment_id=attachment.attachment_id,
    )


def _artifact_item_receipts(
    metadata: dict[str, object],
    *,
    item_indexes: dict[str, int],
) -> tuple[DeliveryItemReceipt, ...]:
    raw_receipts = metadata.get("artifact_receipts")
    if not isinstance(raw_receipts, list):
        return ()
    receipts: list[DeliveryItemReceipt] = []
    for raw in raw_receipts:
        if not isinstance(raw, dict):
            continue
        attachment_id = str(raw.get("attachment_id") or "")
        content_index = item_indexes.get(attachment_id)
        if content_index is None:
            continue
        delivered = raw.get("status") == "delivered"
        receipts.append(
            DeliveryItemReceipt(
                content_index=content_index,
                attachment_id=attachment_id,
                status=(DeliveryItemStatus.ACCEPTED if delivered else DeliveryItemStatus.REJECTED),
                native_message_id=(str(raw.get("platform_message_id") or "") or None),
                detail=(
                    None if delivered else str(raw.get("error") or "attachment delivery failed")
                ),
            )
        )
    return tuple(sorted(receipts, key=lambda receipt: receipt.content_index))


@dataclass(slots=True)
class OutboundMessage:
    channel_id: str
    conversation_id: str
    message_type: str
    text: str
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[OutboundArtifact] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.artifacts = [
            item if isinstance(item, OutboundArtifact) else OutboundArtifact(**item)
            for item in self.artifacts
        ]


def _to_native_outbound(
    *,
    channel_id: str,
    message: PublicOutboundMessage,
) -> OutboundMessage:
    text_parts = [item.text for item in message.content if isinstance(item, TextContent)]
    artifacts = [
        _to_native_artifact(item) for item in message.content if isinstance(item, AttachmentContent)
    ]
    markdown = any(
        isinstance(item, TextContent) and item.format is TextFormat.MARKDOWN
        for item in message.content
    )
    metadata = {
        key: value
        for key, value in message.metadata.items()
        if key not in _NATIVE_OWNED_METADATA_KEYS
    }
    metadata.update(
        {
            "delivery_id": message.delivery_id,
            "reply_to_message_id": message.reply_to,
        }
    )
    return OutboundMessage(
        channel_id=channel_id,
        conversation_id=message.conversation_ref.native_conversation_id,
        message_type="markdown" if markdown else "text",
        text="\n".join(text_parts),
        metadata=metadata,
        artifacts=artifacts,
    )


def _native_delivery_receipt(
    *,
    result: object,
    native_message: OutboundMessage,
    message: PublicOutboundMessage,
) -> DeliveryReceipt:
    native_message_ids = (
        result.native_message_ids if isinstance(result, NativeDeliveryResult) else ()
    )
    native_message_id = native_message_ids[0] if len(native_message_ids) == 1 else None
    detail = (
        "platform call succeeded; native message ID was not returned"
        if not native_message_ids
        else (
            "platform accepted one native message"
            if native_message_id is not None
            else f"platform accepted {len(native_message_ids)} native messages"
        )
    )
    item_indexes = {
        item.attachment_id: index
        for index, item in enumerate(message.content)
        if isinstance(item, AttachmentContent)
    }
    item_receipts = _artifact_item_receipts(
        native_message.metadata,
        item_indexes=item_indexes,
    )
    return DeliveryReceipt(
        status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
        native_message_id=native_message_id,
        detail=detail,
        items=item_receipts,
    )


def split_text(text: str, *, limit: int) -> list[str]:
    """Split text for an IM platform without splitting Unicode code points."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    remaining = text.strip()
    if not remaining:
        return []
    chunks: list[str] = []
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        minimum_soft_break = max(1, limit // 2)
        break_at = max(
            window.rfind("\n\n", minimum_soft_break, limit + 1),
            window.rfind("\n", minimum_soft_break, limit + 1),
            window.rfind(" ", minimum_soft_break, limit + 1),
        )
        if break_at < minimum_soft_break:
            break_at = limit
        chunk = remaining[:break_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            break_at = limit
        chunks.append(chunk)
        remaining = remaining[break_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def deliver_artifact_batch(
    message: OutboundMessage,
    send_one: Callable[
        [OutboundArtifact],
        Awaitable[ArtifactDeliveryReceipt | None],
    ],
) -> None:
    """Deliver artifacts in order for one native-message attempt.

    A cancellation or unclassified failure retains the failed artifact and the
    unattempted suffix on ``message.artifacts`` as truthful in-memory evidence
    for the caller. This helper does not own a durable outbox or authorize
    replay. Known permanent failures become a text notice and do not block the
    remaining artifacts.
    """

    failures: list[str] = []
    original_artifacts = list(message.artifacts)
    for index, artifact in enumerate(original_artifacts):
        try:
            receipt = await send_one(artifact)
        except PermanentArtifactDeliveryError as exc:
            error = str(exc)
            failures.append(f"{artifact.filename}: {error}")
            record_artifact_failure(message, artifact, error=error)
        except asyncio.CancelledError:
            append_artifact_failures(message, failures)
            message.artifacts = original_artifacts[index:]
            raise
        except Exception:
            append_artifact_failures(message, failures)
            message.artifacts = original_artifacts[index:]
            raise
        else:
            receipt = receipt or ArtifactDeliveryReceipt()
            record_artifact_delivery(
                message,
                artifact,
                platform_message_id=receipt.platform_message_id,
                delivery_identity=receipt.delivery_identity,
            )
        message.artifacts = original_artifacts[index + 1 :]
    message.artifacts = []
    append_artifact_failures(message, failures)


async def read_managed_artifact(
    artifact: OutboundArtifact,
    *,
    root: str | Path,
) -> tuple[Path, bytes]:
    """Acquire one verified artifact through the caller-managed trusted root."""

    if artifact.sha256 and (
        len(artifact.sha256) != 64
        or any(character not in "0123456789abcdef" for character in artifact.sha256)
    ):
        raise PermanentArtifactDeliveryError("artifact digest metadata is malformed")
    try:
        managed_root = configure_shared_filesystem_root(root)
        content = await asyncio.to_thread(
            read_local_attachment,
            LocalPath(artifact.local_path),
            shared_filesystem_root=managed_root,
            consumer="native Channel",
            expected_size=artifact.size_bytes,
            max_bytes=MAX_NATIVE_ARTIFACT_BYTES,
        )
    except (NotImplementedError, ValueError) as exc:
        detail = str(exc)
        if "regular file" in detail:
            message = "artifact is no longer a regular file"
        elif "size" in detail or "byte bound" in detail:
            message = "artifact changed after it was staged"
        else:
            message = "artifact is outside the trusted root or no longer exists"
        raise PermanentArtifactDeliveryError(message) from exc
    if artifact.sha256 and hashlib.sha256(content).hexdigest() != artifact.sha256:
        raise PermanentArtifactDeliveryError("artifact changed after it was staged")
    return Path(artifact.local_path), content


def append_artifact_failures(message: OutboundMessage, failures: list[str]) -> None:
    if not failures:
        return
    notice = "Attachment delivery unavailable:\n" + "\n".join(
        f"- {failure}" for failure in failures
    )
    if notice not in message.text:
        message.text = "\n\n".join(part for part in (message.text, notice) if part)
    recorded = message.metadata.setdefault("artifact_failures", [])
    if isinstance(recorded, list):
        for failure in failures:
            if failure not in recorded:
                recorded.append(failure)


def stable_artifact_identity(
    message: OutboundMessage,
    artifact: OutboundArtifact,
) -> str | None:
    delivery_id = str(message.metadata.get("delivery_id") or "").strip()
    if not delivery_id:
        return None
    digest = hashlib.sha256(
        (
            f"{delivery_id}\0{artifact.attachment_id or artifact.sha256 or artifact.local_path}"
        ).encode()
    ).hexdigest()
    return digest


def record_artifact_delivery(
    message: OutboundMessage,
    artifact: OutboundArtifact,
    *,
    platform_message_id: str = "",
    delivery_identity: str = "",
) -> None:
    receipts = message.metadata.setdefault("artifact_receipts", [])
    if not isinstance(receipts, list):
        return
    receipts.append(
        {
            "attachment_id": artifact.attachment_id,
            "filename": artifact.filename,
            "sha256": artifact.sha256,
            "local_path": artifact.local_path,
            "status": "delivered",
            "platform_message_id": platform_message_id,
            "delivery_identity": delivery_identity,
        }
    )


def record_artifact_failure(
    message: OutboundMessage,
    artifact: OutboundArtifact,
    *,
    error: str,
) -> None:
    receipts = message.metadata.setdefault("artifact_receipts", [])
    if not isinstance(receipts, list):
        return
    receipts.append(
        {
            "attachment_id": artifact.attachment_id,
            "filename": artifact.filename,
            "sha256": artifact.sha256,
            "local_path": artifact.local_path,
            "status": "failed",
            "error": error,
            "platform_message_id": "",
            "delivery_identity": "",
        }
    )


def delivered_artifact_message_ids(message: OutboundMessage) -> tuple[str, ...]:
    receipts = message.metadata.get("artifact_receipts")
    if not isinstance(receipts, list):
        return ()
    message_ids: list[str] = []
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("status") != "delivered":
            continue
        message_id = str(receipt.get("platform_message_id") or "").strip()
        if message_id and message_id not in message_ids:
            message_ids.append(message_id)
    return tuple(message_ids)

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..adapters import InboundAdmissionHandler, MessageHandler, OperationHandler
from ..contracts import (
    AttachmentContent,
    AttachmentSourceKind,
    ChannelCapabilities,
    ConversationRef,
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    InboundMessage,
    LocalPath,
    OutboundMessage,
    SupportLevel,
    TextContent,
    TextFormat,
)
from .native.base import ChannelRouteContext
from .native.models import (
    NativeDeliveryResult,
)
from .native.models import (
    OutboundArtifact as NativeOutboundArtifact,
)
from .native.models import (
    OutboundMessage as NativeOutboundMessage,
)


class NativeChannel(Protocol):
    channel_id: str
    middleware: object

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send_message(self, message) -> NativeDeliveryResult: ...


NativeFactory = Callable[[object], NativeChannel]

_CHANNEL_CAPABILITIES = {
    "qq": ChannelCapabilities(
        markdown=SupportLevel.NATIVE,
        attachments=SupportLevel.NATIVE,
        attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
        reply_references=SupportLevel.NATIVE,
        max_text_length=3_500,
    ),
    "telegram": ChannelCapabilities(
        markdown=SupportLevel.FALLBACK,
        attachments=SupportLevel.NATIVE,
        attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
        reply_references=SupportLevel.NATIVE,
        # Match the SDK-owned native adapter's defensive limit so one
        # planned segment remains one native send unit.
        max_text_length=4_000,
        native_threads_or_topics=SupportLevel.NATIVE,
    ),
    "feishu": ChannelCapabilities(
        markdown=SupportLevel.FALLBACK,
        attachments=SupportLevel.NATIVE,
        attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
        reply_references=SupportLevel.NATIVE,
        max_text_length=3_500,
        native_threads_or_topics=SupportLevel.NATIVE,
    ),
    "weixin": ChannelCapabilities(
        markdown=SupportLevel.FALLBACK,
        attachments=SupportLevel.NATIVE,
        attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
        reply_references=SupportLevel.NATIVE,
        max_text_length=4_000,
    ),
}

_TRANSIENT_ROUTE_LIMIT = 4_096
_TRANSIENT_ADMISSION_LIMIT = 16_384
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


class NativeTransportChannelAdapter:
    """Adapt one SDK-owned native transport to the common Channel Port."""

    def __init__(
        self,
        *,
        channel_instance_id: str,
        channel_id: str,
        native_factory: NativeFactory,
    ) -> None:
        if channel_id not in _CHANNEL_CAPABILITIES:
            raise ValueError(f"unsupported native channel: {channel_id}")
        self._channel_instance_id = channel_instance_id
        self._channel_id = channel_id
        self._native_factory = native_factory
        self._native: NativeChannel | None = None

    @property
    def channel_instance_id(self) -> str:
        return self._channel_instance_id

    @property
    def capabilities(self) -> ChannelCapabilities:
        return _CHANNEL_CAPABILITIES[self._channel_id]

    async def start(
        self,
        on_message: MessageHandler,
        on_operation: OperationHandler,
        on_admission: InboundAdmissionHandler | None = None,
    ) -> None:
        if self._native is not None:
            raise RuntimeError("channel is already started")
        middleware = _InboundMiddleware(
            channel_instance_id=self._channel_instance_id,
            on_message=on_message,
            on_operation=on_operation,
            on_admission=on_admission,
        )
        native = self._native_factory(middleware)
        self._native = native
        try:
            await native.start()
        except BaseException:
            self._native = None
            with contextlib.suppress(Exception):
                await native.stop()
            raise

    async def stop(self) -> None:
        native = self._native
        self._native = None
        if native is not None:
            await native.stop()

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        native = self._native
        if native is None:
            raise RuntimeError("channel is not started")
        if message.conversation_ref.channel_instance_id != self._channel_instance_id:
            raise ValueError("message belongs to a different channel instance")
        if getattr(native, "enabled", True) is False:
            raise RuntimeError(f"{self._channel_id} Channel delivery is disabled for this instance")
        native_message = _to_native_outbound(
            channel_id=self._channel_id,
            message=message,
        )
        if not native_message.text.strip() and not native_message.artifacts:
            raise ValueError("outbound messages require text or at least one attachment")
        result = await native.send_message(native_message)
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


def channel_from_config(
    channel_id: str,
    *,
    config: dict[str, object],
    channel_instance_id: str | None = None,
) -> NativeTransportChannelAdapter:
    """Construct one SDK-owned native Channel transport from adapter values."""

    if channel_id == "qq":
        from .native.qq import QQChannelAdapter as NativeAdapter
    elif channel_id == "telegram":
        from .native.telegram import TelegramChannelAdapter as NativeAdapter
    elif channel_id == "feishu":
        from .native.feishu import FeishuChannelAdapter as NativeAdapter
    elif channel_id == "weixin":
        from .native.weixin import WeixinChannelAdapter as NativeAdapter
    else:
        raise ValueError(f"unsupported native channel: {channel_id}")
    resolved_config = dict(config)
    if channel_id == "qq":
        resolved_config.setdefault("markdown_enabled", True)
    return NativeTransportChannelAdapter(
        channel_instance_id=channel_instance_id or channel_id,
        channel_id=channel_id,
        native_factory=lambda middleware: NativeAdapter.from_config(
            config=resolved_config,
            middleware=middleware,
        ),
    )


class _InboundMiddleware:
    def __init__(
        self,
        *,
        channel_instance_id: str,
        on_message: MessageHandler,
        on_operation: OperationHandler,
        on_admission: InboundAdmissionHandler | None,
    ) -> None:
        self._channel_instance_id = channel_instance_id
        self._on_message = on_message
        self._on_operation = on_operation
        self._on_admission = on_admission
        self._routes: dict[tuple[str, str], object] = {}
        self._admitted_inbound: set[tuple[str, str, str]] = set()
        self._admission_lock = asyncio.Lock()

    async def handle_inbound(
        self,
        _adapter,
        inbound,
        *,
        reply_to_message_id: str | None = None,
        prepare_inbound=None,
        pending_attachment_count: int = 0,
        **_options,
    ) -> None:
        del pending_attachment_count
        admission_key = (
            str(inbound.channel_id),
            str(inbound.conversation_id),
            str(inbound.message_id),
        )
        async with self._admission_lock:
            if admission_key in self._admitted_inbound:
                return
            self._admitted_inbound.add(admission_key)
            while len(self._admitted_inbound) > _TRANSIENT_ADMISSION_LIMIT:
                self._admitted_inbound.pop()
        admission = None
        transferred = False
        try:
            if self._on_admission is not None:
                admission = await self._on_admission(
                    ConversationRef(
                        channel_instance_id=self._channel_instance_id,
                        native_conversation_id=str(inbound.conversation_id),
                    ),
                    str(inbound.message_id),
                )
                if admission is None:
                    async with self._admission_lock:
                        self._admitted_inbound.discard(admission_key)
                    return
            if prepare_inbound is not None:
                prepared = prepare_inbound(inbound)
                inbound = await prepared if inspect.isawaitable(prepared) else prepared
            message = self._normalize_inbound(
                inbound,
                reply_to_message_id=reply_to_message_id,
            )
            if admission is None:
                await self._on_message(message)
            else:
                transferred = True
                await admission.deliver(message)
        except BaseException as error:
            if admission is not None and not transferred:
                try:
                    await admission.release()
                except BaseException as release_error:
                    raise release_error from error
            async with self._admission_lock:
                self._admitted_inbound.discard(admission_key)
            raise

    def _normalize_inbound(
        self,
        inbound,
        *,
        reply_to_message_id: str | None,
    ) -> InboundMessage:
        route_key = (str(inbound.channel_id), str(inbound.conversation_id))
        self._routes.pop(route_key, None)
        self._routes[route_key] = ChannelRouteContext(
            admitted_user_id=str(inbound.user_id),
            last_inbound_message_id=str(inbound.message_id),
            last_inbound_seen_at=time.time(),
        )
        while len(self._routes) > _TRANSIENT_ROUTE_LIMIT:
            del self._routes[next(iter(self._routes))]
        content: list[TextContent | AttachmentContent] = []
        if str(inbound.text or ""):
            content.append(TextContent(str(inbound.text), TextFormat.PLAIN))
        for index, attachment in enumerate(getattr(inbound, "attachments", ())):
            content.append(
                AttachmentContent(
                    attachment_id=(
                        str(getattr(attachment, "source_message_id", "") or "")
                        or f"{inbound.message_id}:attachment:{index}"
                    ),
                    media_type=str(attachment.content_type),
                    filename=str(getattr(attachment, "filename", "") or "") or None,
                    size_bytes=int(attachment.size_bytes),
                    source=LocalPath(str(attachment.local_path)),
                    metadata={
                        "kind": str(attachment.kind),
                    },
                )
            )
        return InboundMessage(
            message_id=str(inbound.message_id),
            conversation_ref=ConversationRef(
                channel_instance_id=self._channel_instance_id,
                native_conversation_id=str(inbound.conversation_id),
            ),
            sender=str(inbound.user_id),
            content=tuple(content),
            created_at=_parse_datetime(getattr(inbound, "sent_at", None)),
            reply_to=(
                str(reply_to_message_id)
                if reply_to_message_id is not None
                else getattr(inbound, "reply_to_message_id", None)
            ),
            metadata={
                "channel_id": str(inbound.channel_id),
                "input_error": getattr(inbound, "input_error", None),
                "trace_id": getattr(inbound, "trace_id", None),
            },
        )

    def get_route_context(self, channel_id: str, conversation_id: str):
        return self._routes.get(
            (channel_id, conversation_id),
            ChannelRouteContext(),
        )


def _to_native_outbound(*, channel_id: str, message: OutboundMessage):
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
    return NativeOutboundMessage(
        channel_id=channel_id,
        conversation_id=message.conversation_ref.native_conversation_id,
        message_type="markdown" if markdown else "text",
        text="\n".join(text_parts),
        metadata=metadata,
        artifacts=artifacts,
    )


def _to_native_artifact(attachment: AttachmentContent) -> NativeOutboundArtifact:
    source = attachment.source
    if not isinstance(source, LocalPath):
        raise ValueError("SDK-owned native Channels require outbound attachments to use LocalPath")
    if attachment.size_bytes is None or attachment.size_bytes < 0:
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
    return NativeOutboundArtifact(
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


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)

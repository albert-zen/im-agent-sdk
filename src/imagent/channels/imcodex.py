from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import import_module
from typing import Protocol

from ..adapters import MessageHandler, OperationHandler
from ..contracts import (
    AttachmentContent,
    ChannelCapabilities,
    ConversationRef,
    DeliveryReceipt,
    InboundMessage,
    LocalPath,
    OutboundMessage,
    SupportLevel,
    TextContent,
    TextFormat,
)


class NativeChannel(Protocol):
    channel_id: str
    middleware: object

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send_message(self, message) -> None: ...


NativeFactory = Callable[[object], NativeChannel]

_CHANNEL_CAPABILITIES = {
    "qq": ChannelCapabilities(
        markdown=SupportLevel.NATIVE,
        attachments=SupportLevel.NATIVE,
        reply_references=SupportLevel.NATIVE,
        max_text_length=3_500,
    ),
    "telegram": ChannelCapabilities(
        markdown=SupportLevel.FALLBACK,
        attachments=SupportLevel.NATIVE,
        reply_references=SupportLevel.NATIVE,
        native_threads_or_topics=SupportLevel.NATIVE,
        max_text_length=4_096,
    ),
    "feishu": ChannelCapabilities(
        markdown=SupportLevel.FALLBACK,
        attachments=SupportLevel.NATIVE,
        reply_references=SupportLevel.NATIVE,
        native_threads_or_topics=SupportLevel.NATIVE,
        max_text_length=4_000,
    ),
    "weixin": ChannelCapabilities(
        markdown=SupportLevel.FALLBACK,
        attachments=SupportLevel.NATIVE,
        reply_references=SupportLevel.NATIVE,
    ),
}


class ImcodexChannelAdapter:
    """Deep adapter over the proven IMCodex channel implementations."""

    def __init__(
        self,
        *,
        channel_instance_id: str,
        channel_id: str,
        native_factory: NativeFactory,
    ) -> None:
        if channel_id not in _CHANNEL_CAPABILITIES:
            raise ValueError(f"unsupported IMCodex channel: {channel_id}")
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
    ) -> None:
        if self._native is not None:
            raise RuntimeError("channel is already started")
        middleware = _InboundMiddleware(
            channel_instance_id=self._channel_instance_id,
            on_message=on_message,
            on_operation=on_operation,
        )
        native = self._native_factory(middleware)
        self._native = native
        await native.start()

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
        native_message = _to_native_outbound(
            channel_id=self._channel_id,
            message=message,
        )
        await native.send_message(native_message)
        return DeliveryReceipt(status="sent")


def imcodex_channel(
    channel_id: str,
    *,
    config: dict[str, object],
    channel_instance_id: str | None = None,
) -> ImcodexChannelAdapter:
    """Load any proven IMCodex channel without importing its bridge/runtime."""

    try:
        channels = import_module("imcodex.channels")
    except ImportError as error:
        raise RuntimeError(
            "Install im-agent-sdk[imcodex] to use IMCodex channel adapters"
        ) from error
    adapter_types = {
        "qq": channels.QQChannelAdapter,
        "telegram": channels.TelegramChannelAdapter,
        "feishu": channels.FeishuChannelAdapter,
        "weixin": channels.WeixinChannelAdapter,
    }
    adapter_type = adapter_types.get(channel_id)
    if adapter_type is None:
        raise ValueError(f"unsupported IMCodex channel: {channel_id}")
    resolved_config = dict(config)
    if channel_id == "qq":
        resolved_config.setdefault("markdown_enabled", True)
    return ImcodexChannelAdapter(
        channel_instance_id=channel_instance_id or channel_id,
        channel_id=channel_id,
        native_factory=lambda middleware: adapter_type.from_config(
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
    ) -> None:
        self._channel_instance_id = channel_instance_id
        self._on_message = on_message
        self._on_operation = on_operation
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
        try:
            if prepare_inbound is not None:
                prepared = prepare_inbound(inbound)
                inbound = await prepared if inspect.isawaitable(prepared) else prepared
            await self._deliver_inbound(
                inbound,
                reply_to_message_id=reply_to_message_id,
            )
        except BaseException:
            async with self._admission_lock:
                self._admitted_inbound.discard(admission_key)
            raise

    async def _deliver_inbound(
        self,
        inbound,
        *,
        reply_to_message_id: str | None,
    ) -> None:
        try:
            ChannelRouteContext = import_module("imcodex.channels.base").ChannelRouteContext
        except ImportError:
            ChannelRouteContext = None
        if ChannelRouteContext is not None:
            self._routes[(str(inbound.channel_id), str(inbound.conversation_id))] = (
                ChannelRouteContext(
                    admitted_user_id=str(inbound.user_id),
                    last_inbound_message_id=str(inbound.message_id),
                    last_inbound_seen_at=time.time(),
                )
            )
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
        message = InboundMessage(
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
        await self._on_message(message)

    def get_route_context(self, channel_id: str, conversation_id: str):
        try:
            ChannelRouteContext = import_module("imcodex.channels.base").ChannelRouteContext
        except ImportError:
            return None
        return self._routes.get(
            (channel_id, conversation_id),
            ChannelRouteContext(),
        )


def _to_native_outbound(*, channel_id: str, message: OutboundMessage):
    text_parts = [item.text for item in message.content if isinstance(item, TextContent)]
    markdown = any(
        isinstance(item, TextContent) and item.format is TextFormat.MARKDOWN
        for item in message.content
    )
    try:
        OutboundMessage = import_module("imcodex.models").OutboundMessage
    except ImportError:
        OutboundMessage = _CompatibleOutboundMessage
    return OutboundMessage(
        channel_id=channel_id,
        conversation_id=message.conversation_ref.native_conversation_id,
        message_type="markdown" if markdown else "text",
        text="\n".join(text_parts),
        metadata={
            **dict(message.metadata),
            "delivery_id": message.delivery_id,
            "reply_to_message_id": message.reply_to,
        },
    )


class _CompatibleOutboundMessage:
    def __init__(
        self,
        *,
        channel_id: str,
        conversation_id: str,
        message_type: str,
        text: str,
        metadata: dict[str, object],
    ) -> None:
        self.channel_id = channel_id
        self.conversation_id = conversation_id
        self.message_type = message_type
        self.text = text
        self.metadata = metadata
        self.request_id = None
        self.artifacts = []


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)

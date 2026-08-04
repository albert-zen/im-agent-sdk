from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from ....contracts import (
    ChannelCapabilities,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySupportLevel,
)
from ...media import AttachmentContent, AttachmentSourceKind, LocalPath
from ...messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
    TextFormat,
)
from .. import InboundAdmissionHandler, MessageHandler
from .. import outbound_delivery as _outbound_delivery
from ..outbound_delivery import (
    NativeDeliveryResult,
)
from .base import ChannelRouteContext
from .diagnostics import NativeChannelDiagnosticSnapshot, NativeConnectionDiagnosticSnapshot


class NativeChannel(Protocol):
    channel_id: str
    middleware: object

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send_message(self, message) -> NativeDeliveryResult: ...


NativeFactory = Callable[[object], NativeChannel]
StartupValidator = Callable[[], None]

_CHANNEL_CAPABILITIES = {
    "qq": ChannelCapabilities(
        markdown=DeliverySupportLevel.NATIVE,
        attachments=DeliverySupportLevel.NATIVE,
        attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
        reply_references=DeliverySupportLevel.NATIVE,
        max_text_length=3_500,
    ),
    "telegram": ChannelCapabilities(
        markdown=DeliverySupportLevel.FALLBACK,
        attachments=DeliverySupportLevel.NATIVE,
        attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
        reply_references=DeliverySupportLevel.NATIVE,
        # Match the SDK-owned native adapter's defensive limit so one
        # planned segment remains one native send unit.
        max_text_length=4_000,
        native_threads_or_topics=DeliverySupportLevel.NATIVE,
    ),
    "feishu": ChannelCapabilities(
        markdown=DeliverySupportLevel.FALLBACK,
        attachments=DeliverySupportLevel.NATIVE,
        attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
        reply_references=DeliverySupportLevel.NATIVE,
        max_text_length=3_500,
        native_threads_or_topics=DeliverySupportLevel.NATIVE,
    ),
    "weixin": ChannelCapabilities(
        markdown=DeliverySupportLevel.FALLBACK,
        attachments=DeliverySupportLevel.NATIVE,
        attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
        reply_references=DeliverySupportLevel.NATIVE,
        max_text_length=4_000,
    ),
}

_TRANSIENT_ROUTE_LIMIT = 4_096
_TRANSIENT_ADMISSION_LIMIT = 16_384


class NativeTransportChannelAdapter:
    """Adapt one SDK-owned native transport to the common Channel Port."""

    def __init__(
        self,
        *,
        channel_instance_id: str,
        channel_id: str,
        native_factory: NativeFactory,
        startup_validator: StartupValidator,
    ) -> None:
        if channel_id not in _CHANNEL_CAPABILITIES:
            raise ValueError(f"unsupported native channel: {channel_id}")
        if not callable(startup_validator):
            raise TypeError("startup_validator must be callable")
        self._channel_instance_id = channel_instance_id
        self._channel_id = channel_id
        self._native_factory = native_factory
        self._startup_validator = startup_validator
        self._native: NativeChannel | None = None
        self._last_connection_facts: NativeConnectionDiagnosticSnapshot | None = None
        self._queue_overflow_offsets: dict[str, int] = {}

    def validate_startup_configuration(self) -> None:
        """Validate one detached native instance without starting transport I/O."""

        self._startup_validator()

    @property
    def channel_instance_id(self) -> str:
        return self._channel_instance_id

    @property
    def kind(self) -> str:
        return self._channel_id

    def diagnostic_facts(self) -> NativeChannelDiagnosticSnapshot:
        native = self._native
        facts = (
            self._with_process_lifetime_overflow(_native_connection_facts(native))
            if native is not None
            else None
        )
        return NativeChannelDiagnosticSnapshot(
            channel_instance_id=self._channel_instance_id,
            kind=self._channel_id,
            connection=facts if native is not None else self._last_connection_facts,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return _CHANNEL_CAPABILITIES[self._channel_id]

    async def start(
        self,
        on_message: MessageHandler,
        on_admission: InboundAdmissionHandler | None = None,
    ) -> None:
        if self._native is not None:
            raise RuntimeError("channel is already started")
        middleware = _InboundMiddleware(
            channel_instance_id=self._channel_instance_id,
            on_message=on_message,
            on_admission=on_admission,
        )
        native = self._native_factory(middleware)
        self._last_connection_facts = None
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
        if native is not None:
            try:
                await native.stop()
            except BaseException:
                facts = self._with_process_lifetime_overflow(_native_connection_facts(native))
                self._remember_process_lifetime_overflow(facts)
                self._last_connection_facts = None
                raise
            else:
                facts = self._with_process_lifetime_overflow(_native_connection_facts(native))
                self._remember_process_lifetime_overflow(facts)
                self._last_connection_facts = facts
            finally:
                self._native = None

    def _with_process_lifetime_overflow(
        self,
        facts: NativeConnectionDiagnosticSnapshot | None,
    ) -> NativeConnectionDiagnosticSnapshot | None:
        if facts is None:
            return None
        return replace(
            facts,
            queues=tuple(
                replace(
                    queue,
                    overflow_count=(
                        queue.overflow_count + self._queue_overflow_offsets.get(queue.name, 0)
                    ),
                )
                for queue in facts.queues
            ),
        )

    def _remember_process_lifetime_overflow(
        self,
        facts: NativeConnectionDiagnosticSnapshot | None,
    ) -> None:
        if facts is None:
            return
        self._queue_overflow_offsets = {queue.name: queue.overflow_count for queue in facts.queues}

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        native = self._native
        if native is None:
            raise RuntimeError("channel is not started")
        if message.conversation_ref.channel_instance_id != self._channel_instance_id:
            raise ValueError("message belongs to a different channel instance")
        if getattr(native, "enabled", True) is False:
            raise RuntimeError(f"{self._channel_id} Channel delivery is disabled for this instance")
        native_message = _outbound_delivery._to_native_outbound(
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
        item_receipts = _outbound_delivery._artifact_item_receipts(
            native_message.metadata,
            item_indexes=item_indexes,
        )
        return DeliveryReceipt(
            status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            native_message_id=native_message_id,
            detail=detail,
            items=item_receipts,
        )


def _native_connection_facts(native: object) -> NativeConnectionDiagnosticSnapshot | None:
    provider = getattr(native, "diagnostic_connection_facts", None)
    if not callable(provider):
        return None
    try:
        facts = provider()
        return facts if isinstance(facts, NativeConnectionDiagnosticSnapshot) else None
    except Exception:
        return None


def channel_from_config(
    channel_id: str,
    *,
    config: dict[str, object],
    channel_instance_id: str | None = None,
) -> NativeTransportChannelAdapter:
    """Construct one SDK-owned native Channel transport from adapter values."""

    if channel_id == "qq":
        from .qq import QQChannelAdapter as NativeAdapter
    elif channel_id == "telegram":
        from .telegram import TelegramChannelAdapter as NativeAdapter
    elif channel_id == "feishu":
        from .feishu import FeishuChannelAdapter as NativeAdapter
    elif channel_id == "weixin":
        from .weixin import WeixinChannelAdapter as NativeAdapter
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
        startup_validator=lambda: NativeAdapter.validate_startup_configuration_from_config(
            resolved_config
        ),
    )


class _InboundMiddleware:
    def __init__(
        self,
        *,
        channel_instance_id: str,
        on_message: MessageHandler,
        on_admission: InboundAdmissionHandler | None,
    ) -> None:
        self._channel_instance_id = channel_instance_id
        self._on_message = on_message
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
                    error.add_note(
                        "Failed to release inbound admission after pre-handoff "
                        f"processing failed: {release_error!r}"
                    )
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


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)

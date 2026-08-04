from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from ...media import AttachmentSourceKind
from ...messages import OutboundMessage
from .. import ingress as _ingress
from .. import outbound_delivery as _outbound_delivery
from ..contract import (
    ChannelCapabilities,
    DeliveryReceipt,
    DeliverySupportLevel,
    InboundAdmissionHandler,
    MessageHandler,
)
from ..diagnostics import ChannelDiagnosticFacts
from ..outbound_delivery import (
    NativeDeliveryResult,
)
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

    def diagnostic_facts(self) -> ChannelDiagnosticFacts:
        native = self._native
        facts = (
            self._with_process_lifetime_overflow(_native_connection_facts(native))
            if native is not None
            else None
        )
        snapshot = NativeChannelDiagnosticSnapshot(
            channel_instance_id=self._channel_instance_id,
            kind=self._channel_id,
            connection=facts if native is not None else self._last_connection_facts,
        )
        return snapshot.as_contract()

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
        middleware = _ingress._InboundMiddleware(
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
        return _outbound_delivery._native_delivery_receipt(
            result=result,
            native_message=native_message,
            message=message,
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

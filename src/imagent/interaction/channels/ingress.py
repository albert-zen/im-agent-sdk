from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Literal, Protocol, cast

from ..media import AttachmentContent, LocalPath
from ..messages import ConversationRef, TextContent, TextFormat
from ..messages import (
    InboundMessage as InteractionInboundMessage,
)
from .contract import InboundAdmission, InboundAdmissionHandler, MessageHandler

AccessMatch = Literal["any", "all"]
_UNRESTRICTED = "*"
_DENY_ALL = "none"
_TRANSIENT_ADMISSION_LIMIT = 16_384
ACCESS_DENIAL_REPORT_LIMIT = 10
ACCESS_DENIAL_REPORT_WINDOW_S = 60.0


def parse_id_set(value: object) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        values: Iterable[object] = value.replace("\n", ",").split(",")
    elif isinstance(value, Iterable):
        values = value
    else:
        values = (value,)
    return frozenset(text for item in values if (text := str(item).strip()))


@dataclass(frozen=True, slots=True)
class ChannelAccessPolicy:
    """Optional Channel restrictions based on stable platform identifiers.

    Empty dimensions and ``*`` do not restrict platform-delivered messages.
    ``none`` explicitly denies the whole Channel without disconnecting it.
    """

    allowed_user_ids: frozenset[str] = frozenset()
    allowed_conversation_ids: frozenset[str] = frozenset()
    access_match: AccessMatch = "any"

    def __post_init__(self) -> None:
        if self.access_match not in {"any", "all"}:
            raise ValueError("access_match must be 'any' or 'all'")
        configured_ids = self.allowed_user_ids | self.allowed_conversation_ids
        if _DENY_ALL in configured_ids and configured_ids != {_DENY_ALL}:
            raise ValueError("'none' cannot be combined with any other access value")

    @classmethod
    def from_config(cls, config: dict[str, object]) -> ChannelAccessPolicy:
        access_match = str(config.get("access_match") or "any").strip().lower()
        if access_match not in {"any", "all"}:
            raise ValueError("access_match must be 'any' or 'all'")
        return cls(
            allowed_user_ids=parse_id_set(config.get("allowed_user_ids")),
            allowed_conversation_ids=parse_id_set(config.get("allowed_conversation_ids")),
            access_match=cast(AccessMatch, access_match),
        )

    @classmethod
    def allow_all(cls) -> ChannelAccessPolicy:
        return cls()

    @property
    def denies_all(self) -> bool:
        return _DENY_ALL in self.allowed_user_ids or _DENY_ALL in self.allowed_conversation_ids

    @property
    def restricted_user_ids(self) -> frozenset[str]:
        if _UNRESTRICTED in self.allowed_user_ids or self.denies_all:
            return frozenset()
        return self.allowed_user_ids

    @property
    def restricted_conversation_ids(self) -> frozenset[str]:
        if _UNRESTRICTED in self.allowed_conversation_ids or self.denies_all:
            return frozenset()
        return self.allowed_conversation_ids

    @property
    def active_restriction_count(self) -> int:
        return int(bool(self.restricted_user_ids)) + int(bool(self.restricted_conversation_ids))

    @property
    def mode(self) -> str:
        if self.denies_all:
            return "deny_all"
        if not self.active_restriction_count:
            return "platform"
        return f"restricted_{self.access_match}"

    def allows(self, *, user_id: str, conversation_id: str) -> bool:
        if self.denies_all:
            return False
        matches: list[bool] = []
        if self.restricted_user_ids:
            matches.append(user_id in self.restricted_user_ids)
        if self.restricted_conversation_ids:
            matches.append(conversation_id in self.restricted_conversation_ids)
        if not matches:
            return True
        return any(matches) if self.access_match == "any" else all(matches)


class _AccessDenialLimiter:
    """Bound access-denial diagnostics without weakening the actual gate."""

    def __init__(self) -> None:
        self._reported_at: deque[float] = deque()
        self._suppressed = 0
        self._lock = Lock()

    def note(self) -> int | None:
        now = time.monotonic()
        with self._lock:
            cutoff = now - ACCESS_DENIAL_REPORT_WINDOW_S
            while self._reported_at and self._reported_at[0] <= cutoff:
                self._reported_at.popleft()
            if len(self._reported_at) >= ACCESS_DENIAL_REPORT_LIMIT:
                self._suppressed += 1
                return None
            self._reported_at.append(now)
            suppressed = self._suppressed
            self._suppressed = 0
            return suppressed


def inbound_allowed(*, access_policy: ChannelAccessPolicy, inbound: InboundMessage) -> bool:
    return access_policy.allows(
        user_id=inbound.user_id,
        conversation_id=inbound.conversation_id,
    )


def inbound_access_ready() -> bool:
    return True


def access_policy_health(
    *,
    access_policy: ChannelAccessPolicy,
    inbound_access_ready_value: bool,
) -> dict[str, object]:
    return {
        "inbound_access_ready": inbound_access_ready_value,
        "access_policy_mode": access_policy.mode,
        "access_match": access_policy.access_match,
        "allowed_user_count": len(access_policy.restricted_user_ids),
        "allowed_conversation_count": len(access_policy.restricted_conversation_ids),
    }


def prepare_access_denial_report(limiter: _AccessDenialLimiter) -> int | None:
    return limiter.note()


@dataclass(frozen=True, slots=True)
class InboundAttachment:
    kind: Literal["image", "file"]
    content_type: str
    local_path: str
    size_bytes: int
    filename: str = ""
    source_channel_id: str = ""
    source_message_id: str = ""


@dataclass(slots=True)
class InboundMessage:
    channel_id: str
    conversation_id: str
    user_id: str
    message_id: str
    text: str
    attachments: tuple[InboundAttachment, ...] = ()
    input_error: str | None = None
    reply_to_message_id: str | None = None
    sent_at: str | None = None
    trace_id: str | None = None


class _InboundPreparation(Protocol):
    def __call__(
        self,
        inbound: InboundMessage,
        /,
    ) -> InboundMessage | Awaitable[InboundMessage]: ...


class _InboundNormalizer(Protocol):
    def __call__(
        self,
        inbound: InboundMessage,
        *,
        reply_to_message_id: str | None,
    ) -> InteractionInboundMessage: ...


class _InboundHandoffMiddleware(Protocol):
    async def handle_inbound(
        self,
        adapter: object,
        inbound: InboundMessage,
        **options: object,
    ) -> None: ...


async def dispatch_inbound(
    *,
    adapter: object,
    middleware: _InboundHandoffMiddleware,
    inbound: InboundMessage,
    reply_to_message_id: str | None = None,
    prepare_inbound: _InboundPreparation | None = None,
    pending_attachment_count: int = 0,
) -> None:
    dispatch_options: dict[str, object] = {"reply_to_message_id": reply_to_message_id}
    if prepare_inbound is not None:
        dispatch_options["prepare_inbound"] = prepare_inbound
    if pending_attachment_count:
        dispatch_options["pending_attachment_count"] = pending_attachment_count
    await middleware.handle_inbound(adapter, inbound, **dispatch_options)


class _InboundAdmissionTransaction:
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
        self._admitted_inbound: set[tuple[str, str, str]] = set()
        self._admission_lock = asyncio.Lock()

    async def run(
        self,
        inbound: InboundMessage,
        *,
        normalize_inbound: _InboundNormalizer,
        prepare_inbound: _InboundPreparation | None = None,
        reply_to_message_id: str | None = None,
    ) -> None:
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
        admission: InboundAdmission | None = None
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
            message = normalize_inbound(
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


def _normalize_inbound_message(
    *,
    channel_instance_id: str,
    inbound: InboundMessage,
    reply_to_message_id: str | None,
) -> InteractionInboundMessage:
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
    return InteractionInboundMessage(
        message_id=str(inbound.message_id),
        conversation_ref=ConversationRef(
            channel_instance_id=channel_instance_id,
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


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)

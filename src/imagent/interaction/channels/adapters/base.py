from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from .. import ingress as _ingress
from .. import outbound_delivery as _outbound_delivery
from ..ingress import ChannelAccessPolicy, InboundMessage, _InboundPreparation
from ..outbound_delivery import (
    NativeDeliveryResult,
    OutboundMessage,
)
from .diagnostics import (
    NativeChannelDiagnosticState,
    NativeConnectionDiagnosticSnapshot,
    emit_event,
    mark_channel_health,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChannelRouteContext:
    admitted_user_id: str = ""
    last_inbound_message_id: str = ""
    last_inbound_seen_at: float | None = None


class BaseChannelAdapter(ABC):
    channel_id: str

    def __init__(
        self,
        *,
        middleware,
        access_policy: ChannelAccessPolicy | None = None,
    ) -> None:
        self.middleware = middleware
        self.access_policy = access_policy or ChannelAccessPolicy()
        self._access_denial_limiter = _ingress._AccessDenialLimiter()
        self._diagnostic_state = NativeChannelDiagnosticState()

    def mark_health(self, **state: object) -> None:
        self._diagnostic_state.update(**state)
        mark_channel_health(self.channel_id, **state)

    def diagnostic_connection_facts(self) -> NativeConnectionDiagnosticSnapshot:
        return self._diagnostic_state.snapshot()

    def inbound_allowed(self, inbound: InboundMessage) -> bool:
        return _ingress.inbound_allowed(
            access_policy=self.access_policy,
            inbound=inbound,
        )

    @property
    def inbound_access_ready(self) -> bool:
        return _ingress.inbound_access_ready()

    def access_policy_health(self) -> dict[str, object]:
        return _ingress.access_policy_health(
            access_policy=self.access_policy,
            inbound_access_ready_value=self.inbound_access_ready,
        )

    def prepare_access_denial_report(self) -> int | None:
        return _ingress.prepare_access_denial_report(self._access_denial_limiter)

    def emit_access_denial(self, inbound: InboundMessage, suppressed: int) -> None:
        denial_reason = (
            "access_denied_all"
            if self.access_policy.denies_all
            else "access_restriction_not_matched"
        )
        logger.warning(
            "%s inbound message blocked by access policy user_id=%s "
            "conversation_id=%s suppressed_since_last=%d",
            self.channel_id,
            inbound.user_id,
            inbound.conversation_id,
            suppressed,
        )
        emit_event(
            component=f"channels.{self.channel_id}",
            event="message.inbound.access_denied",
            level="WARNING",
            message="Inbound channel message blocked by access policy",
            channel_id=inbound.channel_id,
            conversation_id=inbound.conversation_id,
            user_id=inbound.user_id,
            message_id=inbound.message_id,
            data={"suppressed_since_last": suppressed},
        )
        self.mark_health(
            **self.access_policy_health(),
            last_inbound_access_denied_at=datetime.now(UTC).isoformat(),
            last_inbound_access_denial_reason=denial_reason,
        )

    def ensure_outbound_allowed(self, message: OutboundMessage) -> None:
        route_user_id = self._last_inbound_user_id(message)
        conversation_user_id = (
            self._conversation_user_id(message.conversation_id) if not route_user_id else None
        )
        decision = _outbound_delivery.ensure_outbound_allowed(
            channel_id=self.channel_id,
            message=message,
            access_policy=self.access_policy,
            route_user_id=route_user_id,
            conversation_user_id=conversation_user_id,
        )
        if decision.allowed:
            return
        emit_event(
            component=f"channels.{self.channel_id}",
            event="message.outbound.access_denied",
            level="ERROR",
            message="Outbound channel message blocked by current access policy",
            channel_id=message.channel_id,
            conversation_id=message.conversation_id,
            user_id=decision.user_id or None,
        )
        raise PermissionError(
            f"{self.channel_id} outbound route is not admitted by the current access policy"
        )

    def _last_inbound_user_id(self, message: OutboundMessage) -> str | None:
        return _outbound_delivery.route_context_user_id(self._route_context(message))

    def _last_inbound_message_id(self, message: OutboundMessage) -> str | None:
        return _outbound_delivery.route_context_message_id(self._route_context(message))

    def _route_context(self, message: OutboundMessage) -> ChannelRouteContext | None:
        resolver = getattr(self.middleware, "get_route_context", None)
        if not callable(resolver):
            return None
        context = resolver(message.channel_id, message.conversation_id)
        if not isinstance(context, ChannelRouteContext):
            return None
        return context

    def _conversation_user_id(self, conversation_id: str) -> str | None:
        return None

    async def dispatch_inbound(
        self,
        inbound: InboundMessage,
        *,
        reply_to_message_id: str | None = None,
        prepare_inbound: _InboundPreparation | None = None,
        pending_attachment_count: int = 0,
    ) -> None:
        if not self.inbound_allowed(inbound):
            suppressed = self.prepare_access_denial_report()
            if suppressed is not None:
                self.emit_access_denial(inbound, suppressed)
            return
        await _ingress.dispatch_inbound(
            adapter=self,
            middleware=cast(_ingress._InboundHandoffMiddleware, self.middleware),
            inbound=inbound,
            reply_to_message_id=reply_to_message_id,
            prepare_inbound=prepare_inbound,
            pending_attachment_count=pending_attachment_count,
        )

    @classmethod
    @abstractmethod
    def from_config(cls, *, config: dict[str, object], middleware):
        raise NotImplementedError

    def validate_startup_configuration(self) -> None:
        """Validate local prerequisites without opening a transport."""

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send_message(self, message: OutboundMessage) -> NativeDeliveryResult:
        raise NotImplementedError

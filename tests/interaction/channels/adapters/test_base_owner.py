from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from typing import cast
from unittest.mock import patch

from imagent.interaction.channels import ingress, outbound_delivery
from imagent.interaction.channels.adapters import feishu, qq, runtime, telegram, weixin
from imagent.interaction.channels.adapters.base import (
    BaseChannelAdapter,
    ChannelRouteContext,
)
from imagent.interaction.channels.ingress import ChannelAccessPolicy, InboundMessage
from imagent.interaction.channels.outbound_delivery import NativeDeliveryResult, OutboundMessage


class _VirtualSurfaceAdapter(BaseChannelAdapter):
    channel_id = "virtual"

    def __init__(self, *, middleware: object, access_policy: object) -> None:
        self.calls: list[str] = []
        self.inbound_result = False
        self.denial_report: int | None = 7
        self.route_user_id: str | None = None
        self.fallback_user_id: str | None = None
        self.health_calls: list[dict[str, object]] = []
        super().__init__(
            middleware=middleware,
            access_policy=cast(ChannelAccessPolicy, access_policy),
        )

    def inbound_allowed(self, inbound: InboundMessage) -> bool:
        self.calls.append("inbound_allowed")
        return self.inbound_result

    def prepare_access_denial_report(self) -> int | None:
        self.calls.append("prepare_access_denial_report")
        return self.denial_report

    def emit_access_denial(self, inbound: InboundMessage, suppressed: int) -> None:
        self.calls.append(f"emit_access_denial:{suppressed}")

    def _last_inbound_user_id(self, message: OutboundMessage) -> str | None:
        self.calls.append("_last_inbound_user_id")
        return self.route_user_id

    def _conversation_user_id(self, conversation_id: str) -> str | None:
        self.calls.append("_conversation_user_id")
        return self.fallback_user_id

    def mark_health(self, **state: object) -> None:
        self.health_calls.append(state)

    @classmethod
    def from_config(
        cls,
        *,
        config: dict[str, object],
        middleware: object,
    ) -> _VirtualSurfaceAdapter:
        raise NotImplementedError

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    async def send_message(self, message: OutboundMessage) -> NativeDeliveryResult:
        raise NotImplementedError


class _RecordingMiddleware:
    def __init__(self) -> None:
        self.handoff_count = 0

    async def handle_inbound(
        self,
        adapter: object,
        inbound: InboundMessage,
        **options: object,
    ) -> None:
        self.handoff_count += 1


class NativeChannelBaseOwnershipTests(unittest.TestCase):
    def test_providers_and_runtime_use_exact_base_owner_objects(self) -> None:
        for adapter_type in (
            feishu.FeishuChannelAdapter,
            qq.QQChannelAdapter,
            telegram.TelegramChannelAdapter,
            weixin.WeixinChannelAdapter,
        ):
            with self.subTest(adapter_type=adapter_type.__name__):
                self.assertIs(adapter_type.__mro__[1], BaseChannelAdapter)
        self.assertIs(runtime.ChannelRouteContext, ChannelRouteContext)
        self.assertEqual(
            BaseChannelAdapter.__module__,
            "imagent.interaction.channels.adapters.base",
        )
        self.assertEqual(
            ChannelRouteContext.__module__,
            "imagent.interaction.channels.adapters.base",
        )

    def test_access_and_delivery_helpers_have_focused_leaf_owners(self) -> None:
        from imagent.interaction.channels.adapters import base

        self.assertFalse(hasattr(base, "_AccessDenialLimiter"))
        self.assertEqual(
            ingress._AccessDenialLimiter.__module__,
            "imagent.interaction.channels.ingress",
        )
        self.assertEqual(
            ingress.dispatch_inbound.__module__,
            "imagent.interaction.channels.ingress",
        )
        self.assertEqual(
            outbound_delivery.ensure_outbound_allowed.__module__,
            "imagent.interaction.channels.outbound_delivery",
        )

    def test_dispatch_inbound_preserves_virtual_denial_order_and_blocks_handoff(self) -> None:
        middleware = _RecordingMiddleware()
        adapter = _VirtualSurfaceAdapter(
            middleware=middleware,
            access_policy=ChannelAccessPolicy.allow_all(),
        )
        inbound = InboundMessage(
            channel_id="virtual",
            conversation_id="conversation-1",
            user_id="user-1",
            message_id="message-1",
            text="blocked",
        )

        asyncio.run(adapter.dispatch_inbound(inbound))

        self.assertEqual(
            adapter.calls,
            [
                "inbound_allowed",
                "prepare_access_denial_report",
                "emit_access_denial:7",
            ],
        )
        self.assertEqual(middleware.handoff_count, 0)

    def test_outbound_access_uses_overrides_and_lazy_fallback(self) -> None:
        message = OutboundMessage(
            channel_id="virtual",
            conversation_id="conversation-1",
            message_type="text",
            text="hello",
        )
        adapter = _VirtualSurfaceAdapter(
            middleware=object(),
            access_policy=ChannelAccessPolicy(
                allowed_user_ids=frozenset({"override-user", "fallback-user"}),
            ),
        )

        adapter.route_user_id = "override-user"
        adapter.fallback_user_id = "fallback-user"
        adapter.ensure_outbound_allowed(message)
        self.assertEqual(adapter.calls, ["_last_inbound_user_id"])

        adapter.calls.clear()
        adapter.route_user_id = None
        adapter.ensure_outbound_allowed(message)
        self.assertEqual(
            adapter.calls,
            ["_last_inbound_user_id", "_conversation_user_id"],
        )

    def test_outbound_denial_event_and_error_only_follow_explicit_decision(self) -> None:
        message = OutboundMessage(
            channel_id="virtual",
            conversation_id="conversation-1",
            message_type="text",
            text="hello",
        )
        adapter = _VirtualSurfaceAdapter(
            middleware=object(),
            access_policy=ChannelAccessPolicy(
                allowed_user_ids=frozenset({"allowed-user"}),
            ),
        )
        adapter.route_user_id = "blocked-user"

        with patch("imagent.interaction.channels.adapters.base.emit_event") as emit_event:
            with self.assertRaisesRegex(
                PermissionError,
                "virtual outbound route is not admitted by the current access policy",
            ):
                adapter.ensure_outbound_allowed(message)

        emit_event.assert_called_once_with(
            component="channels.virtual",
            event="message.outbound.access_denied",
            level="ERROR",
            message="Outbound channel message blocked by current access policy",
            channel_id="virtual",
            conversation_id="conversation-1",
            user_id="blocked-user",
        )

    def test_policy_permission_error_does_not_synthesize_denial_side_effects(self) -> None:
        class RaisingPolicy:
            def allows(self, *, user_id: str, conversation_id: str) -> bool:
                raise PermissionError("policy evaluation failed")

        message = OutboundMessage(
            channel_id="virtual",
            conversation_id="conversation-1",
            message_type="text",
            text="hello",
        )
        adapter = _VirtualSurfaceAdapter(
            middleware=object(),
            access_policy=RaisingPolicy(),
        )
        adapter.route_user_id = "route-user"

        with patch("imagent.interaction.channels.adapters.base.emit_event") as emit_event:
            with self.assertRaisesRegex(PermissionError, "policy evaluation failed"):
                adapter.ensure_outbound_allowed(message)

        emit_event.assert_not_called()
        self.assertEqual(adapter.health_calls, [])

    def test_historical_native_base_path_is_absent(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from importlib.util import find_spec; "
                    "from imagent.interaction.channels.adapters.base import "
                    "BaseChannelAdapter, ChannelRouteContext; "
                    "assert find_spec('imagent.channels.native') is None"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

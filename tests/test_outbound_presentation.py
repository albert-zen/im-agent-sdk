from __future__ import annotations

import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import ConversationRef, OutboundMessage, TextContent
from imagent.gateway import ImAgentGateway
from imagent.storage import InMemoryIdempotencyRepository
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


def _message(delivery_id: str = "delivery-1") -> OutboundMessage:
    return OutboundMessage(
        delivery_id=delivery_id,
        conversation_ref=ConversationRef("fake-channel", "conversation-1"),
        content=(TextContent("commentary"),),
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
        metadata={"native_item_kind": "plan_updated"},
    )


class _Presentation:
    def __init__(
        self,
        result: OutboundMessage | None | Callable[[OutboundMessage], OutboundMessage | None],
    ) -> None:
        self.result = result
        self.seen: list[OutboundMessage] = []

    async def present(self, message: OutboundMessage) -> OutboundMessage | None:
        self.seen.append(message)
        if callable(self.result):
            return self.result(message)
        return self.result


class OutboundPresentationTests(unittest.IsolatedAsyncioTestCase):
    def _gateway(self, channel, presentation, *, idempotency=None) -> ImAgentGateway:
        return ImAgentGateway(
            channels=[channel],
            applications=[FakeAgentApplicationAdapter()],
            bindings=InMemoryBindingRepository(),
            idempotency=idempotency,
            outbound_presentation=presentation,
        )

    async def test_policy_can_transform_one_destination_without_changing_identity(self) -> None:
        channel = FakeChannelAdapter()
        presentation = _Presentation(
            lambda message: replace(message, content=(TextContent("visible"),))
        )
        gateway = self._gateway(channel, presentation)
        await gateway.start()
        try:
            result = await gateway._deliver_outbound(_message())
        finally:
            await gateway.stop()

        self.assertEqual(result, "acquired")
        self.assertEqual(channel.sent[0].content, (TextContent("visible"),))
        self.assertEqual(presentation.seen, [_message()])

    async def test_suppression_is_a_completed_delivery_decision(self) -> None:
        channel = FakeChannelAdapter()
        idempotency = InMemoryIdempotencyRepository()
        gateway = self._gateway(channel, _Presentation(None), idempotency=idempotency)
        await gateway.start()
        try:
            first = await gateway._deliver_outbound(_message())
            replay = await gateway._deliver_outbound(_message())
        finally:
            await gateway.stop()

        self.assertEqual(first, "acquired")
        self.assertEqual(replay, "already_completed")
        self.assertEqual(channel.sent, [])

    async def test_policy_cannot_reroute_or_reidentify_delivery(self) -> None:
        channel = FakeChannelAdapter()
        presentation = _Presentation(lambda message: replace(message, delivery_id="different"))
        gateway = self._gateway(channel, presentation)
        await gateway.start()
        try:
            with self.assertRaisesRegex(Exception, "cannot change delivery identity"):
                await gateway._deliver_outbound(_message())
        finally:
            await gateway.stop()

        self.assertEqual(channel.sent, [])

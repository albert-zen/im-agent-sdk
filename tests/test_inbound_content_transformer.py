from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import (
    AgentInput,
    AttachmentContent,
    ConversationRef,
    InboundMessage,
    InputContinuationPreference,
    LocalPath,
    ProjectMode,
    TextContent,
    derive_client_message_id,
)
from imagent.controllers import SlashController
from imagent.diagnostics import InboundContentTransformFailureCode
from imagent.gateway import (
    GatewayExtensions,
    GatewayLimits,
    GatewayRepositories,
    ImAgentGateway,
    InboundContentTransformer,
)
from imagent.inbound_content import (
    InboundContentTransformationCapacityError,
    InboundContentTransformationError,
    InboundContentTransformationTimeout,
    transform_inbound_content,
)
from imagent.storage import InMemoryIdempotencyRepository
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _RecordingApplication(FakeAgentApplicationAdapter):
    def __init__(self) -> None:
        super().__init__(project_mode=ProjectMode.FLAT)
        self.continuations: list[InputContinuationPreference] = []

    @property
    def inputs(self) -> tuple[AgentInput, ...]:
        return tuple(message for _, message in self._inputs)

    async def send_input(
        self,
        thread_ref,
        message,
        *,
        continuation=InputContinuationPreference.PREFER_ACTIVE_TURN,
        before_dispatch=None,
    ):
        self.continuations.append(continuation)
        return await super().send_input(
            thread_ref,
            message,
            continuation=continuation,
            before_dispatch=before_dispatch,
        )


class _FunctionTransformer:
    def __init__(self, function) -> None:
        self._function = function

    async def transform_content(self, message: InboundMessage):
        return await self._function(message)


class InboundContentTransformerTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapted_typed_content_preserves_envelope_and_dispatch_policy(self) -> None:
        seen: list[InboundMessage] = []
        image = AttachmentContent(
            attachment_id="image-1",
            media_type="image/png",
            source=LocalPath("/staged/image.png"),
            filename="image.png",
        )
        generic_file = AttachmentContent(
            attachment_id="file-1",
            media_type="application/pdf",
            source=LocalPath("/staged/design.pdf"),
            filename="design.pdf",
        )

        async def transform(message: InboundMessage):
            seen.append(message)
            return (TextContent("bounded manifest"), image, generic_file)

        gateway, channel, application = self._gateway(_FunctionTransformer(transform))
        message = self._message("adapted-message", reply_to="quoted-message")

        await gateway.start()
        try:
            await channel.emit_message(message)
            await channel.emit_message(message)
        finally:
            await gateway.stop()

        self.assertEqual(seen, [message])
        self.assertEqual(
            application.inputs,
            (
                AgentInput(
                    client_message_id=derive_client_message_id(
                        message.conversation_ref,
                        message.message_id,
                    ),
                    content=(TextContent("bounded manifest"), image, generic_file),
                    sender=message.sender,
                ),
            ),
        )
        self.assertEqual(
            application.continuations,
            [InputContinuationPreference.PREFER_ACTIVE_TURN],
        )

    async def test_controller_consumption_bypasses_transformer(self) -> None:
        async def must_not_run(_message: InboundMessage):
            raise AssertionError("I1 ran for Controller-consumed input")

        channel = FakeChannelAdapter("fake-channel")
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[],
            repositories=GatewayRepositories(bindings=InMemoryBindingRepository()),
            extensions=GatewayExtensions(
                controller=SlashController(),
                inbound_content_transformer=_FunctionTransformer(must_not_run),
            ),
        )

        await gateway.start()
        try:
            await channel.emit_message(self._message("help", text="/help"))
        finally:
            await gateway.stop()

        self.assertEqual(len(channel.sent), 1)
        facts = gateway.diagnostics_snapshot().gateway.inbound_content_transformer
        assert facts is not None
        self.assertIsNone(facts.last_failure_code)
        self.assertEqual(facts.invocation_count, 0)

    async def test_invalid_output_releases_claim_and_redelivery_can_transform(self) -> None:
        calls = 0

        async def transform(_message: InboundMessage):
            nonlocal calls
            calls += 1
            if calls == 1:
                return cast(Any, [TextContent("invalid container")])
            return (TextContent("retry accepted"),)

        gateway, channel, application = self._gateway(_FunctionTransformer(transform))
        message = self._message("retry-invalid")

        await gateway.start()
        try:
            with self.assertRaisesRegex(InboundContentTransformationError, "return a tuple"):
                await channel.emit_message(message)
            self.assertEqual(application.inputs, ())
            await channel.emit_message(message)
        finally:
            await gateway.stop()

        self.assertEqual(calls, 2)
        self.assertEqual(application.inputs[0].content, (TextContent("retry accepted"),))

    async def test_exception_releases_claim_and_diagnostics_are_redacted(self) -> None:
        calls = 0
        secret = "secret path /private/input.txt"

        async def transform(_message: InboundMessage):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError(secret)
            return (TextContent("replayed safely"),)

        gateway, channel, application = self._gateway(_FunctionTransformer(transform))
        message = self._message("retry-exception")

        await gateway.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "secret path"):
                await channel.emit_message(message)
            self.assertEqual(application.inputs, ())
            failed = gateway.diagnostics_snapshot()
            await channel.emit_message(message)
            completed = gateway.diagnostics_snapshot()
        finally:
            await gateway.stop()

        self.assertEqual(failed.schema_version, 5)
        failed_facts = failed.gateway.inbound_content_transformer
        completed_facts = completed.gateway.inbound_content_transformer
        assert failed_facts is not None
        assert completed_facts is not None
        self.assertEqual(
            failed_facts.last_failure_code,
            InboundContentTransformFailureCode.TRANSFORMER_FAILED,
        )
        self.assertNotIn(secret, json.dumps(asdict(failed), default=str))
        self.assertEqual(completed_facts.invocation_count, 2)
        self.assertEqual(completed_facts.success_count, 1)
        self.assertEqual(completed_facts.failure_count, 1)

    async def test_timeout_cancels_and_joins_transformer_before_reclaim(self) -> None:
        first = True
        cancelled = asyncio.Event()

        async def transform(_message: InboundMessage):
            nonlocal first
            if first:
                first = False
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return (TextContent("after timeout"),)

        gateway, channel, application = self._gateway(
            _FunctionTransformer(transform),
            limits=GatewayLimits(inbound_content_transform_timeout_seconds=0.01),
        )
        message = self._message("retry-timeout")

        await gateway.start()
        try:
            with self.assertRaises(InboundContentTransformationTimeout):
                await channel.emit_message(message)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(application.inputs, ())
            await channel.emit_message(message)
        finally:
            await gateway.stop()

        facts = gateway.diagnostics_snapshot().gateway.inbound_content_transformer
        assert facts is not None
        self.assertEqual(facts.timeout_count, 1)
        self.assertEqual(facts.cancellation_overrun_count, 0)
        self.assertEqual(facts.failure_count, 1)
        self.assertEqual(application.inputs[0].content, (TextContent("after timeout"),))

    async def test_timeout_cleanup_has_a_second_finite_bound(self) -> None:
        first_cancel = asyncio.Event()
        second_cancel = asyncio.Event()

        async def transform(_message: InboundMessage):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                first_cancel.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    second_cancel.set()
                    raise

        gateway, channel, application = self._gateway(
            _FunctionTransformer(transform),
            limits=GatewayLimits(inbound_content_transform_timeout_seconds=0.01),
        )

        await gateway.start()
        try:
            async with asyncio.timeout(0.1):
                with self.assertRaises(InboundContentTransformationTimeout):
                    await channel.emit_message(self._message("cleanup-overrun"))
            await asyncio.wait_for(second_cancel.wait(), timeout=0.1)
        finally:
            await gateway.stop()

        self.assertTrue(first_cancel.is_set())
        self.assertEqual(application.inputs, ())
        facts = gateway.diagnostics_snapshot().gateway.inbound_content_transformer
        assert facts is not None
        self.assertEqual(facts.timeout_count, 1)
        self.assertEqual(facts.cancellation_overrun_count, 1)

    async def test_cancellation_overrun_stays_capacity_bounded_and_stops(self) -> None:
        cancellation_count = 0

        async def transform(_message: InboundMessage):
            nonlocal cancellation_count
            while True:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancellation_count += 1
                    if cancellation_count >= 3:
                        raise

        gateway, channel, application = self._gateway(
            _FunctionTransformer(transform),
            limits=GatewayLimits(
                inbound_content_transform_timeout_seconds=0.01,
                inbound_content_transform_max_concurrency=1,
            ),
        )

        await gateway.start()
        with self.assertRaises(InboundContentTransformationTimeout):
            await channel.emit_message(self._message("overrun-first"))
        with self.assertRaises(InboundContentTransformationCapacityError):
            await channel.emit_message(self._message("overrun-second"))
        self.assertEqual(application.inputs, ())
        before_stop = gateway.diagnostics_snapshot().gateway.inbound_content_transformer
        assert before_stop is not None
        self.assertEqual(before_stop.cancellation_overrun_count, 1)
        self.assertEqual(before_stop.capacity_rejection_count, 1)

        await gateway.stop()
        await asyncio.sleep(0)

        self.assertEqual(cancellation_count, 3)
        self.assertFalse(
            any(
                task.get_name() == "imagent-inbound-content"
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            )
        )

    async def test_transformer_raised_timeout_is_not_sdk_timeout(self) -> None:
        async def transform(_message: InboundMessage):
            raise TimeoutError("consumer dependency timed out")

        gateway, channel, application = self._gateway(_FunctionTransformer(transform))

        await gateway.start()
        try:
            with self.assertRaisesRegex(TimeoutError, "consumer dependency"):
                await channel.emit_message(self._message("consumer-timeout"))
        finally:
            await gateway.stop()

        facts = gateway.diagnostics_snapshot().gateway.inbound_content_transformer
        assert facts is not None
        self.assertEqual(facts.timeout_count, 0)
        self.assertEqual(facts.failure_count, 1)
        self.assertEqual(
            facts.last_failure_code,
            InboundContentTransformFailureCode.TRANSFORMER_FAILED,
        )
        self.assertEqual(application.inputs, ())

    async def test_cancellation_releases_claim_for_same_identity(self) -> None:
        block = True
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def transform(_message: InboundMessage):
            if block:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return (TextContent("after cancellation"),)

        gateway, channel, application = self._gateway(_FunctionTransformer(transform))
        message = self._message("retry-cancel")

        await gateway.start()
        try:
            task = asyncio.create_task(channel.emit_message(message))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(cancelled.is_set())
            self.assertEqual(application.inputs, ())
            block = False
            await channel.emit_message(message)
        finally:
            await gateway.stop()

        facts = gateway.diagnostics_snapshot().gateway.inbound_content_transformer
        assert facts is not None
        self.assertEqual(facts.cancellation_count, 1)
        self.assertEqual(facts.failure_count, 1)
        self.assertEqual(application.inputs[0].content, (TextContent("after cancellation"),))

    async def test_released_claim_can_reenter_i1_after_gateway_restart(self) -> None:
        idempotency = InMemoryIdempotencyRepository()
        bindings = InMemoryBindingRepository()
        message = self._message("restart-replay")

        async def fail(_message: InboundMessage):
            raise RuntimeError("pre-dispatch failure")

        first_gateway, first_channel, first_application = self._gateway(
            _FunctionTransformer(fail),
            bindings=bindings,
            idempotency=idempotency,
        )
        await first_gateway.start()
        try:
            with self.assertRaises(RuntimeError):
                await first_channel.emit_message(message)
        finally:
            await first_gateway.stop()
        self.assertEqual(first_application.inputs, ())

        calls = 0

        async def succeed(_message: InboundMessage):
            nonlocal calls
            calls += 1
            return (TextContent("restart accepted"),)

        second_gateway, second_channel, second_application = self._gateway(
            _FunctionTransformer(succeed),
            bindings=bindings,
            idempotency=idempotency,
        )
        await second_gateway.start()
        try:
            await second_channel.emit_message(message)
        finally:
            await second_gateway.stop()

        self.assertEqual(calls, 1)
        self.assertEqual(
            second_application.inputs[0].content,
            (TextContent("restart accepted"),),
        )

    async def test_result_validation_is_nonempty_typed_and_bounded(self) -> None:
        message = self._message("invalid-results")

        async def result(value):
            async def transform(_message: InboundMessage):
                return value

            return await transform_inbound_content(
                _FunctionTransformer(transform),
                message,
                timeout_seconds=1,
                max_items=1,
            )

        with self.assertRaisesRegex(InboundContentTransformationError, "empty content"):
            await result(())
        with self.assertRaisesRegex(InboundContentTransformationError, "item limit"):
            await result((TextContent("one"), TextContent("two")))
        with self.assertRaisesRegex(InboundContentTransformationError, "unsupported"):
            await result((cast(Any, object()),))

    def test_limits_and_absent_diagnostics_are_explicit(self) -> None:
        gateway, _, _ = self._gateway(None)
        self.assertIsNone(gateway.diagnostics_snapshot().gateway.inbound_content_transformer)
        with self.assertRaisesRegex(ValueError, "timeout"):
            self._gateway(
                cast(InboundContentTransformer, object()),
                limits=GatewayLimits(inbound_content_transform_timeout_seconds=float("inf")),
            )
        with self.assertRaisesRegex(ValueError, "item limit"):
            self._gateway(
                cast(InboundContentTransformer, object()),
                limits=GatewayLimits(inbound_content_transform_max_items=0),
            )
        with self.assertRaisesRegex(ValueError, "concurrency"):
            self._gateway(
                cast(InboundContentTransformer, object()),
                limits=GatewayLimits(inbound_content_transform_max_concurrency=0),
            )

    def _gateway(
        self,
        transformer: InboundContentTransformer | None,
        *,
        limits: GatewayLimits = GatewayLimits(),
        bindings: InMemoryBindingRepository | None = None,
        idempotency: InMemoryIdempotencyRepository | None = None,
    ) -> tuple[ImAgentGateway, FakeChannelAdapter, _RecordingApplication]:
        channel = FakeChannelAdapter("fake-channel")
        application = _RecordingApplication()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings or InMemoryBindingRepository(),
                idempotency=idempotency,
            ),
            limits=limits,
            extensions=GatewayExtensions(
                inbound_content_transformer=transformer,
            ),
        )
        return gateway, channel, application

    @staticmethod
    def _message(
        message_id: str,
        *,
        text: str = "original",
        reply_to: str | None = None,
    ) -> InboundMessage:
        return InboundMessage(
            message_id=message_id,
            conversation_ref=ConversationRef("fake-channel", "conversation-1"),
            sender="user-1",
            content=(TextContent(text),),
            created_at=datetime.now(UTC),
            reply_to=reply_to,
            metadata={"trace": "untrusted"},
        )

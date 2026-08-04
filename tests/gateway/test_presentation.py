from __future__ import annotations

import asyncio
import importlib.util
import inspect
import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import imagent.gateway as gateway_facade
from imagent.adapters import IdempotencyClaimStatus
from imagent.applications.contract import AgentInput, AgentMessage, ThreadRef
from imagent.gateway import GatewayExtensions, GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway import presentation as presentation_owner
from imagent.gateway.persistence import (
    ConversationBinding,
    InMemoryIdempotencyRepository,
    ThreadProjectionRoute,
)
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryProjectionRouteRepository,
)
from imagent.gateway.presentation import (
    OutboundPresentationCapacityError,
    OutboundPresentationContext,
    OutboundPresentationError,
    OutboundPresentationFailureCode,
    OutboundPresentationPolicy,
    OutboundPresentationRuntime,
    OutboundPresentationTimeout,
    ProjectionPresentationOrigin,
    _decide_claimed_outbound_presentation,
    _projection_presentation_context,
)
from imagent.gateway.projection.checkpoints import _ProjectionCheckpointAuthority
from imagent.gateway.projection.observation import (
    _ProjectionRecoveryRequired,
    deliver_projected_message,
)
from imagent.gateway.projection.recovery import ProjectedAgentMessage
from imagent.gateway.routing.projection_routes import derive_projection_route_id
from imagent.interaction.media import AttachmentContent, AttachmentHandle
from imagent.interaction.messages import (
    ConversationRef,
    MessageRole,
    OutboundMessage,
    TextContent,
)
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _Policy:
    def __init__(
        self,
        behavior: Callable[
            [OutboundMessage, OutboundPresentationContext],
            OutboundMessage | None,
        ],
    ) -> None:
        self._behavior = behavior
        self.seen: list[tuple[OutboundMessage, OutboundPresentationContext]] = []

    async def present(
        self,
        message: OutboundMessage,
        context: OutboundPresentationContext,
    ) -> OutboundMessage | None:
        self.seen.append((message, context))
        return self._behavior(message, context)


class _BlockingPolicy:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def present(
        self,
        message: OutboundMessage,
        context: OutboundPresentationContext,
    ) -> OutboundMessage:
        self.entered.set()
        await self.release.wait()
        return message


class _CancellationOverrunPolicy:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def present(
        self,
        message: OutboundMessage,
        context: OutboundPresentationContext,
    ) -> OutboundMessage:
        del context
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await self.release.wait()
        return message


class OutboundPresentationOwnershipTests(unittest.TestCase):
    def test_gateway_facade_uses_exact_owner_and_old_module_is_absent(self) -> None:
        self.assertIs(
            gateway_facade.OutboundPresentationPolicy,
            presentation_owner.OutboundPresentationPolicy,
        )
        self.assertIs(
            gateway_facade.OutboundPresentationContext,
            presentation_owner.OutboundPresentationContext,
        )
        self.assertIs(
            gateway_facade.ProjectionPresentationOrigin,
            presentation_owner.ProjectionPresentationOrigin,
        )
        self.assertIsNone(importlib.util.find_spec("imagent.outbound_presentation"))

    def test_owner_maps_checkpointability_without_transition_authority(self) -> None:
        self.assertEqual(
            _projection_presentation_context(checkpointable=True).origin,
            ProjectionPresentationOrigin.AUTHORITATIVE,
        )
        self.assertEqual(
            _projection_presentation_context(checkpointable=False).origin,
            ProjectionPresentationOrigin.LIVE_ONLY,
        )
        self.assertEqual(
            set(inspect.signature(_decide_claimed_outbound_presentation).parameters),
            {"message", "presentation_context", "runtime"},
        )


class OutboundPresentationTests(unittest.IsolatedAsyncioTestCase):
    def _gateway(
        self,
        policy: OutboundPresentationPolicy | None,
        *,
        channel: FakeChannelAdapter | None = None,
        projections: InMemoryProjectionRouteRepository | None = None,
        idempotency: InMemoryIdempotencyRepository | None = None,
        limits: GatewayLimits | None = None,
    ) -> tuple[ImAgentGateway, FakeChannelAdapter, InMemoryProjectionRouteRepository]:
        configured_channel = channel or FakeChannelAdapter()
        configured_projections = projections or InMemoryProjectionRouteRepository()
        return (
            ImAgentGateway(
                channels=[configured_channel],
                applications=[FakeAgentApplicationAdapter()],
                repositories=GatewayRepositories(
                    bindings=InMemoryBindingRepository(),
                    projections=configured_projections,
                    idempotency=idempotency,
                ),
                limits=limits or GatewayLimits(),
                extensions=GatewayExtensions(outbound_presentation=policy),
            ),
            configured_channel,
            configured_projections,
        )

    async def test_two_destinations_transform_and_suppress_independently(self) -> None:
        def present(
            message: OutboundMessage,
            context: OutboundPresentationContext,
        ) -> OutboundMessage | None:
            self.assertEqual(context.origin, ProjectionPresentationOrigin.AUTHORITATIVE)
            if message.conversation_ref.native_conversation_id == "hidden":
                return None
            return replace(message, content=(TextContent("decorated"),))

        policy = _Policy(present)
        gateway, channel, repository = self._gateway(policy)
        thread = ThreadRef("fake-agent", "thread-1")
        visible = _route(thread, ConversationRef("fake-channel", "visible"))
        hidden = _route(thread, ConversationRef("fake-channel", "hidden"))
        await repository.put_projection_route(visible)
        await repository.put_projection_route(hidden)
        projected = _projected(thread)

        await gateway.start()
        try:
            visible_result = await deliver_projected_message(
                repository,
                visible,
                projected,
                deliver_outbound=gateway._deliver_projected_outbound,
                checkpoint_authority=_ProjectionCheckpointAuthority(
                    projections=repository,
                ),
                authoritative=False,
            )
            hidden_result = await deliver_projected_message(
                repository,
                hidden,
                projected,
                deliver_outbound=gateway._deliver_projected_outbound,
                checkpoint_authority=_ProjectionCheckpointAuthority(
                    projections=repository,
                ),
                authoritative=False,
            )
        finally:
            await gateway.stop()

        self.assertEqual(channel.sent[0].content, (TextContent("decorated"),))
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(len(policy.seen), 2)
        self.assertEqual(visible_result.checkpoint_agent_item_id, "item-1")
        self.assertEqual(hidden_result.checkpoint_agent_item_id, "item-1")
        facts = gateway.diagnostics_snapshot().gateway.outbound_presentation
        self.assertIsNotNone(facts)
        assert facts is not None
        self.assertEqual(facts.delivery_count, 1)
        self.assertEqual(facts.suppression_count, 1)

    async def test_completed_suppression_bypasses_policy_and_converges_checkpoint(self) -> None:
        policy = _Policy(lambda _message, _context: None)
        idempotency = InMemoryIdempotencyRepository()
        gateway, channel, repository = self._gateway(policy, idempotency=idempotency)
        thread = ThreadRef("fake-agent", "thread-crash")
        route = _route(thread, ConversationRef("fake-channel", "crash-window"))
        await repository.put_projection_route(route)
        projected = _projected(thread)
        message = _outbound(route, projected)

        await gateway.start()
        try:
            first = await gateway._deliver_projected_outbound(message, True)
            recovered = await deliver_projected_message(
                repository,
                route,
                projected,
                deliver_outbound=gateway._deliver_projected_outbound,
                checkpoint_authority=_ProjectionCheckpointAuthority(
                    projections=repository,
                ),
                authoritative=True,
            )
        finally:
            await gateway.stop()

        self.assertEqual(first, IdempotencyClaimStatus.ACQUIRED)
        self.assertEqual(recovered.checkpoint_agent_item_id, "item-1")
        self.assertEqual(len(policy.seen), 1)
        self.assertEqual(channel.sent, [])

    async def test_policy_exception_releases_claim_for_projection_recovery(self) -> None:
        attempts = 0

        def present(
            message: OutboundMessage,
            _context: OutboundPresentationContext,
        ) -> OutboundMessage:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("consumer secret")
            return message

        policy = _Policy(present)
        gateway, channel, _ = self._gateway(policy)
        message = _message("retry-after-policy-error")
        await gateway.start()
        try:
            with self.assertRaisesRegex(
                _ProjectionRecoveryRequired,
                "failed before Channel side effect",
            ) as raised:
                await gateway._deliver_projected_outbound(message, True)
            self.assertIsInstance(raised.exception.__cause__, RuntimeError)
            result = await gateway._deliver_projected_outbound(message, True)
        finally:
            await gateway.stop()

        self.assertEqual(result, IdempotencyClaimStatus.ACQUIRED)
        self.assertEqual(len(channel.sent), 1)
        facts = gateway.diagnostics_snapshot().gateway.outbound_presentation
        assert facts is not None
        self.assertEqual(facts.failure_count, 1)
        self.assertEqual(facts.last_failure_code, OutboundPresentationFailureCode.POLICY_FAILED)

    async def test_policy_cancellation_releases_claim_for_projection_recovery(self) -> None:
        attempts = 0

        def present(
            message: OutboundMessage,
            _context: OutboundPresentationContext,
        ) -> OutboundMessage:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise asyncio.CancelledError
            return message

        policy = _Policy(present)
        gateway, channel, _ = self._gateway(policy)
        message = _message("retry-after-policy-cancellation")
        await gateway.start()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await gateway._deliver_projected_outbound(message, True)
            result = await gateway._deliver_projected_outbound(message, True)
        finally:
            await gateway.stop()

        self.assertEqual(result, IdempotencyClaimStatus.ACQUIRED)
        self.assertEqual(len(channel.sent), 1)
        facts = gateway.diagnostics_snapshot().gateway.outbound_presentation
        assert facts is not None
        self.assertEqual(facts.cancellation_count, 1)
        self.assertEqual(facts.last_failure_code, OutboundPresentationFailureCode.CANCELLED)

    async def test_policy_failure_reenters_authoritative_route_recovery(self) -> None:
        attempts = 0

        def present(
            message: OutboundMessage,
            _context: OutboundPresentationContext,
        ) -> OutboundMessage:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient policy failure")
            return message

        policy = _Policy(present)
        application = FakeAgentApplicationAdapter()
        thread = await application.create_thread()
        await application.send_input(
            thread.ref,
            AgentInput(client_message_id="seed", content=(TextContent("run"),)),
        )
        conversation = ConversationRef("fake-channel", "recover-policy")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                thread_ref=thread.ref,
            )
        )
        projections = InMemoryProjectionRouteRepository()
        await projections.put_projection_route(_route(thread.ref, conversation))
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
            limits=GatewayLimits(
                subscription_retry_initial_seconds=0,
                subscription_retry_max_seconds=0,
            ),
            extensions=GatewayExtensions(outbound_presentation=policy),
        )

        await gateway.start()
        try:
            await _wait_until(lambda: len(channel.sent) == 2)
            routes = await _wait_for_checkpoint(
                projections,
                thread.ref,
                "turn-1:message:2",
            )
            health = gateway.get_projection_health(thread.ref)
            assert health is not None
            self.assertGreaterEqual(health.restart_count, 1)
        finally:
            await gateway.stop()

        self.assertGreaterEqual(attempts, 3)
        self.assertEqual(routes[0].checkpoint_agent_item_id, "turn-1:message:2")

    async def test_live_only_suppression_never_advances_checkpoint(self) -> None:
        policy = _Policy(lambda _message, _context: None)
        gateway, channel, repository = self._gateway(policy)
        thread = ThreadRef("fake-agent", "thread-live")
        route = _route(thread, ConversationRef("fake-channel", "live"))
        await repository.put_projection_route(route)
        projected = _projected(thread, checkpoint=False, event_id="event-1")

        await gateway.start()
        try:
            result = await deliver_projected_message(
                repository,
                route,
                projected,
                deliver_outbound=gateway._deliver_projected_outbound,
                checkpoint_authority=_ProjectionCheckpointAuthority(
                    projections=repository,
                ),
                authoritative=False,
            )
        finally:
            await gateway.stop()

        self.assertIsNone(result.checkpoint_agent_item_id)
        self.assertEqual(channel.sent, [])
        self.assertEqual(
            policy.seen[0][1].origin,
            ProjectionPresentationOrigin.LIVE_ONLY,
        )

    async def test_transformed_output_is_revalidated_before_planning(self) -> None:
        policy = _Policy(
            lambda message, _context: replace(
                message,
                conversation_ref=ConversationRef("fake-channel", "rerouted"),
            )
        )
        gateway, channel, _ = self._gateway(policy)
        await gateway.start()
        try:
            with self.assertRaises(_ProjectionRecoveryRequired) as raised:
                await gateway._deliver_projected_outbound(_message("invalid"), True)
            self.assertIsInstance(raised.exception.__cause__, OutboundPresentationError)
            self.assertIn("routing identity", str(raised.exception.__cause__))
        finally:
            await gateway.stop()
        self.assertEqual(channel.sent, [])

    async def test_policy_cannot_introduce_attachment_authority_or_unbounded_output(self) -> None:
        attachment = AttachmentContent(
            attachment_id="new",
            media_type="image/png",
            source=AttachmentHandle("consumer-handle"),
        )
        policy = _Policy(lambda message, _context: replace(message, content=(attachment,)))
        gateway, channel, _ = self._gateway(policy)
        await gateway.start()
        try:
            with self.assertRaises(_ProjectionRecoveryRequired) as raised:
                await gateway._deliver_projected_outbound(_message("attachment"), True)
            self.assertIsInstance(raised.exception.__cause__, OutboundPresentationError)
            self.assertIn("attachment authority", str(raised.exception.__cause__))
        finally:
            await gateway.stop()
        self.assertEqual(channel.sent, [])

    async def test_policy_input_and_nested_attachment_metadata_are_immutable(self) -> None:
        observed: list[tuple[object, object]] = []

        def present(
            message: OutboundMessage,
            _context: OutboundPresentationContext,
        ) -> OutboundMessage | None:
            attachment = message.content[1]
            assert isinstance(attachment, AttachmentContent)
            observed.append(
                (
                    getattr(message.metadata, "__setitem__", None),
                    getattr(attachment.metadata, "__setitem__", None),
                )
            )
            return None

        attachment = AttachmentContent(
            attachment_id="existing",
            media_type="image/png",
            source=AttachmentHandle("existing-handle"),
            metadata={"sha256": "a" * 64},
        )
        message = replace(
            _message("immutable"),
            content=(TextContent("original"), attachment),
        )
        policy = _Policy(present)
        gateway, channel, _ = self._gateway(policy)
        await gateway.start()
        try:
            await gateway._deliver_projected_outbound(message, True)
        finally:
            await gateway.stop()

        self.assertEqual(observed, [(None, None)])
        self.assertEqual(channel.sent, [])

    async def test_timeout_is_bounded_redacted_and_tracks_cancellation_overrun(self) -> None:
        policy = _CancellationOverrunPolicy()
        runtime = OutboundPresentationRuntime(
            policy,
            timeout_seconds=0.001,
            max_items=2,
            max_text_characters=100,
            max_concurrency=1,
        )
        context = OutboundPresentationContext(ProjectionPresentationOrigin.AUTHORITATIVE)
        try:
            with self.assertRaises(OutboundPresentationTimeout) as raised:
                await runtime.present(_message("timeout-secret"), context)
            self.assertTrue(raised.exception.cancellation_overrun)
            facts = runtime.diagnostic_facts()
            self.assertEqual(facts.timeout_count, 1)
            self.assertEqual(facts.cancellation_overrun_count, 1)
            self.assertEqual(facts.last_failure_code, OutboundPresentationFailureCode.TIMED_OUT)
            self.assertNotIn("timeout-secret", repr(facts))
        finally:
            policy.release.set()
            await runtime.close()

    async def test_capacity_rejection_is_fixed_and_releases_its_claim(self) -> None:
        policy = _BlockingPolicy()
        limits = GatewayLimits(outbound_presentation_max_concurrency=1)
        gateway, channel, _ = self._gateway(policy, limits=limits)
        await gateway.start()
        first = asyncio.create_task(
            gateway._deliver_projected_outbound(_message("capacity-1"), True)
        )
        await policy.entered.wait()
        try:
            with self.assertRaises(_ProjectionRecoveryRequired) as raised:
                await gateway._deliver_projected_outbound(_message("capacity-2"), True)
            self.assertIsInstance(raised.exception.__cause__, OutboundPresentationCapacityError)
            policy.release.set()
            await first
            await gateway._deliver_projected_outbound(_message("capacity-2"), True)
        finally:
            policy.release.set()
            await gateway.stop()

        self.assertEqual(len(channel.sent), 2)
        facts = gateway.diagnostics_snapshot().gateway.outbound_presentation
        assert facts is not None
        self.assertEqual(facts.capacity_rejection_count, 1)
        self.assertEqual(
            facts.last_failure_code,
            OutboundPresentationFailureCode.CAPACITY_EXHAUSTED,
        )

    async def test_non_projection_outbound_and_absent_policy_keep_existing_behavior(self) -> None:
        policy = _Policy(lambda message, _context: message)
        gateway, channel, _ = self._gateway(policy)
        await gateway.start()
        try:
            await gateway._deliver_outbound(_message("request-or-proactive"))
        finally:
            await gateway.stop()
        self.assertEqual(policy.seen, [])
        self.assertEqual(len(channel.sent), 1)

        absent, absent_channel, _ = self._gateway(None)
        self.assertIsNone(absent.diagnostics_snapshot().gateway.outbound_presentation)
        await absent.start()
        try:
            await absent._deliver_projected_outbound(_message("absent"), True)
        finally:
            await absent.stop()
        self.assertEqual(len(absent_channel.sent), 1)


def _message(delivery_id: str) -> OutboundMessage:
    return OutboundMessage(
        delivery_id=delivery_id,
        conversation_ref=ConversationRef("fake-channel", "conversation-1"),
        content=(TextContent("original"),),
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
        metadata={"phase": "final"},
    )


def _route(thread: ThreadRef, conversation: ConversationRef) -> ThreadProjectionRoute:
    return ThreadProjectionRoute(
        route_id=derive_projection_route_id(thread, conversation),
        thread_ref=thread,
        conversation_ref=conversation,
    )


def _projected(
    thread: ThreadRef,
    *,
    checkpoint: bool = True,
    event_id: str | None = None,
) -> ProjectedAgentMessage:
    return ProjectedAgentMessage(
        message=AgentMessage(
            agent_item_id="item-1",
            thread_ref=thread,
            role=MessageRole.ASSISTANT,
            content=(TextContent("original"),),
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
            metadata={"phase": "final"},
        ),
        turn_id=None,
        event_id=event_id,
        checkpoint=checkpoint,
    )


def _outbound(
    route: ThreadProjectionRoute,
    projected: ProjectedAgentMessage,
) -> OutboundMessage:
    from imagent.gateway.projection import derive_projection_delivery_id

    return OutboundMessage(
        delivery_id=derive_projection_delivery_id(
            route.conversation_ref,
            route.thread_ref,
            projected.message.agent_item_id,
        ),
        conversation_ref=route.conversation_ref,
        content=(TextContent("original"),),
        created_at=projected.message.created_at,
        metadata={"phase": "final"},
    )


async def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


async def _wait_for_checkpoint(
    repository: InMemoryProjectionRouteRepository,
    thread_ref: ThreadRef,
    checkpoint: str,
    timeout: float = 1.0,
) -> tuple[ThreadProjectionRoute, ...]:
    async with asyncio.timeout(timeout):
        while True:
            routes = await repository.list_projection_routes(thread_ref)
            if routes[0].checkpoint_agent_item_id == checkpoint:
                return routes
            await asyncio.sleep(0)

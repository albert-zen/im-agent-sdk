from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from imagent.adapters import DeliverySubmissionConflict, IdempotencyClaimStatus
from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import (
    AttachmentContent,
    AttachmentSourceKind,
    ChannelCapabilities,
    ConversationDeliveryTarget,
    ConversationRef,
    DeliveryIntent,
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryPrincipal,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySegmentStatus,
    DeliverySubmissionOrigin,
    DeliverySubmissionState,
    LocalPath,
    OutboundMessage,
    ProjectionPolicy,
    ProjectMode,
    SupportLevel,
    TextContent,
    ThreadProjectionRoute,
    ThreadRef,
    ThreadRouteDeliveryTarget,
    derive_delivery_submission_id,
)
from imagent.delivery_coordination import DeliveryCoordinator, DeliveryCoordinatorConfig
from imagent.delivery_planning import DeliveryPlanningError
from imagent.gateway import ImAgentGateway
from imagent.proactive_delivery import (
    DeliveryAuthorizationError,
    DeliveryRouteError,
    InMemoryDeliverySubmissionRepository,
    ScopedDeliveryAuthorizer,
)
from imagent.projections import InMemoryProjectionRouteRepository
from imagent.storage import SQLiteGatewayState
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _OutcomeChannel(FakeChannelAdapter):
    def __init__(
        self,
        channel_instance_id: str,
        *,
        receipt_status: DeliveryReceiptStatus = DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
        capabilities: ChannelCapabilities | None = None,
    ) -> None:
        super().__init__(channel_instance_id)
        self.receipt_status = receipt_status
        self.receipt_detail: str | None = None
        self.retry_after_seconds: float | None = None
        self.native_message_id: str | None = None
        self.receipt_items: tuple[DeliveryItemReceipt, ...] = ()
        if capabilities is not None:
            self._capabilities = capabilities
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.delay = False

    async def send(self, message):
        self.sent.append(message)
        self.entered.set()
        if self.delay:
            await self.release.wait()
        return DeliveryReceipt(
            status=self.receipt_status,
            native_message_id=(
                self.native_message_id
                or (
                    f"{self.channel_instance_id}-{len(self.sent)}"
                    if self.receipt_status is DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM
                    else None
                )
            ),
            detail=(
                self.receipt_detail
                if self.receipt_detail is not None
                else (
                    None
                    if self.receipt_status is DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM
                    else self.receipt_status.value
                )
            ),
            items=self.receipt_items,
            retry_after_seconds=self.retry_after_seconds,
        )


class ProactiveDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.thread_ref = ThreadRef("app", "thread")
        self.conversation_a = ConversationRef("channel-a", "conversation-a")
        self.conversation_b = ConversationRef("channel-b", "conversation-b")
        self.channel_a = _OutcomeChannel("channel-a")
        self.channel_b = _OutcomeChannel("channel-b")
        self.bindings = InMemoryBindingRepository()
        self.projections = InMemoryProjectionRouteRepository()
        self.submissions = InMemoryDeliverySubmissionRepository()
        self.authorizer = ScopedDeliveryAuthorizer()
        self.thread_token = await self.authorizer.issue(
            DeliveryPrincipal(
                principal_id="agent-task",
                allowed_threads=(self.thread_ref,),
            )
        )
        self.conversation_token = await self.authorizer.issue(
            DeliveryPrincipal(
                principal_id="operator",
                allowed_conversations=(self.conversation_a,),
            )
        )

    def gateway(
        self,
        *,
        policy: ProjectionPolicy = ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
        submissions=None,
        coordinator: DeliveryCoordinator | None = None,
    ) -> ImAgentGateway:
        return ImAgentGateway(
            channels=[self.channel_a, self.channel_b],
            applications=[
                FakeAgentApplicationAdapter(
                    application_instance_id="app",
                    project_mode=ProjectMode.FLAT,
                )
            ],
            bindings=self.bindings,
            projections=self.projections,
            delivery_submissions=submissions or self.submissions,
            delivery_authorizer=self.authorizer,
            projection_policy=policy,
            delivery_coordinator=coordinator,
        )

    def intent(self, delivery_id: str = "delivery-1", text: str = "hello"):
        return DeliveryIntent(
            delivery_id=delivery_id,
            target=ThreadRouteDeliveryTarget(self.thread_ref),
            content=(TextContent(text),),
            created_at=datetime.now(UTC),
        )

    async def put_route(
        self,
        conversation_ref: ConversationRef,
        *,
        route_id: str,
        reply_to: str | None = None,
    ) -> ThreadProjectionRoute:
        return await self.projections.put_projection_route(
            ThreadProjectionRoute(
                route_id=route_id,
                thread_ref=self.thread_ref,
                conversation_ref=conversation_ref,
                reply_to_message_id=reply_to,
                updated_at=datetime.now(UTC),
            )
        )

    async def test_explicit_conversation_requires_explicit_scope(self) -> None:
        gateway = self.gateway()
        intent = replace(
            self.intent(),
            target=ConversationDeliveryTarget(self.conversation_a),
        )
        with self.assertRaises(DeliveryAuthorizationError):
            await gateway.deliver_proactively(intent, credential=self.thread_token)

        result = await gateway.deliver_proactively(
            intent,
            credential=self.conversation_token,
        )
        self.assertEqual(result.state, DeliverySubmissionState.ACCEPTED)
        self.assertEqual(self.channel_a.sent[0].conversation_ref, self.conversation_a)

    async def test_thread_route_missing_and_foreground_inactive_are_explicit(self) -> None:
        gateway = self.gateway()
        with self.assertRaises(DeliveryRouteError):
            await gateway.deliver_proactively(self.intent(), credential=self.thread_token)

        await self.put_route(self.conversation_a, route_id="route-a")
        foreground = self.gateway(policy=ProjectionPolicy.FOREGROUND_ONLY)
        with self.assertRaises(DeliveryRouteError):
            await foreground.deliver_proactively(
                self.intent("delivery-foreground"),
                credential=self.thread_token,
            )

    async def test_stale_route_to_unregistered_channel_is_rejected_before_send(
        self,
    ) -> None:
        stale_conversation = ConversationRef("removed-channel", "conversation")
        await self.put_route(stale_conversation, route_id="stale-route")

        result = await self.gateway().deliver_proactively(
            self.intent(delivery_id="delivery-stale-route"),
            credential=self.thread_token,
        )

        self.assertEqual(result.state, DeliverySubmissionState.REJECTED)
        self.assertIsNone(result.destinations[0].error)
        assert result.destinations[0].receipt is not None
        self.assertIsNone(result.destinations[0].receipt.detail)
        self.assertEqual(self.channel_a.sent, [])
        self.assertEqual(self.channel_b.sent, [])

    async def test_all_observers_returns_partial_per_destination(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        await self.put_route(self.conversation_b, route_id="route-b")
        self.channel_b.receipt_status = DeliveryReceiptStatus.REJECTED_BY_PLATFORM

        result = await self.gateway(policy=ProjectionPolicy.ALL_OBSERVERS).deliver_proactively(
            self.intent(),
            credential=self.thread_token,
        )

        self.assertEqual(result.state, DeliverySubmissionState.PARTIAL)
        self.assertEqual(
            {destination.state for destination in result.destinations},
            {
                DeliverySubmissionState.ACCEPTED,
                DeliverySubmissionState.REJECTED,
            },
        )
        self.assertEqual(len(self.channel_a.sent), 1)
        self.assertEqual(len(self.channel_b.sent), 1)

    async def test_route_snapshot_is_pinned_across_route_move(self) -> None:
        await self.projections.replace_thread_projection_routes(
            ThreadProjectionRoute(
                route_id="route-a",
                thread_ref=self.thread_ref,
                conversation_ref=self.conversation_a,
                updated_at=datetime.now(UTC),
            )
        )
        gateway = self.gateway()
        first = await gateway.deliver_proactively(
            self.intent(),
            credential=self.thread_token,
        )
        await self.projections.replace_thread_projection_routes(
            ThreadProjectionRoute(
                route_id="route-b",
                thread_ref=self.thread_ref,
                conversation_ref=self.conversation_b,
                updated_at=datetime.now(UTC),
            )
        )

        replay = await gateway.deliver_proactively(
            self.intent(),
            credential=self.thread_token,
        )
        moved = await gateway.deliver_proactively(
            self.intent("delivery-2"),
            credential=self.thread_token,
        )

        self.assertFalse(first.destinations[0].replayed)
        self.assertTrue(replay.destinations[0].replayed)
        self.assertIsNone(replay.destinations[0].conversation_ref)
        self.assertEqual(replay.destinations[0].route_id, "route-a")
        self.assertIsNone(moved.destinations[0].conversation_ref)
        self.assertEqual(moved.destinations[0].route_id, "route-b")
        self.assertEqual(len(self.channel_a.sent), 1)
        self.assertEqual(len(self.channel_b.sent), 1)

    async def test_same_id_concurrency_sends_once_and_reports_in_flight(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        self.channel_a.delay = True
        gateway = self.gateway()
        first = asyncio.create_task(
            gateway.deliver_proactively(self.intent(), credential=self.thread_token)
        )
        await self.channel_a.entered.wait()

        concurrent = await gateway.deliver_proactively(
            self.intent(),
            credential=self.thread_token,
        )
        self.assertEqual(concurrent.state, DeliverySubmissionState.IN_FLIGHT)
        self.assertTrue(concurrent.destinations[0].replayed)
        self.channel_a.release.set()
        completed = await first
        self.assertEqual(completed.state, DeliverySubmissionState.ACCEPTED)
        self.assertEqual(len(self.channel_a.sent), 1)

    async def test_same_id_conflicts_per_principal_but_principals_are_namespaced(
        self,
    ) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        gateway = self.gateway()
        await gateway.deliver_proactively(self.intent(), credential=self.thread_token)

        with self.assertRaises(DeliverySubmissionConflict):
            await gateway.deliver_proactively(
                self.intent(text="different"),
                credential=self.thread_token,
            )
        other_token = await self.authorizer.issue(
            DeliveryPrincipal(
                principal_id="other-agent",
                allowed_threads=(self.thread_ref,),
            )
        )
        other = await gateway.deliver_proactively(
            self.intent(),
            credential=other_token,
        )
        self.assertEqual(other.state, DeliverySubmissionState.ACCEPTED)
        self.assertEqual(len(self.channel_a.sent), 2)

    async def test_external_delivery_id_cannot_poison_internal_projection_namespace(
        self,
    ) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        gateway = self.gateway()
        malicious_token = await self.authorizer.issue(
            DeliveryPrincipal(
                principal_id="imagent:gateway-internal",
                allowed_threads=(self.thread_ref,),
            )
        )
        await gateway.deliver_proactively(
            self.intent(delivery_id="shared-id"),
            credential=malicious_token,
        )

        claim = await gateway._deliver_outbound(
            OutboundMessage(
                delivery_id="shared-id",
                conversation_ref=self.conversation_a,
                content=(TextContent("internal"),),
                created_at=datetime.now(UTC),
            )
        )

        self.assertEqual(claim, IdempotencyClaimStatus.ACQUIRED)
        self.assertEqual(len(self.channel_a.sent), 2)

    async def test_unknown_outcome_is_sticky_and_not_retried(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        self.channel_a.receipt_status = DeliveryReceiptStatus.UNKNOWN
        gateway = self.gateway()

        first = await gateway.deliver_proactively(
            self.intent(),
            credential=self.thread_token,
        )
        replay = await gateway.deliver_proactively(
            self.intent(),
            credential=self.thread_token,
        )

        self.assertEqual(first.state, DeliverySubmissionState.UNKNOWN)
        self.assertEqual(replay.state, DeliverySubmissionState.UNKNOWN)
        self.assertTrue(replay.destinations[0].replayed)
        self.assertEqual(len(self.channel_a.sent), 1)

    async def test_retryable_outcome_resumes_same_pinned_submission(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        self.channel_a.receipt_status = DeliveryReceiptStatus.RETRYABLE_FAILURE
        self.channel_a.retry_after_seconds = 0
        gateway = self.gateway()

        first = await gateway.deliver_proactively(
            self.intent(delivery_id="delivery-retryable"),
            credential=self.thread_token,
        )
        await self.projections.replace_thread_projection_routes(
            ThreadProjectionRoute(
                route_id="route-b",
                thread_ref=self.thread_ref,
                conversation_ref=self.conversation_b,
                updated_at=datetime.now(UTC),
            )
        )
        self.channel_a.receipt_status = DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM
        self.channel_a.retry_after_seconds = None
        resumed = await gateway.deliver_proactively(
            self.intent(delivery_id="delivery-retryable"),
            credential=self.thread_token,
        )

        self.assertEqual(first.state, DeliverySubmissionState.RETRYABLE)
        self.assertEqual(first.destinations[0].route_id, "route-a")
        assert first.destinations[0].receipt is not None
        self.assertEqual(first.destinations[0].receipt.retry_after_seconds, 0)
        self.assertEqual(resumed.state, DeliverySubmissionState.ACCEPTED)
        self.assertTrue(resumed.destinations[0].replayed)
        self.assertEqual(resumed.destinations[0].route_id, "route-a")
        self.assertEqual(len(self.channel_a.sent), 2)
        self.assertEqual(self.channel_b.sent, [])

    async def test_retryable_outcome_honors_retry_after_before_resume(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        self.channel_a.receipt_status = DeliveryReceiptStatus.RETRYABLE_FAILURE
        self.channel_a.retry_after_seconds = 2
        gateway = self.gateway()

        first = await gateway.deliver_proactively(
            self.intent(delivery_id="delivery-retry-after"),
            credential=self.thread_token,
        )
        self.channel_a.receipt_status = DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM
        self.channel_a.retry_after_seconds = None
        too_early = await gateway.deliver_proactively(
            self.intent(delivery_id="delivery-retry-after"),
            credential=self.thread_token,
        )

        self.assertEqual(first.state, DeliverySubmissionState.RETRYABLE)
        self.assertEqual(too_early.state, DeliverySubmissionState.RETRYABLE)
        self.assertTrue(too_early.destinations[0].replayed)
        self.assertEqual(len(self.channel_a.sent), 1)

    async def test_thread_result_redacts_native_conversation_identity(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        self.channel_a.receipt_status = DeliveryReceiptStatus.UNKNOWN
        self.channel_a.receipt_detail = "failed for conversation-a"
        self.channel_a.native_message_id = "conversation-a"
        self.channel_a.receipt_items = (
            DeliveryItemReceipt(
                content_index=0,
                status=DeliveryItemStatus.UNKNOWN,
                native_message_id="conversation-a",
                detail="platform failed for conversation-a via channel-a",
            ),
        )

        result = await self.gateway().deliver_proactively(
            self.intent(delivery_id="delivery-redacted"),
            credential=self.thread_token,
        )

        destination = result.destinations[0]
        self.assertIsNone(destination.conversation_ref)
        self.assertIsNone(destination.error)
        assert destination.receipt is not None
        self.assertIsNone(destination.receipt.detail)
        self.assertIsNone(destination.receipt.native_message_id)
        self.assertIsNone(destination.receipt.items[0].native_message_id)
        self.assertIsNone(destination.receipt.items[0].detail)
        self.assertTrue(destination.receipt.segments)
        self.assertIsNone(destination.receipt.segments[0].native_message_id)
        self.assertIsNone(destination.receipt.segments[0].detail)

    async def test_thread_preflight_result_omits_hidden_channel_details(self) -> None:
        hidden_conversation = ConversationRef("removed-channel", "hidden-conversation")
        await self.put_route(hidden_conversation, route_id="route-hidden")

        result = await self.gateway().deliver_proactively(
            self.intent(delivery_id="delivery-hidden-channel"),
            credential=self.thread_token,
        )

        destination = result.destinations[0]
        self.assertEqual(destination.state, DeliverySubmissionState.REJECTED)
        self.assertIsNone(destination.conversation_ref)
        self.assertIsNone(destination.error)
        assert destination.receipt is not None
        self.assertIsNone(destination.receipt.detail)
        self.assertTrue(destination.receipt.items)
        self.assertTrue(all(item.detail is None for item in destination.receipt.items))

    async def test_attachment_preflight_rejects_all_before_send(self) -> None:
        self.channel_a._capabilities = ChannelCapabilities(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
            max_attachment_size=4,
            max_attachment_count=2,
        )
        await self.put_route(self.conversation_a, route_id="route-a")
        intent = replace(
            self.intent(),
            content=(
                TextContent("artifact"),
                AttachmentContent(
                    attachment_id="artifact-1",
                    media_type="text/plain",
                    source=LocalPath("C:\\staging\\artifact.txt"),
                    filename="artifact.txt",
                    size_bytes=5,
                    metadata={"sha256": "a" * 64},
                ),
            ),
        )

        result = await self.gateway().deliver_proactively(
            intent,
            credential=self.thread_token,
        )

        self.assertEqual(result.state, DeliverySubmissionState.REJECTED)
        self.assertEqual(len(self.channel_a.sent), 0)
        receipt = result.destinations[0].receipt
        self.assertIsNotNone(receipt)
        assert receipt is not None
        self.assertTrue(receipt.items)

    async def test_proactive_preflight_uses_coordinator_source_and_segment_bounds(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        self.channel_a._capabilities = ChannelCapabilities(max_text_length=1)
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(
                max_source_items_per_delivery=1,
                max_segments_per_delivery=1,
            )
        )
        gateway = self.gateway(coordinator=coordinator)

        oversized_source = replace(
            self.intent("oversized-source"),
            content=(TextContent("first"), TextContent("second")),
        )
        with self.assertRaisesRegex(DeliveryPlanningError, "source item limit"):
            await gateway.deliver_proactively(
                oversized_source,
                credential=self.thread_token,
            )

        oversized_plan = replace(
            self.intent("oversized-plan", "ab"),
            target=ConversationDeliveryTarget(self.conversation_a),
        )
        result = await gateway.deliver_proactively(
            oversized_plan,
            credential=self.conversation_token,
        )
        self.assertIs(result.state, DeliverySubmissionState.REJECTED)
        self.assertIn("segment limit", result.destinations[0].error or "")
        self.assertEqual(self.channel_a.sent, [])

    async def test_internal_source_bound_releases_gateway_idempotency_claim(self) -> None:
        coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(max_source_items_per_delivery=1)
        )
        gateway = self.gateway(coordinator=coordinator)
        oversized = OutboundMessage(
            delivery_id="internal-oversized-source",
            conversation_ref=self.conversation_a,
            content=(TextContent("first"), TextContent("second")),
            created_at=datetime.now(UTC),
        )

        for _attempt in range(2):
            with self.assertRaisesRegex(DeliveryPlanningError, "source item limit"):
                await gateway._deliver_outbound(oversized)

        accepted = await gateway._deliver_outbound(
            replace(oversized, content=(TextContent("now-valid"),))
        )
        self.assertIs(accepted, IdempotencyClaimStatus.ACQUIRED)

    async def test_proactive_local_path_requires_content_digest(self) -> None:
        self.channel_a._capabilities = ChannelCapabilities(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
        )
        await self.put_route(self.conversation_a, route_id="route-a")
        intent = replace(
            self.intent(delivery_id="delivery-missing-digest"),
            content=(
                AttachmentContent(
                    attachment_id="artifact-1",
                    media_type="text/plain",
                    source=LocalPath("C:\\staging\\artifact.txt"),
                    filename="artifact.txt",
                    size_bytes=5,
                ),
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "require lowercase metadata.sha256",
        ):
            await self.gateway().deliver_proactively(
                intent,
                credential=self.thread_token,
            )
        self.assertEqual(self.channel_a.sent, [])

    async def test_proactive_local_path_requires_lowercase_digest(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        intent = replace(
            self.intent(delivery_id="delivery-uppercase-digest"),
            content=(
                AttachmentContent(
                    attachment_id="artifact-1",
                    media_type="text/plain",
                    source=LocalPath("C:\\staging\\artifact.txt"),
                    metadata={"sha256": "A" * 64},
                ),
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "require lowercase metadata.sha256",
        ):
            await self.gateway().deliver_proactively(
                intent,
                credential=self.thread_token,
            )

    async def test_preflight_rejection_pins_route_for_same_delivery_id(self) -> None:
        self.channel_a._capabilities = ChannelCapabilities(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
            max_attachment_size=4,
        )
        await self.put_route(self.conversation_a, route_id="route-a")
        intent = replace(
            self.intent(delivery_id="delivery-rejected"),
            content=(
                AttachmentContent(
                    attachment_id="artifact-1",
                    media_type="text/plain",
                    source=LocalPath("C:\\staging\\artifact.txt"),
                    filename="artifact.txt",
                    size_bytes=5,
                    metadata={"sha256": "a" * 64},
                ),
            ),
        )
        gateway = self.gateway()

        first = await gateway.deliver_proactively(
            intent,
            credential=self.thread_token,
        )
        await self.projections.replace_thread_projection_routes(
            ThreadProjectionRoute(
                route_id="route-b",
                thread_ref=self.thread_ref,
                conversation_ref=self.conversation_b,
                updated_at=datetime.now(UTC),
            )
        )
        self.channel_a._capabilities = ChannelCapabilities(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
            max_attachment_size=10,
        )
        replay = await gateway.deliver_proactively(
            intent,
            credential=self.thread_token,
        )

        self.assertEqual(first.state, DeliverySubmissionState.REJECTED)
        self.assertEqual(replay.state, DeliverySubmissionState.REJECTED)
        self.assertTrue(replay.destinations[0].replayed)
        self.assertEqual(replay.destinations[0].route_id, "route-a")
        self.assertIsNone(replay.destinations[0].conversation_ref)
        self.assertEqual(self.channel_a.sent, [])
        self.assertEqual(self.channel_b.sent, [])

    async def test_sqlite_submission_survives_restart_with_pinned_snapshot(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "gateway.sqlite")
            state = SQLiteGatewayState(path)
            gateway = self.gateway(submissions=state)
            first = await gateway.deliver_proactively(
                self.intent(),
                credential=self.thread_token,
            )
            await state.close()

            await self.projections.replace_thread_projection_routes(
                ThreadProjectionRoute(
                    route_id="route-b",
                    thread_ref=self.thread_ref,
                    conversation_ref=self.conversation_b,
                    updated_at=datetime.now(UTC),
                )
            )
            reopened = SQLiteGatewayState(path)
            replay = await self.gateway(submissions=reopened).deliver_proactively(
                self.intent(),
                credential=self.thread_token,
            )
            await reopened.close()

        self.assertEqual(first.state, DeliverySubmissionState.ACCEPTED)
        self.assertEqual(replay.destinations[0].route_id, "route-a")
        self.assertIsNone(replay.destinations[0].conversation_ref)
        self.assertTrue(replay.destinations[0].replayed)
        self.assertEqual(len(self.channel_b.sent), 0)

    async def test_sqlite_retryable_receipt_survives_restart_and_can_resume(self) -> None:
        await self.put_route(self.conversation_a, route_id="route-a")
        self.channel_a.receipt_status = DeliveryReceiptStatus.RETRYABLE_FAILURE
        self.channel_a.retry_after_seconds = 0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "gateway.sqlite")
            state = SQLiteGatewayState(path)
            first = await self.gateway(submissions=state).deliver_proactively(
                self.intent(delivery_id="delivery-sqlite-retryable"),
                credential=self.thread_token,
            )
            await state.close()

            self.channel_a.receipt_status = DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM
            self.channel_a.retry_after_seconds = None
            reopened = SQLiteGatewayState(path)
            restored = await reopened.get_delivery_submission(
                derive_delivery_submission_id(
                    DeliverySubmissionOrigin.EXTERNAL,
                    "agent-task",
                    "delivery-sqlite-retryable",
                )
            )
            self.assertIsNotNone(restored)
            assert restored is not None
            restored_receipt = restored.destinations[0].receipt
            self.assertIsNotNone(restored_receipt)
            assert restored_receipt is not None
            self.assertEqual(
                [segment.status for segment in restored_receipt.segments],
                [DeliverySegmentStatus.RETRYABLE_FAILURE],
            )
            resumed = await self.gateway(submissions=reopened).deliver_proactively(
                self.intent(delivery_id="delivery-sqlite-retryable"),
                credential=self.thread_token,
            )
            await reopened.close()

        self.assertEqual(first.state, DeliverySubmissionState.RETRYABLE)
        assert first.destinations[0].receipt is not None
        self.assertEqual(first.destinations[0].receipt.retry_after_seconds, 0)
        self.assertEqual(resumed.state, DeliverySubmissionState.ACCEPTED)
        self.assertEqual(len(self.channel_a.sent), 2)


if __name__ == "__main__":
    unittest.main()

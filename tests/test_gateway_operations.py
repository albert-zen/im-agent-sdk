from __future__ import annotations

import asyncio
import shlex
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from imagent.adapters import IdempotencyClaimStatus
from imagent.contracts import (
    ActivateNativeThread,
    ApplicationRef,
    ApprovalRequest,
    ApprovalResponse,
    BindConversationToThread,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    CreateThread,
    DeliveryReceipt,
    GatewayOperationFailed,
    InboundMessage,
    ListApplications,
    ListProjects,
    ListThreads,
    NativeThreadActivated,
    ObserveThread,
    OperationErrorCode,
    OutboundMessage,
    ProjectionPolicy,
    ProjectMode,
    ProjectRef,
    ProjectsListed,
    RequestChoice,
    RequestRef,
    RequestResponseRouted,
    RequestRouteState,
    RespondToRequest,
    SelectApplication,
    SupportLevel,
    TextContent,
    ThreadCreated,
    ThreadProjectionRoute,
    ThreadRef,
    ThreadsListed,
    UserInputQuestion,
    UserInputResponse,
)
from imagent.gateway import GatewayExtensions, GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.admission import inbound_idempotency_identity
from imagent.gateway.delivery import DeliveryCoordinator, DeliveryCoordinatorConfig
from imagent.gateway.persistence import InMemoryIdempotencyRepository
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryProjectionRouteRepository,
    InMemoryRequestCorrelationRepository,
)
from imagent.interaction.controllers import MarkdownRequestPresenter, SlashController
from imagent.keyed_locks import KeyedLockCapacityError
from imagent.projections import (
    derive_projection_route_id,
)
from imagent.storage import SQLiteGatewayState
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _DelayedRequestChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.delayed_conversation: ConversationRef | None = None
        self.delay_started = asyncio.Event()
        self.release_delay = asyncio.Event()

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        if message.conversation_ref == self.delayed_conversation:
            self.delay_started.set()
            await self.release_delay.wait()
        return await super().send(message)


class _BlockingBindingRepository(InMemoryBindingRepository):
    def __init__(self, blocked_conversation: ConversationRef) -> None:
        super().__init__()
        self.blocked_conversation = blocked_conversation
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._blocked_once = False

    async def get(self, conversation: ConversationRef) -> ConversationBinding | None:
        if conversation == self.blocked_conversation and not self._blocked_once:
            self._blocked_once = True
            self.entered.set()
            await self.release.wait()
        return await super().get(conversation)


class _CapacityPresenter:
    def __init__(self) -> None:
        self.phases: list[str] = []

    async def present_failure(
        self,
        phase,
        *,
        conversation_ref,
        delivery_id,
        reply_to_message_id,
    ) -> OutboundMessage:
        self.phases.append(phase.value)
        return OutboundMessage(
            delivery_id=delivery_id,
            conversation_ref=conversation_ref,
            content=(TextContent("Gateway is at bounded capacity."),),
            created_at=_now(),
            reply_to=reply_to_message_id,
        )


class TypedGatewayOperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.application = FakeAgentApplicationAdapter()
        self.bindings = InMemoryBindingRepository()
        self.gateway = ImAgentGateway(
            channels=[],
            applications=[self.application],
            repositories=GatewayRepositories(
                bindings=self.bindings,
            ),
        )
        self.conversation = ConversationRef("fake-channel", "conversation-1")
        self.project = ProjectRef("fake-agent", "contract-project")

    def test_gateway_composition_groups_are_frozen_and_keep_defaults(self) -> None:
        repositories = GatewayRepositories(bindings=self.bindings)
        limits = GatewayLimits()
        extensions = GatewayExtensions()

        self.assertIs(repositories.bindings, self.bindings)
        self.assertIsNone(repositories.idempotency)
        self.assertIsNone(repositories.projections)
        self.assertEqual(limits.baseline_history_limit, 3)
        self.assertEqual(limits.startup_buffer_max_pending, 256)
        self.assertEqual(limits.subscription_retry_initial_seconds, 0.05)
        self.assertEqual(limits.turn_correlation_retention_seconds, 7 * 24 * 60 * 60)
        self.assertEqual(limits.delivery_submission_max_records, 4096)
        self.assertEqual(limits.inbound_content_transform_timeout_seconds, 30.0)
        self.assertEqual(limits.inbound_content_transform_max_items, 64)
        self.assertEqual(limits.inbound_content_transform_max_concurrency, 16)
        self.assertEqual(limits.inbound_failure_present_timeout_seconds, 30.0)
        self.assertEqual(limits.inbound_failure_present_max_items, 64)
        self.assertEqual(limits.inbound_failure_present_max_text_characters, 16_384)
        self.assertEqual(limits.inbound_failure_present_max_concurrency, 16)
        self.assertEqual(limits.outbound_presentation_timeout_seconds, 30.0)
        self.assertEqual(limits.outbound_presentation_max_items, 64)
        self.assertEqual(limits.outbound_presentation_max_text_characters, 16_384)
        self.assertEqual(limits.outbound_presentation_max_concurrency, 16)
        self.assertEqual(limits.delivery_outcome_observer_timeout_seconds, 30.0)
        self.assertEqual(limits.delivery_outcome_observer_max_items, 256)
        self.assertEqual(limits.delivery_outcome_observer_max_text_characters, 65_536)
        self.assertEqual(limits.delivery_outcome_observer_max_concurrency, 16)
        self.assertEqual(limits.conversation_serialization_max_active_keys, 4096)
        self.assertIsNone(extensions.controller)
        self.assertIsNone(extensions.request_presenter)
        self.assertIsNone(extensions.inbound_content_transformer)
        self.assertIsNone(extensions.inbound_failure_presenter)
        self.assertIsNone(extensions.outbound_presentation)
        self.assertIsNone(extensions.delivery_outcome_observer)
        with self.assertRaises(FrozenInstanceError):
            limits.baseline_history_limit = 4  # type: ignore[misc]

        for invalid in (0, -1, True, cast(int, 1.5)):
            with self.subTest(invalid_delivery_submission_max_records=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    GatewayLimits(delivery_submission_max_records=invalid)
            with self.subTest(invalid_conversation_serialization_max_active_keys=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    GatewayLimits(conversation_serialization_max_active_keys=invalid)

    def test_gateway_limits_preserve_existing_positional_layout(self) -> None:
        limits = GatewayLimits(
            3,
            10,
            5,
            10,
            20,
            256,
            256,
            256,
            0.125,
            3.0,
        )

        self.assertEqual(limits.subscription_retry_initial_seconds, 0.125)
        self.assertEqual(limits.subscription_retry_max_seconds, 3.0)
        self.assertEqual(limits.delivery_submission_max_records, 4096)
        self.assertEqual(limits.conversation_serialization_max_active_keys, 4096)

    def test_flat_gateway_repository_constructor_is_removed(self) -> None:
        with self.assertRaisesRegex(TypeError, "bindings"):
            cast(Any, ImAgentGateway)(
                channels=[],
                applications=[],
                bindings=self.bindings,
            )

    def test_group_defaults_remain_private_to_each_gateway(self) -> None:
        repositories = GatewayRepositories(bindings=self.bindings)
        first = ImAgentGateway(channels=[], applications=[], repositories=repositories)
        second = ImAgentGateway(channels=[], applications=[], repositories=repositories)

        self.assertIsNot(first._idempotency, second._idempotency)
        self.assertIsNot(first._request_correlations, second._request_correlations)
        self.assertIsNot(first._delivery_coordinator, second._delivery_coordinator)
        self.assertIsNot(
            first._projection_runtime._projections,
            second._projection_runtime._projections,
        )
        self.assertIsNot(
            first._delivery_service._submissions,
            second._delivery_service._submissions,
        )

    async def test_conversation_serialization_is_bounded_waiter_safe_and_ephemeral(
        self,
    ) -> None:
        first_conversation = ConversationRef("fake-channel", "capacity-first")
        second_conversation = ConversationRef("fake-channel", "capacity-second")
        bindings = _BlockingBindingRepository(first_conversation)
        gateway = ImAgentGateway(
            channels=[],
            applications=[self.application],
            repositories=GatewayRepositories(bindings=bindings),
            limits=GatewayLimits(conversation_serialization_max_active_keys=1),
        )
        first = asyncio.create_task(
            gateway.execute_gateway(
                SelectApplication(
                    operation_id="capacity-first",
                    conversation_ref=first_conversation,
                    actor="user",
                    application_ref=self.application.summary.ref,
                    created_at=_now(),
                )
            )
        )
        await bindings.entered.wait()
        same_key_waiter = asyncio.create_task(
            gateway.execute_gateway(
                ListApplications(
                    operation_id="capacity-same-key",
                    conversation_ref=first_conversation,
                    actor="user",
                    created_at=_now(),
                )
            )
        )
        await asyncio.sleep(0)

        rejected = await gateway.execute_gateway(
            ListApplications(
                operation_id="capacity-new-key",
                conversation_ref=second_conversation,
                actor="user",
                created_at=_now(),
            )
        )
        self.assertIsInstance(rejected, GatewayOperationFailed)
        assert isinstance(rejected, GatewayOperationFailed)
        self.assertEqual(rejected.error.code, OperationErrorCode.CAPACITY_EXHAUSTED.value)
        self.assertTrue(rejected.error.retryable)
        self.assertEqual(gateway._conversation_locks.active_key_count, 1)

        bindings.release.set()
        self.assertNotIsInstance(await first, GatewayOperationFailed)
        self.assertNotIsInstance(await same_key_waiter, GatewayOperationFailed)
        self.assertEqual(gateway._conversation_locks.active_key_count, 0)

    async def test_cancelled_conversation_waiter_releases_usage_exactly_once(self) -> None:
        conversation = ConversationRef("fake-channel", "capacity-cancel")
        other = ConversationRef("fake-channel", "capacity-after-cancel")
        bindings = _BlockingBindingRepository(conversation)
        gateway = ImAgentGateway(
            channels=[],
            applications=[self.application],
            repositories=GatewayRepositories(bindings=bindings),
            limits=GatewayLimits(conversation_serialization_max_active_keys=1),
        )
        owner = asyncio.create_task(
            gateway.execute_gateway(
                SelectApplication(
                    operation_id="capacity-owner",
                    conversation_ref=conversation,
                    actor="user",
                    application_ref=self.application.summary.ref,
                    created_at=_now(),
                )
            )
        )
        await bindings.entered.wait()
        waiter = asyncio.create_task(
            gateway.execute_gateway(
                ListApplications(
                    operation_id="capacity-cancelled-waiter",
                    conversation_ref=conversation,
                    actor="user",
                    created_at=_now(),
                )
            )
        )
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(gateway._conversation_locks.active_key_count, 1)

        rejected = await gateway.execute_gateway(
            ListApplications(
                operation_id="capacity-still-full",
                conversation_ref=other,
                actor="user",
                created_at=_now(),
            )
        )
        self.assertIsInstance(rejected, GatewayOperationFailed)
        bindings.release.set()
        self.assertNotIsInstance(await owner, GatewayOperationFailed)
        self.assertEqual(gateway._conversation_locks.active_key_count, 0)

    async def test_inbound_capacity_rejection_preserves_i2_claim_rules(self) -> None:
        for with_presenter in (False, True):
            with self.subTest(with_presenter=with_presenter):
                occupied = ConversationRef("fake-channel", "capacity-occupied")
                incoming = ConversationRef("fake-channel", f"capacity-inbound-{with_presenter}")
                bindings = _BlockingBindingRepository(occupied)
                idempotency = InMemoryIdempotencyRepository()
                channel = FakeChannelAdapter()
                presenter = _CapacityPresenter() if with_presenter else None
                application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
                gateway = ImAgentGateway(
                    channels=[channel],
                    applications=[application],
                    repositories=GatewayRepositories(
                        bindings=bindings,
                        idempotency=idempotency,
                    ),
                    limits=GatewayLimits(conversation_serialization_max_active_keys=1),
                    extensions=GatewayExtensions(inbound_failure_presenter=presenter),
                )
                message = InboundMessage(
                    message_id=f"capacity-message-{with_presenter}",
                    conversation_ref=incoming,
                    sender="user",
                    content=(TextContent("hello"),),
                    created_at=_now(),
                )

                await gateway.start()
                owner = asyncio.create_task(
                    gateway.execute_gateway(
                        SelectApplication(
                            operation_id=f"capacity-block-{with_presenter}",
                            conversation_ref=occupied,
                            actor="user",
                            application_ref=application.summary.ref,
                            created_at=_now(),
                        )
                    )
                )
                await bindings.entered.wait()
                try:
                    if with_presenter:
                        await channel.emit_message(message)
                    else:
                        with self.assertRaises(KeyedLockCapacityError):
                            await channel.emit_message(message)
                    scope, key = inbound_idempotency_identity(incoming, message.message_id)
                    reclaim = await idempotency.claim(
                        scope,
                        key,
                        owner_token="replacement",
                    )
                    expected = (
                        IdempotencyClaimStatus.ALREADY_COMPLETED
                        if with_presenter
                        else IdempotencyClaimStatus.ACQUIRED
                    )
                    self.assertEqual(reclaim, expected)
                    self.assertEqual(application._inputs, [])
                    if presenter is not None:
                        self.assertEqual(presenter.phases, ["pre_acceptance"])
                        self.assertEqual(len(channel.sent), 1)
                finally:
                    bindings.release.set()
                    await owner
                    await gateway.stop()

    async def test_resource_listing_never_changes_conversation_binding(self) -> None:
        original = await self.bindings.put(
            ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=ApplicationRef("fake-agent"),
                project_ref=self.project,
            )
        )

        projects = await self.gateway.execute_application(
            ListProjects(
                operation_id="project-list",
                application_ref=ApplicationRef("fake-agent"),
                created_at=_now(),
            )
        )
        threads = await self.gateway.execute_application(
            ListThreads(
                operation_id="thread-list",
                application_ref=ApplicationRef("fake-agent"),
                project_ref=self.project,
                created_at=_now(),
            )
        )

        self.assertIsInstance(projects, ProjectsListed)
        self.assertIsInstance(threads, ThreadsListed)
        self.assertEqual(await self.bindings.get(self.conversation), original)

    async def test_binding_thread_does_not_activate_native_thread(self) -> None:
        created = await self.gateway.execute_application(
            CreateThread(
                operation_id="thread-create",
                application_ref=ApplicationRef("fake-agent"),
                project_ref=self.project,
                title="Typed operations",
                created_at=_now(),
            )
        )
        self.assertIsInstance(created, ThreadCreated)
        assert isinstance(created, ThreadCreated)

        bound = await self.gateway.execute_gateway(
            BindConversationToThread(
                operation_id="bind-thread",
                conversation_ref=self.conversation,
                actor="user-1",
                thread_ref=created.thread.ref,
                created_at=_now(),
            )
        )

        self.assertIsInstance(bound, ConversationBound)
        self.assertEqual(self.application.activated_threads, [])

        activated = await self.gateway.execute_application(
            ActivateNativeThread(
                operation_id="activate-thread",
                application_ref=ApplicationRef("fake-agent"),
                thread_ref=created.thread.ref,
                created_at=_now(),
            )
        )

        self.assertIsInstance(activated, NativeThreadActivated)
        assert isinstance(activated, NativeThreadActivated)
        self.assertEqual(activated.thread_ref, created.thread.ref)
        self.assertEqual(self.application.activated_threads, [created.thread.ref])


class GatewayLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_gateway_can_restart_with_fresh_delivery_lifecycle(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
            ),
        )
        conversation = ConversationRef("fake-channel", "restart")

        await gateway.start()
        try:
            await gateway._deliver_outbound(
                OutboundMessage(
                    delivery_id="before-restart",
                    conversation_ref=conversation,
                    content=(TextContent("before"),),
                    created_at=_now(),
                )
            )
        finally:
            await gateway.stop()

        await gateway.start()
        try:
            await gateway._deliver_outbound(
                OutboundMessage(
                    delivery_id="after-restart",
                    conversation_ref=conversation,
                    content=(TextContent("after"),),
                    created_at=_now(),
                )
            )
        finally:
            await gateway.stop()

        self.assertEqual(len(channel.sent), 2)


class InteractiveRequestGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        self.thread = await self.application.create_thread()
        self.channel = _DelayedRequestChannel()
        self.correlations = InMemoryRequestCorrelationRepository()
        self.bindings = InMemoryBindingRepository()
        self.projections = InMemoryProjectionRouteRepository()
        self.coordinator = DeliveryCoordinator(
            config=DeliveryCoordinatorConfig(
                max_pending=1,
                backpressure_retry_after_seconds=0.01,
            )
        )
        self.gateway = ImAgentGateway(
            channels=[self.channel],
            applications=[self.application],
            repositories=GatewayRepositories(
                bindings=self.bindings,
                projections=self.projections,
                request_correlations=self.correlations,
            ),
            extensions=GatewayExtensions(
                controller=SlashController(),
                request_presenter=MarkdownRequestPresenter(),
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            delivery_coordinator=self.coordinator,
            limits=GatewayLimits(
                request_delivery_max_pending=2,
            ),
        )
        await self.gateway.start()
        self.conversation_a = ConversationRef("fake-channel", "conversation-a")
        self.conversation_b = ConversationRef("fake-channel", "conversation-b")
        for index, conversation in enumerate(
            (self.conversation_a, self.conversation_b),
            start=1,
        ):
            result = await self.gateway.execute_gateway(
                ObserveThread(
                    operation_id=f"observe-{index}",
                    conversation_ref=conversation,
                    actor=f"user-{index}",
                    thread_ref=self.thread.ref,
                    created_at=_now(),
                )
            )
            self.assertNotIsInstance(result, GatewayOperationFailed)

    async def asyncTearDown(self) -> None:
        await self.gateway.stop()

    async def _restart_with_coordinator(
        self,
        coordinator: DeliveryCoordinator,
        *,
        request_delivery_max_pending: int = 2,
    ) -> None:
        await self.gateway.stop()
        self.channel = _DelayedRequestChannel()
        self.coordinator = coordinator
        self.gateway = ImAgentGateway(
            channels=[self.channel],
            applications=[self.application],
            repositories=GatewayRepositories(
                bindings=self.bindings,
                projections=self.projections,
                request_correlations=self.correlations,
            ),
            extensions=GatewayExtensions(
                controller=SlashController(),
                request_presenter=MarkdownRequestPresenter(),
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            delivery_coordinator=self.coordinator,
            limits=GatewayLimits(
                request_delivery_max_pending=request_delivery_max_pending,
            ),
        )
        await self.gateway.start()

    async def test_only_delivered_destinations_can_race_native_first_writer(
        self,
    ) -> None:
        request = await self.application.open_approval_request(
            self.thread.ref,
            turn_id="turn-external",
            choices=(
                RequestChoice("accept", "Approve once"),
                RequestChoice("accept_for_session", "Approve for session"),
                RequestChoice("decline", "Deny"),
                RequestChoice("cancel", "Cancel"),
            ),
        )
        await _wait_for_correlation_count(
            self.correlations,
            request.request_ref,
            2,
        )
        stored = await self.correlations.list_request_correlations(request_ref=request.request_ref)
        self.assertEqual(
            {item.conversation_ref for item in stored},
            {self.conversation_a, self.conversation_b},
        )
        self.assertTrue(all(item.state is RequestRouteState.OPEN for item in stored))

        unauthorized = await self.gateway.execute_gateway(
            RespondToRequest(
                operation_id="respond-unauthorized",
                conversation_ref=ConversationRef(
                    "fake-channel",
                    "conversation-c",
                ),
                actor="user-c",
                request_ref=request.request_ref,
                response=ApprovalResponse("accept"),
                created_at=_now(),
            )
        )
        self.assertIsInstance(unauthorized, GatewayOperationFailed)
        assert isinstance(unauthorized, GatewayOperationFailed)
        self.assertEqual(
            unauthorized.error.code,
            OperationErrorCode.UNAUTHORIZED_DESTINATION.value,
        )

        contenders = await asyncio.gather(
            *(
                self.gateway.execute_gateway(
                    RespondToRequest(
                        operation_id=f"respond-{index}",
                        conversation_ref=conversation,
                        actor=f"user-{index}",
                        request_ref=request.request_ref,
                        response=ApprovalResponse("accept_for_session"),
                        created_at=_now(),
                    )
                )
                for index, conversation in enumerate(
                    (self.conversation_a, self.conversation_b),
                    start=1,
                )
            )
        )
        self.assertEqual(
            sum(isinstance(result, RequestResponseRouted) for result in contenders),
            1,
        )
        failures = [result for result in contenders if isinstance(result, GatewayOperationFailed)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].error.code,
            OperationErrorCode.REQUEST_DUPLICATE.value,
        )
        self.assertEqual(
            self.application.request_responses[request.request_ref],
            ApprovalResponse("accept_for_session"),
        )
        self.assertTrue(
            all(
                item.state is RequestRouteState.RESPONDED
                for item in await self.correlations.list_request_correlations(
                    request_ref=request.request_ref
                )
            )
        )
        self.assertEqual(self.gateway._request_locks.active_key_count, 0)

    async def test_transient_capacity_pressure_does_not_lose_open_request(self) -> None:
        blocker_conversation = ConversationRef("fake-channel", "capacity-blocker")
        self.channel.delayed_conversation = blocker_conversation
        blocker = self.coordinator.submit(
            self.channel,
            OutboundMessage(
                delivery_id="request-capacity-blocker",
                conversation_ref=blocker_conversation,
                content=(TextContent("block"),),
                created_at=_now(),
            ),
        )
        await self.channel.delay_started.wait()
        try:
            request = await self.application.open_approval_request(
                self.thread.ref,
                turn_id="turn-under-capacity-pressure",
            )
            await asyncio.sleep(0.03)
            self.assertEqual(
                await self.correlations.list_request_correlations(request_ref=request.request_ref),
                (),
            )

            self.channel.release_delay.set()
            await blocker.result()
            await _wait_for_correlation_count(
                self.correlations,
                request.request_ref,
                2,
            )
            stored = await self.correlations.list_request_correlations(
                request_ref=request.request_ref
            )
            self.assertEqual(
                {item.conversation_ref for item in stored},
                {self.conversation_a, self.conversation_b},
            )
            self.assertTrue(all(item.state is RequestRouteState.OPEN for item in stored))
        finally:
            self.channel.release_delay.set()
            await blocker.cancel()

    async def test_request_resolution_cancels_capacity_retry_without_blocking_events(
        self,
    ) -> None:
        blocker_conversation = ConversationRef("fake-channel", "resolution-blocker")
        self.channel.delayed_conversation = blocker_conversation
        blocker = self.coordinator.submit(
            self.channel,
            OutboundMessage(
                delivery_id="request-resolution-blocker",
                conversation_ref=blocker_conversation,
                content=(TextContent("block"),),
                created_at=_now(),
            ),
        )
        await self.channel.delay_started.wait()
        try:
            request = await self.application.open_approval_request(
                self.thread.ref,
                turn_id="turn-resolved-under-pressure",
            )
            await _wait_for_retry_task_count(self.gateway, 2)

            await self.application.resolve_request(request.request_ref)
            await _wait_for_retry_task_count(self.gateway, 0)

            self.channel.release_delay.set()
            await blocker.result()
            await asyncio.sleep(0.03)
            self.assertEqual(
                await self.correlations.list_request_correlations(request_ref=request.request_ref),
                (),
            )
            self.assertEqual(
                [
                    message
                    for message in self.channel.sent
                    if message.conversation_ref in {self.conversation_a, self.conversation_b}
                ],
                [],
            )
        finally:
            self.channel.release_delay.set()
            await blocker.cancel()

    async def test_request_expiry_cancels_capacity_retry(self) -> None:
        blocker_conversation = ConversationRef("fake-channel", "expiry-blocker")
        self.channel.delayed_conversation = blocker_conversation
        blocker = self.coordinator.submit(
            self.channel,
            OutboundMessage(
                delivery_id="request-expiry-blocker",
                conversation_ref=blocker_conversation,
                content=(TextContent("block"),),
                created_at=_now(),
            ),
        )
        await self.channel.delay_started.wait()
        try:
            request = await self.application.open_approval_request(
                self.thread.ref,
                turn_id="turn-expired-under-pressure",
                expires_at=_now() + timedelta(milliseconds=40),
            )
            await _wait_for_retry_task_count(self.gateway, 2)
            await _wait_for_retry_task_count(self.gateway, 0)

            self.channel.release_delay.set()
            await blocker.result()
            self.assertEqual(
                await self.correlations.list_request_correlations(request_ref=request.request_ref),
                (),
            )
        finally:
            self.channel.release_delay.set()
            await blocker.cancel()

    async def test_request_expiry_cancels_admitted_queued_attempt(self) -> None:
        await self._restart_with_coordinator(
            DeliveryCoordinator(
                config=DeliveryCoordinatorConfig(
                    max_pending=4,
                    max_concurrent_destinations=1,
                )
            )
        )
        blocker_conversation = ConversationRef("fake-channel", "queued-expiry-blocker")
        self.channel.delayed_conversation = blocker_conversation
        blocker = self.coordinator.submit(
            self.channel,
            OutboundMessage(
                delivery_id="request-queued-expiry-blocker",
                conversation_ref=blocker_conversation,
                content=(TextContent("block"),),
                created_at=_now(),
            ),
        )
        await self.channel.delay_started.wait()
        try:
            request = await self.application.open_approval_request(
                self.thread.ref,
                turn_id="turn-queued-expiry",
                expires_at=_now() + timedelta(milliseconds=40),
            )
            await _wait_for_retry_task_count(self.gateway, 2)
            await _wait_for_retry_task_count(self.gateway, 0)

            self.channel.release_delay.set()
            await blocker.result()
            self.assertEqual(
                await self.correlations.list_request_correlations(request_ref=request.request_ref),
                (),
            )
            self.assertEqual(
                [
                    message
                    for message in self.channel.sent
                    if message.conversation_ref in {self.conversation_a, self.conversation_b}
                ],
                [],
            )
        finally:
            self.channel.release_delay.set()
            await blocker.cancel()

    async def test_request_resolution_cancels_admitted_queued_attempt(self) -> None:
        await self._restart_with_coordinator(
            DeliveryCoordinator(
                config=DeliveryCoordinatorConfig(
                    max_pending=4,
                    max_concurrent_destinations=1,
                )
            )
        )
        blocker_conversation = ConversationRef(
            "fake-channel",
            "queued-resolution-blocker",
        )
        self.channel.delayed_conversation = blocker_conversation
        blocker = self.coordinator.submit(
            self.channel,
            OutboundMessage(
                delivery_id="request-queued-resolution-blocker",
                conversation_ref=blocker_conversation,
                content=(TextContent("block"),),
                created_at=_now(),
            ),
        )
        await self.channel.delay_started.wait()
        try:
            request = await self.application.open_approval_request(
                self.thread.ref,
                turn_id="turn-queued-resolution",
            )
            await _wait_for_retry_task_count(self.gateway, 2)
            await self.application.resolve_request(request.request_ref)
            await _wait_for_retry_task_count(self.gateway, 0)

            self.channel.release_delay.set()
            await blocker.result()
            self.assertEqual(
                await self.correlations.list_request_correlations(request_ref=request.request_ref),
                (),
            )
            self.assertEqual(
                [
                    message
                    for message in self.channel.sent
                    if message.conversation_ref in {self.conversation_a, self.conversation_b}
                ],
                [],
            )
        finally:
            self.channel.release_delay.set()
            await blocker.cancel()

    async def test_request_backlog_waits_atomically_at_configured_bound(self) -> None:
        blocker_conversation = ConversationRef("fake-channel", "backlog-blocker")
        self.channel.delayed_conversation = blocker_conversation
        blocker = self.coordinator.submit(
            self.channel,
            OutboundMessage(
                delivery_id="request-backlog-blocker",
                conversation_ref=blocker_conversation,
                content=(TextContent("block"),),
                created_at=_now(),
            ),
        )
        await self.channel.delay_started.wait()
        try:
            first = await self.application.open_approval_request(
                self.thread.ref,
                turn_id="turn-backlog-first",
            )
            await _wait_for_retry_task_count(self.gateway, 2)
            second = await self.application.open_approval_request(
                self.thread.ref,
                turn_id="turn-backlog-second",
            )
            await asyncio.sleep(0.03)
            self.assertEqual(
                len(self.gateway._projection_runtime._routes._request_retry_tasks),
                2,
            )

            self.channel.release_delay.set()
            await blocker.result()
            await _wait_for_correlation_count(self.correlations, first.request_ref, 2)
            await _wait_for_correlation_count(self.correlations, second.request_ref, 2)
        finally:
            self.channel.release_delay.set()
            await blocker.cancel()

    async def test_request_fanout_larger_than_backlog_is_delivered_in_batches(self) -> None:
        await self._restart_with_coordinator(
            DeliveryCoordinator(),
            request_delivery_max_pending=1,
        )
        request = await self.application.open_approval_request(
            self.thread.ref,
            turn_id="turn-batched-fanout",
        )
        await _wait_for_correlation_count(self.correlations, request.request_ref, 2)
        self.assertEqual(
            {
                item.conversation_ref
                for item in await self.correlations.list_request_correlations(
                    request_ref=request.request_ref
                )
            },
            {self.conversation_a, self.conversation_b},
        )

    async def test_inactive_cancellation_is_scoped_to_one_thread(self) -> None:
        other_thread = await self.application.create_thread()
        routes = self.gateway._projection_runtime._routes

        async def pending_forever() -> None:
            await asyncio.Event().wait()

        first_task = asyncio.create_task(pending_forever())
        second_task = asyncio.create_task(pending_forever())
        first_ref = RequestRef(self.application.summary.ref, "request-first-thread")
        second_ref = RequestRef(self.application.summary.ref, "request-second-thread")
        routes._request_retry_tasks[(self.thread.ref, "inactive-first-route", first_ref)] = (
            first_task
        )
        routes._request_retry_tasks[(other_thread.ref, "active-second-route", second_ref)] = (
            second_task
        )
        try:
            await routes.cancel_inactive_request_deliveries(self.thread.ref)
            self.assertTrue(first_task.cancelled())
            self.assertFalse(second_task.done())
        finally:
            second_task.cancel()
            await asyncio.gather(second_task, return_exceptions=True)
            routes._request_retry_tasks.pop(
                (other_thread.ref, "active-second-route", second_ref),
                None,
            )

    async def test_managed_request_task_records_active_route_lookup_failure(self) -> None:
        routes = self.gateway._projection_runtime._routes
        route = (await self.projections.list_projection_routes(self.thread.ref))[0]

        async def fail_active_routes(_thread_ref):
            raise RuntimeError("active route lookup failed")

        routes._active_routes = fail_active_routes
        request = ApprovalRequest(
            request_ref=RequestRef(
                self.application.summary.ref,
                "request-active-route-failure",
            ),
            thread_ref=self.thread.ref,
            turn_id="turn-active-route-failure",
            prompt="Approve?",
            choices=(RequestChoice("accept", "Approve"),),
        )
        await routes.deliver_request_to_routes((route,), request)
        await _wait_for_retry_task_count(self.gateway, 0)

        health = self.gateway.get_projection_health(self.thread.ref)
        assert health is not None
        self.assertEqual(health.last_delivery_error, "active route lookup failed")

    async def test_slow_destination_inherits_first_writer_state(self) -> None:
        self.channel.delayed_conversation = self.conversation_b
        request = await self.application.open_approval_request(
            self.thread.ref,
            turn_id="turn-with-slow-destination",
        )
        await self.channel.delay_started.wait()
        async with asyncio.timeout(1):
            while True:
                correlations = await self.correlations.list_request_correlations(
                    request_ref=request.request_ref
                )
                if any(item.conversation_ref == self.conversation_a for item in correlations):
                    break
                await asyncio.sleep(0)

        result = await self.gateway.execute_gateway(
            RespondToRequest(
                operation_id="respond-before-slow-delivery",
                conversation_ref=self.conversation_a,
                actor="user-a",
                request_ref=request.request_ref,
                response=ApprovalResponse("accept"),
                created_at=_now(),
            )
        )
        self.assertIsInstance(result, RequestResponseRouted)
        self.channel.release_delay.set()
        async with asyncio.timeout(1):
            while True:
                correlations = await self.correlations.list_request_correlations(
                    request_ref=request.request_ref
                )
                if len(correlations) == 2:
                    break
                await asyncio.sleep(0)
        self.assertTrue(all(item.state is RequestRouteState.RESPONDED for item in correlations))

    async def test_invalid_choice_does_not_consume_request(self) -> None:
        request = await self.application.open_approval_request(
            self.thread.ref,
            turn_id="turn-external",
        )
        await _wait_for_correlation_count(
            self.correlations,
            request.request_ref,
            2,
        )
        invalid = await self.gateway.execute_gateway(
            RespondToRequest(
                operation_id="respond-invalid",
                conversation_ref=self.conversation_a,
                actor="user-a",
                request_ref=request.request_ref,
                response=ApprovalResponse("approve"),
                created_at=_now(),
            )
        )
        self.assertIsInstance(invalid, GatewayOperationFailed)
        assert isinstance(invalid, GatewayOperationFailed)
        self.assertEqual(
            invalid.error.code,
            OperationErrorCode.INVALID_OPERATION.value,
        )
        valid = await self.gateway.execute_gateway(
            RespondToRequest(
                operation_id="respond-valid",
                conversation_ref=self.conversation_a,
                actor="user-a",
                request_ref=request.request_ref,
                response=ApprovalResponse("accept"),
                created_at=_now(),
            )
        )
        self.assertIsInstance(valid, RequestResponseRouted)

    async def test_secret_input_notice_creates_no_plaintext_response_route(
        self,
    ) -> None:
        request = await self.application.open_user_input_request(
            self.thread.ref,
            turn_id="turn-secret",
            questions=(
                UserInputQuestion(
                    question_id="token",
                    prompt="Enter the API token",
                    allows_other=True,
                    secret=True,
                ),
            ),
        )
        await _wait_until(lambda: len(self.channel.sent) == 2)
        self.assertEqual(
            await self.correlations.list_request_correlations(request_ref=request.request_ref),
            (),
        )
        for outbound in self.channel.sent:
            text = outbound.content[0]
            assert isinstance(text, TextContent)
            self.assertIn("cannot collect", text.text)
            self.assertNotIn("/answer", text.text)
        result = await self.gateway.execute_gateway(
            RespondToRequest(
                operation_id="respond-secret-over-text",
                conversation_ref=self.conversation_a,
                actor="user-a",
                request_ref=request.request_ref,
                response=UserInputResponse({"token": ("plaintext-secret",)}),
                created_at=_now(),
            )
        )
        self.assertIsInstance(result, GatewayOperationFailed)
        assert isinstance(result, GatewayOperationFailed)
        self.assertEqual(
            result.error.code,
            OperationErrorCode.REQUEST_STALE.value,
        )

    async def test_authoritative_resolution_closes_every_destination(self) -> None:
        request = await self.application.open_approval_request(
            self.thread.ref,
            turn_id="turn-resolved",
        )
        await _wait_for_correlation_count(
            self.correlations,
            request.request_ref,
            2,
        )
        await self.application.resolve_request(request.request_ref)
        async with asyncio.timeout(1):
            while True:
                correlations = await self.correlations.list_request_correlations(
                    request_ref=request.request_ref
                )
                if correlations and all(
                    item.state is RequestRouteState.RESOLVED for item in correlations
                ):
                    break
                await asyncio.sleep(0)
        result = await self.gateway.execute_gateway(
            RespondToRequest(
                operation_id="respond-after-resolution",
                conversation_ref=self.conversation_a,
                actor="user-a",
                request_ref=request.request_ref,
                response=ApprovalResponse("accept"),
                created_at=_now(),
            )
        )
        self.assertIsInstance(result, GatewayOperationFailed)
        assert isinstance(result, GatewayOperationFailed)
        self.assertEqual(
            result.error.code,
            OperationErrorCode.REQUEST_RESOLVED.value,
        )

    async def test_typed_action_and_markdown_use_same_response_operation(
        self,
    ) -> None:
        native_request = await self.application.open_approval_request(
            self.thread.ref,
            turn_id="turn-native-action",
        )
        await _wait_for_correlation_count(
            self.correlations,
            native_request.request_ref,
            2,
        )
        result = await self.gateway.execute_gateway(
            RespondToRequest(
                operation_id="typed-action-response",
                conversation_ref=self.conversation_a,
                actor="user-a",
                request_ref=native_request.request_ref,
                response=ApprovalResponse("accept"),
                created_at=_now(),
            )
        )
        self.assertNotIsInstance(result, GatewayOperationFailed)
        self.assertEqual(
            self.application.request_responses[native_request.request_ref],
            ApprovalResponse("accept"),
        )

        markdown_request = await self.application.open_approval_request(
            self.thread.ref,
            turn_id="turn-markdown-response",
        )
        await _wait_for_correlation_count(
            self.correlations,
            markdown_request.request_ref,
            2,
        )
        command = " ".join(
            (
                "/respond",
                shlex.quote(markdown_request.request_ref.application_ref.application_instance_id),
                shlex.quote(markdown_request.request_ref.native_request_id),
                shlex.quote("decline"),
            )
        )
        await self.channel.on_message(
            InboundMessage(
                message_id="markdown-response",
                conversation_ref=self.conversation_a,
                sender="user-a",
                content=(TextContent(command),),
                created_at=_now(),
            )
        )
        self.assertEqual(
            self.application.request_responses[markdown_request.request_ref],
            ApprovalResponse("decline"),
        )

    async def test_unknown_request_locks_do_not_accumulate(self) -> None:
        for index in range(300):
            result = await self.gateway.execute_gateway(
                RespondToRequest(
                    operation_id=f"unknown-{index}",
                    conversation_ref=self.conversation_a,
                    actor="user-a",
                    request_ref=RequestRef(
                        self.application.summary.ref,
                        f"unknown-{index}",
                    ),
                    response=ApprovalResponse("accept"),
                    created_at=_now(),
                )
            )
            self.assertIsInstance(result, GatewayOperationFailed)
        self.assertEqual(self.gateway._request_locks.active_key_count, 0)


class RequestRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_request_is_observed_without_pending_snapshot(
        self,
    ) -> None:
        class StartRequestApplication(FakeAgentApplicationAdapter):
            def __init__(self) -> None:
                super().__init__(project_mode=ProjectMode.FLAT)
                self.start_thread_ref: ThreadRef | None = None
                self.started_request: ApprovalRequest | None = None

            async def start(self) -> None:
                assert self.start_thread_ref is not None
                self.started_request = await self.open_approval_request(
                    self.start_thread_ref,
                    turn_id="turn-opened-during-start",
                )

        application = StartRequestApplication()
        application._summary = replace(
            application.summary,
            capabilities=replace(
                application.summary.capabilities,
                runtime=replace(
                    application.summary.capabilities.runtime,
                    pending_request_snapshot=SupportLevel.UNSUPPORTED,
                ),
            ),
        )
        thread = await application.create_thread()
        application.start_thread_ref = thread.ref
        conversation = ConversationRef("fake-channel", "startup-request")
        projections = InMemoryProjectionRouteRepository()
        await projections.put_projection_route(
            ThreadProjectionRoute(
                route_id=derive_projection_route_id(thread.ref, conversation),
                thread_ref=thread.ref,
                conversation_ref=conversation,
                updated_at=_now(),
            )
        )
        channel = FakeChannelAdapter()
        correlations = InMemoryRequestCorrelationRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=projections,
                request_correlations=correlations,
            ),
            extensions=GatewayExtensions(
                request_presenter=MarkdownRequestPresenter(),
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            assert application.started_request is not None
            await _wait_for_correlation_count(
                correlations,
                application.started_request.request_ref,
                1,
            )
            stored = await correlations.list_request_correlations(
                request_ref=application.started_request.request_ref
            )
            self.assertEqual(len(stored), 1)
            self.assertIs(stored[0].state, RequestRouteState.OPEN)
        finally:
            await gateway.stop()

    async def test_authoritative_pending_snapshot_preserves_restart_response(
        self,
    ) -> None:
        await self._assert_restart_outcome(snapshot_supported=True)

    async def test_missing_pending_snapshot_marks_restart_correlation_stale(
        self,
    ) -> None:
        await self._assert_restart_outcome(snapshot_supported=False)

    async def _assert_restart_outcome(self, *, snapshot_supported: bool) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = SQLiteGatewayState(Path(directory) / "gateway.sqlite3")
            application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
            if not snapshot_supported:
                capabilities = replace(
                    application.summary.capabilities,
                    runtime=replace(
                        application.summary.capabilities.runtime,
                        pending_request_snapshot=SupportLevel.UNSUPPORTED,
                    ),
                )
                application._summary = replace(
                    application.summary,
                    capabilities=capabilities,
                )
            thread = await application.create_thread()
            conversation = ConversationRef("fake-channel", "conversation")
            first_channel = FakeChannelAdapter()
            first = ImAgentGateway(
                channels=[first_channel],
                applications=[application],
                repositories=GatewayRepositories(
                    bindings=state,
                    idempotency=state,
                    projections=state,
                    request_correlations=state,
                ),
                extensions=GatewayExtensions(
                    request_presenter=MarkdownRequestPresenter(),
                ),
                projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            )
            await first.start()
            try:
                await first.execute_gateway(
                    ObserveThread(
                        operation_id="observe-before-restart",
                        conversation_ref=conversation,
                        actor="user",
                        thread_ref=thread.ref,
                        created_at=_now(),
                    )
                )
                request = await application.open_approval_request(
                    thread.ref,
                    turn_id="turn-before-restart",
                )
                await _wait_for_correlation_count(
                    state,
                    request.request_ref,
                    1,
                )
            finally:
                await first.stop()

            second = ImAgentGateway(
                channels=[FakeChannelAdapter()],
                applications=[application],
                repositories=GatewayRepositories(
                    bindings=state,
                    idempotency=state,
                    projections=state,
                    request_correlations=state,
                ),
                extensions=GatewayExtensions(
                    request_presenter=MarkdownRequestPresenter(),
                ),
                projection_policy=ProjectionPolicy.ALL_OBSERVERS,
            )
            await second.start()
            try:
                result = await second.execute_gateway(
                    RespondToRequest(
                        operation_id="respond-after-restart",
                        conversation_ref=conversation,
                        actor="user",
                        request_ref=request.request_ref,
                        response=ApprovalResponse("accept"),
                        created_at=_now(),
                    )
                )
                if snapshot_supported:
                    self.assertIsInstance(result, RequestResponseRouted)
                else:
                    self.assertIsInstance(result, GatewayOperationFailed)
                    assert isinstance(result, GatewayOperationFailed)
                    self.assertEqual(
                        result.error.code,
                        OperationErrorCode.REQUEST_STALE.value,
                    )
            finally:
                await second.stop()
                await state.close()


async def _wait_until(predicate) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


async def _wait_for_correlation_count(
    repository,
    request_ref: RequestRef,
    count: int,
) -> None:
    async with asyncio.timeout(1):
        while True:
            correlations = await repository.list_request_correlations(request_ref=request_ref)
            if len(correlations) == count:
                return
            await asyncio.sleep(0)


async def _wait_for_retry_task_count(
    gateway: ImAgentGateway,
    count: int,
) -> None:
    async with asyncio.timeout(1):
        while len(gateway._projection_runtime._routes._request_retry_tasks) != count:
            await asyncio.sleep(0)


def _now() -> datetime:
    return datetime.now(UTC)


if __name__ == "__main__":
    unittest.main()

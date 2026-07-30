from __future__ import annotations

import asyncio
import shlex
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from imagent.bindings import InMemoryBindingRepository
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
    SupportLevel,
    TextContent,
    ThreadCreated,
    ThreadProjectionRoute,
    ThreadRef,
    ThreadsListed,
    UserInputQuestion,
    UserInputResponse,
)
from imagent.controllers import MarkdownRequestPresenter, SlashController
from imagent.gateway import ImAgentGateway
from imagent.projections import (
    InMemoryProjectionRouteRepository,
    derive_projection_route_id,
)
from imagent.request_correlations import InMemoryRequestCorrelationRepository
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


class TypedGatewayOperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.application = FakeAgentApplicationAdapter()
        self.bindings = InMemoryBindingRepository()
        self.gateway = ImAgentGateway(
            channels=[],
            applications=[self.application],
            bindings=self.bindings,
        )
        self.conversation = ConversationRef("fake-channel", "conversation-1")
        self.project = ProjectRef("fake-agent", "contract-project")

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


class InteractiveRequestGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        self.thread = await self.application.create_thread()
        self.channel = _DelayedRequestChannel()
        self.correlations = InMemoryRequestCorrelationRepository()
        self.gateway = ImAgentGateway(
            channels=[self.channel],
            applications=[self.application],
            bindings=InMemoryBindingRepository(),
            projections=InMemoryProjectionRouteRepository(),
            request_correlations=self.correlations,
            request_presenter=MarkdownRequestPresenter(),
            controller=SlashController(),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
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
        await _wait_until(lambda: len(self.channel.sent) == 2)
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
        await _wait_until(lambda: len(self.channel.sent) == 2)
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
        await _wait_until(lambda: len(self.channel.sent) == 2)
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

    async def test_native_action_and_markdown_use_same_typed_response_operation(
        self,
    ) -> None:
        native_request = await self.application.open_approval_request(
            self.thread.ref,
            turn_id="turn-native-action",
        )
        await _wait_until(lambda: len(self.channel.sent) == 2)
        await self.channel.on_operation(
            RespondToRequest(
                operation_id="native-action-response",
                conversation_ref=self.conversation_a,
                actor="user-a",
                request_ref=native_request.request_ref,
                response=ApprovalResponse("accept"),
                created_at=_now(),
            )
        )
        self.assertEqual(
            self.application.request_responses[native_request.request_ref],
            ApprovalResponse("accept"),
        )

        markdown_request = await self.application.open_approval_request(
            self.thread.ref,
            turn_id="turn-markdown-response",
        )
        await _wait_until(lambda: len(self.channel.sent) == 4)
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
            bindings=InMemoryBindingRepository(),
            projections=projections,
            request_correlations=correlations,
            request_presenter=MarkdownRequestPresenter(),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        await gateway.start()
        try:
            await _wait_until(lambda: len(channel.sent) == 1)
            assert application.started_request is not None
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
                bindings=state,
                projections=state,
                idempotency=state,
                request_correlations=state,
                request_presenter=MarkdownRequestPresenter(),
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
                await _wait_until(lambda: len(first_channel.sent) == 1)
            finally:
                await first.stop()

            second = ImAgentGateway(
                channels=[FakeChannelAdapter()],
                applications=[application],
                bindings=state,
                projections=state,
                idempotency=state,
                request_correlations=state,
                request_presenter=MarkdownRequestPresenter(),
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


def _now() -> datetime:
    return datetime.now(UTC)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any, cast

from imagent.applications import (
    CodexApplicationAdapter,
    ZenApplicationAdapter,
)
from imagent.applications.appserver_requests import (
    build_appserver_response,
    derive_appserver_request_ref,
    map_appserver_request,
    map_zen_appserver_request,
)
from imagent.contracts import (
    AgentEventType,
    ApplicationOperationFailed,
    ApplicationRef,
    ApprovalRequest,
    ApprovalResponse,
    ConversationRef,
    ObserveThread,
    OperationErrorCode,
    ProjectionPolicy,
    RequestResponded,
    RequestResponseRouted,
    RespondRequest,
    RespondToRequest,
    SupportLevel,
    TextContent,
    ThreadRef,
    UserInputRequest,
    UserInputResponse,
)
from imagent.controllers import MarkdownRequestPresenter
from imagent.events import EventStreamReset
from imagent.gateway import GatewayExtensions, GatewayRepositories, ImAgentGateway
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.projections import InMemoryProjectionRouteRepository
from imagent.request_correlations import InMemoryRequestCorrelationRepository
from imagent.testing import FakeChannelAdapter


class AppServerRequestMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application = ApplicationRef("codex-main")

    def test_command_choices_preserve_native_response_payloads(self) -> None:
        amendment = {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "status"]}}
        pending = map_appserver_request(
            self.application,
            _server_request(
                method="item/commandExecution/requestApproval",
                params={
                    "command": "git status",
                    "availableDecisions": [
                        "accept",
                        "acceptForSession",
                        amendment,
                        "decline",
                        "cancel",
                    ],
                },
            ),
        )
        self.assertIsInstance(pending.request, ApprovalRequest)
        request = pending.request
        assert isinstance(request, ApprovalRequest)
        self.assertEqual(
            [choice.choice_id for choice in request.choices[:2]],
            ["accept", "acceptForSession"],
        )
        amendment_choice = request.choices[2]
        self.assertTrue(amendment_choice.choice_id.startswith("native:sha256:"))
        self.assertEqual(
            build_appserver_response(
                pending,
                ApprovalResponse(amendment_choice.choice_id),
            ),
            {"decision": amendment},
        )

    def test_zen_mapper_rejects_unevidenced_appserver_request_shapes(self) -> None:
        with self.assertRaisesRegex(
            NotImplementedError,
            "unsupported Zen App Server request method",
        ):
            map_zen_appserver_request(
                ApplicationRef("zen-main"),
                _server_request(
                    method="item/tool/requestUserInput",
                    params={"questions": []},
                ),
            )

    def test_user_input_is_explicitly_single_select_and_maps_option_labels(
        self,
    ) -> None:
        pending = map_appserver_request(
            self.application,
            _server_request(
                method="item/tool/requestUserInput",
                params={
                    "questions": [
                        {
                            "id": "environment",
                            "header": "Environment",
                            "question": "Where should this run?",
                            "options": [
                                {
                                    "label": "Staging 東京",
                                    "description": "Safer target",
                                },
                                {
                                    "label": "Production",
                                    "description": "Live target",
                                },
                            ],
                            "isOther": False,
                            "isSecret": False,
                        }
                    ]
                },
            ),
        )
        self.assertIsInstance(pending.request, UserInputRequest)
        request = pending.request
        assert isinstance(request, UserInputRequest)
        question = request.questions[0]
        self.assertEqual((question.min_answers, question.max_answers), (1, 1))
        self.assertEqual(
            build_appserver_response(
                pending,
                UserInputResponse({"environment": (question.choices[0].choice_id,)}),
            ),
            {"answers": {"environment": {"answers": ["Staging 東京"]}}},
        )

    def test_secret_user_input_remains_typed_for_secure_presenters(
        self,
    ) -> None:
        pending = map_appserver_request(
            self.application,
            _server_request(
                method="item/tool/requestUserInput",
                params={
                    "questions": [
                        {
                            "id": "token",
                            "header": "Secret",
                            "question": "Enter token",
                            "isSecret": True,
                        }
                    ]
                },
            ),
        )
        self.assertIsInstance(pending.request, UserInputRequest)
        request = pending.request
        assert isinstance(request, UserInputRequest)
        self.assertTrue(request.questions[0].secret)

    def test_permission_approval_preserves_native_permissions_payload(
        self,
    ) -> None:
        permissions = {
            "fileSystem": {"write": ["D:/repo"]},
            "network": {"enabled": True},
        }
        pending = map_appserver_request(
            self.application,
            _server_request(
                method="item/permissions/requestApproval",
                params={"permissions": permissions},
            ),
        )
        self.assertIsInstance(pending.request, ApprovalRequest)
        request = pending.request
        assert isinstance(request, ApprovalRequest)
        self.assertEqual(
            build_appserver_response(
                pending,
                ApprovalResponse("grant_requested_permissions"),
            ),
            {"permissions": permissions},
        )
        self.assertEqual(
            build_appserver_response(
                pending,
                ApprovalResponse("decline_requested_permissions"),
            ),
            {"permissions": {}},
        )
        self.assertIn(
            '"write": [\n      "D:/repo"',
            request.prompt,
        )

    def test_untrusted_native_fields_cannot_escape_approval_code_block(
        self,
    ) -> None:
        pending = map_appserver_request(
            self.application,
            _server_request(
                method="item/commandExecution/requestApproval",
                params={
                    "reason": "needed\n[Grant now](https://evil.invalid)",
                    "command": "echo ```\n## Fake approval\n`payload`",
                    "cwd": "D:/repo/`](https://evil.invalid)",
                    "availableDecisions": ["accept", "decline"],
                },
            ),
        )
        rendered = MarkdownRequestPresenter().present_request(
            pending.request,
            conversation_ref=ConversationRef("telegram-main", "chat-1"),
            delivery_id="delivery-1",
            reply_to_message_id=None,
        )
        content = rendered.message.content[0]
        assert isinstance(content, TextContent)
        body = content.text
        self.assertIn(
            "    [Grant now](https://evil.invalid)",
            body,
        )
        self.assertIn("    echo ```", body)
        self.assertIn("    ## Fake approval", body)
        self.assertNotIn("\n[Grant now](https://evil.invalid)", body)
        self.assertNotIn("\n```", body)

    def test_untrusted_question_labels_and_descriptions_are_escaped(
        self,
    ) -> None:
        pending = map_appserver_request(
            self.application,
            _server_request(
                method="item/tool/requestUserInput",
                params={
                    "questions": [
                        {
                            "id": "target",
                            "header": "Where?\n## Fake heading",
                            "question": "[Click](https://evil.invalid)",
                            "options": [
                                {
                                    "label": "[Approve](https://evil.invalid)",
                                    "description": "safe\n> injected quote",
                                }
                            ],
                        }
                    ]
                },
            ),
        )
        rendered = MarkdownRequestPresenter().present_request(
            pending.request,
            conversation_ref=ConversationRef("telegram-main", "chat-1"),
            delivery_id="delivery-2",
            reply_to_message_id=None,
        )
        content = rendered.message.content[0]
        assert isinstance(content, TextContent)
        body = content.text
        self.assertIn(
            r"\[Approve\]\(https://evil\.invalid\)",
            body,
        )
        self.assertIn("safe &gt; injected quote", body)
        self.assertNotIn("[Approve](https://evil.invalid)", body)

    def test_transport_id_reuse_is_scoped_by_connection_epoch(self) -> None:
        first = derive_appserver_request_ref(
            self.application,
            connection_epoch=1,
            transport_request_id=7,
        )
        second = derive_appserver_request_ref(
            self.application,
            connection_epoch=2,
            transport_request_id=7,
        )
        other_application = derive_appserver_request_ref(
            ApplicationRef("codex-other"),
            connection_epoch=1,
            transport_request_id=7,
        )
        self.assertEqual(len({first, second, other_application}), 3)


class InteractiveClient:
    def __init__(self) -> None:
        self.connection_epoch = 3
        self.notification_handlers = []
        self.server_request_handlers = []
        self.reset_handlers = []
        self.replies = []
        self.errors = []
        self.reply_started: asyncio.Event | None = None
        self.release_reply: asyncio.Event | None = None

    def add_notification_handler(self, handler) -> None:
        self.notification_handlers.append(handler)

    def add_server_request_handler(self, handler) -> None:
        self.server_request_handlers.append(handler)

    def add_connection_reset_handler(self, handler) -> None:
        self.reset_handlers.append(handler)

    async def reply_to_transport_request(
        self,
        request_id,
        result,
        *,
        expected_connection_epoch=None,
    ):
        self.replies.append((request_id, result, expected_connection_epoch))
        if self.reply_started is not None:
            self.reply_started.set()
        if self.release_reply is not None:
            await self.release_reply.wait()

    async def reply_error_to_transport_request(
        self,
        request_id,
        *,
        code,
        message,
        expected_connection_epoch=None,
    ):
        self.errors.append((request_id, code, message, expected_connection_epoch))

    async def emit_request(self, request: dict) -> None:
        for handler in self.server_request_handlers:
            result = handler(request)
            if asyncio.iscoroutine(result):
                await result

    async def emit_notification(self, notification: dict) -> None:
        params = notification.setdefault("params", {})
        if isinstance(params, dict):
            params.setdefault("_connection_epoch", self.connection_epoch)
        for handler in self.notification_handlers:
            result = handler(notification)
            if asyncio.iscoroutine(result):
                await result

    async def reset(self) -> None:
        previous = self.connection_epoch
        self.connection_epoch += 1
        for handler in self.reset_handlers:
            result = handler(previous)
            if asyncio.iscoroutine(result):
                await result

    async def read_thread(self, thread_id: str, *, include_turns: bool = False):
        return {
            "thread": {
                "id": thread_id,
                "cwd": "D:/repo",
                "status": {"type": "idle"},
                "turns": [] if include_turns else None,
            }
        }

    async def list_thread_turns(self, _thread_id: str, **_params):
        return {"data": [], "nextCursor": None}


class AppServerAdapterRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = InteractiveClient()
        self.adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=cast(Any, self.client),
            cwd="D:/repo",
        )
        self.thread = ThreadRef("codex-main", "thread-1")
        self.events = cast(Any, self.adapter.subscribe_thread(self.thread))

    async def asyncTearDown(self) -> None:
        await self.events.aclose()

    async def test_native_request_response_and_resolution_round_trip(self) -> None:
        self.assertIs(
            self.adapter.summary.capabilities.runtime.interactive_requests,
            SupportLevel.NATIVE,
        )
        opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_request(
            _server_request(
                method="item/fileChange/requestApproval",
                params={"reason": "Update generated files"},
            )
        )
        event = await opened
        self.assertIs(event.type, AgentEventType.REQUEST_OPENED)
        assert event.request is not None
        result = await self.adapter.execute(
            RespondRequest(
                operation_id="respond-1",
                application_ref=self.adapter.summary.ref,
                request_ref=event.request.request_ref,
                response=ApprovalResponse("acceptForSession"),
                thread_ref=self.thread,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(result, RequestResponded)
        self.assertEqual(
            self.client.replies,
            [(7, {"decision": "acceptForSession"}, 3)],
        )

        duplicate = await self.adapter.execute(
            RespondRequest(
                operation_id="respond-duplicate",
                application_ref=self.adapter.summary.ref,
                request_ref=event.request.request_ref,
                response=ApprovalResponse("accept"),
                thread_ref=self.thread,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(duplicate, ApplicationOperationFailed)
        assert isinstance(duplicate, ApplicationOperationFailed)
        self.assertEqual(
            duplicate.error.code,
            OperationErrorCode.REQUEST_DUPLICATE.value,
        )

        resolved = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_notification(
            {
                "method": "serverRequest/resolved",
                "params": {"requestId": 7},
            }
        )
        resolution_event = await resolved
        self.assertIs(
            resolution_event.type,
            AgentEventType.REQUEST_RESOLVED,
        )
        assert resolution_event.request_resolution is not None
        self.assertEqual(
            resolution_event.request_resolution.request_ref,
            event.request.request_ref,
        )

    async def test_connection_reset_emits_stale_and_rejects_late_response(
        self,
    ) -> None:
        opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_request(
            _server_request(
                method="item/commandExecution/requestApproval",
                params={"availableDecisions": ["accept", "decline"]},
            )
        )
        request_event = await opened
        assert request_event.request is not None
        stale = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.reset()
        stale_event = await stale
        self.assertIs(stale_event.type, AgentEventType.REQUEST_RESOLVED)

        result = await self.adapter.execute(
            RespondRequest(
                operation_id="respond-stale",
                application_ref=self.adapter.summary.ref,
                request_ref=request_event.request.request_ref,
                response=ApprovalResponse("accept"),
                thread_ref=self.thread,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(result, ApplicationOperationFailed)
        assert isinstance(result, ApplicationOperationFailed)
        self.assertEqual(
            result.error.code,
            OperationErrorCode.REQUEST_STALE.value,
        )

    async def test_capacity_one_reset_preserves_stale_before_terminal_gap(self) -> None:
        client = InteractiveClient()
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-capacity-one",
            client=cast(Any, client),
            cwd="D:/repo",
            event_buffer_max_pending=1,
        )
        thread_ref = ThreadRef("codex-capacity-one", "thread-1")
        events = cast(Any, adapter.subscribe_thread(thread_ref))
        try:
            await client.emit_request(
                _server_request(
                    method="item/commandExecution/requestApproval",
                    params={"availableDecisions": ["accept", "decline"]},
                )
            )
            opened = await anext(events)
            self.assertIs(opened.type, AgentEventType.REQUEST_OPENED)

            await client.reset()

            stale = await anext(events)
            self.assertIs(stale.type, AgentEventType.REQUEST_RESOLVED)
            with self.assertRaises(EventStreamReset):
                await anext(events)
        finally:
            await events.aclose()

    async def test_native_resolution_wins_during_response_writeback(self) -> None:
        opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_request(
            _server_request(
                method="item/fileChange/requestApproval",
                params={"reason": "Concurrent resolution"},
            )
        )
        request_event = await opened
        assert request_event.request is not None
        self.client.reply_started = asyncio.Event()
        self.client.release_reply = asyncio.Event()
        responding = asyncio.create_task(
            self.adapter.execute(
                RespondRequest(
                    operation_id="respond-while-resolving",
                    application_ref=self.adapter.summary.ref,
                    request_ref=request_event.request.request_ref,
                    response=ApprovalResponse("accept"),
                    thread_ref=self.thread,
                    created_at=datetime.now(UTC),
                )
            )
        )
        await self.client.reply_started.wait()
        resolved = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_notification(
            {
                "method": "serverRequest/resolved",
                "params": {"requestId": 7},
            }
        )
        self.assertIs((await resolved).type, AgentEventType.REQUEST_RESOLVED)
        self.client.release_reply.set()
        self.assertIsInstance(await responding, RequestResponded)

        repeated = await self.adapter.execute(
            RespondRequest(
                operation_id="respond-after-concurrent-resolution",
                application_ref=self.adapter.summary.ref,
                request_ref=request_event.request.request_ref,
                response=ApprovalResponse("accept"),
                thread_ref=self.thread,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(repeated, ApplicationOperationFailed)
        assert isinstance(repeated, ApplicationOperationFailed)
        self.assertEqual(
            repeated.error.code,
            OperationErrorCode.REQUEST_RESOLVED.value,
        )

    async def test_secret_request_emits_typed_request_for_secure_presenter(
        self,
    ) -> None:
        opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_request(
            _server_request(
                method="item/tool/requestUserInput",
                params={
                    "questions": [
                        {
                            "id": "token",
                            "header": "Secret",
                            "question": "Enter token",
                            "isSecret": True,
                        }
                    ]
                },
            )
        )
        event = await opened
        self.assertIs(event.type, AgentEventType.REQUEST_OPENED)
        self.assertIsInstance(event.request, UserInputRequest)
        assert isinstance(event.request, UserInputRequest)
        self.assertTrue(event.request.questions[0].secret)
        self.assertEqual(self.client.errors, [])

    async def test_unsupported_method_uses_method_not_found_error(self) -> None:
        await self.client.emit_request(
            _server_request(
                method="item/unknown/request",
                params={},
            )
        )
        self.assertEqual(self.client.errors[0][1], -32601)

    async def test_malformed_known_request_uses_invalid_params_error(
        self,
    ) -> None:
        await self.client.emit_request(
            _server_request(
                method="item/tool/requestUserInput",
                params={"questions": []},
            )
        )
        self.assertEqual(self.client.errors[0][1], -32602)

    async def test_late_old_epoch_resolution_cannot_resolve_reused_id(
        self,
    ) -> None:
        first_opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_request(
            _server_request(
                method="item/fileChange/requestApproval",
                params={},
            )
        )
        first_event = await first_opened
        assert first_event.request is not None

        stale = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.reset()
        await stale
        with self.assertRaises(EventStreamReset):
            await anext(self.events)
        self.events = cast(Any, self.adapter.subscribe_thread(self.thread))

        second_opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_request(
            _server_request(
                method="item/fileChange/requestApproval",
                params={},
                connection_epoch=4,
            )
        )
        second_event = await second_opened
        assert second_event.request is not None
        self.assertNotEqual(
            first_event.request.request_ref,
            second_event.request.request_ref,
        )

        await self.client.emit_notification(
            {
                "method": "serverRequest/resolved",
                "params": {
                    "requestId": 7,
                    "_connection_epoch": 3,
                },
            }
        )
        result = await self.adapter.execute(
            RespondRequest(
                operation_id="respond-current-epoch",
                application_ref=self.adapter.summary.ref,
                request_ref=second_event.request.request_ref,
                response=ApprovalResponse("accept"),
                thread_ref=self.thread,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(result, RequestResponded)

    async def test_zen_native_approval_round_trip_uses_evidenced_appserver_protocol(
        self,
    ) -> None:
        client = InteractiveClient()
        adapter = ZenApplicationAdapter(
            application_instance_id="zen-main",
            client=cast(Any, client),
            cwd="D:/repo",
        )
        self.assertIs(
            adapter.summary.capabilities.runtime.interactive_requests,
            SupportLevel.NATIVE,
        )
        self.assertEqual(len(client.server_request_handlers), 1)
        thread = ThreadRef("zen-main", "thread-1")
        events = cast(Any, adapter.subscribe_thread(thread))
        try:
            opened = asyncio.create_task(anext(events))
            await asyncio.sleep(0)
            await client.emit_request(
                _server_request(
                    method="item/commandExecution/requestApproval",
                    params={
                        "command": "printf hello",
                        "cwd": "D:/repo",
                        "availableDecisions": ["accept", "decline", "cancel"],
                    },
                )
            )
            event = await opened
            self.assertIs(event.type, AgentEventType.REQUEST_OPENED)
            assert isinstance(event.request, ApprovalRequest)
            self.assertEqual(event.request.thread_ref, thread)

            result = await adapter.execute(
                RespondRequest(
                    operation_id="zen-respond-1",
                    application_ref=adapter.summary.ref,
                    request_ref=event.request.request_ref,
                    response=ApprovalResponse("accept"),
                    thread_ref=thread,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(result, RequestResponded)
            self.assertEqual(client.replies, [(7, {"decision": "accept"}, 3)])

            await client.emit_request(
                _server_request(
                    method="item/tool/requestUserInput",
                    params={"questions": []},
                    transport_request_id=8,
                )
            )
            self.assertEqual(client.errors[0][1], -32601)
        finally:
            await events.aclose()

    async def test_zen_approval_projects_and_responds_through_gateway(self) -> None:
        client = InteractiveClient()
        adapter = ZenApplicationAdapter(
            application_instance_id="zen-gateway",
            client=cast(Any, client),
            cwd="D:/repo",
        )
        channel = FakeChannelAdapter("zen-channel")
        correlations = InMemoryRequestCorrelationRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[adapter],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=InMemoryProjectionRouteRepository(),
                request_correlations=correlations,
            ),
            extensions=GatewayExtensions(
                request_presenter=MarkdownRequestPresenter(),
            ),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )
        conversation = ConversationRef("zen-channel", "conversation-1")
        thread = ThreadRef("zen-gateway", "thread-1")
        await gateway.start()
        try:
            observed = await gateway.execute_gateway(
                ObserveThread(
                    operation_id="observe-zen-thread",
                    conversation_ref=conversation,
                    actor="user-1",
                    thread_ref=thread,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertEqual(observed.type.value, "thread.observe")

            await client.emit_request(
                _server_request(
                    method="item/commandExecution/requestApproval",
                    params={
                        "command": "git status",
                        "availableDecisions": ["accept", "decline"],
                    },
                )
            )
            request_ref = derive_appserver_request_ref(
                adapter.summary.ref,
                connection_epoch=3,
                transport_request_id=7,
            )
            async with asyncio.timeout(1):
                while not await correlations.list_request_correlations(request_ref=request_ref):
                    await asyncio.sleep(0)
            self.assertEqual(channel.sent[0].conversation_ref, conversation)

            routed = await gateway.execute_gateway(
                RespondToRequest(
                    operation_id="respond-to-zen-request",
                    conversation_ref=conversation,
                    actor="user-1",
                    request_ref=request_ref,
                    response=ApprovalResponse("accept"),
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(routed, RequestResponseRouted)
            self.assertEqual(client.replies, [(7, {"decision": "accept"}, 3)])
        finally:
            await gateway.stop()

    async def test_terminal_request_diagnostics_are_bounded(self) -> None:
        for request_id in range(300):
            await self.client.emit_request(
                _server_request(
                    method="item/fileChange/requestApproval",
                    params={},
                    transport_request_id=request_id,
                )
            )
            result = await self.adapter.execute(
                RespondRequest(
                    operation_id=f"respond-{request_id}",
                    application_ref=self.adapter.summary.ref,
                    request_ref=derive_appserver_request_ref(
                        self.adapter.summary.ref,
                        connection_epoch=3,
                        transport_request_id=request_id,
                    ),
                    response=ApprovalResponse("accept"),
                    thread_ref=self.thread,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(result, RequestResponded)
        published = [await anext(self.events) for _ in range(344)]
        retention_stale = [
            event for event in published if event.type is AgentEventType.REQUEST_RESOLVED
        ]
        self.assertEqual(len(retention_stale), 44)
        assert retention_stale[0].request_resolution is not None
        self.assertEqual(
            retention_stale[0].request_resolution.request_ref,
            derive_appserver_request_ref(
                self.adapter.summary.ref,
                connection_epoch=3,
                transport_request_id=0,
            ),
        )
        self.assertLessEqual(
            self.adapter._request_runtime.terminal_count,
            256,
        )
        self.assertEqual(self.adapter._request_runtime.pending_count, 0)
        recent = await self.adapter.execute(
            RespondRequest(
                operation_id="respond-recent-again",
                application_ref=self.adapter.summary.ref,
                request_ref=derive_appserver_request_ref(
                    self.adapter.summary.ref,
                    connection_epoch=3,
                    transport_request_id=299,
                ),
                response=ApprovalResponse("accept"),
                thread_ref=self.thread,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(recent, ApplicationOperationFailed)
        assert isinstance(recent, ApplicationOperationFailed)
        self.assertEqual(
            recent.error.code,
            OperationErrorCode.REQUEST_DUPLICATE.value,
        )

        evicted = await self.adapter.execute(
            RespondRequest(
                operation_id="respond-evicted-again",
                application_ref=self.adapter.summary.ref,
                request_ref=derive_appserver_request_ref(
                    self.adapter.summary.ref,
                    connection_epoch=3,
                    transport_request_id=0,
                ),
                response=ApprovalResponse("accept"),
                thread_ref=self.thread,
                created_at=datetime.now(UTC),
            )
        )
        self.assertIsInstance(evicted, ApplicationOperationFailed)
        assert isinstance(evicted, ApplicationOperationFailed)
        self.assertEqual(
            evicted.error.code,
            OperationErrorCode.REQUEST_STALE.value,
        )


def _server_request(
    *,
    method: str,
    params: dict,
    connection_epoch: int = 3,
    transport_request_id: str | int = 7,
) -> dict:
    return {
        "id": transport_request_id,
        "method": method,
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-1",
            "_transport_request_id": transport_request_id,
            "_connection_epoch": connection_epoch,
            **params,
        },
    }


if __name__ == "__main__":
    unittest.main()

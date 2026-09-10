from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any, cast

from imagent.applications.adapters.appserver.mapping import APP_SERVER_MAPPING_ERROR_MESSAGE
from imagent.applications.adapters.appserver.requests import (
    build_appserver_response,
    derive_appserver_request_ref,
    map_appserver_request,
    map_zen_appserver_request,
)
from imagent.applications.adapters.codex import CodexApplicationAdapter
from imagent.applications.adapters.zen import ZenApplicationAdapter
from imagent.applications.capabilities import SupportLevel
from imagent.applications.contract import ApplicationRef, ProjectRef, ThreadRef, TurnRef
from imagent.applications.events import AgentEventType, EventStreamReset
from imagent.applications.operations import (
    ApplicationOperationFailed,
    RequestResponded,
    RespondRequest,
)
from imagent.applications.requests import (
    MAX_INTERACTIVE_REQUEST_CHOICES,
    MAX_INTERACTIVE_REQUEST_QUESTIONS,
    ApprovalRequest,
    ApprovalResponse,
    UserInputRequest,
    UserInputResponse,
)
from imagent.interaction.controllers import MarkdownRequestPresenter
from imagent.interaction.messages import ConversationRef, TextContent
from imagent.interaction.operations import OperationErrorCode


class AppServerRequestMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application = ApplicationRef("codex-main")
        self.project = ProjectRef("codex-main", "workspace")

    def test_command_choices_preserve_native_response_payloads(self) -> None:
        amendment = {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "status"]}}
        pending = map_appserver_request(
            self.project,
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
                ProjectRef("zen-main", "workspace"),
                _server_request(
                    method="item/tool/requestUserInput",
                    params={"questions": []},
                ),
            )

    def test_mapper_rejects_oversized_request_collections_before_mapping(self) -> None:
        with self.assertRaisesRegex(ValueError, "questions exceed"):
            map_appserver_request(
                self.project,
                _server_request(
                    method="item/tool/requestUserInput",
                    params={
                        "questions": [
                            {
                                "id": f"question-{index}",
                                "question": "Choose one",
                            }
                            for index in range(MAX_INTERACTIVE_REQUEST_QUESTIONS + 1)
                        ]
                    },
                ),
            )
        with self.assertRaisesRegex(ValueError, "options exceed"):
            map_appserver_request(
                self.project,
                _server_request(
                    method="item/tool/requestUserInput",
                    params={
                        "questions": [
                            {
                                "id": "question-1",
                                "question": "Choose one",
                                "options": [
                                    {"label": f"Option {index}"}
                                    for index in range(MAX_INTERACTIVE_REQUEST_CHOICES + 1)
                                ],
                            }
                        ]
                    },
                ),
            )
        with self.assertRaisesRegex(ValueError, "decisions exceed"):
            map_appserver_request(
                self.project,
                _server_request(
                    method="item/commandExecution/requestApproval",
                    params={
                        "command": "true",
                        "availableDecisions": [
                            f"decision-{index}"
                            for index in range(MAX_INTERACTIVE_REQUEST_CHOICES + 1)
                        ],
                    },
                ),
            )

    def test_user_input_is_explicitly_single_select_and_maps_option_labels(
        self,
    ) -> None:
        pending = map_appserver_request(
            self.project,
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
            self.project,
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
            self.project,
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
            self.project,
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
            self.project,
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
    def __init__(self, *, workspace_cwd: str | None = "D:/repo") -> None:
        self.connection_epoch = 3
        self.workspace_cwd = workspace_cwd
        self.notification_handlers = []
        self.server_request_handlers = []
        self.reset_handlers = []
        self.replies = []
        self.errors = []
        self.reply_started: asyncio.Event | None = None
        self.release_reply: asyncio.Event | None = None
        self.read_calls = 0
        self.max_successful_reads: int | None = None
        self.fail_reads_after_reply = False
        self.fail_reads = False

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
        if self.fail_reads_after_reply:
            self.fail_reads = True
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
        notification.setdefault("_connection_epoch", self.connection_epoch)
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
        self.read_calls += 1
        if self.fail_reads or (
            self.max_successful_reads is not None and self.read_calls > self.max_successful_reads
        ):
            raise RuntimeError("injected native scope-read failure")
        thread = {
            "id": thread_id,
            "status": {"type": "idle"},
            "turns": [] if include_turns else None,
        }
        if self.workspace_cwd is not None:
            thread["cwd"] = self.workspace_cwd
        return {"thread": thread}

    async def list_thread_turns(self, _thread_id: str, **_params):
        return {"data": [], "nextCursor": None}


class AppServerAdapterRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = InteractiveClient()
        self.adapter = CodexApplicationAdapter(
            application_instance_id="codex-main",
            client=cast(Any, self.client),
            workspace_id="workspace",
            cwd="D:/repo",
        )
        self.thread = ThreadRef(ProjectRef("codex-main", "workspace"), "thread-1")
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
                turn_ref=event.request.turn_ref,
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
                turn_ref=event.request.turn_ref,
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

    async def test_request_open_carries_single_pre_effect_scope_verification(self) -> None:
        client = InteractiveClient()
        client.max_successful_reads = 1
        adapter = CodexApplicationAdapter(
            application_instance_id="codex-single-scope-read",
            client=cast(Any, client),
            workspace_id="workspace",
            cwd="D:/repo",
        )
        thread_ref = ThreadRef(
            ProjectRef("codex-single-scope-read", "workspace"),
            "thread-1",
        )
        events = cast(Any, adapter.subscribe_thread(thread_ref))
        try:
            opened = asyncio.create_task(anext(events))
            await asyncio.sleep(0)
            await client.emit_request(
                _server_request(
                    method="item/fileChange/requestApproval",
                    params={"reason": "verified once before publication"},
                )
            )
            self.assertIs((await opened).type, AgentEventType.REQUEST_OPENED)
            self.assertEqual(client.read_calls, 1)
            self.assertEqual(adapter._request_runtime.pending_count, 1)
        finally:
            await events.aclose()

    async def test_retention_publication_cannot_reverse_native_response_success(self) -> None:
        for request_id in range(257):
            await self.client.emit_request(
                _server_request(
                    method="item/fileChange/requestApproval",
                    params={},
                    transport_request_id=request_id,
                )
            )
            if request_id == 256:
                self.client.fail_reads_after_reply = True
            result = await self.adapter.execute(
                RespondRequest(
                    operation_id=f"retention-response-{request_id}",
                    application_ref=self.adapter.summary.ref,
                    request_ref=derive_appserver_request_ref(
                        self.adapter.summary.ref,
                        connection_epoch=3,
                        transport_request_id=request_id,
                    ),
                    response=ApprovalResponse("accept"),
                    turn_ref=TurnRef(self.thread, "turn-1"),
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(result, RequestResponded)
        self.assertTrue(self.client.fail_reads)
        self.assertEqual(self.adapter._request_runtime.pending_count, 0)
        self.assertEqual(self.adapter._request_runtime.terminal_count, 256)

    async def test_request_open_scope_failure_resets_without_admitting_state(self) -> None:
        for native_cwd, fail_reads in (
            (None, False),
            ("D:/other-workspace", False),
            ("D:/repo", True),
        ):
            with self.subTest(native_cwd=native_cwd, fail_reads=fail_reads):
                client = InteractiveClient(workspace_cwd=native_cwd)
                client.fail_reads = fail_reads
                adapter = CodexApplicationAdapter(
                    application_instance_id="codex-scoped-request",
                    client=cast(Any, client),
                    workspace_id="workspace",
                    cwd="D:/repo",
                )
                thread_ref = ThreadRef(
                    ProjectRef("codex-scoped-request", "workspace"),
                    "thread-1",
                )
                events = cast(Any, adapter.subscribe_thread(thread_ref))
                opened = asyncio.create_task(anext(events))
                await asyncio.sleep(0)
                try:
                    await client.emit_request(
                        _server_request(
                            method="item/fileChange/requestApproval",
                            params={"reason": "must not escape foreign scope"},
                        )
                    )
                    with self.assertRaisesRegex(
                        EventStreamReset,
                        "application_native_mapping_failed",
                    ):
                        await opened
                    self.assertEqual(
                        client.errors,
                        [(7, -32602, APP_SERVER_MAPPING_ERROR_MESSAGE, 3)],
                    )
                    self.assertEqual(adapter._request_runtime.pending_count, 0)
                    self.assertEqual(adapter._request_runtime.terminal_count, 0)
                finally:
                    await events.aclose()

    async def test_request_response_rechecks_native_workspace_before_mutation(self) -> None:
        opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_request(
            _server_request(
                method="item/fileChange/requestApproval",
                params={"reason": "scope may change before response"},
            )
        )
        event = await opened
        assert event.request is not None
        self.client.workspace_cwd = "D:/other-workspace"

        result = await self.adapter.execute(
            RespondRequest(
                operation_id="respond-foreign-workspace",
                application_ref=self.adapter.summary.ref,
                request_ref=event.request.request_ref,
                response=ApprovalResponse("accept"),
                turn_ref=event.request.turn_ref,
                created_at=datetime.now(UTC),
            )
        )

        self.assertIsInstance(result, ApplicationOperationFailed)
        self.assertEqual(self.client.replies, [])

    async def test_request_resolution_scope_failure_resets_without_mutating_state(
        self,
    ) -> None:
        for native_cwd, fail_reads in ((None, False), ("D:/repo", True)):
            with self.subTest(native_cwd=native_cwd, fail_reads=fail_reads):
                client = InteractiveClient()
                adapter = CodexApplicationAdapter(
                    application_instance_id="codex-resolution-scope",
                    client=cast(Any, client),
                    workspace_id="workspace",
                    cwd="D:/repo",
                )
                thread_ref = ThreadRef(
                    ProjectRef("codex-resolution-scope", "workspace"),
                    "thread-1",
                )
                events = cast(Any, adapter.subscribe_thread(thread_ref))
                try:
                    opened = asyncio.create_task(anext(events))
                    await asyncio.sleep(0)
                    await client.emit_request(
                        _server_request(
                            method="item/fileChange/requestApproval",
                            params={"reason": "scope may disappear before resolution"},
                        )
                    )
                    self.assertIs((await opened).type, AgentEventType.REQUEST_OPENED)
                    client.workspace_cwd = native_cwd
                    client.fail_reads = fail_reads

                    await client.emit_notification(
                        {
                            "method": "serverRequest/resolved",
                            "params": {"requestId": 7},
                        }
                    )

                    with self.assertRaisesRegex(
                        EventStreamReset,
                        "application_native_mapping_failed",
                    ):
                        await anext(events)
                    self.assertEqual(adapter._request_runtime.pending_count, 1)
                    self.assertEqual(adapter._request_runtime.terminal_count, 0)
                finally:
                    await events.aclose()

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
                turn_ref=request_event.request.turn_ref,
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
            workspace_id="workspace",
            cwd="D:/repo",
            event_buffer_max_pending=1,
        )
        thread_ref = ThreadRef(ProjectRef("codex-capacity-one", "workspace"), "thread-1")
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
                    turn_ref=request_event.request.turn_ref,
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
                turn_ref=request_event.request.turn_ref,
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

    async def test_unsupported_method_without_params_uses_method_not_found_error(self) -> None:
        opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_request(
            {
                "id": 7,
                "method": "item/unknown/request",
                "_connection_epoch": 3,
            }
        )
        await asyncio.sleep(0)
        self.assertFalse(opened.done())
        opened.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await opened
        self.assertEqual(self.client.errors[0][1], -32601)
        self.assertEqual(self.adapter._request_runtime.pending_count, 0)

    async def test_malformed_known_request_uses_invalid_params_error(
        self,
    ) -> None:
        opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        await self.client.emit_request(
            _server_request(
                method="item/tool/requestUserInput",
                params={"questions": []},
            )
        )
        await asyncio.sleep(0)
        self.assertFalse(opened.done())
        opened.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await opened
        self.assertEqual(
            self.client.errors,
            [(7, -32602, APP_SERVER_MAPPING_ERROR_MESSAGE, 3)],
        )
        self.assertEqual(self.adapter._request_runtime.pending_count, 0)

    async def test_invalid_native_request_identity_rejects_before_request_open(self) -> None:
        opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        malformed = _server_request(
            method="item/fileChange/requestApproval",
            params={"reason": "must not become a request"},
        )
        malformed["params"]["threadId"] = ""
        await self.client.emit_request(malformed)
        await asyncio.sleep(0)
        self.assertFalse(opened.done())
        opened.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await opened
        self.assertEqual(
            self.client.errors,
            [(7, -32602, APP_SERVER_MAPPING_ERROR_MESSAGE, 3)],
        )
        self.assertEqual(self.adapter._request_runtime.pending_count, 0)

    async def test_conflicting_native_request_item_alias_rejects_before_request_open(self) -> None:
        opened = asyncio.create_task(anext(self.events))
        await asyncio.sleep(0)
        malformed = _server_request(
            method="item/fileChange/requestApproval",
            params={
                "reason": "must not become a request",
                "item": {"id": "item-conflicts-with-outer-alias"},
            },
        )
        await self.client.emit_request(malformed)
        await asyncio.sleep(0)
        self.assertFalse(opened.done())
        opened.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await opened
        self.assertEqual(
            self.client.errors,
            [(7, -32602, APP_SERVER_MAPPING_ERROR_MESSAGE, 3)],
        )
        self.assertEqual(self.adapter._request_runtime.pending_count, 0)

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

        with self.assertLogs(
            "imagent.applications.adapters.appserver.requests", level="WARNING"
        ) as logs:
            await self.client.emit_notification(
                {
                    "method": "serverRequest/resolved",
                    "_connection_epoch": 3,
                    "params": {
                        "requestId": "native-request-id-sentinel",
                    },
                }
            )
        self.assertNotIn("native-request-id-sentinel", "\n".join(logs.output))
        result = await self.adapter.execute(
            RespondRequest(
                operation_id="respond-current-epoch",
                application_ref=self.adapter.summary.ref,
                request_ref=second_event.request.request_ref,
                response=ApprovalResponse("accept"),
                turn_ref=second_event.request.turn_ref,
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
            workspace_id="workspace",
            cwd="D:/repo",
        )
        self.assertIs(
            adapter.summary.capabilities.runtime.interactive_requests,
            SupportLevel.NATIVE,
        )
        self.assertEqual(len(client.server_request_handlers), 1)
        thread = ThreadRef(ProjectRef("zen-main", "workspace"), "thread-1")
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
            self.assertEqual(event.request.turn_ref.thread_ref, thread)

            result = await adapter.execute(
                RespondRequest(
                    operation_id="zen-respond-1",
                    application_ref=adapter.summary.ref,
                    request_ref=event.request.request_ref,
                    response=ApprovalResponse("accept"),
                    turn_ref=event.request.turn_ref,
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
                    turn_ref=TurnRef(self.thread, "turn-1"),
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
                turn_ref=TurnRef(self.thread, "turn"),
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
                turn_ref=TurnRef(self.thread, "turn"),
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
        "_connection_epoch": connection_epoch,
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-1",
            **params,
        },
    }


if __name__ == "__main__":
    unittest.main()

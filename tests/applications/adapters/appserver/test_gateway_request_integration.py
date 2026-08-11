from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any, cast

from imagent.applications import ZenApplicationAdapter
from imagent.applications.adapters.appserver.requests import (
    derive_appserver_request_ref,
)
from imagent.applications.contract import ProjectRef, ThreadRef
from imagent.applications.requests import ApprovalResponse
from imagent.gateway import GatewayExtensions
from imagent.gateway.composition import _GatewayRuntimeDependencies
from imagent.gateway.orchestration import _GatewayRuntime
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryProjectionRouteRepository,
    InMemoryRequestCorrelationRepository,
)
from imagent.gateway.projection import RequestResponseRouted, RespondToRequest
from imagent.gateway.routing import ObserveThread, ProjectionPolicy
from imagent.interaction.controllers import MarkdownRequestPresenter
from imagent.interaction.messages import ConversationRef
from imagent.testing import FakeChannelAdapter


class InteractiveClient:
    def __init__(self) -> None:
        self.connection_epoch = 3
        self.notification_handlers = []
        self.server_request_handlers = []
        self.reset_handlers = []
        self.replies = []
        self.errors = []

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


class AppServerGatewayRequestIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_zen_approval_projects_and_responds_through_gateway(self) -> None:
        client = InteractiveClient()
        adapter = ZenApplicationAdapter(
            application_instance_id="zen-gateway",
            client=cast(Any, client),
            workspace_id="workspace",
            cwd="D:/repo",
        )
        channel = FakeChannelAdapter("zen-channel")
        correlations = InMemoryRequestCorrelationRepository()
        gateway = _GatewayRuntime(
            channels=[channel],
            applications=[adapter],
            repositories=_GatewayRuntimeDependencies(
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
        thread = ThreadRef(ProjectRef("zen-gateway", "workspace"), "thread-1")
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

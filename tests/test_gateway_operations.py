from __future__ import annotations

import unittest
from datetime import UTC, datetime

from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import (
    ActivateNativeThread,
    ApplicationRef,
    BindConversationToThread,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    CreateThread,
    ListProjects,
    ListThreads,
    NativeThreadActivated,
    ProjectRef,
    ProjectsListed,
    ThreadCreated,
    ThreadsListed,
)
from imagent.gateway import ImAgentGateway
from imagent.testing import FakeAgentApplicationAdapter


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


def _now() -> datetime:
    return datetime.now(UTC)


if __name__ == "__main__":
    unittest.main()

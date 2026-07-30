from __future__ import annotations

import unittest

from imagent.bindings import BindingConflict, InMemoryBindingRepository
from imagent.contracts import (
    ApplicationRef,
    ConversationBinding,
    ConversationRef,
    ThreadRef,
)


class InMemoryBindingRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryBindingRepository()
        self.conversation = ConversationRef("qq-primary", "c2c:user-1")

    async def test_put_assigns_monotonic_revision(self) -> None:
        first = await self.repository.put(
            ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=ApplicationRef("zen-local"),
            )
        )
        second = await self.repository.put(
            ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=ApplicationRef("zen-local"),
                thread_ref=ThreadRef("zen-local", "thread-1"),
            ),
            expected_revision=first.revision,
        )
        self.assertEqual(first.revision, 1)
        self.assertEqual(second.revision, 2)
        self.assertIsNotNone(second.updated_at)
        self.assertEqual(await self.repository.get(self.conversation), second)

    async def test_put_rejects_stale_revision(self) -> None:
        await self.repository.put(
            ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=ApplicationRef("zen-local"),
            )
        )
        with self.assertRaises(BindingConflict):
            await self.repository.put(
                ConversationBinding(
                    conversation_ref=self.conversation,
                    application_ref=ApplicationRef("t3-local"),
                ),
                expected_revision=0,
            )

    async def test_delete_supports_revision_guard(self) -> None:
        stored = await self.repository.put(
            ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=ApplicationRef("zen-local"),
            )
        )
        await self.repository.delete(self.conversation, expected_revision=stored.revision)
        self.assertIsNone(await self.repository.get(self.conversation))


if __name__ == "__main__":
    unittest.main()

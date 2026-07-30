from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from imagent.contracts import (
    ApplicationRef,
    ConversationBinding,
    ConversationRef,
    ProjectRef,
    ThreadRef,
)
from imagent.storage import SQLiteGatewayState


class SQLiteGatewayStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_bindings_and_completed_idempotency_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.sqlite3"
            conversation = ConversationRef("qq-main", "c2c:user-1")
            project = ProjectRef("t3-main", "project-1")
            first = SQLiteGatewayState(path)
            stored = await first.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("t3-main"),
                    project_ref=project,
                    thread_ref=ThreadRef(
                        "t3-main",
                        "thread-1",
                        project,
                    ),
                )
            )
            self.assertTrue(await first.claim("inbound:qq-main", "message-1"))
            await first.complete("inbound:qq-main", "message-1")
            await first.close()

            second = SQLiteGatewayState(path)
            try:
                self.assertEqual(await second.get(conversation), stored)
                self.assertFalse(await second.claim("inbound:qq-main", "message-1"))
                self.assertTrue(await second.claim("inbound:qq-main", "released-message"))
                await second.release(
                    "inbound:qq-main",
                    "released-message",
                )
                self.assertTrue(await second.claim("inbound:qq-main", "released-message"))
                self.assertTrue(await second.claim("inbound:qq-main", "stale-message"))
            finally:
                await second.close()

            recovered = SQLiteGatewayState(
                path,
                stale_claim_after_seconds=0,
            )
            try:
                self.assertTrue(await recovered.claim("inbound:qq-main", "stale-message"))
                self.assertFalse(await recovered.claim("inbound:qq-main", "message-1"))
            finally:
                await recovered.close()


if __name__ == "__main__":
    unittest.main()

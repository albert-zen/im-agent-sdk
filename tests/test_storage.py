from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from imagent.adapters import IdempotencyClaimStatus
from imagent.contracts import (
    AgentInput,
    ApplicationRef,
    ConversationBinding,
    ConversationRef,
    ProjectMode,
    ProjectRef,
    TextContent,
    ThreadProjectionRoute,
    ThreadRef,
    TurnReplyCorrelation,
)
from imagent.gateway import ImAgentGateway
from imagent.projections import derive_projection_route_id, derive_turn_reply_correlation_id
from imagent.storage import SQLiteGatewayState
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


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
            self.assertEqual(
                await first.claim("inbound:qq-main", "message-1"),
                IdempotencyClaimStatus.ACQUIRED,
            )
            await first.complete("inbound:qq-main", "message-1")
            await first.close()

            second = SQLiteGatewayState(path)
            try:
                self.assertEqual(await second.get(conversation), stored)
                self.assertEqual(
                    await second.claim("inbound:qq-main", "message-1"),
                    IdempotencyClaimStatus.ALREADY_COMPLETED,
                )
                self.assertEqual(
                    await second.claim("inbound:qq-main", "released-message"),
                    IdempotencyClaimStatus.ACQUIRED,
                )
                await second.release(
                    "inbound:qq-main",
                    "released-message",
                )
                self.assertEqual(
                    await second.claim("inbound:qq-main", "released-message"),
                    IdempotencyClaimStatus.ACQUIRED,
                )
                self.assertEqual(
                    await second.claim("inbound:qq-main", "stale-message"),
                    IdempotencyClaimStatus.ACQUIRED,
                )
            finally:
                await second.close()

            recovered = SQLiteGatewayState(
                path,
                stale_claim_after_seconds=0,
            )
            try:
                self.assertEqual(
                    await recovered.claim("inbound:qq-main", "stale-message"),
                    IdempotencyClaimStatus.ACQUIRED,
                )
                self.assertEqual(
                    await recovered.claim("inbound:qq-main", "message-1"),
                    IdempotencyClaimStatus.ALREADY_COMPLETED,
                )
            finally:
                await recovered.close()

    async def test_existing_database_migrates_without_losing_bridge_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            now = datetime.now(UTC)
            conversation = ConversationRef("qq-main", "c2c:user-1")
            project = ProjectRef("t3-main", "project-1")
            thread = ThreadRef("t3-main", "thread-1", project)
            route_id = derive_projection_route_id(thread, conversation)
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE conversation_bindings (
                        channel_instance_id TEXT NOT NULL,
                        native_conversation_id TEXT NOT NULL,
                        application_instance_id TEXT,
                        project_id TEXT,
                        thread_id TEXT,
                        revision INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (channel_instance_id, native_conversation_id)
                    );
                    CREATE TABLE idempotency_records (
                        scope TEXT NOT NULL,
                        record_key TEXT NOT NULL,
                        status TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (scope, record_key)
                    );
                    CREATE TABLE thread_projection_routes (
                        route_id TEXT NOT NULL PRIMARY KEY,
                        application_instance_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        thread_id TEXT NOT NULL,
                        channel_instance_id TEXT NOT NULL,
                        native_conversation_id TEXT NOT NULL,
                        reply_to_message_id TEXT,
                        updated_at TEXT NOT NULL,
                        UNIQUE (
                            application_instance_id,
                            project_id,
                            thread_id,
                            channel_instance_id,
                            native_conversation_id
                        )
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO conversation_bindings
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation.channel_instance_id,
                        conversation.native_conversation_id,
                        "t3-main",
                        "project-1",
                        "thread-1",
                        7,
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    "INSERT INTO idempotency_records VALUES (?, ?, ?, ?)",
                    ("outbound:qq-main", "delivery-1", "completed", now.isoformat()),
                )
                connection.execute(
                    """
                    INSERT INTO thread_projection_routes
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        route_id,
                        "t3-main",
                        "project-1",
                        "thread-1",
                        conversation.channel_instance_id,
                        conversation.native_conversation_id,
                        "legacy-reply",
                        now.isoformat(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            state = SQLiteGatewayState(path)
            try:
                binding = await state.get(conversation)
                assert binding is not None
                self.assertEqual(binding.revision, 7)
                self.assertEqual(binding.thread_ref, thread)
                self.assertEqual(
                    await state.claim("outbound:qq-main", "delivery-1"),
                    IdempotencyClaimStatus.ALREADY_COMPLETED,
                )
                legacy_route = (await state.list_projection_routes(thread))[0]
                self.assertIsNone(legacy_route.reply_to_message_id)
                self.assertIsNone(legacy_route.checkpoint_agent_item_id)

                checkpointed_at = now + timedelta(seconds=1)
                advanced = await state.advance_projection_checkpoint(
                    route_id,
                    expected_agent_item_id=None,
                    agent_item_id="agent-item-1",
                    checkpointed_at=checkpointed_at,
                )
                refreshed = await state.put_projection_route(
                    ThreadProjectionRoute(
                        route_id=route_id,
                        thread_ref=thread,
                        conversation_ref=conversation,
                        reply_to_message_id=None,
                    )
                )
                self.assertEqual(
                    refreshed.checkpoint_agent_item_id,
                    advanced.checkpoint_agent_item_id,
                )

                correlation = TurnReplyCorrelation(
                    correlation_id=derive_turn_reply_correlation_id(
                        thread,
                        "turn-1",
                    ),
                    thread_ref=thread,
                    turn_id="turn-1",
                    client_message_id="client-message-1",
                    conversation_ref=conversation,
                    reply_to_message_id="origin-message-1",
                    created_at=now,
                )
                await state.put_turn_reply_correlation(correlation)
                self.assertEqual(
                    await state.get_turn_reply_correlation(thread, "turn-1"),
                    correlation,
                )
                self.assertTrue(await state.delete_turn_reply_correlation(thread, "turn-1"))
                await state.put_turn_reply_correlation(correlation)
                self.assertEqual(
                    await state.delete_turn_reply_correlations(thread_ref=thread),
                    1,
                )
            finally:
                await state.close()

    async def test_migration_clears_legacy_latest_reply_for_external_turn(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-route.sqlite3"
            application = FakeAgentApplicationAdapter(
                application_instance_id="fake-agent",
                project_mode=ProjectMode.FLAT,
            )
            thread = await application.create_thread()
            conversation = ConversationRef("fake-channel", "conversation")
            route_id = derive_projection_route_id(thread.ref, conversation)
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE thread_projection_routes (
                        route_id TEXT NOT NULL PRIMARY KEY,
                        application_instance_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        thread_id TEXT NOT NULL,
                        channel_instance_id TEXT NOT NULL,
                        native_conversation_id TEXT NOT NULL,
                        reply_to_message_id TEXT,
                        updated_at TEXT NOT NULL,
                        UNIQUE (
                            application_instance_id,
                            project_id,
                            thread_id,
                            channel_instance_id,
                            native_conversation_id
                        )
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO thread_projection_routes
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        route_id,
                        thread.ref.application_instance_id,
                        "",
                        thread.ref.native_thread_id,
                        conversation.channel_instance_id,
                        conversation.native_conversation_id,
                        "stale-latest-inbound",
                        datetime.now(UTC).isoformat(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            state = SQLiteGatewayState(path)
            channel = FakeChannelAdapter()
            await state.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=application.summary.ref,
                    thread_ref=thread.ref,
                )
            )
            gateway = ImAgentGateway(
                channels=[channel],
                applications=[application],
                bindings=state,
                projections=state,
                idempotency=state,
            )
            await gateway.start()
            try:
                migrated = (await state.list_projection_routes(thread.ref))[0]
                self.assertIsNone(migrated.reply_to_message_id)
                await application.send_input(
                    thread.ref,
                    AgentInput(
                        client_message_id="external-after-upgrade",
                        content=(TextContent("external"),),
                    ),
                )
                async with asyncio.timeout(1):
                    while len(channel.sent) < 2:
                        await asyncio.sleep(0)
                self.assertEqual(
                    [message.reply_to for message in channel.sent],
                    [None, None],
                )
            finally:
                await gateway.stop()
                await state.close()


if __name__ == "__main__":
    unittest.main()

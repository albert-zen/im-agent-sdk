from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from imagent.adapters import (
    IdempotencyClaimStatus,
    RequestCorrelationConflict,
    TurnReplyCorrelationConflict,
)
from imagent.contracts import (
    MAX_DELIVERY_SUBMISSION_DESTINATIONS,
    AgentInput,
    ApplicationRef,
    ApprovalResponseShape,
    ConversationBinding,
    ConversationRef,
    DeliveryRouteSnapshot,
    DeliverySubmissionOrigin,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    ProjectMode,
    ProjectRef,
    RequestRef,
    RequestRouteCorrelation,
    RequestRouteState,
    TextContent,
    ThreadProjectionRoute,
    ThreadRef,
    TurnReplyCorrelation,
)
from imagent.gateway import GatewayRepositories, ImAgentGateway
from imagent.gateway.persistence import BindingConflict
from imagent.gateway.persistence.memory import InMemoryProjectionRouteRepository
from imagent.interaction.operations import ContractViolation
from imagent.projections import (
    derive_projection_route_id,
    derive_turn_reply_correlation_id,
)
from imagent.request_correlations import (
    InMemoryRequestCorrelationRepository,
    derive_request_correlation_id,
)
from imagent.storage import SQLiteGatewayState
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class SQLiteGatewayStateTests(unittest.IsolatedAsyncioTestCase):
    def _request_correlation(
        self,
        *,
        application_id: str,
        native_request_id: str,
        conversation_id: str,
        now: datetime,
    ) -> RequestRouteCorrelation:
        request_ref = RequestRef(
            application_ref=ApplicationRef(application_id),
            native_request_id=native_request_id,
        )
        conversation = ConversationRef("qq-main", conversation_id)
        return RequestRouteCorrelation(
            correlation_id=derive_request_correlation_id(
                request_ref,
                conversation,
            ),
            request_ref=request_ref,
            thread_ref=ThreadRef(application_id, f"thread-{application_id}"),
            turn_id=f"turn-{application_id}",
            conversation_ref=conversation,
            delivery_id=f"delivery-{application_id}-{conversation_id}",
            response_shape=ApprovalResponseShape(
                ("approve_once", "approve_session", "decline", "cancel")
            ),
            state=RequestRouteState.OPEN,
            created_at=now,
            updated_at=now,
        )

    async def test_turn_reply_correlation_is_create_only_and_survives_restart(
        self,
    ) -> None:
        now = datetime.now(UTC)
        thread = ThreadRef("codex-main", "thread-1")
        original = TurnReplyCorrelation(
            correlation_id=derive_turn_reply_correlation_id(thread, "turn-active"),
            thread_ref=thread,
            turn_id="turn-active",
            client_message_id="client-a",
            conversation_ref=ConversationRef("qq-main", "conversation-a"),
            reply_to_message_id="message-a",
            created_at=now,
        )
        conflicting = TurnReplyCorrelation(
            correlation_id=original.correlation_id,
            thread_ref=thread,
            turn_id=original.turn_id,
            client_message_id="client-b",
            conversation_ref=ConversationRef("qq-main", "conversation-b"),
            reply_to_message_id="message-b",
            created_at=now + timedelta(seconds=1),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "turn-correlations.sqlite3"
            sqlite_state = SQLiteGatewayState(path)
            repositories = (InMemoryProjectionRouteRepository(), sqlite_state)
            try:
                for repository in repositories:
                    stored = await repository.put_turn_reply_correlation(original)
                    repeated = await repository.put_turn_reply_correlation(
                        TurnReplyCorrelation(
                            correlation_id=original.correlation_id,
                            thread_ref=thread,
                            turn_id=original.turn_id,
                            client_message_id=original.client_message_id,
                            conversation_ref=original.conversation_ref,
                            reply_to_message_id=original.reply_to_message_id,
                            created_at=now + timedelta(seconds=2),
                        )
                    )
                    self.assertEqual(stored, original)
                    self.assertEqual(repeated, original)
                    with self.assertRaises(TurnReplyCorrelationConflict):
                        await repository.put_turn_reply_correlation(conflicting)
                    self.assertEqual(
                        await repository.get_turn_reply_correlation(thread, "turn-active"),
                        original,
                    )
            finally:
                await sqlite_state.close()

            recovered = SQLiteGatewayState(path)
            try:
                self.assertEqual(
                    await recovered.get_turn_reply_correlation(thread, "turn-active"),
                    original,
                )
                with self.assertRaises(TurnReplyCorrelationConflict):
                    await recovered.put_turn_reply_correlation(conflicting)
            finally:
                await recovered.close()

    async def test_oversized_destinations_fail_before_sqlite_reservation(self) -> None:
        now = datetime.now(UTC)
        destination = DestinationDeliveryRecord(
            delivery_id="destination-1",
            snapshot=DeliveryRouteSnapshot(ConversationRef("channel", "conversation")),
            state=DeliverySubmissionState.IN_FLIGHT,
            updated_at=now,
        )
        record = DeliverySubmissionRecord(
            submission_id="submission-1",
            delivery_id="delivery-1",
            origin=DeliverySubmissionOrigin.EXTERNAL,
            principal_id="principal-1",
            target_fingerprint="target-1",
            payload_fingerprint="payload-1",
            destinations=(destination,) * (MAX_DELIVERY_SUBMISSION_DESTINATIONS + 1),
            created_at=now,
            updated_at=now,
        )
        with tempfile.TemporaryDirectory() as directory:
            sqlite_state = SQLiteGatewayState(Path(directory) / "delivery.sqlite3")
            try:
                with self.assertRaisesRegex(ContractViolation, "destinations exceed"):
                    await sqlite_state.reserve_delivery_submission(record)
                self.assertIsNone(await sqlite_state.get_delivery_submission(record.submission_id))
            finally:
                await sqlite_state.close()

    async def test_request_correlations_are_scoped_by_application_and_epoch(
        self,
    ) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as directory:
            sqlite_state = SQLiteGatewayState(Path(directory) / "requests.sqlite3")
            repositories = (
                InMemoryRequestCorrelationRepository(),
                sqlite_state,
            )
            try:
                for repository in repositories:
                    with self.subTest(repository=type(repository).__name__):
                        first = self._request_correlation(
                            application_id="app-a",
                            native_request_id="epoch-1:request-7",
                            conversation_id="conversation-a",
                            now=now,
                        )
                        same_native_id_other_app = self._request_correlation(
                            application_id="app-b",
                            native_request_id="epoch-1:request-7",
                            conversation_id="conversation-a",
                            now=now,
                        )
                        reused_transport_id_next_epoch = self._request_correlation(
                            application_id="app-a",
                            native_request_id="epoch-2:request-7",
                            conversation_id="conversation-a",
                            now=now,
                        )
                        for correlation in (
                            first,
                            same_native_id_other_app,
                            reused_transport_id_next_epoch,
                        ):
                            await repository.put_request_correlation(correlation)

                        transitioned = await repository.transition_request_correlations(
                            first.request_ref,
                            expected_states=(RequestRouteState.OPEN,),
                            state=RequestRouteState.RESPONDED,
                            updated_at=now + timedelta(seconds=1),
                        )
                        self.assertEqual(len(transitioned), 1)
                        self.assertEqual(
                            (
                                await repository.list_request_correlations(
                                    request_ref=same_native_id_other_app.request_ref
                                )
                            )[0].state,
                            RequestRouteState.OPEN,
                        )
                        self.assertEqual(
                            (
                                await repository.list_request_correlations(
                                    request_ref=reused_transport_id_next_epoch.request_ref
                                )
                            )[0].state,
                            RequestRouteState.OPEN,
                        )
                        with self.assertRaises(RequestCorrelationConflict):
                            await repository.transition_request_correlations(
                                first.request_ref,
                                expected_states=(RequestRouteState.OPEN,),
                                state=RequestRouteState.STALE,
                                updated_at=now + timedelta(seconds=2),
                            )
            finally:
                await sqlite_state.close()

    async def test_request_correlation_shape_and_state_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request-restart.sqlite3"
            now = datetime.now(UTC)
            correlation = self._request_correlation(
                application_id="app-a",
                native_request_id="epoch-1:request-7",
                conversation_id="conversation-a",
                now=now,
            )
            first = SQLiteGatewayState(path)
            await first.put_request_correlation(correlation)
            await first.close()

            second = SQLiteGatewayState(path)
            try:
                self.assertEqual(
                    await second.list_request_correlations(request_ref=correlation.request_ref),
                    (correlation,),
                )
                with self.assertRaises(ValueError):
                    await second.delete_request_correlations()
            finally:
                await second.close()

    async def test_late_request_destination_inherits_request_wide_state(
        self,
    ) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as directory:
            sqlite_state = SQLiteGatewayState(Path(directory) / "late-route.sqlite3")
            repositories = (
                InMemoryRequestCorrelationRepository(),
                sqlite_state,
            )
            try:
                for repository in repositories:
                    with self.subTest(repository=type(repository).__name__):
                        first = self._request_correlation(
                            application_id="app-a",
                            native_request_id="epoch-1:request-late-route",
                            conversation_id="conversation-a",
                            now=now,
                        )
                        await repository.put_request_correlation(first)
                        await repository.transition_request_correlations(
                            first.request_ref,
                            expected_states=(RequestRouteState.OPEN,),
                            state=RequestRouteState.RESPONDED,
                            updated_at=now + timedelta(seconds=1),
                        )
                        late = self._request_correlation(
                            application_id="app-a",
                            native_request_id="epoch-1:request-late-route",
                            conversation_id="conversation-b",
                            now=now + timedelta(seconds=2),
                        )
                        stored = await repository.put_request_correlation(late)
                        self.assertIs(stored.state, RequestRouteState.RESPONDED)

                        await repository.transition_request_correlations(
                            first.request_ref,
                            expected_states=(RequestRouteState.RESPONDED,),
                            state=RequestRouteState.RESOLVED,
                            updated_at=now + timedelta(seconds=3),
                        )
                        latest = self._request_correlation(
                            application_id="app-a",
                            native_request_id="epoch-1:request-late-route",
                            conversation_id="conversation-c",
                            now=now + timedelta(seconds=4),
                        )
                        stored = await repository.put_request_correlation(latest)
                        self.assertIs(stored.state, RequestRouteState.RESOLVED)

                        stale = self._request_correlation(
                            application_id="app-a",
                            native_request_id="epoch-1:request-stale",
                            conversation_id="conversation-a",
                            now=now,
                        )
                        await repository.put_request_correlation(stale)
                        await repository.transition_request_correlations(
                            stale.request_ref,
                            expected_states=(RequestRouteState.OPEN,),
                            state=RequestRouteState.STALE,
                            updated_at=now + timedelta(seconds=1),
                        )
                        repeated = await repository.put_request_correlation(
                            self._request_correlation(
                                application_id="app-a",
                                native_request_id="epoch-1:request-stale",
                                conversation_id="conversation-a",
                                now=now + timedelta(seconds=2),
                            )
                        )
                        self.assertIs(repeated.state, RequestRouteState.STALE)
            finally:
                await sqlite_state.close()

    async def test_binding_conflict_is_shared_and_rolls_back_stale_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = SQLiteGatewayState(Path(directory) / "gateway.sqlite3")
            conversation = ConversationRef("qq-main", "c2c:user-conflict")
            original = await state.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("t3-main"),
                )
            )
            try:
                with self.assertRaises(BindingConflict):
                    await state.put(
                        ConversationBinding(
                            conversation_ref=conversation,
                            application_ref=ApplicationRef("zen-main"),
                        ),
                        expected_revision=0,
                    )
                self.assertEqual(await state.get(conversation), original)

                with self.assertRaises(BindingConflict):
                    await state.delete(conversation, expected_revision=0)
                self.assertEqual(await state.get(conversation), original)
            finally:
                await state.close()

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

    async def test_side_effect_started_claim_is_not_reclaimed_after_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.sqlite3"
            first = SQLiteGatewayState(path)
            self.assertEqual(
                await first.claim("inbound:qq-main", "message-unknown"),
                IdempotencyClaimStatus.ACQUIRED,
            )
            await first.mark_side_effect_started(
                "inbound:qq-main",
                "message-unknown",
            )
            await first.close()

            recovered = SQLiteGatewayState(
                path,
                stale_claim_after_seconds=0,
            )
            try:
                self.assertEqual(
                    await recovered.claim("inbound:qq-main", "message-unknown"),
                    IdempotencyClaimStatus.IN_FLIGHT,
                )
            finally:
                await recovered.close()

    async def test_reclaimed_lease_fences_stale_owner_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = SQLiteGatewayState(
                Path(directory) / "gateway.sqlite3",
                stale_claim_after_seconds=0,
            )
            scope = "inbound:qq-main"
            key = "overlapping-message"
            try:
                self.assertEqual(
                    await state.claim(scope, key, owner_token="owner-a"),
                    IdempotencyClaimStatus.ACQUIRED,
                )
                self.assertEqual(
                    await state.claim(scope, key, owner_token="owner-b"),
                    IdempotencyClaimStatus.ACQUIRED,
                )
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    await state.refresh(scope, key, owner_token="owner-a")
                await state.refresh(scope, key, owner_token="owner-b")
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    await state.mark_side_effect_started(
                        scope,
                        key,
                        owner_token="owner-a",
                    )
                await state.release(scope, key, owner_token="owner-a")
                await state.mark_side_effect_started(
                    scope,
                    key,
                    owner_token="owner-b",
                )
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    await state.refresh(scope, key, owner_token="owner-b")
                await state.release(scope, key, owner_token="owner-a")
                self.assertEqual(
                    await state.claim(scope, key, owner_token="owner-c"),
                    IdempotencyClaimStatus.IN_FLIGHT,
                )
            finally:
                await state.close()

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

                request_correlation = self._request_correlation(
                    application_id="t3-main",
                    native_request_id="epoch-1:request-legacy",
                    conversation_id=conversation.native_conversation_id,
                    now=now,
                )
                await state.put_request_correlation(request_correlation)
                self.assertEqual(
                    await state.list_request_correlations(
                        request_ref=request_correlation.request_ref
                    ),
                    (request_correlation,),
                )
                transitioned = await state.transition_request_correlations(
                    request_correlation.request_ref,
                    expected_states=(RequestRouteState.OPEN,),
                    state=RequestRouteState.RESOLVED,
                    updated_at=now + timedelta(seconds=2),
                )
                self.assertEqual(transitioned[0].state, RequestRouteState.RESOLVED)
                self.assertEqual(
                    await state.delete_request_correlations(
                        request_ref=request_correlation.request_ref
                    ),
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
                repositories=GatewayRepositories(
                    bindings=state,
                    idempotency=state,
                    projections=state,
                ),
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

    async def test_valid_legacy_route_upgrade_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-restart.sqlite3"
            now = datetime.now(UTC)
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
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO thread_projection_routes VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "route-legacy",
                        "app-legacy",
                        "",
                        "thread-legacy",
                        "qq",
                        "c",
                        "old",
                        now.isoformat(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            first = SQLiteGatewayState(path)
            try:
                migrated = (await first.list_projection_routes())[0]
                self.assertIsNone(migrated.reply_to_message_id)
                self.assertIsNone(migrated.checkpoint_agent_item_id)
            finally:
                await first.close()

            second = SQLiteGatewayState(path)
            try:
                recovered = (await second.list_projection_routes())[0]
                self.assertEqual(recovered, migrated)
            finally:
                await second.close()

    async def test_malformed_current_rows_fail_closed_without_repair(self) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.sqlite3"
            state = SQLiteGatewayState(path)
            binding = ConversationBinding(
                conversation_ref=ConversationRef("qq", "binding"),
                application_ref=ApplicationRef("app"),
                project_ref=ProjectRef("app", "project"),
            )
            route = ThreadProjectionRoute(
                route_id="route",
                thread_ref=ThreadRef("app", "thread"),
                conversation_ref=ConversationRef("qq", "route"),
                updated_at=now,
            )
            request = self._request_correlation(
                application_id="app",
                native_request_id="request",
                conversation_id="request",
                now=now,
            )
            malformed_shape_request = self._request_correlation(
                application_id="app",
                native_request_id="malformed-shape",
                conversation_id="malformed-shape",
                now=now,
            )
            turn_correlation = TurnReplyCorrelation(
                correlation_id="turn-correlation",
                thread_ref=ThreadRef("app", "turn-thread"),
                turn_id="turn",
                client_message_id="client",
                conversation_ref=ConversationRef("qq", "turn"),
                reply_to_message_id="reply",
                created_at=now,
            )
            try:
                await state.put(binding)
                await state.put_projection_route(route)
                await state.put_request_correlation(request)
                await state.put_request_correlation(malformed_shape_request)
                await state.put_turn_reply_correlation(turn_correlation)
            finally:
                await state.close()

            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    UPDATE conversation_bindings
                    SET application_instance_id = NULL, project_id = 'orphan'
                    """
                )
                connection.execute(
                    """
                    UPDATE thread_projection_routes
                    SET checkpoint_agent_item_id = 'item', checkpointed_at = NULL
                    """
                )
                connection.execute(
                    """
                    UPDATE request_route_correlations
                    SET state = 'invalid'
                    WHERE native_request_id = 'request'
                    """
                )
                connection.execute(
                    """
                    UPDATE request_route_correlations
                    SET response_shape_json = '{"kind":"unknown"}'
                    WHERE native_request_id = 'malformed-shape'
                    """
                )
                connection.execute(
                    """
                    UPDATE turn_reply_correlations SET created_at = 'not-a-timestamp'
                    """
                )
                connection.execute(
                    """
                    INSERT INTO delivery_submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "submission",
                        "delivery",
                        "external",
                        "principal",
                        "target",
                        "payload",
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO delivery_submission_destinations VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "submission",
                        "destination",
                        "qq",
                        "delivery",
                        "",
                        "",
                        "",
                        "",
                        None,
                        None,
                        "accepted",
                        "{not-json}",
                        None,
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?)
                    """,
                    ("scope", "key", "not-a-status", None, now.isoformat()),
                )
                connection.execute(
                    """
                    INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "scope",
                        "naive-time",
                        "in_flight",
                        None,
                        now.replace(tzinfo=None).isoformat(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            corrupted = SQLiteGatewayState(path)
            try:
                with self.assertRaisesRegex(ValueError, "binding scope"):
                    await corrupted.get(binding.conversation_ref)
                with self.assertRaisesRegex(Exception, "checkpoint"):
                    await corrupted.list_projection_routes()
                with self.assertRaisesRegex(ValueError, "RequestRouteState"):
                    await corrupted.list_request_correlations(request_ref=request.request_ref)
                with self.assertRaisesRegex(ValueError, "response shape"):
                    await corrupted.list_request_correlations(
                        request_ref=malformed_shape_request.request_ref
                    )
                with self.assertRaisesRegex(ValueError, "created_at"):
                    await corrupted.list_turn_reply_correlations()
                with self.assertRaisesRegex(ValueError, "receipt_json"):
                    await corrupted.get_delivery_submission("submission")
                with self.assertRaisesRegex(ValueError, "idempotency status"):
                    await corrupted.claim("scope", "key")
                with self.assertRaisesRegex(ValueError, "timezone"):
                    await corrupted.claim("scope", "naive-time")
            finally:
                await corrupted.close()

            connection = sqlite3.connect(path)
            try:
                binding_row = connection.execute(
                    "SELECT application_instance_id, project_id FROM conversation_bindings"
                ).fetchone()
                self.assertEqual(binding_row, (None, "orphan"))
                route_row = connection.execute(
                    "SELECT checkpoint_agent_item_id, checkpointed_at FROM thread_projection_routes"
                ).fetchone()
                self.assertEqual(route_row, ("item", None))
                receipt_row = connection.execute(
                    "SELECT receipt_json FROM delivery_submission_destinations"
                ).fetchone()
                self.assertEqual(receipt_row, ("{not-json}",))
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()

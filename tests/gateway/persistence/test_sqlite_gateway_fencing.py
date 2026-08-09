from __future__ import annotations

import asyncio
import contextvars
import sqlite3
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from imagent.applications.contract import ApplicationRef, ProjectRef, ThreadRef, TurnRef
from imagent.applications.requests import ApprovalResponseShape, RequestRef
from imagent.gateway.delivery import DeliverySubmissionOrigin
from imagent.gateway.outcomes import Failed
from imagent.gateway.persistence.effects import (
    ActionErrorCode,
    ActionIdentity,
    EffectCategory,
    EffectPhase,
    StoreMutationPlan,
    StoreMutationRequest,
    derive_action_fingerprint,
)
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.persistence.state_contracts import (
    ConversationBinding,
    DeliveryRouteSnapshot,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
)
from imagent.gateway.persistence.store import GatewayStoreSession, StaleRuntimeFence
from imagent.gateway.projection.request_correlation import (
    derive_request_correlation_id,
    derive_turn_reply_correlation_id,
)
from imagent.interaction.messages import ConversationRef


class SQLiteGatewayFencingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self._path = Path(self._temporary.name) / "gateway.sqlite3"

    async def asyncTearDown(self) -> None:
        self._temporary.cleanup()

    async def test_fresh_context_cannot_inherit_maintenance_bypass(self) -> None:
        store = SQLiteGatewayStore(self._path)
        session = await self._session(store, "owner-a")
        conversation = ConversationRef("channel", "conversation")
        blank_context = contextvars.Context()
        task = blank_context.run(
            asyncio.create_task,
            store._state.put(  # noqa: SLF001 - verifies the internal guard boundary.
                ConversationBinding(conversation, ApplicationRef("application"))
            ),
        )

        with self.assertRaises(StaleRuntimeFence):
            await task
        self.assertIsNone(await session.get(conversation))
        await session.release_runtime()
        await store.close()

    async def test_stale_owner_rejected_before_zero_row_and_convergent_mutations(self) -> None:
        store = SQLiteGatewayStore(self._path)
        stale = await self._session(store, "owner-a")
        conversation = ConversationRef("channel", "conversation")
        project = ProjectRef("application", "project")
        thread = ThreadRef(project, "thread")
        turn = TurnRef(thread, "turn")
        now = datetime.now(UTC)
        turn_correlation = TurnReplyCorrelation(
            derive_turn_reply_correlation_id(turn),
            turn,
            "client-message",
            conversation,
            "reply-message",
            now,
        )
        request_ref = RequestRef(ApplicationRef("application"), "request")
        request_correlation = RequestRouteCorrelation(
            derive_request_correlation_id(request_ref, conversation),
            request_ref,
            turn,
            conversation,
            "delivery",
            ApprovalResponseShape(("approve_once", "decline")),
            RequestRouteState.OPEN,
            now,
            now,
        )
        destination = DestinationDeliveryRecord(
            "destination",
            DeliveryRouteSnapshot(conversation),
            DeliverySubmissionState.IN_FLIGHT,
            now,
        )
        delivery = DeliverySubmissionRecord(
            "submission",
            "delivery",
            DeliverySubmissionOrigin.EXTERNAL,
            "principal",
            "target-fingerprint",
            "payload-fingerprint",
            (destination,),
            now,
            now,
        )

        self._expire_runtime_lease()
        successor = await self._session(store, "owner-b")
        mutations: tuple[tuple[str, Callable[[], Awaitable[object]]], ...] = (
            (
                "binding",
                lambda: stale.put(ConversationBinding(conversation, ApplicationRef("application"))),
            ),
            (
                "projection zero-row delete",
                lambda: stale.delete_projection_routes(thread, conversation),
            ),
            (
                "turn correlation",
                lambda: stale.put_turn_reply_correlation(turn_correlation),
            ),
            ("idempotency", lambda: stale.release("scope", "missing")),
            (
                "request correlation",
                lambda: stale.put_request_correlation(request_correlation),
            ),
            (
                "delivery submission",
                lambda: stale.reserve_delivery_submission(delivery),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(family=name), self.assertRaises(StaleRuntimeFence):
                await mutate()

        await successor.release_runtime()
        await store.close()

    async def test_session_exposes_only_focused_repository_protocol(self) -> None:
        store = SQLiteGatewayStore(self._path)
        session = await self._session(store, "owner")

        self.assertTrue(callable(session.get))
        for name in ("_connection", "_read_projection_route", "_state", "execute"):
            with self.subTest(name=name), self.assertRaises(AttributeError):
                getattr(session, name)

        await session.release_runtime()
        await store.close()

    async def test_native_start_transition_has_one_atomic_winner(self) -> None:
        store = SQLiteGatewayStore(self._path)
        session = await self._session(store, "owner")
        conversation = ConversationRef("channel", "conversation")
        fingerprint = derive_action_fingerprint(
            ActionIdentity("gateway", "principal", "native.send", "send", conversation),
            (("message_id", "message"),),
        )
        await session.reserve_effect(
            fingerprint,
            category=EffectCategory.NATIVE,
            native_phase_id=fingerprint.phase_id("native"),
        )

        first, first_won = await session.mark_native_side_effect_started(fingerprint)
        repeated, repeated_won = await session.mark_native_side_effect_started(fingerprint)

        self.assertTrue(first_won)
        self.assertFalse(repeated_won)
        self.assertIs(first.phase, EffectPhase.NATIVE_SIDE_EFFECT_STARTED)
        self.assertEqual(repeated, first)
        await session.release_runtime()
        await store.close()

    async def test_route_only_delete_is_scoped_to_its_conversation(self) -> None:
        store = SQLiteGatewayStore(self._path)
        session = await self._session(store, "owner")
        project = ProjectRef("application", "project")
        thread = ThreadRef(project, "thread")
        first = ConversationRef("channel", "first")
        other = ConversationRef("channel", "other")
        await session.put_projection_route(ThreadProjectionRoute("route", thread, first))
        fingerprint = derive_action_fingerprint(
            ActionIdentity("gateway", "principal", "route.delete", "delete", other),
            (("route_id", "route"),),
        )

        receipt = await session.commit_store_mutation(
            StoreMutationRequest(
                fingerprint,
                StoreMutationPlan(
                    conversation_ref=other,
                    route_delete_id="route",
                ),
            )
        )

        self.assertIsInstance(receipt.outcome, Failed)
        assert isinstance(receipt.outcome, Failed)
        self.assertEqual(receipt.outcome.error.code, ActionErrorCode.CONFLICT)
        routes = await session.list_projection_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].route_id, "route")
        self.assertEqual(routes[0].conversation_ref, first)
        await session.release_runtime()
        await store.close()

    async def _session(
        self,
        store: SQLiteGatewayStore,
        owner_token: str,
    ) -> GatewayStoreSession:
        return await store.acquire_runtime(
            gateway_id="gateway",
            owner_token=owner_token,
            lease_duration_seconds=30,
        )

    def _expire_runtime_lease(self) -> None:
        with closing(sqlite3.connect(self._path)) as connection:
            connection.execute(
                "UPDATE gateway_runtime_lease SET expires_at = 0 WHERE singleton = 1"
            )
            connection.commit()


if __name__ == "__main__":
    unittest.main()

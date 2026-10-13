from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from imagent.applications.contract import (
    ApplicationRef,
    InputContinuationPreference,
    ProjectRef,
    QueuedInputReceipt,
    QueuedInputState,
    ThreadRef,
    TurnRef,
)
from imagent.gateway.persistence.effects import (
    ActionError,
    ActionErrorCode,
    ActionIdentity,
    EffectCategory,
    derive_action_fingerprint,
)
from imagent.gateway.persistence.memory_store import MemoryGatewayStore
from imagent.gateway.persistence.repository_contracts import (
    IdempotencyClaimStatus,
    TurnReplyCorrelationConflict,
    _inbound_idempotency_identity,
)
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.persistence.state_contracts import (
    ConversationBinding,
    QueuedInputCorrelation,
    QueuedInputOrigin,
    TurnReplyCorrelation,
    validate_queued_input_correlation,
)
from imagent.gateway.persistence.store import (
    EffectReceiptCapacityError,
    GatewayStore,
    GatewayStoreSession,
    QueuedInputFenceConflict,
    QueuedInputReceiptConflict,
    StaleRuntimeFence,
    _is_gateway_store_session,
)
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation


class QueuedInputStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.stores: tuple[GatewayStore, ...] = (
            MemoryGatewayStore(max_effect_receipts=2),
            SQLiteGatewayStore(Path(self.temporary.name) / "queue.sqlite3", max_effect_receipts=2),
        )
        self.conversation = ConversationRef("channel", "conversation")
        self.thread = ThreadRef(ProjectRef("application", "project"), "thread")

    async def asyncTearDown(self) -> None:
        for store in self.stores:
            await store.close()
        self.temporary.cleanup()

    async def _prepare(
        self,
        store: GatewayStore,
        *,
        message_id: str = "original-message",
    ) -> tuple[GatewayStoreSession, QueuedInputOrigin]:
        session = await store.acquire_runtime(
            gateway_id="gateway",
            owner_token="runtime",
            lease_duration_seconds=60,
        )
        binding = await session.get(self.conversation)
        if binding is None:
            binding = await session.put(
                ConversationBinding(
                    self.conversation,
                    ApplicationRef("application"),
                    self.thread.project_ref,
                    self.thread,
                )
            )
        origin = QueuedInputOrigin(
            self.conversation,
            message_id,
            "a" * 64,
            self.thread,
            binding.generation,
            "native-" + message_id,
            "b" * 64,
            InputContinuationPreference.QUEUE,
        )
        scope, key = _inbound_idempotency_identity(self.conversation, message_id)
        await session.claim(scope, key, owner_token="inbound-owner")
        return session, origin

    async def _fence(
        self,
        session: GatewayStoreSession,
        origin: QueuedInputOrigin,
        *,
        owner_token: str = "inbound-owner",
    ) -> QueuedInputCorrelation:
        scope, key = _inbound_idempotency_identity(origin.conversation_ref, origin.message_id)
        return await session.fence_queued_input(
            origin,
            claim_scope=scope,
            claim_key=key,
            owner_token=owner_token,
        )

    def _receipt(
        self,
        origin: QueuedInputOrigin,
        state: QueuedInputState = QueuedInputState.QUEUED,
    ) -> QueuedInputReceipt:
        return QueuedInputReceipt(
            origin.thread_ref,
            origin.client_message_id,
            "queued-" + origin.message_id,
            state,
            TurnRef(origin.thread_ref, "turn-" + origin.message_id)
            if state is QueuedInputState.ADMITTED
            else None,
            input_fingerprint=origin.input_fingerprint,
        )

    def _reply(self, origin: QueuedInputOrigin) -> TurnReplyCorrelation:
        return TurnReplyCorrelation(
            "reply-" + origin.message_id,
            TurnRef(origin.thread_ref, "turn-" + origin.message_id),
            origin.client_message_id,
            origin.conversation_ref,
            origin.message_id,
            datetime.now(UTC),
        )

    def _effect(self, action_id: str):
        return derive_action_fingerprint(
            ActionIdentity("gateway", "principal", "create_thread", action_id, self.conversation),
            (("purpose", "synthetic"),),
        )

    async def test_fence_joins_existing_claim_and_cannot_reauthorize(self) -> None:
        for store in self.stores:
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway", owner_token="runtime", lease_duration_seconds=60
                )
                binding = await session.put(
                    ConversationBinding(
                        self.conversation,
                        ApplicationRef("application"),
                        self.thread.project_ref,
                        self.thread,
                    )
                )
                origin = QueuedInputOrigin(
                    self.conversation,
                    "original-message",
                    "a" * 64,
                    self.thread,
                    binding.generation,
                    "native-client",
                    "b" * 64,
                    InputContinuationPreference.QUEUE,
                )
                await session.claim(
                    "inbound:channel", "conversation:original-message", owner_token="inbound-owner"
                )
                record = await session.fence_queued_input(
                    origin,
                    claim_scope="inbound:channel",
                    claim_key="conversation:original-message",
                    owner_token="inbound-owner",
                )
                self.assertEqual(record.origin, origin)
                self.assertIsNone(record.receipt)
                self.assertEqual(
                    await session.get_queued_input(self.conversation, origin.message_id), record
                )
                self.assertEqual(await session.list_queued_inputs(self.thread), (record,))
                with self.assertRaises(RuntimeError):
                    await session.refresh(
                        "inbound:channel",
                        "conversation:original-message",
                        owner_token="inbound-owner",
                    )
                with self.assertRaises(QueuedInputFenceConflict):
                    await session.fence_queued_input(
                        origin,
                        claim_scope="inbound:channel",
                        claim_key="conversation:original-message",
                        owner_token="inbound-owner",
                    )

    async def test_admitted_evidence_and_original_reply_are_atomic_and_monotonic(self) -> None:
        for store in self.stores:
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway", owner_token="runtime", lease_duration_seconds=60
                )
                binding = await session.put(
                    ConversationBinding(
                        self.conversation,
                        ApplicationRef("application"),
                        self.thread.project_ref,
                        self.thread,
                    )
                )
                origin = QueuedInputOrigin(
                    self.conversation,
                    "original-message",
                    "a" * 64,
                    self.thread,
                    binding.generation,
                    "native-client",
                    "b" * 64,
                    InputContinuationPreference.QUEUE,
                )
                await session.claim(
                    "inbound:channel", "conversation:original-message", owner_token="inbound-owner"
                )
                await session.fence_queued_input(
                    origin,
                    claim_scope="inbound:channel",
                    claim_key="conversation:original-message",
                    owner_token="inbound-owner",
                )
                turn = TurnRef(self.thread, "original-turn")
                reply = TurnReplyCorrelation(
                    "reply-owner",
                    turn,
                    origin.client_message_id,
                    self.conversation,
                    origin.message_id,
                    datetime.now(UTC),
                )
                admitted = QueuedInputReceipt(
                    self.thread,
                    origin.client_message_id,
                    "queued-item",
                    QueuedInputState.ADMITTED,
                    turn,
                    input_fingerprint=origin.input_fingerprint,
                )
                saved = await session.merge_queued_input_receipt(
                    origin, admitted, turn_correlation=reply
                )
                queued = QueuedInputReceipt(
                    self.thread,
                    origin.client_message_id,
                    "queued-item",
                    QueuedInputState.QUEUED,
                    input_fingerprint=origin.input_fingerprint,
                )
                self.assertEqual(await session.merge_queued_input_receipt(origin, queued), saved)
                self.assertEqual(
                    await session.get_turn_reply_correlation(self.thread, turn.turn_id), reply
                )
                await session.delete_turn_reply_correlation(self.thread, turn.turn_id)
                await session.merge_queued_input_receipt(origin, admitted, turn_correlation=reply)
                self.assertEqual(
                    await session.get_turn_reply_correlation(self.thread, turn.turn_id), reply
                )

    async def test_session_structural_contract_and_unknown_read_scope(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            self.assertTrue(_is_gateway_store_session(session))
            self.assertIsNone(await session.get_queued_input(self.conversation, origin.message_id))
            saved = await self._fence(session, origin)
            self.assertEqual(await session.list_queued_inputs(), (saved,))
            self.assertEqual(
                await session.list_queued_inputs(replace(self.thread, thread_id="other")), ()
            )
            self.assertIsNone(
                await session.get_queued_input(
                    ConversationRef("other", "conversation"), origin.message_id
                )
            )

    async def test_invalid_origin_and_datetime_cannot_reserve(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            malformed = (
                replace(origin, actor_fingerprint="raw actor"),
                replace(origin, actor_fingerprint="A" * 64),
                replace(origin, input_fingerprint="B" * 64),
                replace(origin, input_fingerprint="b" * 63),
                replace(origin, binding_generation=True),
                replace(origin, binding_generation=-1),
                replace(origin, binding_generation=cast(Any, 1.5)),
                replace(origin, continuation=cast(Any, "queue")),
                replace(origin, continuation=InputContinuationPreference.PREFER_ACTIVE_TURN),
                replace(origin, message_id=""),
                replace(origin, message_id=cast(Any, ["raw content"])),
                replace(origin, client_message_id=""),
                replace(origin, message_id="x" * 513),
                replace(origin, thread_ref=replace(self.thread, thread_id="")),
                replace(origin, conversation_ref=ConversationRef("", "conversation")),
            )
            for invalid in malformed:
                with self.subTest(store=type(store).__name__, origin=invalid):
                    with self.assertRaises(ContractViolation):
                        await self._fence(session, invalid)
            self.assertEqual(await session.list_queued_inputs(), ())
            now = datetime.now(UTC)
            for invalid_time in (datetime(2026, 1, 1), cast(Any, "not a datetime")):
                with self.assertRaises(ContractViolation):
                    validate_queued_input_correlation(
                        QueuedInputCorrelation(origin, None, invalid_time, now)
                    )
            with self.assertRaises(ContractViolation):
                validate_queued_input_correlation(
                    QueuedInputCorrelation(origin, None, now, now - timedelta(seconds=1))
                )
            await self._fence(session, origin)

    async def test_wrong_claim_identity_or_owner_cannot_reserve(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            for scope, key, owner in (
                ("inbound:other", "conversation:original-message", "inbound-owner"),
                ("inbound:channel", "other:original-message", "inbound-owner"),
                ("inbound:channel", "conversation:original-message", "wrong-owner"),
            ):
                await session.claim(scope, key, owner_token=owner)
                with self.assertRaises(QueuedInputFenceConflict):
                    await session.fence_queued_input(
                        origin, claim_scope=scope, claim_key=key, owner_token=owner
                    )
            self.assertEqual(await session.list_queued_inputs(), ())
            await self._fence(session, origin)

    async def test_missing_completed_and_protected_claims_cannot_fence(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            scope, key = _inbound_idempotency_identity(self.conversation, origin.message_id)
            await session.release(scope, key, owner_token="inbound-owner")
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(session, origin)
            await session.claim(scope, key, owner_token="inbound-owner")
            await session.complete(scope, key, owner_token="inbound-owner")
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(session, origin)
            session, origin = await self._prepare(store, message_id="protected")
            scope, key = _inbound_idempotency_identity(self.conversation, origin.message_id)
            await session.mark_side_effect_started(scope, key, owner_token="inbound-owner")
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(session, origin)
            self.assertEqual(await session.list_queued_inputs(), ())

    async def test_binding_generation_thread_and_aba_are_fenced(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            for changed in (
                replace(origin, binding_generation=origin.binding_generation + 1),
                replace(origin, thread_ref=replace(self.thread, thread_id="other")),
            ):
                with self.assertRaises(QueuedInputFenceConflict):
                    await self._fence(session, changed)
            await session.delete(self.conversation)
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(session, origin)
            binding = await session.put(
                ConversationBinding(
                    self.conversation,
                    ApplicationRef("application"),
                    self.thread.project_ref,
                    self.thread,
                )
            )
            self.assertGreater(binding.generation, origin.binding_generation)
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(session, origin)
            await self._fence(session, replace(origin, binding_generation=binding.generation))

    async def test_concurrent_fences_have_one_winner_and_reclaim_cannot_reauthorize(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            results = await asyncio.gather(
                self._fence(session, origin), self._fence(session, origin), return_exceptions=True
            )
            self.assertEqual(
                sum(isinstance(result, QueuedInputCorrelation) for result in results), 1
            )
            self.assertEqual(
                sum(isinstance(result, QueuedInputFenceConflict) for result in results), 1
            )
            scope, key = _inbound_idempotency_identity(self.conversation, origin.message_id)
            await session.release(scope, key, owner_token="inbound-owner")
            await session.claim(scope, key, owner_token="new-inbound-owner")
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(session, origin, owner_token="new-inbound-owner")

    async def test_shared_effect_capacity_in_both_reservation_directions(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            effect = self._effect("one")
            await session.reserve_effect(
                effect, category=EffectCategory.NATIVE, native_phase_id="native-one"
            )
            await self._fence(session, origin)
            with self.assertRaises(EffectReceiptCapacityError):
                await session.reserve_effect(
                    self._effect("two"),
                    category=EffectCategory.NATIVE,
                    native_phase_id="native-two",
                )
            with self.assertRaises(EffectReceiptCapacityError):
                await session.commit_store_preflight_failure(
                    self._effect("three"), error=ActionError(ActionErrorCode.UNSUPPORTED)
                )
            session, other = await self._prepare(store, message_id="second-message")
            with self.assertRaises(EffectReceiptCapacityError):
                await self._fence(session, other)
            scope, key = _inbound_idempotency_identity(self.conversation, other.message_id)
            await session.refresh(scope, key, owner_token="inbound-owner")
            self.assertIsNone(await session.get_queued_input(self.conversation, other.message_id))
            saved = await session.merge_queued_input_receipt(
                origin, self._receipt(origin, QueuedInputState.CANCELLED)
            )
            self.assertEqual(
                (await session.get_queued_input(self.conversation, origin.message_id)), saved
            )
            await session.reserve_effect(
                effect, category=EffectCategory.NATIVE, native_phase_id="native-one"
            )
            with self.assertRaises(EffectReceiptCapacityError):
                await self._fence(session, other)

    async def test_merge_requires_existing_exact_origin_and_canonical_scope(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            receipt = self._receipt(origin)
            with self.assertRaises(KeyError):
                await session.merge_queued_input_receipt(origin, receipt)
            initial = await self._fence(session, origin)
            for changed in (
                replace(origin, actor_fingerprint="c" * 64),
                replace(origin, input_fingerprint="c" * 64),
                replace(origin, client_message_id="other"),
                replace(origin, binding_generation=99),
            ):
                with self.assertRaises(QueuedInputReceiptConflict):
                    await session.merge_queued_input_receipt(changed, receipt)
            for changed in (
                replace(receipt, input_fingerprint="c" * 64),
                replace(receipt, client_message_id="other"),
                replace(receipt, thread_ref=replace(self.thread, thread_id="other")),
            ):
                with self.assertRaises(QueuedInputReceiptConflict):
                    await session.merge_queued_input_receipt(origin, changed)
            self.assertEqual(
                await session.get_queued_input(self.conversation, origin.message_id), initial
            )

    async def test_canonical_lineage_terminal_facts_and_reply_destination_cannot_change(
        self,
    ) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            await self._fence(session, origin)
            queued = self._receipt(origin)
            await session.merge_queued_input_receipt(origin, queued)
            with self.assertRaises(QueuedInputReceiptConflict):
                await session.merge_queued_input_receipt(
                    origin, replace(queued, queued_item_id="other")
                )
            admitted = self._receipt(origin, QueuedInputState.ADMITTED)
            reply = self._reply(origin)
            for wrong_reply in (
                None,
                replace(reply, reply_to_message_id="other"),
                replace(reply, client_message_id="other"),
                replace(reply, conversation_ref=ConversationRef("other", "conversation")),
            ):
                with self.assertRaises(QueuedInputReceiptConflict):
                    await session.merge_queued_input_receipt(
                        origin, admitted, turn_correlation=wrong_reply
                    )
            with self.assertRaises(QueuedInputReceiptConflict):
                await session.merge_queued_input_receipt(origin, queued, turn_correlation=reply)
            saved = await session.merge_queued_input_receipt(
                origin, admitted, turn_correlation=reply
            )
            cancelled = self._receipt(origin, QueuedInputState.CANCELLED)
            with self.assertRaises(QueuedInputReceiptConflict):
                await session.merge_queued_input_receipt(origin, cancelled)
            other_turn = TurnRef(self.thread, "other-turn")
            with self.assertRaises(QueuedInputReceiptConflict):
                await session.merge_queued_input_receipt(
                    origin,
                    replace(admitted, turn_ref=other_turn),
                    turn_correlation=replace(reply, turn_ref=other_turn),
                )
            self.assertEqual(await session.merge_queued_input_receipt(origin, queued), saved)

    async def test_cancelled_evidence_never_regresses_or_becomes_admitted(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            await self._fence(session, origin)
            saved = await session.merge_queued_input_receipt(
                origin, self._receipt(origin, QueuedInputState.CANCELLED)
            )
            self.assertEqual(
                await session.merge_queued_input_receipt(origin, self._receipt(origin)), saved
            )
            with self.assertRaises(QueuedInputReceiptConflict):
                await session.merge_queued_input_receipt(
                    origin,
                    self._receipt(origin, QueuedInputState.ADMITTED),
                    turn_correlation=self._reply(origin),
                )
            self.assertIsNone(
                await session.get_turn_reply_correlation(
                    self.thread, self._reply(origin).turn_ref.turn_id
                )
            )

    async def test_conflicting_turn_correlation_rolls_back_admitted_evidence(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            initial = await self._fence(session, origin)
            reply = self._reply(origin)
            wrong = replace(reply, reply_to_message_id="another-message")
            await session.put_turn_reply_correlation(wrong)
            with self.assertRaises(TurnReplyCorrelationConflict):
                await session.merge_queued_input_receipt(
                    origin, self._receipt(origin, QueuedInputState.ADMITTED), turn_correlation=reply
                )
            self.assertEqual(
                await session.get_queued_input(self.conversation, origin.message_id), initial
            )
            self.assertEqual(
                await session.get_turn_reply_correlation(self.thread, reply.turn_ref.turn_id), wrong
            )
            await session.delete_turn_reply_correlation(self.thread, reply.turn_ref.turn_id)
            await session.put_turn_reply_correlation(reply)
            await session.merge_queued_input_receipt(
                origin,
                self._receipt(origin, QueuedInputState.ADMITTED),
                turn_correlation=replace(reply, created_at=datetime.now(UTC)),
            )
            self.assertEqual(
                await session.get_turn_reply_correlation(self.thread, reply.turn_ref.turn_id), reply
            )

    async def test_post_acceptance_binding_change_does_not_retarget_original_reply(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            await self._fence(session, origin)
            await session.delete(self.conversation)
            await session.merge_queued_input_receipt(
                origin,
                self._receipt(origin, QueuedInputState.ADMITTED),
                turn_correlation=self._reply(origin),
            )
            self.assertIsNone(await session.get(self.conversation))
            saved_reply = await session.get_turn_reply_correlation(
                self.thread,
                self._reply(origin).turn_ref.turn_id,
            )
            assert saved_reply is not None
            self.assertEqual(saved_reply.reply_to_message_id, origin.message_id)

    async def test_stale_runtime_owner_epoch_and_expiry_fence_even_converged_merges(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            await self._fence(session, origin)
            receipt = self._receipt(origin)
            saved = await session.merge_queued_input_receipt(origin, receipt)
            _, pending = await self._prepare(store, message_id="next-message")
            internal = cast(Any, session)
            original_lease = internal._lease
            for wrong in (
                replace(original_lease, owner_token="other"),
                replace(original_lease, epoch=original_lease.epoch + 1),
            ):
                internal._lease = wrong
                with self.assertRaises(StaleRuntimeFence):
                    await session.merge_queued_input_receipt(origin, receipt)
                with self.assertRaises(StaleRuntimeFence):
                    await self._fence(session, pending)
            internal._lease = original_lease
            if isinstance(store, MemoryGatewayStore):
                store._lease = replace(
                    original_lease, expires_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            else:
                assert isinstance(store, SQLiteGatewayStore)
                store._state._connection.execute("UPDATE gateway_runtime_lease SET expires_at = 0")
                store._state._connection.commit()
            with self.assertRaises(StaleRuntimeFence):
                await session.merge_queued_input_receipt(origin, receipt)
            with self.assertRaises(StaleRuntimeFence):
                await self._fence(session, pending)
            successor = await store.acquire_runtime(
                gateway_id="gateway", owner_token="new-runtime", lease_duration_seconds=60
            )
            with self.assertRaises(StaleRuntimeFence):
                await session.merge_queued_input_receipt(origin, receipt)
            self.assertEqual(await successor.merge_queued_input_receipt(origin, receipt), saved)

    async def test_fence_second_write_failure_rolls_back_in_both_stores(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            if isinstance(store, MemoryGatewayStore):
                # Fail staging the second mapping after the new row is staged.
                with patch(
                    "imagent.gateway.persistence.memory_store.dict",
                    create=True,
                    side_effect=({}, RuntimeError("injected second write")),
                ):
                    with self.assertRaisesRegex(RuntimeError, "injected second write"):
                        await self._fence(session, origin)
            else:
                assert isinstance(store, SQLiteGatewayStore)
                store._state._connection.execute("""
                    CREATE TRIGGER fail_queue_claim BEFORE UPDATE ON idempotency_records
                    WHEN NEW.status = 'side_effect_started'
                    BEGIN SELECT RAISE(ABORT, 'injected second write'); END
                """)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "injected second write"):
                    await self._fence(session, origin)
                store._state._connection.execute("DROP TRIGGER fail_queue_claim")
            self.assertIsNone(await session.get_queued_input(self.conversation, origin.message_id))
            scope, key = _inbound_idempotency_identity(self.conversation, origin.message_id)
            await session.refresh(scope, key, owner_token="inbound-owner")
            await self._fence(session, origin)

    async def test_cancelled_fence_before_lock_does_not_publish_partial_state(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            lock = (
                store._lock
                if isinstance(store, MemoryGatewayStore)
                else cast(SQLiteGatewayStore, store)._state._lock
            )
            async with lock:
                task = asyncio.create_task(self._fence(session, origin))
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(await session.list_queued_inputs(), ())
            scope, key = _inbound_idempotency_identity(self.conversation, origin.message_id)
            await session.refresh(scope, key, owner_token="inbound-owner")
            await self._fence(session, origin)

    async def test_lost_return_after_commit_keeps_both_fence_writes(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            if isinstance(store, SQLiteGatewayStore):
                connection = store._state._connection

                class LostAcknowledgement:
                    def __getattr__(self, name: str) -> Any:
                        return getattr(connection, name)

                    def commit(self) -> None:
                        connection.commit()
                        raise RuntimeError("lost commit acknowledgement")

                store._state._connection = cast(Any, LostAcknowledgement())
                try:
                    with self.assertRaisesRegex(RuntimeError, "lost commit acknowledgement"):
                        await self._fence(session, origin)
                finally:
                    store._state._connection = connection
            else:

                async def committed_then_cancelled() -> None:
                    await self._fence(session, origin)
                    raise asyncio.CancelledError()

                with self.assertRaises(asyncio.CancelledError):
                    await committed_then_cancelled()
            saved = await session.get_queued_input(self.conversation, origin.message_id)
            self.assertIsNotNone(saved)
            scope, key = _inbound_idempotency_identity(self.conversation, origin.message_id)
            with self.assertRaises(RuntimeError):
                await session.refresh(scope, key, owner_token="inbound-owner")
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(session, origin)

    async def test_sqlite_admitted_second_write_failure_and_lost_acknowledgement(self) -> None:
        store = cast(SQLiteGatewayStore, self.stores[1])
        session, origin = await self._prepare(store)
        initial = await self._fence(session, origin)
        admitted = self._receipt(origin, QueuedInputState.ADMITTED)
        reply = self._reply(origin)
        connection = store._state._connection
        connection.execute("""
            CREATE TRIGGER fail_queue_turn BEFORE INSERT ON turn_reply_correlations
            BEGIN SELECT RAISE(ABORT, 'injected Turn failure'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected Turn failure"):
            await session.merge_queued_input_receipt(origin, admitted, turn_correlation=reply)
        self.assertEqual(
            await session.get_queued_input(self.conversation, origin.message_id), initial
        )
        self.assertIsNone(
            await session.get_turn_reply_correlation(self.thread, reply.turn_ref.turn_id)
        )
        connection.execute("DROP TRIGGER fail_queue_turn")

        class LostAcknowledgement:
            def __getattr__(self, name: str) -> Any:
                return getattr(connection, name)

            def commit(self) -> None:
                connection.commit()
                raise RuntimeError("lost commit acknowledgement")

        store._state._connection = cast(Any, LostAcknowledgement())
        try:
            with self.assertRaisesRegex(RuntimeError, "lost commit acknowledgement"):
                await session.merge_queued_input_receipt(origin, admitted, turn_correlation=reply)
        finally:
            store._state._connection = connection
        saved = await session.get_queued_input(self.conversation, origin.message_id)
        assert saved is not None
        self.assertEqual(saved.receipt, admitted)
        self.assertEqual(
            await session.get_turn_reply_correlation(self.thread, reply.turn_ref.turn_id), reply
        )
        await session.merge_queued_input_receipt(origin, admitted, turn_correlation=reply)

    async def test_sqlite_restart_retains_unknown_and_original_admitted_identity(self) -> None:
        store = cast(SQLiteGatewayStore, self.stores[1])
        session, origin = await self._prepare(store)
        await self._fence(session, origin)
        session, admitted_origin = await self._prepare(store, message_id="admitted-message")
        await self._fence(session, admitted_origin)
        admitted = self._receipt(admitted_origin, QueuedInputState.ADMITTED)
        reply = self._reply(admitted_origin)
        await session.merge_queued_input_receipt(admitted_origin, admitted, turn_correlation=reply)
        await session.release_runtime()
        await store.close()
        reopened = SQLiteGatewayStore(
            Path(self.temporary.name) / "queue.sqlite3", max_effect_receipts=2
        )
        try:
            restarted = await reopened.acquire_runtime(
                gateway_id="gateway", owner_token="new-runtime", lease_duration_seconds=60
            )
            unknown = await restarted.get_queued_input(self.conversation, origin.message_id)
            self.assertIsNotNone(unknown)
            assert unknown is not None
            self.assertEqual(unknown.origin, origin)
            self.assertIsNone(unknown.receipt)
            saved = await restarted.get_queued_input(self.conversation, admitted_origin.message_id)
            assert saved is not None
            self.assertEqual(saved.receipt, admitted)
            self.assertEqual(
                await restarted.get_turn_reply_correlation(self.thread, reply.turn_ref.turn_id),
                reply,
            )
            scope, key = _inbound_idempotency_identity(self.conversation, origin.message_id)
            self.assertIs(
                await restarted.claim(scope, key, owner_token="new-inbound-owner"),
                IdempotencyClaimStatus.IN_FLIGHT,
            )
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(restarted, origin)
            with self.assertRaises(EffectReceiptCapacityError):
                await restarted.reserve_effect(
                    self._effect("after-restart"),
                    category=EffectCategory.NATIVE,
                    native_phase_id="new-native",
                )
        finally:
            await reopened.close()

    async def test_old_sqlite_schema_migrates_without_inventing_queue_metadata(self) -> None:
        store = cast(SQLiteGatewayStore, self.stores[1])
        session, origin = await self._prepare(store)
        scope, key = _inbound_idempotency_identity(self.conversation, origin.message_id)
        await session.mark_side_effect_started(scope, key, owner_token="inbound-owner")
        await session.release_runtime()
        await store.close()
        path = Path(self.temporary.name) / "queue.sqlite3"
        with closing(sqlite3.connect(path)) as database:
            database.execute("DROP TABLE queued_input_correlations")
            database.commit()
        reopened = SQLiteGatewayStore(path)
        try:
            restarted = await reopened.acquire_runtime(
                gateway_id="gateway", owner_token="new-runtime", lease_duration_seconds=60
            )
            self.assertEqual(await restarted.list_queued_inputs(), ())
            self.assertIs(
                await restarted.claim(scope, key, owner_token="new-inbound-owner"),
                IdempotencyClaimStatus.IN_FLIGHT,
            )
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(restarted, origin, owner_token="new-inbound-owner")
        finally:
            await reopened.close()

    async def test_sqlite_schema_and_rows_are_bounded_evidence_and_default_deny(self) -> None:
        store = cast(SQLiteGatewayStore, self.stores[1])
        session, origin = await self._prepare(store)
        await self._fence(session, origin)
        connection = store._state._connection
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(queued_input_correlations)")
        }
        self.assertEqual(
            columns,
            {
                "channel_instance_id",
                "native_conversation_id",
                "message_id",
                "actor_fingerprint",
                "application_instance_id",
                "project_id",
                "thread_id",
                "binding_generation",
                "client_message_id",
                "input_fingerprint",
                "continuation",
                "receipt_state",
                "queued_item_id",
                "turn_id",
                "created_at",
                "updated_at",
            },
        )
        row = dict(connection.execute("SELECT * FROM queued_input_correlations").fetchone())
        self.assertEqual(row["actor_fingerprint"], "a" * 64)
        self.assertIsNone(row["receipt_state"])
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'queued_input_correlations'"
            )
        }
        self.assertEqual(len(triggers), 3)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("UPDATE queued_input_correlations SET message_id = 'unauthorized'")
        connection.rollback()
        saved = await session.get_queued_input(self.conversation, origin.message_id)
        assert saved is not None
        self.assertEqual(saved.origin, origin)

    async def test_same_reply_id_cannot_alias_a_different_turn(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            initial = await self._fence(session, origin)
            reply = self._reply(origin)
            other = replace(reply, turn_ref=TurnRef(self.thread, "other-turn"))
            await session.put_turn_reply_correlation(other)
            with self.assertRaises(TurnReplyCorrelationConflict):
                await session.merge_queued_input_receipt(
                    origin, self._receipt(origin, QueuedInputState.ADMITTED), turn_correlation=reply
                )
            self.assertEqual(
                await session.get_queued_input(self.conversation, origin.message_id), initial
            )

    async def test_malformed_canonical_lineage_is_never_stored(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            initial = await self._fence(session, origin)
            queued = self._receipt(origin)
            for bad in (
                replace(queued, queued_item_id=cast(Any, ["content"])),
                replace(queued, queued_item_id=""),
                replace(queued, state=cast(Any, "queued")),
                replace(queued, turn_ref=TurnRef(self.thread, "unexpected-turn")),
            ):
                with self.assertRaises(ContractViolation):
                    await session.merge_queued_input_receipt(origin, bad)
            self.assertEqual(
                await session.get_queued_input(self.conversation, origin.message_id), initial
            )

    async def test_replaced_inbound_owner_cannot_fence_queue(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            scope, key = _inbound_idempotency_identity(self.conversation, origin.message_id)
            if isinstance(store, SQLiteGatewayStore):
                store._state._stale_claim_after_seconds = 0
            else:
                await session.release(scope, key, owner_token="inbound-owner")
            self.assertIs(
                await session.claim(scope, key, owner_token="replacement-owner"),
                IdempotencyClaimStatus.ACQUIRED,
            )
            with self.assertRaises(QueuedInputFenceConflict):
                await self._fence(session, origin)
            self.assertEqual(await session.list_queued_inputs(), ())
            await self._fence(session, origin, owner_token="replacement-owner")

    async def test_turn_correlation_creation_time_must_be_aware(self) -> None:
        for store in self.stores:
            session, origin = await self._prepare(store)
            initial = await self._fence(session, origin)
            reply = replace(self._reply(origin), created_at=datetime(2026, 1, 1))
            with self.assertRaises(ContractViolation):
                await session.merge_queued_input_receipt(
                    origin, self._receipt(origin, QueuedInputState.ADMITTED), turn_correlation=reply
                )
            self.assertEqual(
                await session.get_queued_input(self.conversation, origin.message_id), initial
            )

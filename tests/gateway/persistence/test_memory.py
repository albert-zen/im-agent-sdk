from __future__ import annotations

import asyncio
import importlib.util
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

from imagent.adapters import DeliverySubmissionCapacityError, DeliverySubmissionConflict
from imagent.contracts import (
    ApplicationRef,
    ConversationBinding,
    ConversationRef,
    DeliveryRouteSnapshot,
    DeliverySubmissionOrigin,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    ThreadRef,
)
from imagent.gateway.delivery import proactive as proactive_owner
from imagent.gateway.persistence import BindingConflict
from imagent.gateway.persistence import repository_contracts as repository_contract_owner
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryDeliverySubmissionRepository,
)
from imagent.interaction.operations import ContractViolation


class InMemoryBindingRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryBindingRepository()
        self.conversation = ConversationRef("qq-primary", "c2c:user-1")

    def test_binding_owners_are_exact_and_historical_module_is_absent(self) -> None:
        self.assertIs(BindingConflict, repository_contract_owner.BindingConflict)
        self.assertEqual(
            InMemoryBindingRepository.__module__,
            "imagent.gateway.persistence.memory",
        )
        self.assertIsNone(importlib.util.find_spec("imagent.bindings"))

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

    async def test_put_rejects_stale_revision_without_mutation(self) -> None:
        stored = await self.repository.put(
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
        self.assertEqual(await self.repository.get(self.conversation), stored)

    async def test_invalid_put_fails_before_mutation(self) -> None:
        stored = await self.repository.put(
            ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=ApplicationRef("zen-local"),
            )
        )
        with self.assertRaises(ContractViolation):
            await self.repository.put(
                ConversationBinding(
                    conversation_ref=self.conversation,
                    application_ref=ApplicationRef("zen-local"),
                    thread_ref=ThreadRef("t3-local", "thread-other"),
                ),
                expected_revision=stored.revision,
            )
        self.assertEqual(await self.repository.get(self.conversation), stored)

    async def test_concurrent_puts_serialize_revisions(self) -> None:
        first, second = await asyncio.gather(
            self.repository.put(
                ConversationBinding(
                    conversation_ref=self.conversation,
                    application_ref=ApplicationRef("zen-local"),
                )
            ),
            self.repository.put(
                ConversationBinding(
                    conversation_ref=self.conversation,
                    application_ref=ApplicationRef("t3-local"),
                )
            ),
        )
        self.assertEqual({first.revision, second.revision}, {1, 2})
        current = await self.repository.get(self.conversation)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.revision, 2)

    async def test_fresh_repository_starts_without_binding_state(self) -> None:
        await self.repository.put(
            ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=ApplicationRef("zen-local"),
            )
        )
        restarted = InMemoryBindingRepository()
        self.assertIsNone(await restarted.get(self.conversation))

    async def test_delete_supports_revision_guard(self) -> None:
        stored = await self.repository.put(
            ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=ApplicationRef("zen-local"),
            )
        )
        with self.assertRaises(BindingConflict):
            await self.repository.delete(self.conversation, expected_revision=0)
        self.assertEqual(await self.repository.get(self.conversation), stored)

        await self.repository.delete(self.conversation, expected_revision=stored.revision)
        self.assertIsNone(await self.repository.get(self.conversation))


def _submission() -> DeliverySubmissionRecord:
    now = datetime.now(UTC)
    return DeliverySubmissionRecord(
        submission_id="submission-1",
        delivery_id="delivery-1",
        origin=DeliverySubmissionOrigin.EXTERNAL,
        principal_id="principal-1",
        target_fingerprint="target-1",
        payload_fingerprint="payload-1",
        destinations=(
            DestinationDeliveryRecord(
                delivery_id="destination-1",
                snapshot=DeliveryRouteSnapshot(ConversationRef("channel", "conversation-1")),
                state=DeliverySubmissionState.IN_FLIGHT,
                updated_at=now,
            ),
        ),
        created_at=now,
        updated_at=now,
    )


def _different_submission(
    record: DeliverySubmissionRecord, suffix: str
) -> DeliverySubmissionRecord:
    return replace(
        record,
        submission_id=f"submission-{suffix}",
        delivery_id=f"delivery-{suffix}",
        destinations=(
            replace(
                record.destinations[0],
                delivery_id=f"destination-{suffix}",
                snapshot=DeliveryRouteSnapshot(
                    ConversationRef("channel", f"conversation-{suffix}")
                ),
            ),
        ),
    )


class InMemoryDeliverySubmissionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def test_delivery_orchestration_no_longer_owns_memory_repository(self) -> None:
        self.assertFalse(hasattr(proactive_owner, "InMemoryDeliverySubmissionRepository"))

    def test_record_capacity_must_be_a_positive_integer(self) -> None:
        for invalid in (0, -1, True, cast(int, 1.5)):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    InMemoryDeliverySubmissionRepository(max_records=invalid)

    async def test_concurrent_identical_reservation_has_one_winner(self) -> None:
        repository = InMemoryDeliverySubmissionRepository()
        record = _submission()

        first, second = await asyncio.gather(
            repository.reserve_delivery_submission(record),
            repository.reserve_delivery_submission(record),
        )

        self.assertEqual({first.acquired, second.acquired}, {False, True})
        self.assertIs(first.record, record)
        self.assertIs(second.record, record)

    async def test_reservation_rejects_changed_immutable_identity(self) -> None:
        repository = InMemoryDeliverySubmissionRepository()
        record = _submission()
        await repository.reserve_delivery_submission(record)

        with self.assertRaisesRegex(DeliverySubmissionConflict, "different submission"):
            await repository.reserve_delivery_submission(
                replace(record, payload_fingerprint="payload-2")
            )

    async def test_destination_update_requires_expected_state_and_preserves_record(self) -> None:
        repository = InMemoryDeliverySubmissionRepository()
        record = _submission()
        await repository.reserve_delivery_submission(record)
        current = record.destinations[0]
        accepted = replace(
            current,
            state=DeliverySubmissionState.ACCEPTED,
            updated_at=current.updated_at + timedelta(seconds=1),
        )

        updated = await repository.update_delivery_destination(
            record.submission_id,
            current.delivery_id,
            expected_state=DeliverySubmissionState.IN_FLIGHT,
            destination=accepted,
        )

        self.assertIs(updated.destinations[0].state, DeliverySubmissionState.ACCEPTED)
        self.assertEqual(updated.created_at, record.created_at)
        repeated = await repository.update_delivery_destination(
            record.submission_id,
            current.delivery_id,
            expected_state=DeliverySubmissionState.IN_FLIGHT,
            destination=accepted,
        )
        self.assertEqual(repeated, updated)
        with self.assertRaisesRegex(DeliverySubmissionConflict, "state changed"):
            await repository.update_delivery_destination(
                record.submission_id,
                current.delivery_id,
                expected_state=DeliverySubmissionState.IN_FLIGHT,
                destination=replace(accepted, error="changed"),
            )
        with self.assertRaisesRegex(DeliverySubmissionConflict, "snapshot changed"):
            await repository.update_delivery_destination(
                record.submission_id,
                current.delivery_id,
                expected_state=DeliverySubmissionState.ACCEPTED,
                destination=replace(
                    accepted,
                    snapshot=DeliveryRouteSnapshot(ConversationRef("channel", "conversation-2")),
                ),
            )
        with self.assertRaisesRegex(KeyError, "destination does not exist"):
            await repository.update_delivery_destination(
                record.submission_id,
                "missing-destination",
                expected_state=DeliverySubmissionState.ACCEPTED,
                destination=replace(accepted, delivery_id="missing-destination"),
            )
        with self.assertRaisesRegex(KeyError, "does not exist"):
            await repository.update_delivery_destination(
                "missing",
                current.delivery_id,
                expected_state=DeliverySubmissionState.IN_FLIGHT,
                destination=accepted,
            )

    async def test_invalid_reservation_fails_before_write(self) -> None:
        repository = InMemoryDeliverySubmissionRepository()
        invalid = replace(_submission(), destinations=())

        with self.assertRaises(ContractViolation):
            await repository.reserve_delivery_submission(invalid)

        self.assertIsNone(await repository.get_delivery_submission(invalid.submission_id))

    async def test_capacity_retains_every_state_and_preserves_existing_replay(self) -> None:
        for state in (
            DeliverySubmissionState.IN_FLIGHT,
            DeliverySubmissionState.ACCEPTED,
            DeliverySubmissionState.REJECTED,
            DeliverySubmissionState.PARTIAL,
            DeliverySubmissionState.RETRYABLE,
            DeliverySubmissionState.UNKNOWN,
        ):
            with self.subTest(state=state):
                repository = InMemoryDeliverySubmissionRepository(max_records=1)
                record = _submission()
                await repository.reserve_delivery_submission(record)
                if state is not DeliverySubmissionState.IN_FLIGHT:
                    destination = replace(
                        record.destinations[0],
                        state=state,
                        updated_at=record.updated_at + timedelta(seconds=1),
                    )
                    stored = await repository.update_delivery_destination(
                        record.submission_id,
                        destination.delivery_id,
                        expected_state=DeliverySubmissionState.IN_FLIGHT,
                        destination=destination,
                    )
                else:
                    stored = record

                replay = await repository.reserve_delivery_submission(record)
                self.assertFalse(replay.acquired)
                self.assertEqual(replay.record, stored)
                with self.assertRaises(DeliverySubmissionCapacityError):
                    await repository.reserve_delivery_submission(
                        _different_submission(record, "new")
                    )
                self.assertIsNone(await repository.get_delivery_submission("submission-new"))

    async def test_last_slot_is_atomic_and_a_fresh_repository_starts_empty(self) -> None:
        repository = InMemoryDeliverySubmissionRepository(max_records=2)
        record = _submission()
        await repository.reserve_delivery_submission(record)
        contenders = (
            _different_submission(record, "a"),
            _different_submission(record, "b"),
        )

        results = await asyncio.gather(
            *(repository.reserve_delivery_submission(item) for item in contenders),
            return_exceptions=True,
        )

        self.assertEqual(
            sum(isinstance(result, DeliverySubmissionCapacityError) for result in results),
            1,
        )
        self.assertEqual(
            sum(getattr(result, "acquired", False) is True for result in results),
            1,
        )
        fresh = InMemoryDeliverySubmissionRepository(max_records=1)
        restarted = await fresh.reserve_delivery_submission(contenders[0])
        self.assertTrue(restarted.acquired)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from imagent.adapters import DeliverySubmissionConflict
from imagent.contracts import (
    ConversationRef,
    DeliveryRouteSnapshot,
    DeliverySubmissionOrigin,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
)
from imagent.gateway.delivery import proactive as proactive_owner
from imagent.gateway.persistence.memory import InMemoryDeliverySubmissionRepository
from imagent.interaction.operations import ContractViolation


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


class InMemoryDeliverySubmissionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def test_delivery_orchestration_no_longer_owns_memory_repository(self) -> None:
        self.assertFalse(hasattr(proactive_owner, "InMemoryDeliverySubmissionRepository"))

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


if __name__ == "__main__":
    unittest.main()

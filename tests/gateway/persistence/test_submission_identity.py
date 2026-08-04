from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from imagent.adapters import DeliverySubmissionConflict
from imagent.contracts import (
    ConversationRef,
    ThreadRef,
)
from imagent.gateway.delivery import DeliverySubmissionOrigin
from imagent.gateway.persistence import (
    DeliveryRouteSnapshot,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
)
from imagent.gateway.persistence.memory import InMemoryDeliverySubmissionRepository
from imagent.interaction.channels import DeliveryReceipt, DeliveryReceiptStatus
from imagent.storage import SQLiteGatewayState


def _submission() -> DeliverySubmissionRecord:
    now = datetime.now(UTC)
    thread_ref = ThreadRef("app", "thread-1")
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
                snapshot=DeliveryRouteSnapshot(
                    conversation_ref=ConversationRef("channel", "conversation-1"),
                    thread_ref=thread_ref,
                    route_id="route-1",
                    route_updated_at=now,
                    reply_to_message_id="reply-1",
                ),
                state=DeliverySubmissionState.IN_FLIGHT,
                updated_at=now,
            ),
            DestinationDeliveryRecord(
                delivery_id="destination-2",
                snapshot=DeliveryRouteSnapshot(
                    conversation_ref=ConversationRef("channel", "conversation-2"),
                    thread_ref=thread_ref,
                    route_id="route-2",
                    route_updated_at=now,
                    reply_to_message_id="reply-2",
                ),
                state=DeliverySubmissionState.IN_FLIGHT,
                updated_at=now,
            ),
        ),
        created_at=now,
        updated_at=now,
    )


def _same_identity_with_terminal_outcomes(
    record: DeliverySubmissionRecord,
) -> DeliverySubmissionRecord:
    later = record.updated_at + timedelta(seconds=1)
    destinations = tuple(
        replace(
            destination,
            state=DeliverySubmissionState.ACCEPTED,
            updated_at=later,
            receipt=DeliveryReceipt(
                status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            ),
        )
        for destination in reversed(record.destinations)
    )
    return replace(record, destinations=destinations, updated_at=later)


def _identity_mutations(
    record: DeliverySubmissionRecord,
) -> dict[str, DeliverySubmissionRecord]:
    destination = record.destinations[0]
    snapshot = destination.snapshot
    mutations = {
        "destination_id": replace(destination, delivery_id="destination-new"),
        "conversation_ref": replace(
            destination,
            snapshot=replace(
                snapshot,
                conversation_ref=ConversationRef("channel", "conversation-new"),
            ),
        ),
        "thread_ref": replace(
            destination,
            snapshot=replace(snapshot, thread_ref=ThreadRef("app", "thread-new")),
        ),
        "route_id": replace(
            destination,
            snapshot=replace(snapshot, route_id="route-new"),
        ),
        "route_updated_at": replace(
            destination,
            snapshot=replace(
                snapshot,
                route_updated_at=record.updated_at + timedelta(seconds=1),
            ),
        ),
        "reply_to_message_id": replace(
            destination,
            snapshot=replace(snapshot, reply_to_message_id="reply-new"),
        ),
    }
    return {
        name: replace(record, destinations=(replacement, record.destinations[1]))
        for name, replacement in mutations.items()
    }


class DeliverySubmissionIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_identity_is_snapshot_set_not_mutable_outcome(self) -> None:
        repository = InMemoryDeliverySubmissionRepository()
        record = _submission()
        await repository.reserve_delivery_submission(record)

        repeated = await repository.reserve_delivery_submission(
            _same_identity_with_terminal_outcomes(record)
        )

        self.assertFalse(repeated.acquired)
        self.assertEqual(repeated.record, record)

    async def test_memory_rejects_every_destination_identity_change(self) -> None:
        repository = InMemoryDeliverySubmissionRepository()
        record = _submission()
        await repository.reserve_delivery_submission(record)

        for name, changed in _identity_mutations(record).items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    DeliverySubmissionConflict,
                    "snapshot set changed",
                ):
                    await repository.reserve_delivery_submission(changed)

    async def test_sqlite_restart_preserves_snapshot_set_identity(self) -> None:
        record = _submission()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "gateway.sqlite")
            repository = SQLiteGatewayState(path)
            await repository.reserve_delivery_submission(record)
            await repository.close()

            reopened = SQLiteGatewayState(path)
            repeated = await reopened.reserve_delivery_submission(
                _same_identity_with_terminal_outcomes(record)
            )
            self.assertFalse(repeated.acquired)
            self.assertEqual(repeated.record, record)

            for name, changed in _identity_mutations(record).items():
                with self.subTest(name=name):
                    with self.assertRaisesRegex(
                        DeliverySubmissionConflict,
                        "snapshot set changed",
                    ):
                        await reopened.reserve_delivery_submission(changed)
            await reopened.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
from dataclasses import replace

from ...adapters import DeliverySubmissionConflict
from ...contracts import (
    DeliveryReservation,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    validate_delivery_submission_record,
)
from .submission_identity import ensure_same_delivery_submission_reservation


class InMemoryDeliverySubmissionRepository:
    """Process-local route snapshots and outcomes without message content."""

    def __init__(self) -> None:
        self._records: dict[str, DeliverySubmissionRecord] = {}
        self._lock = asyncio.Lock()

    async def get_delivery_submission(
        self,
        submission_id: str,
    ) -> DeliverySubmissionRecord | None:
        async with self._lock:
            return self._records.get(submission_id)

    async def reserve_delivery_submission(
        self,
        record: DeliverySubmissionRecord,
    ) -> DeliveryReservation:
        validate_delivery_submission_record(record)
        async with self._lock:
            existing = self._records.get(record.submission_id)
            if existing is not None:
                ensure_same_delivery_submission_reservation(existing, record)
                return DeliveryReservation(acquired=False, record=existing)
            self._records[record.submission_id] = record
            return DeliveryReservation(acquired=True, record=record)

    async def update_delivery_destination(
        self,
        submission_id: str,
        destination_delivery_id: str,
        *,
        expected_state: DeliverySubmissionState,
        destination: DestinationDeliveryRecord,
    ) -> DeliverySubmissionRecord:
        async with self._lock:
            existing = self._records.get(submission_id)
            if existing is None:
                raise KeyError(f"delivery submission does not exist: {submission_id}")
            updated = _replace_destination(
                existing,
                destination_delivery_id,
                expected_state=expected_state,
                replacement=destination,
            )
            validate_delivery_submission_record(updated)
            self._records[submission_id] = updated
            return updated


def _replace_destination(
    record: DeliverySubmissionRecord,
    destination_delivery_id: str,
    *,
    expected_state: DeliverySubmissionState,
    replacement: DestinationDeliveryRecord,
) -> DeliverySubmissionRecord:
    if replacement.delivery_id != destination_delivery_id:
        raise DeliverySubmissionConflict("destination delivery identity changed")
    found = False
    destinations: list[DestinationDeliveryRecord] = []
    for current in record.destinations:
        if current.delivery_id != destination_delivery_id:
            destinations.append(current)
            continue
        found = True
        if current.state is not expected_state:
            if current == replacement:
                destinations.append(current)
                continue
            raise DeliverySubmissionConflict("delivery destination state changed")
        if current.snapshot != replacement.snapshot:
            raise DeliverySubmissionConflict("delivery destination snapshot changed")
        destinations.append(replacement)
    if not found:
        raise KeyError(f"delivery destination does not exist: {destination_delivery_id}")
    return replace(
        record,
        destinations=tuple(destinations),
        updated_at=max(record.updated_at, replacement.updated_at),
    )

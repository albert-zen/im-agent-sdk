from __future__ import annotations

from ...contracts import DeliverySubmissionRecord
from .repository_contracts import DeliverySubmissionConflict


def ensure_same_delivery_submission_reservation(
    existing: DeliverySubmissionRecord,
    replacement: DeliverySubmissionRecord,
) -> None:
    """Require the immutable root and resolved destination snapshot set."""

    if (
        existing.submission_id != replacement.submission_id
        or existing.delivery_id != replacement.delivery_id
        or existing.origin is not replacement.origin
        or existing.principal_id != replacement.principal_id
        or existing.target_fingerprint != replacement.target_fingerprint
        or existing.payload_fingerprint != replacement.payload_fingerprint
    ):
        raise DeliverySubmissionConflict(
            f"delivery ID belongs to a different submission: {existing.delivery_id}"
        )
    existing_snapshots = {
        destination.delivery_id: destination.snapshot for destination in existing.destinations
    }
    replacement_snapshots = {
        destination.delivery_id: destination.snapshot for destination in replacement.destinations
    }
    if existing_snapshots != replacement_snapshots:
        raise DeliverySubmissionConflict("delivery submission destination snapshot set changed")

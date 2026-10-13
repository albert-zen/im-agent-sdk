"""Pure monotonic evidence merge for the coherent stores' queued-input seam."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ...applications.contract import (
    QueuedInputReceipt,
    QueuedInputState,
    validate_queued_input_receipt,
)
from ...interaction.operations import ContractViolation
from .state_contracts import (
    QueuedInputCorrelation,
    QueuedInputOrigin,
    TurnReplyCorrelation,
    _require_queued_identifier,
    validate_queued_input_correlation,
    validate_queued_input_origin,
    validate_turn_reply_correlation,
)
from .store import QueuedInputReceiptConflict


def _merge_queued_receipt(
    stored: QueuedInputCorrelation,
    origin: QueuedInputOrigin,
    receipt: QueuedInputReceipt,
    turn_correlation: TurnReplyCorrelation | None,
    now: datetime,
) -> QueuedInputCorrelation:
    validate_queued_input_origin(origin)
    validate_queued_input_receipt(receipt)
    validate_queued_input_correlation(stored)
    if stored.origin != origin:
        raise QueuedInputReceiptConflict("queued input origin changed")
    if (
        receipt.thread_ref != origin.thread_ref
        or receipt.client_message_id != origin.client_message_id
        or receipt.input_fingerprint != origin.input_fingerprint
    ):
        raise QueuedInputReceiptConflict("queued input receipt does not match original input")
    if receipt.state is QueuedInputState.ADMITTED:
        if turn_correlation is None:
            raise QueuedInputReceiptConflict(
                "admitted queue evidence requires original Turn correlation"
            )
        validate_turn_reply_correlation(turn_correlation)
        _require_queued_identifier(turn_correlation.correlation_id, "correlation_id")
        created_at = turn_correlation.created_at
        if (
            not isinstance(created_at, datetime)
            or created_at.tzinfo is None
            or created_at.utcoffset() is None
        ):
            raise ContractViolation("queued Turn correlation time must include a valid timezone")
        if (
            turn_correlation.turn_ref != receipt.turn_ref
            or turn_correlation.client_message_id != origin.client_message_id
            or turn_correlation.conversation_ref != origin.conversation_ref
            or turn_correlation.reply_to_message_id != origin.message_id
        ):
            raise QueuedInputReceiptConflict(
                "Turn correlation does not match original queued input"
            )
    elif turn_correlation is not None:
        raise QueuedInputReceiptConflict("only admitted queue evidence can create Turn correlation")
    previous = stored.receipt
    if previous is not None:
        if previous.queued_item_id != receipt.queued_item_id:
            raise QueuedInputReceiptConflict("queued item identity changed")
        if previous.state is not QueuedInputState.QUEUED:
            if receipt.state is QueuedInputState.QUEUED:
                return stored
            if previous != receipt:
                raise QueuedInputReceiptConflict("terminal queued input evidence conflicts")
        if previous == receipt:
            return stored
    result = replace(stored, receipt=receipt, updated_at=max(stored.updated_at, now))
    validate_queued_input_correlation(result)
    return result

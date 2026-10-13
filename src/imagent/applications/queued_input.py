"""Optional bounded queued-input observation and control ports."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..interaction.operations import ContractViolation
from .contract import QueuedInputReceipt, ThreadRef, validate_thread_ref

MAX_QUEUED_INPUT_READ_IDS = 4096


class QueuedInputNoticeKind(StrEnum):
    CHANGED = "changed"
    ATTEMPT_FAILED = "attempt_failed"


@dataclass(frozen=True, slots=True)
class QueuedInputNotice:
    thread_ref: ThreadRef
    client_message_id: str
    queued_item_id: str
    kind: QueuedInputNoticeKind
    notice_id: str

    def __post_init__(self) -> None:
        validate_thread_ref(self.thread_ref)
        for value in (self.client_message_id, self.queued_item_id, self.notice_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ContractViolation("queued notice requires bounded stable identities")
        if not isinstance(self.kind, QueuedInputNoticeKind):
            raise ContractViolation("queued notice requires a typed kind")


@runtime_checkable
class QueuedInputSnapshotReader(Protocol):
    async def read_queued_input_receipts(
        self, thread_ref: ThreadRef, client_message_ids: tuple[str, ...]
    ) -> tuple[QueuedInputReceipt | None, ...]: ...


@runtime_checkable
class QueuedInputControl(Protocol):
    async def cancel_queued_input_receipt(
        self, receipt: QueuedInputReceipt
    ) -> QueuedInputReceipt: ...


@runtime_checkable
class QueuedInputSubscription(AsyncIterator[QueuedInputNotice], Protocol):
    async def aclose(self) -> None: ...


@runtime_checkable
class QueuedInputObserver(Protocol):
    async def subscribe_queued_inputs(self, thread_ref: ThreadRef) -> QueuedInputSubscription: ...

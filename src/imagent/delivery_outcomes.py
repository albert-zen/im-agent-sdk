from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import islice
from types import MappingProxyType
from typing import Protocol

from .contracts import (
    DeliveryItemReceipt,
    DeliveryReceipt,
    DeliverySegmentReceipt,
)
from .diagnostics import (
    DeliveryOutcomeObserverDiagnosticFacts,
    DeliveryOutcomeObserverFailureCode,
)
from .interaction.media import AttachmentContent, AttachmentHandle, LocalPath, RemoteUrl
from .interaction.messages import OutboundMessage, TextContent


class DeliveryOutcomeErrorCode(StrEnum):
    """Bounded O2 errors when no final Coordinator receipt exists."""

    PLANNING_FAILED = "planning_failed"
    EXECUTION_FAILED = "execution_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DeliveryOutcomeContext:
    """Immutable bounded original message for one destination attempt."""

    message: OutboundMessage


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Exactly one final typed receipt or fixed execution error."""

    receipt: DeliveryReceipt | None = None
    error: DeliveryOutcomeErrorCode | None = None

    def __post_init__(self) -> None:
        if (self.receipt is None) == (self.error is None):
            raise ValueError("delivery outcome requires exactly one receipt or error")
        if self.receipt is not None and not isinstance(self.receipt, DeliveryReceipt):
            raise TypeError("delivery outcome receipt must use the common contract")
        if self.error is not None and not isinstance(self.error, DeliveryOutcomeErrorCode):
            raise TypeError("delivery outcome error must use the fixed vocabulary")


class DeliveryOutcomeObserver(Protocol):
    """Best-effort O2 observation without receipt or retry authority."""

    async def observe_delivery_outcome(
        self,
        context: DeliveryOutcomeContext,
        outcome: DeliveryOutcome,
    ) -> None: ...


class DeliveryOutcomeObserverRuntime:
    """Detached finite O2 admission, lifetime, and redacted diagnostics."""

    def __init__(
        self,
        observer: DeliveryOutcomeObserver,
        *,
        timeout_seconds: float,
        max_items: int,
        max_text_characters: int,
        max_concurrency: int,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("delivery outcome observer timeout must be finite and positive")
        if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 1:
            raise ValueError("delivery outcome observer item limit must be positive")
        if (
            not isinstance(max_text_characters, int)
            or isinstance(max_text_characters, bool)
            or max_text_characters < 1
        ):
            raise ValueError("delivery outcome observer text limit must be positive")
        if (
            not isinstance(max_concurrency, int)
            or isinstance(max_concurrency, bool)
            or max_concurrency < 1
        ):
            raise ValueError("delivery outcome observer concurrency must be positive")
        self._observer = observer
        self._timeout_seconds = timeout_seconds
        self._max_items = max_items
        self._max_text_characters = max_text_characters
        self._max_concurrency = max_concurrency
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._overrun_tasks: set[asyncio.Task[None]] = set()
        self._notification_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._timeout_count = 0
        self._cancellation_count = 0
        self._cancellation_overrun_count = 0
        self._capacity_rejection_count = 0
        self._last_failure_code: DeliveryOutcomeObserverFailureCode | None = None
        self._closed = False

    def notify(
        self,
        message: OutboundMessage,
        *,
        receipt: DeliveryReceipt | None = None,
        error: DeliveryOutcomeErrorCode | None = None,
    ) -> None:
        """Offer one attempt outcome without delaying its resolved caller."""

        self._notification_count += 1
        if (
            self._closed
            or len(self._active_tasks) + len(self._overrun_tasks) >= self._max_concurrency
        ):
            self._capacity_rejection_count += 1
            self._record_failure(DeliveryOutcomeObserverFailureCode.CAPACITY_EXHAUSTED)
            return
        try:
            bounded_message, message_string_characters = _immutable_message(
                message,
                max_items=self._max_items,
                max_text_characters=self._max_text_characters,
            )
            context = DeliveryOutcomeContext(bounded_message)
            outcome = DeliveryOutcome(
                receipt=(
                    _bounded_receipt(
                        receipt,
                        max_items=self._max_items,
                        max_text_characters=(self._max_text_characters - message_string_characters),
                    )
                    if receipt is not None
                    else None
                ),
                error=error,
            )
        except BaseException:
            self._record_failure(DeliveryOutcomeObserverFailureCode.INVALID_FACTS)
            return
        coroutine = self._observe(context, outcome)
        try:
            task = asyncio.create_task(
                coroutine,
                name="imagent-delivery-outcome",
            )
        except BaseException:
            coroutine.close()
            self._record_failure(DeliveryOutcomeObserverFailureCode.INVALID_FACTS)
            return
        self._active_tasks.add(task)
        task.add_done_callback(self._finish_task)

    def start(self) -> None:
        if self._active_tasks or self._overrun_tasks:
            raise RuntimeError("delivery outcome observer cannot start with active work")
        self._closed = False

    async def _observe(
        self,
        context: DeliveryOutcomeContext,
        outcome: DeliveryOutcome,
    ) -> None:
        task = asyncio.create_task(
            self._observer.observe_delivery_outcome(context, outcome),
            name="imagent-delivery-outcome-observer",
        )
        try:
            done, _ = await asyncio.wait((task,), timeout=self._timeout_seconds)
            if not done:
                joined = await _cancel_and_join(task, timeout_seconds=self._timeout_seconds)
                self._timeout_count += 1
                if not joined:
                    self._cancellation_overrun_count += 1
                    self._track_overrun(task)
                self._record_failure(DeliveryOutcomeObserverFailureCode.TIMED_OUT)
                return
            task.result()
        except asyncio.CancelledError:
            joined = True
            if not task.done():
                joined = await _cancel_and_join(task, timeout_seconds=self._timeout_seconds)
            self._cancellation_count += 1
            if not joined:
                self._cancellation_overrun_count += 1
                self._track_overrun(task)
            self._record_failure(DeliveryOutcomeObserverFailureCode.CANCELLED)
            raise
        except BaseException:
            if not task.done():
                await _cancel_and_join(task, timeout_seconds=self._timeout_seconds)
            self._record_failure(DeliveryOutcomeObserverFailureCode.OBSERVER_FAILED)
            return
        self._success_count += 1

    def diagnostic_facts(self) -> DeliveryOutcomeObserverDiagnosticFacts:
        return DeliveryOutcomeObserverDiagnosticFacts(
            notification_count=self._notification_count,
            success_count=self._success_count,
            failure_count=self._failure_count,
            timeout_count=self._timeout_count,
            cancellation_count=self._cancellation_count,
            cancellation_overrun_count=self._cancellation_overrun_count,
            capacity_rejection_count=self._capacity_rejection_count,
            last_failure_code=self._last_failure_code,
        )

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._active_tasks | self._overrun_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self._timeout_seconds)
            for task in done:
                _consume_task_result(task)
            for task in pending:
                task.cancel()

    def _finish_task(self, task: asyncio.Task[None]) -> None:
        self._active_tasks.discard(task)
        _consume_task_result(task)

    def _track_overrun(self, task: asyncio.Task[None]) -> None:
        self._overrun_tasks.add(task)
        task.add_done_callback(self._finish_overrun)

    def _finish_overrun(self, task: asyncio.Task[None]) -> None:
        self._overrun_tasks.discard(task)
        _consume_task_result(task)

    def _record_failure(self, code: DeliveryOutcomeObserverFailureCode) -> None:
        self._failure_count += 1
        self._last_failure_code = code


def _immutable_message(
    message: OutboundMessage,
    *,
    max_items: int,
    max_text_characters: int,
) -> tuple[OutboundMessage, int]:
    if not isinstance(message, OutboundMessage):
        raise TypeError("delivery outcome context requires OutboundMessage")
    if not isinstance(message.content, tuple) or len(message.content) > max_items:
        raise ValueError("delivery outcome context content is not bounded")
    strings = [
        message.delivery_id,
        message.conversation_ref.channel_instance_id,
        message.conversation_ref.native_conversation_id,
    ]
    if message.reply_to is not None:
        strings.append(message.reply_to)
    for item in message.content:
        if isinstance(item, TextContent):
            strings.append(item.text)
            continue
        if not isinstance(item, AttachmentContent):
            raise TypeError("delivery outcome context content must use common typed items")
        strings.extend((item.attachment_id, item.media_type))
        if item.filename is not None:
            strings.append(item.filename)
        if isinstance(item.source, LocalPath):
            strings.append(item.source.path)
        elif isinstance(item.source, RemoteUrl):
            strings.append(item.source.url)
        elif isinstance(item.source, AttachmentHandle):
            strings.append(item.source.handle_id)
        else:
            raise TypeError("delivery outcome attachment source is invalid")
    string_characters = _require_bounded_strings(
        strings,
        max_characters=max_text_characters,
    )
    content = tuple(
        replace(item, metadata=_bounded_metadata(item.metadata))
        if isinstance(item, AttachmentContent)
        else item
        for item in message.content
    )
    return (
        replace(
            message,
            content=content,
            metadata=_bounded_metadata(message.metadata),
        ),
        string_characters,
    )


def _bounded_metadata(metadata: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(metadata, Mapping):
        raise TypeError("delivery outcome metadata must be a mapping")
    keys = list(islice(metadata, 17))
    if len(keys) > 16:
        raise ValueError("delivery outcome metadata exceeds 16 items")
    copied: dict[str, object] = {}
    for key in keys:
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ValueError("delivery outcome metadata key is invalid")
        value = metadata[key]
        if isinstance(value, str):
            if len(value) > 256:
                raise ValueError("delivery outcome metadata text is too long")
        elif value is None or isinstance(value, bool):
            pass
        elif isinstance(value, int):
            if not -(2**63) <= value <= 2**63 - 1:
                raise ValueError("delivery outcome metadata integer is out of range")
        elif not (isinstance(value, float) and math.isfinite(value)):
            raise ValueError("delivery outcome metadata must contain bounded scalars")
        copied[key] = value
    return MappingProxyType(copied)


def _bounded_receipt(
    receipt: DeliveryReceipt,
    *,
    max_items: int,
    max_text_characters: int,
) -> DeliveryReceipt:
    if not isinstance(receipt, DeliveryReceipt):
        raise TypeError("delivery outcome receipt must use the common contract")
    if (
        not isinstance(receipt.items, tuple)
        or not isinstance(receipt.segments, tuple)
        or len(receipt.items) > max_items
        or len(receipt.segments) > max_items
    ):
        raise ValueError("delivery outcome receipt exceeds its item limit")
    if not all(isinstance(item, DeliveryItemReceipt) for item in receipt.items):
        raise TypeError("delivery outcome item receipt must use the common contract")
    if not all(isinstance(segment, DeliverySegmentReceipt) for segment in receipt.segments):
        raise TypeError("delivery outcome segment receipt must use the common contract")
    if any(
        not isinstance(segment.source_content_indexes, tuple)
        or len(segment.source_content_indexes) > max_items
        for segment in receipt.segments
    ):
        raise ValueError("delivery outcome segment receipt exceeds its source index limit")
    strings: list[str] = []
    if receipt.native_message_id is not None:
        strings.append(receipt.native_message_id)
    for item in receipt.items:
        if item.attachment_id is not None:
            strings.append(item.attachment_id)
        if item.native_message_id is not None:
            strings.append(item.native_message_id)
    for segment in receipt.segments:
        strings.append(segment.delivery_id)
        if segment.native_message_id is not None:
            strings.append(segment.native_message_id)
    _require_bounded_strings(strings, max_characters=max_text_characters)
    return replace(
        receipt,
        detail=None,
        items=tuple(
            replace(item, detail=None) if isinstance(item, DeliveryItemReceipt) else item
            for item in receipt.items
        ),
        segments=tuple(
            replace(item, detail=None) if isinstance(item, DeliverySegmentReceipt) else item
            for item in receipt.segments
        ),
    )


def _require_bounded_strings(strings: list[str], *, max_characters: int) -> int:
    total = 0
    for value in strings:
        if not isinstance(value, str):
            raise TypeError("delivery outcome string facts must be typed")
        total += len(value)
        if total > max_characters:
            raise ValueError("delivery outcome string facts exceed their configured limit")
    return total


async def _cancel_and_join(task: asyncio.Task[None], *, timeout_seconds: float) -> bool:
    task.cancel()
    done, _ = await asyncio.wait((task,), timeout=timeout_seconds)
    if not done:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        return False
    _consume_task_result(task)
    return True


def _consume_task_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except BaseException:
        pass

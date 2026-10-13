"""Bounded native resume ownership and redacted queue reconciliation wakeups."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ...interaction.operations import ContractViolation
from ..contract import ThreadRef
from ..events import EventStreamGap, EventStreamOverflow, EventStreamReset
from ..queued_input import QueuedInputNotice, QueuedInputNoticeKind
from .appserver.client.handoff import AppServerResponse
from .zen_queue import (
    _MAX_SNAPSHOT_ITEMS,
    _QUEUE_CAPABILITY,
    _identifier,
    _native_input_capabilities,
    _queued_receipts_from_items,
)


class _ObservationClient(Protocol):
    connection_epoch: int

    async def call_with_dispatch_position(
        self, method: str, params: dict[str, Any]
    ) -> AppServerResponse: ...

    async def resume_zen_thread(
        self, thread_id: str, *, expected_connection_epoch: int
    ) -> AppServerResponse: ...


@dataclass(frozen=True, slots=True)
class _NativeChange:
    process_epoch: str
    watermark: int
    kind: str
    client_id: str | None = None
    queued_id: str | None = None
    identity: str | None = None


def _watermark(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ContractViolation("native queue watermark is invalid")
    return value


def _change(params: object, thread: ThreadRef) -> _NativeChange:
    if not isinstance(params, Mapping) or params.get("threadId") != thread.thread_id:
        raise ContractViolation("native queue event has a foreign Thread")
    epoch = _identifier(params.get("processEpoch"))
    watermark = _watermark(params.get("watermark"))
    event = params.get("event")
    if not isinstance(event, Mapping):
        raise ContractViolation("native queue event is invalid")
    kind = event.get("type")
    if kind == "queue_failed":
        if event.get("threadId") != thread.thread_id:
            raise ContractViolation("native queue failure has a foreign Thread")
        return _NativeChange(
            epoch,
            watermark,
            "failed",
            _identifier(event.get("clientId")),
            _identifier(event.get("queuedItemId")),
            _identifier(event.get("attemptId")),
        )
    if kind != "item_completed":
        return _NativeChange(epoch, watermark, "other")
    item = event.get("item")
    if not isinstance(item, Mapping) or item.get("threadId") != thread.thread_id:
        raise ContractViolation("native canonical event has a foreign Thread")
    item_kind = item.get("type")
    if item_kind not in {
        "user_message_queued",
        "user_message",
        "user_message_queue_cancelled",
        "user_message_queue_edited",
    }:
        return _NativeChange(epoch, watermark, "other")
    identity = _identifier(item.get("id"))
    client = _identifier(item.get("clientId"))
    queued = (
        identity
        if item_kind == "user_message_queued"
        else (None if item_kind == "user_message" else _identifier(item.get("queuedItemId")))
    )
    return _NativeChange(epoch, watermark, str(item_kind), client, queued, identity)


class _QueueSubscription:
    def __init__(self, owner: _ZenQueueObservation, entry: _Entry) -> None:
        self._owner = owner
        self._entry = entry
        self._queue: asyncio.Queue[QueuedInputNotice | EventStreamGap | None] = asyncio.Queue(
            owner.max_pending + 1
        )
        self._closed = False
        self._terminal = False

    def __aiter__(self) -> _QueueSubscription:
        return self

    async def __anext__(self) -> QueuedInputNotice:
        if self._closed:
            raise StopAsyncIteration
        try:
            item = await self._queue.get()
        except asyncio.CancelledError:
            await self.aclose()
            raise
        if item is None or isinstance(item, EventStreamGap):
            self._closed = True
            if item is not None:
                raise item
            raise StopAsyncIteration
        return item

    async def aclose(self) -> None:
        self._finish(None)

    def _publish(self, notice: QueuedInputNotice) -> None:
        if self._terminal:
            return
        if self._queue.qsize() >= self._owner.max_pending:
            self._finish(EventStreamOverflow(max_pending=self._owner.max_pending))
        else:
            self._queue.put_nowait(notice)

    def _finish(self, error: EventStreamGap | None) -> None:
        if self._terminal:
            return
        self._terminal = True
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(error)
        self._entry.subscribers.discard(self)
        self._owner._retire(self._entry)


@dataclass(slots=True)
class _Entry:
    thread: ThreadRef
    subscribers: set[_QueueSubscription] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    epoch: int = 0
    process_epoch: str = ""
    watermark: int | None = None
    snapshot_watermark: int = 0
    last_change: _NativeChange | None = None
    queued_ids: dict[str, str] = field(default_factory=dict)
    pending: list[_NativeChange] = field(default_factory=list)
    error: EventStreamGap | None = None


class _ZenQueueObservation:
    def __init__(
        self,
        client: _ObservationClient,
        validate_thread: Callable[[ThreadRef, object], Mapping[str, object]],
        *,
        max_pending: int,
    ) -> None:
        self.client = client
        self.validate_thread = validate_thread
        self.max_pending = max_pending
        self.entries: dict[ThreadRef, _Entry] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.closing = False

    async def subscribe(self, thread: ThreadRef) -> _QueueSubscription:
        if self.closing:
            raise EventStreamReset("queued_input_observer_closed")
        entry = self.entries.get(thread)
        if entry is None:
            entry = _Entry(thread)
            self.entries[thread] = entry
        subscription = _QueueSubscription(self, entry)
        entry.subscribers.add(subscription)
        if entry.task is None:
            entry.task = asyncio.create_task(self._install(entry))
            self.tasks.add(entry.task)

            def finished(task: asyncio.Task[None]) -> None:
                self.tasks.discard(task)
                if not task.cancelled():
                    task.exception()
                self._retire(entry)

            entry.task.add_done_callback(finished)
        try:
            await asyncio.shield(entry.task)
            if entry.error is not None:
                raise entry.error
            if self.closing:
                raise EventStreamReset("queued_input_observer_closed")
            return subscription
        except BaseException:
            await subscription.aclose()
            raise

    def _retire(self, entry: _Entry) -> None:
        if not entry.subscribers and entry.task is not None and entry.task.done():
            if self.entries.get(entry.thread) is entry:
                self.entries.pop(entry.thread, None)

    def _fail(self, entry: _Entry, error: EventStreamGap) -> None:
        entry.error = error
        entry.pending.clear()
        for subscription in tuple(entry.subscribers):
            subscription._finish(error)

    async def _install(self, entry: _Entry) -> None:
        try:
            initialized = await self.client.call_with_dispatch_position("zen/initialize", {})
            if _QUEUE_CAPABILITY not in _native_input_capabilities(initialized.result):
                raise NotImplementedError("native Host does not support queued observation")
            entry.epoch = initialized.dispatch_position.connection_epoch
            entry.process_epoch = _identifier(initialized.result.get("processEpoch"))
            response = await self.client.resume_zen_thread(
                entry.thread.thread_id, expected_connection_epoch=entry.epoch
            )
            # Native queue events and CAS message/request output have distinct
            # subscription sets. Install both read-only barriers explicitly;
            # neither RPC resumes queue execution or mutates Thread options.
            output = await self.client.call_with_dispatch_position(
                "thread/resume", {"threadId": entry.thread.thread_id}
            )
            if output.dispatch_position.connection_epoch != entry.epoch:
                raise ContractViolation("CAS queue output observation changed connection epoch")
            self.validate_thread(entry.thread, output.result.get("thread"))
            snapshot = response.result
            if (
                response.dispatch_position.connection_epoch != entry.epoch
                or self.client.connection_epoch != entry.epoch
                or snapshot.get("processEpoch") != entry.process_epoch
                or snapshot.get("threadId") != entry.thread.thread_id
            ):
                raise ContractViolation("native queue resume epoch or Thread changed")
            thread = self.validate_thread(entry.thread, snapshot.get("thread"))
            items = thread.get("items")
            _queued_receipts_from_items(items, entry.thread, ())
            assert isinstance(items, list)
            for item in items:
                if item.get("type") == "user_message_queued":
                    client, queued = _identifier(item.get("clientId")), _identifier(item.get("id"))
                    if client in entry.queued_ids:
                        raise ContractViolation("native resume queue lineage conflicts")
                    # This index only identifies reconciliation wakeups. Other
                    # native surfaces may use different modes and payloads;
                    # exact requested SDK receipts validate those separately.
                    entry.queued_ids[client] = queued
            watermark = _watermark(snapshot.get("watermark"))
            tail = snapshot.get("events")
            if not isinstance(tail, list) or len(tail) > self.max_pending:
                raise ContractViolation("native resume requires a bounded complete event tail")
            prior = -1
            for raw in tail:
                change = _change(raw, entry.thread)
                if (
                    change.process_epoch != entry.process_epoch
                    or change.watermark <= prior
                    or change.watermark > watermark
                ):
                    raise ContractViolation("native resume tail ordering is invalid")
                prior = change.watermark
                self._publish(entry, change)
            entry.snapshot_watermark = watermark
            entry.watermark = watermark
            pending, entry.pending = entry.pending, []
            for change in pending:
                self._accept(entry, change)
            if entry.error is not None:
                raise entry.error
        except BaseException:
            self._fail(entry, EventStreamReset("queued_input_resume_failed"))
            raise

    def notification(self, notification: Mapping[str, object]) -> None:
        params = notification.get("params")
        if not isinstance(params, Mapping):
            self.reset()
            return
        for entry in tuple(self.entries.values()):
            if params.get("threadId") != entry.thread.thread_id or entry.error is not None:
                continue
            if entry.epoch == 0:
                continue
            try:
                if notification.get("_connection_epoch") != entry.epoch:
                    raise ContractViolation("native queue connection epoch changed")
                change = _change(params, entry.thread)
                if entry.watermark is None:
                    if len(entry.pending) >= self.max_pending:
                        raise EventStreamOverflow(max_pending=self.max_pending)
                    entry.pending.append(change)
                else:
                    self._accept(entry, change)
            except Exception as error:
                self._fail(
                    entry,
                    error
                    if isinstance(error, EventStreamGap)
                    else EventStreamReset("queued_input_event_invalid"),
                )

    def _accept(self, entry: _Entry, change: _NativeChange) -> None:
        if change.process_epoch != entry.process_epoch:
            raise ContractViolation("native queue process epoch changed")
        assert entry.watermark is not None
        if change.watermark <= entry.snapshot_watermark:
            return  # Canonical snapshot and tail already cover this wire duplicate.
        if change == entry.last_change:
            return
        if change.watermark != entry.watermark + 1:
            raise ContractViolation("native queue observation has a watermark gap")
        entry.watermark = change.watermark
        entry.last_change = change
        self._publish(entry, change)

    def _publish(self, entry: _Entry, change: _NativeChange) -> None:
        client = change.client_id
        if client is None:
            return
        queued = entry.queued_ids.get(client)
        if change.kind == "user_message_queued":
            if queued is not None and queued != change.queued_id:
                raise ContractViolation("native live queue lineage conflicts")
            assert change.queued_id is not None
            queued = change.queued_id
            if queued is not None and client not in entry.queued_ids:
                if len(entry.queued_ids) >= _MAX_SNAPSHOT_ITEMS:
                    raise EventStreamOverflow(max_pending=_MAX_SNAPSHOT_ITEMS)
                entry.queued_ids[client] = queued
        if queued is None:
            if change.kind == "user_message":
                return  # An ordinary human message has no queued lineage.
            raise ContractViolation("native queue event has no canonical queued lineage")
        if change.queued_id is not None and change.queued_id != queued:
            raise ContractViolation("native queue event changed queued identity")
        identity = hashlib.sha256(
            repr((entry.process_epoch, entry.thread, change.watermark, change.identity)).encode()
        ).hexdigest()
        notice = QueuedInputNotice(
            entry.thread,
            client,
            queued,
            QueuedInputNoticeKind.ATTEMPT_FAILED
            if change.kind == "failed"
            else QueuedInputNoticeKind.CHANGED,
            identity,
        )
        for subscription in tuple(entry.subscribers):
            subscription._publish(notice)

    def reset(self) -> None:
        for entry in tuple(self.entries.values()):
            self._fail(entry, EventStreamReset("queued_input_connection_reset"))

    def begin_close(self) -> None:
        self.closing = True
        self.reset()

    async def wait_closed(self) -> None:
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        self.entries.clear()

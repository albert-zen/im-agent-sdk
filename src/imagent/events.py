from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Hashable, Iterable
from typing import Generic, TypeVar, cast

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class _OverflowSignal:
    pass


_OVERFLOW_SIGNAL = _OverflowSignal()


class CursorExpired(ValueError):
    pass


class EventBufferOverflow(RuntimeError):
    """A bounded live projection lost events and requires reconciliation."""

    def __init__(self, gap_code: str, *, max_pending: int) -> None:
        super().__init__(f"{gap_code} (capacity={max_pending})")
        self.gap_code = gap_code
        self.max_pending = max_pending


class EventStreamOverflow(EventBufferOverflow):
    def __init__(self, *, max_pending: int) -> None:
        super().__init__("application_event_fanout_overflow", max_pending=max_pending)


class FanoutSubscription(AsyncIterator[V], Generic[K, V]):
    def __init__(
        self,
        key: K,
        queue: asyncio.Queue[V | _OverflowSignal],
        close: Callable[[K, FanoutSubscription[K, V]], None],
    ) -> None:
        self._key = key
        self._queue = queue
        self._close_callback = close
        self._closed = False
        self._overflow: EventStreamOverflow | None = None

    def __aiter__(self) -> FanoutSubscription[K, V]:
        return self

    async def __anext__(self) -> V:
        if self._closed:
            raise StopAsyncIteration
        try:
            item = await self._queue.get()
        except asyncio.CancelledError:
            await self.aclose()
            raise
        if item is _OVERFLOW_SIGNAL:
            error = self._overflow
            self._overflow = None
            self._closed = True
            if error is None:
                raise RuntimeError("event stream ended with an invalid overflow signal")
            raise error
        return cast(V, item)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_callback(self._key, self)

    @property
    def pending_count(self) -> int:
        if self._overflow is not None:
            return 0
        return self._queue.qsize()

    def _publish(self, event: V) -> bool:
        if self._closed or self._overflow is not None:
            return False
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            while not self._queue.empty():
                self._queue.get_nowait()
            self._overflow = EventStreamOverflow(max_pending=self._queue.maxsize)
            self._queue.put_nowait(_OVERFLOW_SIGNAL)
            self._close_callback(self._key, self)
            return True
        return False


class EventBroadcaster(Generic[K, V]):
    """Live, non-blocking fan-out without retaining an authoritative event log."""

    def __init__(self, *, max_pending: int = 1024) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self._max_pending = max_pending
        self._subscribers: dict[K, set[FanoutSubscription[K, V]]] = {}

    def subscribe(
        self,
        key: K,
        *,
        initial: Iterable[V] = (),
    ) -> FanoutSubscription[K, V]:
        subscription = FanoutSubscription(
            key,
            asyncio.Queue[V | _OverflowSignal](maxsize=self._max_pending),
            self._remove,
        )
        self._subscribers.setdefault(key, set()).add(subscription)
        for event in initial:
            subscription._publish(event)
        return subscription

    def publish(self, key: K, event: V) -> None:
        for subscription in tuple(self._subscribers.get(key, ())):
            subscription._publish(event)

    def subscriber_count(self, key: K) -> int:
        return len(self._subscribers.get(key, ()))

    def _remove(
        self,
        key: K,
        subscription: FanoutSubscription[K, V],
    ) -> None:
        subscribers = self._subscribers.get(key)
        if subscribers is None:
            return
        subscribers.discard(subscription)
        if not subscribers:
            self._subscribers.pop(key, None)

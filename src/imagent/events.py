from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Hashable, Iterable
from typing import Generic, TypeVar, cast

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class _TerminalSignal:
    pass


_TERMINAL_SIGNAL = _TerminalSignal()


class CursorExpired(ValueError):
    pass


class EventStreamGap(RuntimeError):
    """A live Application observation lost continuity and requires reconciliation."""

    def __init__(self, gap_code: str, message: str) -> None:
        super().__init__(message)
        self.gap_code = gap_code


class EventBufferOverflow(EventStreamGap):
    """A bounded live projection overflowed and requires reconciliation."""

    def __init__(self, gap_code: str, *, max_pending: int) -> None:
        super().__init__(gap_code, f"{gap_code} (capacity={max_pending})")
        self.max_pending = max_pending


class EventStreamOverflow(EventBufferOverflow):
    def __init__(self, *, max_pending: int) -> None:
        super().__init__("application_event_fanout_overflow", max_pending=max_pending)


class EventStreamReset(EventStreamGap):
    def __init__(self, gap_code: str = "application_event_connection_reset") -> None:
        super().__init__(gap_code, gap_code)


class FanoutSubscription(AsyncIterator[V], Generic[K, V]):
    def __init__(
        self,
        key: K,
        queue: asyncio.Queue[V | _TerminalSignal],
        close: Callable[[K, FanoutSubscription[K, V]], None],
    ) -> None:
        self._key = key
        self._queue = queue
        self._close_callback = close
        self._closed = False
        self._terminal_error: EventStreamGap | None = None

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
        if item is _TERMINAL_SIGNAL:
            error = self._terminal_error
            self._terminal_error = None
            self._closed = True
            if error is None:
                raise RuntimeError("event stream ended with an invalid terminal signal")
            raise error
        return cast(V, item)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_callback(self._key, self)

    @property
    def pending_count(self) -> int:
        if self._terminal_error is not None:
            return max(0, self._queue.qsize() - 1)
        return self._queue.qsize()

    def _publish(self, event: V) -> bool:
        if self._closed or self._terminal_error is not None:
            return False
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._fail(EventStreamOverflow(max_pending=self._queue.maxsize))
            return True
        return False

    def _fail(self, error: EventStreamGap, *, discard_pending: bool = True) -> None:
        if self._closed or self._terminal_error is not None:
            return
        if discard_pending:
            while not self._queue.empty():
                self._queue.get_nowait()
        elif self._queue.full():
            self._queue.get_nowait()
        self._terminal_error = error
        self._queue.put_nowait(_TERMINAL_SIGNAL)
        self._close_callback(self._key, self)


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
            asyncio.Queue[V | _TerminalSignal](maxsize=self._max_pending),
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

    def fail_all(
        self,
        error_factory: Callable[[], EventStreamGap],
        *,
        discard_pending: bool = True,
    ) -> None:
        """Terminate every current subscriber with an explicit recoverable gap."""

        subscriptions = tuple(
            subscription
            for subscribers in self._subscribers.values()
            for subscription in subscribers
        )
        for subscription in subscriptions:
            subscription._fail(error_factory(), discard_pending=discard_pending)

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

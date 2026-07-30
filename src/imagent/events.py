from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Hashable, Iterable
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class CursorExpired(ValueError):
    pass


class FanoutSubscription(AsyncIterator[V], Generic[K, V]):
    def __init__(
        self,
        key: K,
        queue: asyncio.Queue[V],
        close: Callable[[K, FanoutSubscription[K, V]], None],
    ) -> None:
        self._key = key
        self._queue = queue
        self._close_callback = close
        self._closed = False

    def __aiter__(self) -> FanoutSubscription[K, V]:
        return self

    async def __anext__(self) -> V:
        if self._closed:
            raise StopAsyncIteration
        try:
            return await self._queue.get()
        except asyncio.CancelledError:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_callback(self._key, self)

    def _publish(self, event: V) -> None:
        if not self._closed:
            self._queue.put_nowait(event)


class EventBroadcaster(Generic[K, V]):
    """Live, non-blocking fan-out without retaining an authoritative event log."""

    def __init__(self) -> None:
        self._subscribers: dict[K, set[FanoutSubscription[K, V]]] = {}

    def subscribe(
        self,
        key: K,
        *,
        initial: Iterable[V] = (),
    ) -> FanoutSubscription[K, V]:
        subscription = FanoutSubscription(key, asyncio.Queue(), self._remove)
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

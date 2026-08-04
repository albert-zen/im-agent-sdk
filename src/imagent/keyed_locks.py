from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass(slots=True)
class _LockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class KeyedLockCapacityError(RuntimeError):
    """A new key cannot be admitted without exceeding the configured bound."""


class KeyedLockRegistry:
    """Waiter-safe keyed serialization with an optional active-key bound."""

    def __init__(self, *, max_active_keys: int | None = None) -> None:
        if max_active_keys is not None and (
            not isinstance(max_active_keys, int)
            or isinstance(max_active_keys, bool)
            or max_active_keys < 1
        ):
            raise ValueError("max_active_keys must be a positive integer")
        self._max_active_keys = max_active_keys
        self._entries: dict[object, _LockEntry] = {}

    @property
    def capacity(self) -> int | None:
        return self._max_active_keys

    @property
    def active_key_count(self) -> int:
        return len(self._entries)

    @asynccontextmanager
    async def hold(self, key: object) -> AsyncIterator[None]:
        entry = self._entries.get(key)
        if entry is None:
            if self._max_active_keys is not None and len(self._entries) >= self._max_active_keys:
                raise KeyedLockCapacityError(
                    f"keyed serialization capacity is exhausted (capacity={self._max_active_keys})"
                )
            entry = _LockEntry()
            self._entries[key] = entry
        entry.users += 1
        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            entry.users -= 1
            if entry.users == 0 and self._entries.get(key) is entry:
                self._entries.pop(key, None)

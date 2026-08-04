from __future__ import annotations

from collections import deque
from typing import Generic, TypeVar

T = TypeVar("T")


class GatewayStartupOverflow(RuntimeError):
    """Inbound startup admission exceeded its bounded process-local capacity."""

    def __init__(self, *, max_pending: int) -> None:
        super().__init__(f"gateway startup admission overflowed (capacity={max_pending})")
        self.max_pending = max_pending


class GatewayNotRunning(RuntimeError):
    """A Channel callback arrived outside the Gateway's live admission window."""


class GatewayStartupAdmission(Generic[T]):
    """One bounded FIFO for claimed inbound received during Gateway startup."""

    def __init__(self, *, max_pending: int) -> None:
        if max_pending < 1:
            raise ValueError("startup_buffer_max_pending must be positive")
        self._max_pending = max_pending
        self._entries: deque[T] = deque()
        self._overflow: GatewayStartupOverflow | None = None
        self._overflow_count = 0

    def __bool__(self) -> bool:
        return bool(self._entries)

    def reset(self) -> None:
        self._entries.clear()
        self._overflow = None

    def clear(self) -> None:
        self._entries.clear()

    def admit(self, entry: T) -> None:
        self.raise_if_overflowed()
        if len(self._entries) >= self._max_pending:
            self._overflow_count += 1
            self._overflow = GatewayStartupOverflow(max_pending=self._max_pending)
            raise self._overflow
        self._entries.append(entry)

    def popleft(self) -> T:
        return self._entries.popleft()

    def raise_if_overflowed(self) -> None:
        if self._overflow is not None:
            raise self._overflow

    @property
    def capacity(self) -> int:
        return self._max_pending

    @property
    def depth(self) -> int:
        return len(self._entries)

    @property
    def overflow_count(self) -> int:
        return self._overflow_count

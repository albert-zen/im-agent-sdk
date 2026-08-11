from __future__ import annotations

from asyncio import CancelledError
from collections import deque
from typing import Generic, TypeVar
from unicodedata import category

from .diagnostics import (
    _CLEANUP_DETAIL_MAX_CHARS,
    _CLEANUP_OWNER_MAX_CHARS,
    _bounded_cleanup_error_summary,
    _bounded_cleanup_text,
)

T = TypeVar("T")

class GatewayLifecycleFailure(RuntimeError):
    """Bounded public projection of one lifecycle owner failure."""

    def __init__(self, owner: str, error: BaseException) -> None:
        self.owner = _bounded_cleanup_text(owner, _CLEANUP_OWNER_MAX_CHARS)
        self.original_error = error
        self.original_type = type(error).__name__
        summary = _bounded_cleanup_error_summary(owner, error)
        super().__init__(f"Gateway lifecycle failure: {summary}")
        self.__cause__ = error
        self.__suppress_context__ = True


def _public_lifecycle_error(
    error: BaseException,
    owner: str,
) -> BaseException:
    """Project lifecycle failures before they cross a public error boundary."""

    if isinstance(error, GatewayLifecycleFailure):
        return error
    if isinstance(error, (GatewayStartupOverflow, GatewayNotRunning)):
        return error
    if isinstance(error, (CancelledError, KeyboardInterrupt, SystemExit)):
        return error
    try:
        detail = str(error)
    except BaseException:
        detail = "<unprintable>"
    if len(detail) <= _CLEANUP_DETAIL_MAX_CHARS and not any(
        category(character) in {"Cc", "Cf"} for character in detail
    ):
        return error
    return GatewayLifecycleFailure(owner, error)


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

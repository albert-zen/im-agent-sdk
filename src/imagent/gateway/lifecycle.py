from __future__ import annotations

from collections import deque
from typing import Generic, TypeVar
from unicodedata import category

T = TypeVar("T")

_CLEANUP_OWNER_MAX_CHARS = 96
_CLEANUP_TYPE_MAX_CHARS = 64
_CLEANUP_DETAIL_MAX_CHARS = 192
_CLEANUP_SUMMARY_MAX_CHARS = 384


def _bounded_cleanup_error_summary(owner: str, error: BaseException) -> str:
    """Return content-safe finite evidence without expanding a traceback."""

    owner_summary = _bounded_cleanup_text(owner, _CLEANUP_OWNER_MAX_CHARS)
    error_type = _bounded_cleanup_text(type(error).__name__, _CLEANUP_TYPE_MAX_CHARS)
    try:
        detail = str(error)
    except BaseException:
        detail = "<unprintable>"
    detail_summary = _bounded_cleanup_text(detail, _CLEANUP_DETAIL_MAX_CHARS)
    return f"{owner_summary}: {error_type}: {detail_summary}"[:_CLEANUP_SUMMARY_MAX_CHARS]


def _bounded_cleanup_text(value: str, max_chars: int) -> str:
    sanitized = "".join(
        "?" if category(character) in {"Cc", "Cf"} else character for character in value
    )
    normalized = " ".join(sanitized.split())
    if not normalized:
        normalized = "<empty>"
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3]}..."


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

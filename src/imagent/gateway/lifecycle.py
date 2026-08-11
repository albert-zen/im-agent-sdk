from __future__ import annotations

from asyncio import CancelledError
from collections import deque
from typing import Generic, TypeVar

from .diagnostics import (
    _CLEANUP_OWNER_MAX_CHARS,
    _CLEANUP_SUMMARY_MAX_CHARS,
    _CLEANUP_TYPE_MAX_CHARS,
    _bounded_cleanup_error_summary,
    _bounded_cleanup_text,
)

T = TypeVar("T")


class GatewayLifecycleFailure(RuntimeError):
    """Bounded public projection of one lifecycle owner failure."""

    def __init__(self, owner: str, error: BaseException) -> None:
        self._initialize_bounded(
            owner,
            type(error).__name__,
            _bounded_cleanup_error_summary(owner, error),
        )

    def _initialize_bounded(
        self,
        owner: str,
        original_type: str,
        summary: str,
    ) -> None:
        self.owner = _bounded_cleanup_text(owner, _CLEANUP_OWNER_MAX_CHARS)
        self.original_type = _bounded_cleanup_text(
            original_type,
            _CLEANUP_TYPE_MAX_CHARS,
        )
        self.summary = _bounded_cleanup_text(summary, _CLEANUP_SUMMARY_MAX_CHARS)
        super().__init__(f"Gateway lifecycle failure: {self.summary}")
        self.__cause__ = RuntimeError(f"Gateway lifecycle source: {self.summary}")
        self.__context__ = None
        self.__suppress_context__ = True

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        notes = tuple(
            _bounded_cleanup_text(str(note), _CLEANUP_SUMMARY_MAX_CHARS)
            for note in getattr(self, "__notes__", ())
        )
        return (
            _restore_gateway_lifecycle_failure,
            (self.owner, self.original_type, self.summary, notes),
        )


def _restore_gateway_lifecycle_failure(
    owner: str,
    original_type: str,
    summary: str,
    notes: tuple[str, ...],
) -> GatewayLifecycleFailure:
    error = GatewayLifecycleFailure.__new__(GatewayLifecycleFailure)
    error._initialize_bounded(owner, original_type, summary)
    for note in notes:
        error.add_note(_bounded_cleanup_text(note, _CLEANUP_SUMMARY_MAX_CHARS))
    return error


def _detach_public_lifecycle_context(error: BaseException) -> BaseException:
    """Remove interpreter-attached raw context from one bounded public error."""

    if isinstance(error, GatewayLifecycleFailure):
        error.__context__ = None
    return error


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

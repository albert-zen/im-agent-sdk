from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Generic, TypeVar, cast

from ..interaction.messages import Metadata
from ..interaction.operations import ContractViolation, require_identifier
from .capabilities import ApplicationCapabilities, EventSequenceScope, SupportLevel

if TYPE_CHECKING:
    from ..contracts.model import (
        ProjectRef,
        ThreadRef,
    )
    from .requests import InteractiveRequest, RequestResolution

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class AgentEventType(StrEnum):
    MESSAGE_CREATED = "message.created"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    THREAD_CREATED = "thread.created"
    THREAD_UPDATED = "thread.updated"
    THREAD_DELETED = "thread.deleted"
    TURN_STARTED = "turn.started"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_INTERRUPTED = "turn.interrupted"
    STATUS_CHANGED = "status.changed"
    REQUEST_OPENED = "request.opened"
    REQUEST_RESOLVED = "request.resolved"
    BINDING_CHANGED = "binding.changed"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_id: str
    application_instance_id: str
    type: AgentEventType
    data: Metadata
    created_at: datetime
    project_ref: ProjectRef | None = None
    thread_ref: ThreadRef | None = None
    turn_id: str | None = None
    sequence: int | None = None
    sequence_epoch: str | None = None
    cursor: str | None = None
    request: InteractiveRequest | None = None
    request_resolution: RequestResolution | None = None


def validate_agent_event(
    event: AgentEvent,
    capabilities: ApplicationCapabilities,
) -> None:
    """Validate event facts without claiming request/resource ownership."""

    # Event validation delegates request facts to the request leaf and keeps
    # its event ownership/semantics intact.
    from ..contracts._validation import validate_thread_ref
    from .requests import (
        validate_interactive_request,
        validate_request_resolution,
    )

    require_identifier(event.event_id, "event_id")
    require_identifier(event.application_instance_id, "application_instance_id")
    if event.thread_ref is not None:
        validate_thread_ref(event.thread_ref)
        if event.thread_ref.application_instance_id != event.application_instance_id:
            raise ContractViolation("event thread belongs to a different application")
    if event.sequence is None:
        if event.sequence_epoch is not None:
            raise ContractViolation("sequence_epoch requires sequence")
    else:
        if event.sequence < 0:
            raise ContractViolation("event sequence cannot be negative")
        if capabilities.runtime.event_sequence_scope is EventSequenceScope.NONE:
            raise ContractViolation("event sequence has no declared scope")
        require_identifier(event.sequence_epoch or "", "sequence_epoch")
    if (
        event.cursor is not None
        and capabilities.runtime.replay_from_cursor is SupportLevel.UNSUPPORTED
    ):
        raise ContractViolation("event cursor requires replay-from-cursor support")
    if event.type is AgentEventType.REQUEST_OPENED:
        if event.request is None or event.request_resolution is not None:
            raise ContractViolation("request.opened requires one typed request")
        validate_interactive_request(event.request)
        if capabilities.runtime.interactive_requests is SupportLevel.UNSUPPORTED:
            raise ContractViolation("request event requires interactive request support")
        if event.thread_ref != event.request.thread_ref:
            raise ContractViolation("request event belongs to a different Thread")
        if event.turn_id != event.request.turn_id:
            raise ContractViolation("request event belongs to a different Turn")
        if (
            event.request.request_ref.application_ref.application_instance_id
            != event.application_instance_id
        ):
            raise ContractViolation("request event belongs to a different application")
    elif event.type is AgentEventType.REQUEST_RESOLVED:
        if event.request is not None or event.request_resolution is None:
            raise ContractViolation("request.resolved requires one typed resolution")
        validate_request_resolution(event.request_resolution)
        if (
            event.request_resolution.request_ref.application_ref.application_instance_id
            != event.application_instance_id
        ):
            raise ContractViolation("request resolution belongs to a different application")
    elif event.request is not None or event.request_resolution is not None:
        raise ContractViolation("typed request fields require a request event")


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
        max_pending: int,
        close: Callable[[K, FanoutSubscription[K, V]], None],
    ) -> None:
        self._key = key
        self._queue = queue
        self._max_pending = max_pending
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
        if self._queue.qsize() >= self._max_pending:
            self._fail(EventStreamOverflow(max_pending=self._max_pending))
            return True
        self._queue.put_nowait(event)
        return False

    def _fail(self, error: EventStreamGap, *, discard_pending: bool = True) -> None:
        if self._closed or self._terminal_error is not None:
            return
        if discard_pending:
            while not self._queue.empty():
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
            asyncio.Queue[V | _TerminalSignal](maxsize=self._max_pending + 1),
            self._max_pending,
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

    def fail(
        self,
        key: K,
        error_factory: Callable[[], EventStreamGap],
        *,
        discard_pending: bool = True,
    ) -> None:
        """Terminate one key's subscribers with an explicit recoverable gap."""

        for subscription in tuple(self._subscribers.get(key, ())):
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


# Bind the remaining historical resource/request references only after the
# event owner is fully defined, so `get_type_hints(AgentEvent)` stays stable
# without an import-time cycle through the contracts facade.
from ..contracts.model import ProjectRef, ThreadRef  # noqa: E402
from .requests import InteractiveRequest, RequestResolution  # noqa: E402

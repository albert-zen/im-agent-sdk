from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ConnectionDiagnosticState",
    "DiagnosticFailureCode",
    "QueueDiagnosticName",
    "QueueDiagnosticFacts",
    "ConnectionDiagnosticFacts",
]


class ConnectionDiagnosticState(StrEnum):
    """Stable, transport-neutral connection lifecycle states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    RECONNECTING = "reconnecting"


class DiagnosticFailureCode(StrEnum):
    """Bounded failure classifications safe for metrics labels and health views."""

    CONNECT_FAILED = "connect_failed"
    TRANSPORT_FAILED = "transport_failed"
    NOTIFICATION_OVERFLOW = "notification_overflow"
    SERVER_REQUEST_OVERFLOW = "server_request_overflow"
    OTHER = "other"


class QueueDiagnosticName(StrEnum):
    """Fixed queue names keep consumer metric labels bounded."""

    GATEWAY_STARTUP = "gateway_startup"
    NOTIFICATION = "notification"
    SERVER_REQUEST = "server_request"
    CHANNEL_INBOUND = "channel_inbound"


@dataclass(frozen=True, slots=True)
class QueueDiagnosticFacts:
    """Read-only facts for one process-local bounded queue."""

    name: QueueDiagnosticName
    capacity: int
    depth: int
    overflow_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.name, QueueDiagnosticName):
            raise ValueError("diagnostic queue name must use the fixed vocabulary")
        if any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in (self.capacity, self.depth, self.overflow_count)
        ):
            raise TypeError("diagnostic queue counts must be integers")
        if self.capacity < 1:
            raise ValueError("diagnostic queue capacity must be positive")
        if not 0 <= self.depth <= self.capacity:
            raise ValueError("diagnostic queue depth must be within capacity")
        if self.overflow_count < 0:
            raise ValueError("diagnostic queue overflow count must not be negative")


@dataclass(frozen=True, slots=True)
class ConnectionDiagnosticFacts:
    """Redacted connection and worker facts owned by one adapter."""

    state: ConnectionDiagnosticState
    connection_epoch: int
    reconnect_count: int
    worker_running: bool
    worker_degraded: bool
    last_failure_code: DiagnosticFailureCode | None = None
    queues: tuple[QueueDiagnosticFacts, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.state, ConnectionDiagnosticState):
            raise ValueError("diagnostic connection state must use the fixed vocabulary")
        if self.last_failure_code is not None and not isinstance(
            self.last_failure_code, DiagnosticFailureCode
        ):
            raise ValueError("diagnostic failure code must use the fixed vocabulary")
        if (
            not isinstance(self.connection_epoch, int)
            or isinstance(self.connection_epoch, bool)
            or self.connection_epoch < 0
            or not isinstance(self.reconnect_count, int)
            or isinstance(self.reconnect_count, bool)
            or self.reconnect_count < 0
            or not isinstance(self.worker_running, bool)
            or not isinstance(self.worker_degraded, bool)
        ):
            raise TypeError("invalid diagnostic connection fact types")
        names = tuple(queue.name for queue in self.queues)
        if len(names) != len(set(names)):
            raise ValueError("diagnostic connection queue names must be unique")
        allowed = {
            QueueDiagnosticName.NOTIFICATION,
            QueueDiagnosticName.SERVER_REQUEST,
            QueueDiagnosticName.CHANNEL_INBOUND,
        }
        if not set(names).issubset(allowed):
            raise ValueError("diagnostic connection queue name is not connection-scoped")

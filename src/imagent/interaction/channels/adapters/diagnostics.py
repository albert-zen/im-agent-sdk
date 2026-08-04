from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any

from ...diagnostics import (
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
    DiagnosticFailureCode,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
)
from ..diagnostics import ChannelDiagnosticFacts

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NativeQueueDiagnosticSnapshot:
    name: str
    capacity: int
    depth: int
    overflow_count: int = 0

    def as_contract(self) -> QueueDiagnosticFacts:
        return QueueDiagnosticFacts(
            name=QueueDiagnosticName(str(self.name)),
            capacity=self.capacity,
            depth=self.depth,
            overflow_count=self.overflow_count,
        )


@dataclass(frozen=True, slots=True)
class NativeConnectionDiagnosticSnapshot:
    state: str
    connection_epoch: int
    reconnect_count: int
    worker_running: bool
    worker_degraded: bool
    last_failure_code: str | None = None
    queues: tuple[NativeQueueDiagnosticSnapshot, ...] = ()

    def as_contract(self) -> ConnectionDiagnosticFacts:
        return ConnectionDiagnosticFacts(
            state=ConnectionDiagnosticState(str(self.state)),
            connection_epoch=self.connection_epoch,
            reconnect_count=self.reconnect_count,
            worker_running=self.worker_running,
            worker_degraded=self.worker_degraded,
            last_failure_code=(
                DiagnosticFailureCode(str(self.last_failure_code))
                if self.last_failure_code is not None
                else None
            ),
            queues=tuple(queue.as_contract() for queue in self.queues),
        )


@dataclass(frozen=True, slots=True)
class NativeChannelDiagnosticSnapshot:
    channel_instance_id: str
    kind: str
    connection: NativeConnectionDiagnosticSnapshot | None = None

    def as_contract(self) -> ChannelDiagnosticFacts:
        return ChannelDiagnosticFacts(
            channel_instance_id=self.channel_instance_id,
            kind=self.kind,
            connection=self.connection.as_contract() if self.connection is not None else None,
        )


class NativeChannelDiagnosticState:
    """Keep bounded process-local facts without native resource identities."""

    def __init__(self) -> None:
        self._state = "disconnected"
        self._connection_epoch = 0
        self._reconnect_count = 0
        self._worker_running = False
        self._worker_degraded = False
        self._last_failure_code: str | None = None
        self._lock = Lock()

    def update(self, **state: Any) -> None:
        status = state.get("status")
        connected = state.get("connected")
        next_state = self._normalized_state(status=status, connected=connected)
        if next_state is None:
            return
        with self._lock:
            if next_state == "ready" and self._state != "ready":
                self._connection_epoch += 1
            if next_state == "reconnecting" and self._state != "reconnecting":
                self._reconnect_count += 1
            self._state = next_state
            self._worker_running = status != "stopped"
            self._worker_degraded = status in {
                "auth_required",
                "degraded",
                "error",
                "reconnecting",
            }
            self._last_failure_code = "transport_failed" if self._worker_degraded else None

    def snapshot(
        self,
        *,
        queues: tuple[NativeQueueDiagnosticSnapshot, ...] = (),
    ) -> NativeConnectionDiagnosticSnapshot:
        with self._lock:
            return NativeConnectionDiagnosticSnapshot(
                state=self._state,
                connection_epoch=self._connection_epoch,
                reconnect_count=self._reconnect_count,
                worker_running=self._worker_running,
                worker_degraded=self._worker_degraded,
                last_failure_code=self._last_failure_code,
                queues=queues,
            )

    @staticmethod
    def _normalized_state(*, status: object, connected: object) -> str | None:
        if status == "connecting":
            return "connecting"
        if status in {"connected", "degraded"} and connected is True:
            return "ready"
        if status == "reconnecting":
            return "reconnecting"
        if status in {"auth_required", "error", "stopped"}:
            return "disconnected"
        return None


def emit_event(**event: Any) -> None:
    """Keep transferred diagnostics observable without defining telemetry API."""

    logger.debug("native channel event: %s", event)


def mark_channel_health(channel_id: str, **state: Any) -> None:
    """Log native health changes until Issue #13 defines telemetry export."""

    logger.debug("native channel health %s: %s", channel_id, state)

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NativeChannelConnectionSnapshot:
    state: str
    connection_epoch: int
    reconnect_count: int
    worker_running: bool
    worker_degraded: bool
    last_failure_code: str | None = None


class NativeChannelDiagnosticState:
    """Small redacted lifecycle recorder owned by one native Channel instance."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state = "disconnected"
        self._connection_epoch = 0
        self._reconnect_count = 0
        self._worker_running = False
        self._worker_degraded = False
        self._last_failure_code: str | None = None

    def update(self, **state: Any) -> None:
        if "status" not in state and "connected" not in state:
            return
        status = str(state.get("status") or "").casefold()
        connected = bool(state.get("connected"))
        with self._lock:
            previous = self._state
            if connected or status == "connected":
                current = "ready"
            elif status == "connecting":
                current = "connecting"
            elif status == "reconnecting":
                current = "reconnecting"
            else:
                current = "disconnected"
            if current == "ready" and previous != current:
                self._connection_epoch += 1
            if current == "reconnecting":
                self._reconnect_count += 1
            self._state = current
            self._worker_running = status not in {"", "auth_required", "error", "stopped"}
            self._worker_degraded = status in {
                "auth_required",
                "degraded",
                "error",
                "reconnecting",
            }
            if state.get("error_type") or state.get("error_code"):
                self._last_failure_code = "transport_failed"
            elif current == "ready":
                self._last_failure_code = None

    def snapshot(self) -> NativeChannelConnectionSnapshot:
        with self._lock:
            return NativeChannelConnectionSnapshot(
                state=self._state,
                connection_epoch=self._connection_epoch,
                reconnect_count=self._reconnect_count,
                worker_running=self._worker_running,
                worker_degraded=self._worker_degraded,
                last_failure_code=self._last_failure_code,
            )


def emit_event(**event: Any) -> None:
    """Keep transferred diagnostics observable without defining telemetry API."""

    logger.debug("native channel event: %s", event)


def mark_channel_health(channel_id: str, **state: Any) -> None:
    """Log native health changes until Issue #13 defines telemetry export."""

    logger.debug("native channel health %s: %s", channel_id, state)

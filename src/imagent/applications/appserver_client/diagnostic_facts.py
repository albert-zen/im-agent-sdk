from __future__ import annotations

from dataclasses import dataclass

from ...diagnostics import (
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
    DiagnosticFailureCode,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
)


@dataclass(slots=True)
class AppServerDiagnosticState:
    """Process-local diagnostic counters kept outside the transport client."""

    notification_capacity: int
    server_request_capacity: int
    notification_overflow_count: int = 0
    server_request_overflow_count: int = 0
    last_failure_code: DiagnosticFailureCode | None = None

    def record_connect_failure(self) -> None:
        self.last_failure_code = DiagnosticFailureCode.CONNECT_FAILED

    def record_transport_failure(self) -> None:
        self.last_failure_code = DiagnosticFailureCode.TRANSPORT_FAILED

    def record_overflow(self, queue_name: QueueDiagnosticName) -> None:
        if queue_name is QueueDiagnosticName.SERVER_REQUEST:
            self.server_request_overflow_count += 1
            self.last_failure_code = DiagnosticFailureCode.SERVER_REQUEST_OVERFLOW
        else:
            self.notification_overflow_count += 1
            self.last_failure_code = DiagnosticFailureCode.NOTIFICATION_OVERFLOW

    @property
    def overflow_counts(self) -> tuple[int, int]:
        return self.notification_overflow_count, self.server_request_overflow_count

    def snapshot(
        self,
        *,
        transport_open: bool,
        initialized: bool,
        reconnecting: bool,
        connection_epoch: int,
        closing: bool,
        worker_running: bool,
        notification_depth: int,
        server_request_depth: int,
    ) -> ConnectionDiagnosticFacts:
        if transport_open and initialized:
            state = ConnectionDiagnosticState.READY
        elif reconnecting:
            state = ConnectionDiagnosticState.RECONNECTING
        elif transport_open:
            state = ConnectionDiagnosticState.CONNECTING
        else:
            state = ConnectionDiagnosticState.DISCONNECTED
        worker_degraded = reconnecting or (transport_open and not worker_running)
        worker_degraded = worker_degraded or (
            not transport_open and not closing and self.last_failure_code is not None
        )
        return ConnectionDiagnosticFacts(
            state=state,
            connection_epoch=connection_epoch,
            reconnect_count=max(0, connection_epoch - 1),
            worker_running=worker_running,
            worker_degraded=worker_degraded,
            last_failure_code=self.last_failure_code,
            queues=(
                QueueDiagnosticFacts(
                    name=QueueDiagnosticName.NOTIFICATION,
                    capacity=self.notification_capacity,
                    depth=notification_depth,
                    overflow_count=self.notification_overflow_count,
                ),
                QueueDiagnosticFacts(
                    name=QueueDiagnosticName.SERVER_REQUEST,
                    capacity=self.server_request_capacity,
                    depth=server_request_depth,
                    overflow_count=self.server_request_overflow_count,
                ),
            ),
        )

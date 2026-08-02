from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


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


@dataclass(frozen=True, slots=True)
class ApplicationDiagnosticFacts:
    """Optional Application-adapter facts without native resource identities."""

    application_instance_id: str
    kind: str
    connection: ConnectionDiagnosticFacts | None = None

    def __post_init__(self) -> None:
        if self.connection is not None and any(
            queue.name is QueueDiagnosticName.CHANNEL_INBOUND for queue in self.connection.queues
        ):
            raise ValueError("Channel inbound queue is not Application-scoped")


@dataclass(frozen=True, slots=True)
class ChannelDiagnosticFacts:
    """Optional Channel-adapter facts without native Conversation identities."""

    channel_instance_id: str
    kind: str
    connection: ConnectionDiagnosticFacts | None = None

    def __post_init__(self) -> None:
        if self.connection is not None and any(
            queue.name is not QueueDiagnosticName.CHANNEL_INBOUND
            for queue in self.connection.queues
        ):
            raise ValueError("diagnostic queue is not Channel-scoped")


@dataclass(frozen=True, slots=True)
class ProjectionDiagnosticFacts:
    """Identity-free aggregate of process-local projection worker health."""

    worker_count: int = 0
    running_count: int = 0
    retrying_count: int = 0
    stopped_count: int = 0
    degraded_count: int = 0
    restart_count: int = 0
    delivery_failure_count: int = 0
    event_overflow_count: int = 0
    request_recovery_degraded_count: int = 0
    recovery_gap_count: int = 0
    recovery_gap_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GatewayDiagnosticFacts:
    """Bounded process-local Gateway admission facts."""

    accepting_inbound: bool
    starting: bool
    startup_queue: QueueDiagnosticFacts


@dataclass(frozen=True, slots=True)
class DiagnosticsSnapshot:
    """Stable, read-only, non-authoritative SDK diagnostics snapshot."""

    applications: tuple[ApplicationDiagnosticFacts, ...]
    projections: ProjectionDiagnosticFacts
    gateway: GatewayDiagnosticFacts
    generated_at: datetime
    schema_version: int = 2
    authoritative: bool = False
    channels: tuple[ChannelDiagnosticFacts, ...] = ()


class DiagnosticsProvider(Protocol):
    """Optional structural seam; it is not a required Core adapter capability."""

    def diagnostic_facts(self) -> ApplicationDiagnosticFacts: ...


class ChannelDiagnosticsProvider(Protocol):
    """Optional structural Channel seam; not a required lifecycle capability."""

    def diagnostic_facts(self) -> ChannelDiagnosticFacts: ...


class _ProjectionHealth(Protocol):
    @property
    def state(self) -> object: ...

    @property
    def restart_count(self) -> int: ...

    @property
    def delivery_failure_count(self) -> int: ...

    @property
    def event_overflow_count(self) -> int: ...

    @property
    def last_subscription_error(self) -> str | None: ...

    @property
    def last_recovery_error(self) -> str | None: ...

    @property
    def last_delivery_error(self) -> str | None: ...

    @property
    def last_gap(self) -> str | None: ...

    @property
    def last_event_gap(self) -> str | None: ...

    @property
    def interactive_request_recovery_degraded(self) -> bool: ...


class _ApplicationRef(Protocol):
    @property
    def application_instance_id(self) -> str: ...


class _ApplicationSummary(Protocol):
    @property
    def ref(self) -> _ApplicationRef: ...

    @property
    def kind(self) -> str: ...


class _DiagnosticApplication(Protocol):
    @property
    def summary(self) -> _ApplicationSummary: ...


_KNOWN_RECOVERY_GAPS = frozenset(
    {
        "application_event_connection_reset",
        "application_event_fanout_overflow",
        "checkpoint_missing",
        "checkpoint_out_of_window",
        "projection_window_truncated",
    }
)


def summarize_projection_health(
    records: Iterable[_ProjectionHealth],
) -> ProjectionDiagnosticFacts:
    """Aggregate worker facts without copying Thread, route, or error identities."""

    materialized = tuple(records)
    state_counts = {"running": 0, "retrying": 0, "stopped": 0}
    degraded_count = 0
    gap_codes: set[str] = set()
    for record in materialized:
        state = str(record.state)
        if state in state_counts:
            state_counts[state] += 1
        raw_gaps = tuple(gap for gap in (record.last_gap, record.last_event_gap) if gap is not None)
        degraded = (
            state == "retrying"
            or record.last_subscription_error is not None
            or record.last_recovery_error is not None
            or record.last_delivery_error is not None
            or record.interactive_request_recovery_degraded
            or bool(raw_gaps)
        )
        degraded_count += int(degraded)
        gap_codes.update(_bounded_gap_code(gap) for gap in raw_gaps)
    return ProjectionDiagnosticFacts(
        worker_count=len(materialized),
        running_count=state_counts["running"],
        retrying_count=state_counts["retrying"],
        stopped_count=state_counts["stopped"],
        degraded_count=degraded_count,
        restart_count=sum(record.restart_count for record in materialized),
        delivery_failure_count=sum(record.delivery_failure_count for record in materialized),
        event_overflow_count=sum(record.event_overflow_count for record in materialized),
        request_recovery_degraded_count=sum(
            int(record.interactive_request_recovery_degraded) for record in materialized
        ),
        recovery_gap_count=sum(
            record.last_gap is not None or record.last_event_gap is not None
            for record in materialized
        ),
        recovery_gap_codes=tuple(sorted(gap_codes)),
    )


def collect_application_diagnostics(
    applications: Iterable[_DiagnosticApplication],
) -> tuple[ApplicationDiagnosticFacts, ...]:
    """Read optional providers while preserving configured registry identity."""

    collected: list[ApplicationDiagnosticFacts] = []
    for application in applications:
        provider = getattr(application, "diagnostic_facts", None)
        try:
            facts = provider() if callable(provider) else None
        except Exception:
            facts = None
        summary = application.summary
        if (
            isinstance(facts, ApplicationDiagnosticFacts)
            and facts.application_instance_id == summary.ref.application_instance_id
            and facts.kind == summary.kind
        ):
            collected.append(facts)
        else:
            collected.append(
                ApplicationDiagnosticFacts(
                    application_instance_id=summary.ref.application_instance_id,
                    kind=summary.kind,
                )
            )
    return tuple(sorted(collected, key=lambda facts: facts.application_instance_id))


def collect_channel_diagnostics(
    channels: Iterable[object],
) -> tuple[ChannelDiagnosticFacts, ...]:
    """Read optional providers while preserving configured Channel identity."""

    collected: list[ChannelDiagnosticFacts] = []
    for channel in channels:
        channel_instance_id = str(getattr(channel, "channel_instance_id", ""))
        kind = str(getattr(channel, "kind", "unknown"))
        try:
            provider = getattr(channel, "diagnostic_facts", None)
            facts = provider() if callable(provider) else None
        except Exception:
            facts = None
        try:
            provider_instance_id = getattr(facts, "channel_instance_id", None)
            provider_kind = getattr(facts, "kind", None)
        except Exception:
            provider_instance_id = None
            provider_kind = None
        if (
            not isinstance(provider_instance_id, str)
            or provider_instance_id != channel_instance_id
            or not isinstance(provider_kind, str)
            or provider_kind != kind
        ):
            collected.append(ChannelDiagnosticFacts(channel_instance_id, kind))
            continue
        try:
            connection = _coerce_channel_connection(getattr(facts, "connection", None))
        except Exception:
            connection = None
        collected.append(ChannelDiagnosticFacts(channel_instance_id, kind, connection))
    return tuple(sorted(collected, key=lambda facts: facts.channel_instance_id))


def _coerce_channel_connection(value: object) -> ConnectionDiagnosticFacts | None:
    if value is None:
        return None
    state = ConnectionDiagnosticState(str(getattr(value, "state")))
    failure = getattr(value, "last_failure_code", None)
    connection_epoch = getattr(value, "connection_epoch")
    reconnect_count = getattr(value, "reconnect_count")
    worker_running = getattr(value, "worker_running")
    worker_degraded = getattr(value, "worker_degraded")
    if (
        not isinstance(connection_epoch, int)
        or isinstance(connection_epoch, bool)
        or connection_epoch < 0
        or not isinstance(reconnect_count, int)
        or isinstance(reconnect_count, bool)
        or reconnect_count < 0
        or not isinstance(worker_running, bool)
        or not isinstance(worker_degraded, bool)
    ):
        raise TypeError("invalid Channel diagnostic fact types")
    raw_queues = getattr(value, "queues", ())
    if not isinstance(raw_queues, tuple) or len(raw_queues) > 1:
        raise TypeError("invalid Channel diagnostic queue collection")
    queues = tuple(_coerce_channel_queue(queue) for queue in raw_queues)
    if any(queue.name is not QueueDiagnosticName.CHANNEL_INBOUND for queue in queues):
        raise ValueError("invalid Channel diagnostic queue scope")
    return ConnectionDiagnosticFacts(
        state=state,
        connection_epoch=connection_epoch,
        reconnect_count=reconnect_count,
        worker_running=worker_running,
        worker_degraded=worker_degraded,
        last_failure_code=(DiagnosticFailureCode(str(failure)) if failure is not None else None),
        queues=queues,
    )


def _coerce_channel_queue(value: object) -> QueueDiagnosticFacts:
    capacity = getattr(value, "capacity")
    depth = getattr(value, "depth")
    overflow_count = getattr(value, "overflow_count")
    if any(
        not isinstance(item, int) or isinstance(item, bool)
        for item in (capacity, depth, overflow_count)
    ):
        raise TypeError("invalid Channel diagnostic queue fact types")
    return QueueDiagnosticFacts(
        name=QueueDiagnosticName(str(getattr(value, "name"))),
        capacity=capacity,
        depth=depth,
        overflow_count=overflow_count,
    )


def new_diagnostics_snapshot(
    *,
    applications: tuple[ApplicationDiagnosticFacts, ...],
    channels: tuple[ChannelDiagnosticFacts, ...],
    projections: ProjectionDiagnosticFacts,
    gateway: GatewayDiagnosticFacts,
) -> DiagnosticsSnapshot:
    return DiagnosticsSnapshot(
        applications=applications,
        channels=channels,
        projections=projections,
        gateway=gateway,
        generated_at=datetime.now(UTC),
    )


def _bounded_gap_code(value: str) -> str:
    for code in _KNOWN_RECOVERY_GAPS:
        if value == code or value.endswith(f":{code}"):
            return code
    return "other"

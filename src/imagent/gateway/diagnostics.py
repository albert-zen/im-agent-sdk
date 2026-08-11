from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, TypeVar
from unicodedata import category

from ..applications.diagnostics import ApplicationDiagnosticFacts
from ..interaction.channels.diagnostics import ChannelDiagnosticFacts
from ..interaction.diagnostics import (
    _DIAGNOSTIC_COUNTER_MAX,
    _DIAGNOSTIC_ID_MAX_CHARS,
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
    DiagnosticFailureCode,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
)

__all__ = [
    "InboundContentTransformFailureCode",
    "InboundContentTransformerDiagnosticFacts",
    "InboundFailurePresentationFailureCode",
    "InboundFailurePresenterDiagnosticFacts",
    "OutboundPresentationFailureCode",
    "OutboundPresentationDiagnosticFacts",
    "DeliveryOutcomeObserverFailureCode",
    "DeliveryOutcomeObserverDiagnosticFacts",
    "ProjectionDiagnosticFacts",
    "GatewayDiagnosticFacts",
    "DiagnosticsSnapshot",
    "summarize_projection_health",
    "collect_application_diagnostics",
    "collect_channel_diagnostics",
    "new_diagnostics_snapshot",
]

_CLEANUP_OWNER_MAX_CHARS = 96
_CLEANUP_TYPE_MAX_CHARS = 64
_CLEANUP_DETAIL_MAX_CHARS = 192
_CLEANUP_SUMMARY_MAX_CHARS = 384
_PROJECTION_DIAGNOSTIC_MAX_RECORDS = 4_096
_DiagnosticItem = TypeVar("_DiagnosticItem")


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


def _bounded_lifecycle_error_summary(owner: str, error: BaseException) -> str:
    """Return only a bounded owner/type lifecycle classification."""

    owner_summary = _bounded_cleanup_text(owner, _CLEANUP_OWNER_MAX_CHARS)
    error_type = _bounded_cleanup_text(type(error).__name__, _CLEANUP_TYPE_MAX_CHARS)
    return f"{owner_summary}: {error_type}"[:_CLEANUP_SUMMARY_MAX_CHARS]


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


class InboundContentTransformFailureCode(StrEnum):
    """Fixed I1 failure categories without consumer-controlled detail."""

    INVALID_OUTPUT = "invalid_output"
    TRANSFORMER_FAILED = "transformer_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


class InboundFailurePresentationFailureCode(StrEnum):
    """Fixed I2 render failure categories without consumer-controlled detail."""

    INVALID_OUTPUT = "invalid_output"
    PRESENTER_FAILED = "presenter_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


class OutboundPresentationFailureCode(StrEnum):
    """Fixed O1 failure categories without message or policy-controlled detail."""

    INVALID_OUTPUT = "invalid_output"
    POLICY_FAILED = "policy_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


class DeliveryOutcomeObserverFailureCode(StrEnum):
    """Fixed O2 failure categories without delivery or observer-controlled detail."""

    INVALID_FACTS = "invalid_facts"
    OBSERVER_FAILED = "observer_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


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

    def __post_init__(self) -> None:
        counts = (
            self.worker_count,
            self.running_count,
            self.retrying_count,
            self.stopped_count,
            self.degraded_count,
            self.restart_count,
            self.delivery_failure_count,
            self.event_overflow_count,
            self.request_recovery_degraded_count,
            self.recovery_gap_count,
        )
        if any(
            type(count) is not int or not 0 <= count <= _DIAGNOSTIC_COUNTER_MAX for count in counts
        ):
            raise ValueError("projection diagnostic counts exceed the fixed bound")
        if self.worker_count > _PROJECTION_DIAGNOSTIC_MAX_RECORDS:
            raise ValueError("projection diagnostic worker count exceeds the fixed bound")
        if self.running_count + self.retrying_count + self.stopped_count > self.worker_count:
            raise ValueError("projection diagnostic state counts exceed worker count")
        if self.degraded_count > self.worker_count:
            raise ValueError("projection degraded count exceeds worker count")
        if self.request_recovery_degraded_count > self.worker_count:
            raise ValueError("projection request recovery count exceeds worker count")
        if self.recovery_gap_count > self.worker_count:
            raise ValueError("projection recovery gap count exceeds worker count")
        if (
            not isinstance(self.recovery_gap_codes, tuple)
            or len(self.recovery_gap_codes) > len(_KNOWN_RECOVERY_GAPS) + 1
            or any(
                code not in _KNOWN_RECOVERY_GAPS and code != "other"
                for code in self.recovery_gap_codes
            )
        ):
            raise ValueError("projection recovery gaps must use the fixed vocabulary")


@dataclass(frozen=True, slots=True)
class InboundContentTransformerDiagnosticFacts:
    """Redacted process-lifetime counters for configured I1 execution."""

    invocation_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    cancellation_count: int = 0
    cancellation_overrun_count: int = 0
    capacity_rejection_count: int = 0
    last_failure_code: InboundContentTransformFailureCode | None = None

    def __post_init__(self) -> None:
        counts = (
            self.invocation_count,
            self.success_count,
            self.failure_count,
            self.timeout_count,
            self.cancellation_count,
            self.cancellation_overrun_count,
            self.capacity_rejection_count,
        )
        if any(
            type(count) is not int or not 0 <= count <= _DIAGNOSTIC_COUNTER_MAX for count in counts
        ):
            raise TypeError("inbound transformer diagnostic counts must be non-negative integers")
        if self.success_count + self.failure_count > self.invocation_count:
            raise ValueError("inbound transformer outcomes cannot exceed invocations")
        if self.timeout_count + self.cancellation_count > self.failure_count:
            raise ValueError("inbound transformer failure counts are inconsistent")
        if self.cancellation_overrun_count > self.timeout_count:
            raise ValueError("inbound transformer cancellation overruns exceed timeouts")
        if self.capacity_rejection_count > self.failure_count:
            raise ValueError("inbound transformer capacity rejections exceed failures")
        if self.last_failure_code is not None and not isinstance(
            self.last_failure_code,
            InboundContentTransformFailureCode,
        ):
            raise ValueError("inbound transformer failure code must use the fixed vocabulary")


@dataclass(frozen=True, slots=True)
class InboundFailurePresenterDiagnosticFacts:
    """Redacted process-lifetime counters for configured I2 rendering."""

    invocation_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    cancellation_count: int = 0
    cancellation_overrun_count: int = 0
    capacity_rejection_count: int = 0
    last_failure_code: InboundFailurePresentationFailureCode | None = None

    def __post_init__(self) -> None:
        counts = (
            self.invocation_count,
            self.success_count,
            self.failure_count,
            self.timeout_count,
            self.cancellation_count,
            self.cancellation_overrun_count,
            self.capacity_rejection_count,
        )
        if any(
            type(count) is not int or not 0 <= count <= _DIAGNOSTIC_COUNTER_MAX for count in counts
        ):
            raise TypeError("inbound presenter diagnostic counts must be non-negative integers")
        if self.success_count + self.failure_count > self.invocation_count:
            raise ValueError("inbound presenter outcomes cannot exceed invocations")
        if self.timeout_count + self.cancellation_count > self.failure_count:
            raise ValueError("inbound presenter failure counts are inconsistent")
        if self.cancellation_overrun_count > self.timeout_count + self.cancellation_count:
            raise ValueError("inbound presenter cancellation overruns exceed cancellations")
        if self.capacity_rejection_count > self.failure_count:
            raise ValueError("inbound presenter capacity rejections exceed failures")
        if self.last_failure_code is not None and not isinstance(
            self.last_failure_code,
            InboundFailurePresentationFailureCode,
        ):
            raise ValueError("inbound presenter failure code must use the fixed vocabulary")


@dataclass(frozen=True, slots=True)
class OutboundPresentationDiagnosticFacts:
    """Redacted process-lifetime counters for configured O1 presentation."""

    invocation_count: int = 0
    delivery_count: int = 0
    suppression_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    cancellation_count: int = 0
    cancellation_overrun_count: int = 0
    capacity_rejection_count: int = 0
    last_failure_code: OutboundPresentationFailureCode | None = None

    def __post_init__(self) -> None:
        counts = (
            self.invocation_count,
            self.delivery_count,
            self.suppression_count,
            self.failure_count,
            self.timeout_count,
            self.cancellation_count,
            self.cancellation_overrun_count,
            self.capacity_rejection_count,
        )
        if any(
            type(count) is not int or not 0 <= count <= _DIAGNOSTIC_COUNTER_MAX for count in counts
        ):
            raise TypeError("outbound presentation diagnostic counts must be non-negative integers")
        if (
            self.delivery_count + self.suppression_count + self.failure_count
            > self.invocation_count
        ):
            raise ValueError("outbound presentation outcomes cannot exceed invocations")
        if self.timeout_count + self.cancellation_count > self.failure_count:
            raise ValueError("outbound presentation failure counts are inconsistent")
        if self.cancellation_overrun_count > self.timeout_count + self.cancellation_count:
            raise ValueError("outbound presentation cancellation overruns exceed cancellations")
        if self.capacity_rejection_count > self.failure_count:
            raise ValueError("outbound presentation capacity rejections exceed failures")
        if self.last_failure_code is not None and not isinstance(
            self.last_failure_code,
            OutboundPresentationFailureCode,
        ):
            raise ValueError("outbound presentation failure code must use the fixed vocabulary")


@dataclass(frozen=True, slots=True)
class DeliveryOutcomeObserverDiagnosticFacts:
    """Redacted process-lifetime counters for configured O2 observation."""

    notification_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    cancellation_count: int = 0
    cancellation_overrun_count: int = 0
    capacity_rejection_count: int = 0
    last_failure_code: DeliveryOutcomeObserverFailureCode | None = None

    def __post_init__(self) -> None:
        counts = (
            self.notification_count,
            self.success_count,
            self.failure_count,
            self.timeout_count,
            self.cancellation_count,
            self.cancellation_overrun_count,
            self.capacity_rejection_count,
        )
        if any(
            type(count) is not int or not 0 <= count <= _DIAGNOSTIC_COUNTER_MAX for count in counts
        ):
            raise TypeError("delivery outcome diagnostic counts must be non-negative integers")
        if self.success_count + self.failure_count > self.notification_count:
            raise ValueError("delivery outcome observer results cannot exceed notifications")
        if self.timeout_count + self.cancellation_count > self.failure_count:
            raise ValueError("delivery outcome observer failure counts are inconsistent")
        if self.cancellation_overrun_count > self.timeout_count + self.cancellation_count:
            raise ValueError("delivery outcome cancellation overruns exceed cancellations")
        if self.capacity_rejection_count > self.failure_count:
            raise ValueError("delivery outcome capacity rejections exceed failures")
        if self.last_failure_code is not None and not isinstance(
            self.last_failure_code,
            DeliveryOutcomeObserverFailureCode,
        ):
            raise ValueError("delivery outcome failure code must use the fixed vocabulary")


@dataclass(frozen=True, slots=True)
class GatewayDiagnosticFacts:
    """Bounded process-local Gateway admission facts."""

    accepting_inbound: bool
    starting: bool
    startup_queue: QueueDiagnosticFacts
    inbound_content_transformer: InboundContentTransformerDiagnosticFacts | None = None
    inbound_failure_presenter: InboundFailurePresenterDiagnosticFacts | None = None
    outbound_presentation: OutboundPresentationDiagnosticFacts | None = None
    delivery_outcome_observer: DeliveryOutcomeObserverDiagnosticFacts | None = None

    def __post_init__(self) -> None:
        optional_facts = (
            (self.inbound_content_transformer, InboundContentTransformerDiagnosticFacts),
            (self.inbound_failure_presenter, InboundFailurePresenterDiagnosticFacts),
            (self.outbound_presentation, OutboundPresentationDiagnosticFacts),
            (self.delivery_outcome_observer, DeliveryOutcomeObserverDiagnosticFacts),
        )
        if (
            type(self.accepting_inbound) is not bool
            or type(self.starting) is not bool
            or type(self.startup_queue) is not QueueDiagnosticFacts
            or any(
                value is not None and type(value) is not expected
                for value, expected in optional_facts
            )
        ):
            raise TypeError("Gateway diagnostics must use the exact bounded fact shapes")


@dataclass(frozen=True, slots=True)
class DiagnosticsSnapshot:
    """Stable, read-only, non-authoritative SDK diagnostics snapshot."""

    applications: tuple[ApplicationDiagnosticFacts, ...]
    projections: ProjectionDiagnosticFacts
    gateway: GatewayDiagnosticFacts
    generated_at: datetime
    schema_version: int = 8
    authoritative: bool = False
    channels: tuple[ChannelDiagnosticFacts, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.applications) is not tuple
            or len(self.applications) > _PROJECTION_DIAGNOSTIC_MAX_RECORDS
            or any(type(facts) is not ApplicationDiagnosticFacts for facts in self.applications)
            or type(self.channels) is not tuple
            or len(self.channels) > _PROJECTION_DIAGNOSTIC_MAX_RECORDS
            or any(type(facts) is not ChannelDiagnosticFacts for facts in self.channels)
            or type(self.projections) is not ProjectionDiagnosticFacts
            or type(self.gateway) is not GatewayDiagnosticFacts
            or type(self.generated_at) is not datetime
        ):
            raise ValueError("diagnostics snapshot exceeds the fixed fact bounds")
        if self.schema_version != 8 or self.authoritative is not False:
            raise ValueError("diagnostics snapshot metadata is fixed and non-authoritative")


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
        "application_event_poll_failed",
        "application_event_poll_window_gap",
        "checkpoint_missing",
        "checkpoint_out_of_window",
        "projection_window_truncated",
    }
)


def summarize_projection_health(
    records: Iterable[_ProjectionHealth],
) -> ProjectionDiagnosticFacts:
    """Aggregate worker facts without copying Thread, route, or error identities."""

    materialized: list[_ProjectionHealth] = []
    try:
        iterator = iter(records)
        for _ in range(_PROJECTION_DIAGNOSTIC_MAX_RECORDS):
            try:
                materialized.append(next(iterator))
            except StopIteration:
                break
    except BaseException:
        # A hostile provider cannot widen diagnostics or leak its failure.
        materialized = []
    state_counts = {"running": 0, "retrying": 0, "stopped": 0}
    degraded_count = 0
    gap_codes: set[str] = set()
    restart_count = 0
    delivery_failure_count = 0
    event_overflow_count = 0
    request_recovery_degraded_count = 0
    recovery_gap_count = 0
    for record in materialized:
        try:
            raw_state = record.state
            state_value = raw_state.value if isinstance(raw_state, StrEnum) else raw_state
            if type(state_value) is not str or state_value not in state_counts:
                raise ValueError("invalid projection diagnostic state")
            state = state_value
            last_subscription_error = record.last_subscription_error
            last_recovery_error = record.last_recovery_error
            last_delivery_error = record.last_delivery_error
            errors = (
                last_subscription_error,
                last_recovery_error,
                last_delivery_error,
            )
            if any(
                error is not None and (type(error) is not str or len(error) > 128)
                for error in errors
            ):
                raise ValueError("invalid projection diagnostic failure fact")
            interactive_value = record.interactive_request_recovery_degraded
            if type(interactive_value) is not bool:
                raise ValueError("invalid projection diagnostic recovery fact")
            interactive_degraded = interactive_value
            gap_values = (record.last_gap, record.last_event_gap)
            if any(
                gap is not None and (type(gap) is not str or len(gap) > 128) for gap in gap_values
            ):
                raise ValueError("invalid projection diagnostic gap")
            raw_gaps = tuple(gap for gap in gap_values if gap is not None)
            counters = (
                record.restart_count,
                record.delivery_failure_count,
                record.event_overflow_count,
            )
            if any(
                type(counter) is not int or not 0 <= counter <= _DIAGNOSTIC_COUNTER_MAX
                for counter in counters
            ):
                raise ValueError("invalid projection diagnostic counter")
            restart_count = _saturating_add(restart_count, counters[0])
            delivery_failure_count = _saturating_add(
                delivery_failure_count,
                counters[1],
            )
            event_overflow_count = _saturating_add(event_overflow_count, counters[2])
        except BaseException:
            state = "stopped"
            last_subscription_error = True
            last_recovery_error = None
            last_delivery_error = None
            interactive_degraded = False
            raw_gaps = ("other",)
        if state in state_counts:
            state_counts[state] += 1
        degraded = (
            state == "retrying"
            or last_subscription_error is not None
            or last_recovery_error is not None
            or last_delivery_error is not None
            or interactive_degraded
            or bool(raw_gaps)
        )
        degraded_count += int(degraded)
        gap_codes.update(_bounded_gap_code(gap) for gap in raw_gaps)
        request_recovery_degraded_count += int(interactive_degraded)
        recovery_gap_count += int(bool(raw_gaps))
    return ProjectionDiagnosticFacts(
        worker_count=len(materialized),
        running_count=state_counts["running"],
        retrying_count=state_counts["retrying"],
        stopped_count=state_counts["stopped"],
        degraded_count=degraded_count,
        restart_count=restart_count,
        delivery_failure_count=delivery_failure_count,
        event_overflow_count=event_overflow_count,
        request_recovery_degraded_count=request_recovery_degraded_count,
        recovery_gap_count=recovery_gap_count,
        recovery_gap_codes=tuple(sorted(gap_codes)),
    )


def collect_application_diagnostics(
    applications: Iterable[_DiagnosticApplication],
) -> tuple[ApplicationDiagnosticFacts, ...]:
    """Read optional providers while preserving configured registry identity."""

    collected: list[ApplicationDiagnosticFacts] = []
    for application in _bounded_diagnostic_items(applications):
        try:
            summary = application.summary
            application_instance_id = _bounded_diagnostic_identity(
                summary.ref.application_instance_id
            )
            kind = _bounded_diagnostic_identity(summary.kind)
        except BaseException:
            continue
        try:
            provider = getattr(application, "diagnostic_facts", None)
            facts = provider() if callable(provider) else None
            normalized = _coerce_application_facts(facts)
        except BaseException:
            normalized = None
        if (
            normalized is None
            or normalized.application_instance_id != application_instance_id
            or normalized.kind != kind
        ):
            normalized = ApplicationDiagnosticFacts(
                application_instance_id=application_instance_id,
                kind=kind,
            )
        collected.append(normalized)
    try:
        return tuple(sorted(collected, key=lambda facts: facts.application_instance_id))
    except BaseException:
        return ()


def collect_channel_diagnostics(
    channels: Iterable[object],
) -> tuple[ChannelDiagnosticFacts, ...]:
    """Read optional providers while preserving configured Channel identity."""

    collected: list[ChannelDiagnosticFacts] = []
    for channel in _bounded_diagnostic_items(channels):
        try:
            channel_instance_id = _bounded_diagnostic_identity(
                getattr(channel, "channel_instance_id")
            )
            kind = _bounded_diagnostic_identity(getattr(channel, "kind"))
        except BaseException:
            continue
        try:
            provider = getattr(channel, "diagnostic_facts", None)
            facts = provider() if callable(provider) else None
            provider_instance_id = getattr(facts, "channel_instance_id", None)
            provider_kind = getattr(facts, "kind", None)
        except BaseException:
            facts = None
            provider_instance_id = None
            provider_kind = None
        try:
            provider_instance_id = _bounded_diagnostic_identity(provider_instance_id)
            provider_kind = _bounded_diagnostic_identity(provider_kind)
        except BaseException:
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
        except BaseException:
            connection = None
        collected.append(ChannelDiagnosticFacts(channel_instance_id, kind, connection))
    try:
        return tuple(sorted(collected, key=lambda facts: facts.channel_instance_id))
    except BaseException:
        return ()


def _bounded_diagnostic_items(
    values: Iterable[_DiagnosticItem],
) -> tuple[_DiagnosticItem, ...]:
    materialized: list[_DiagnosticItem] = []
    try:
        iterator = iter(values)
        for _ in range(_PROJECTION_DIAGNOSTIC_MAX_RECORDS):
            try:
                materialized.append(next(iterator))
            except StopIteration:
                break
    except BaseException:
        return ()
    return tuple(materialized)


def _bounded_diagnostic_identity(value: object) -> str:
    if type(value) is not str or not value or len(value) > _DIAGNOSTIC_ID_MAX_CHARS:
        raise ValueError("diagnostic identity exceeds the fixed bound")
    return value


def _coerce_application_facts(value: object) -> ApplicationDiagnosticFacts | None:
    if type(value) is not ApplicationDiagnosticFacts:
        return None
    connection = _coerce_channel_connection(value.connection, channel_scoped=False)
    presentation = value.presentation
    if presentation is not None:
        presentation = replace(presentation)
    artifact = value.artifact_materialization
    if artifact is not None:
        artifact = replace(artifact)
    return ApplicationDiagnosticFacts(
        application_instance_id=_bounded_diagnostic_identity(value.application_instance_id),
        kind=_bounded_diagnostic_identity(value.kind),
        connection=connection,
        presentation=presentation,
        artifact_materialization=artifact,
    )


def _coerce_channel_connection(
    value: object,
    *,
    channel_scoped: bool = True,
) -> ConnectionDiagnosticFacts | None:
    if value is None:
        return None
    state = ConnectionDiagnosticState(str(getattr(value, "state")))
    failure = getattr(value, "last_failure_code", None)
    connection_epoch = getattr(value, "connection_epoch")
    reconnect_count = getattr(value, "reconnect_count")
    worker_running = getattr(value, "worker_running")
    worker_degraded = getattr(value, "worker_degraded")
    if (
        type(connection_epoch) is not int
        or connection_epoch < 0
        or type(reconnect_count) is not int
        or reconnect_count < 0
        or not isinstance(worker_running, bool)
        or not isinstance(worker_degraded, bool)
    ):
        raise TypeError("invalid Channel diagnostic fact types")
    raw_queues = getattr(value, "queues", ())
    max_queues = 1 if channel_scoped else 2
    if type(raw_queues) is not tuple or len(raw_queues) > max_queues:
        raise TypeError("invalid Channel diagnostic queue collection")
    queues = tuple(_coerce_channel_queue(queue) for queue in raw_queues)
    allowed_queue_names = (
        {QueueDiagnosticName.CHANNEL_INBOUND}
        if channel_scoped
        else {QueueDiagnosticName.NOTIFICATION, QueueDiagnosticName.SERVER_REQUEST}
    )
    if any(queue.name not in allowed_queue_names for queue in queues):
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
    if any(type(item) is not int for item in (capacity, depth, overflow_count)):
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


def _saturating_add(total: int, value: object) -> int:
    if type(value) is not int or value < 0:
        return total
    return min(_DIAGNOSTIC_COUNTER_MAX, total + value)

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..interaction.diagnostics import (
    _DIAGNOSTIC_COUNTER_MAX,
    _DIAGNOSTIC_ID_MAX_CHARS,
    ConnectionDiagnosticFacts,
    QueueDiagnosticName,
)

__all__ = [
    "ApplicationPresentationFailureCode",
    "ApplicationArtifactMaterializationFailureCode",
    "ApplicationDiagnosticFacts",
    "ApplicationPresentationDiagnosticFacts",
    "ApplicationArtifactMaterializationDiagnosticFacts",
    "ApplicationDiagnosticsProvider",
    "DiagnosticsProvider",
]


class ApplicationPresentationFailureCode(StrEnum):
    """Fixed A1 failure categories without native or consumer-controlled detail."""

    INVALID_OUTPUT = "invalid_output"
    PRESENTER_FAILED = "presenter_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


class ApplicationArtifactMaterializationFailureCode(StrEnum):
    """Fixed A1 artifact failures without candidate or consumer-controlled detail."""

    INVALID_FACTS = "invalid_facts"
    INVALID_OUTPUT = "invalid_output"
    MATERIALIZER_FAILED = "materializer_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


@dataclass(frozen=True, slots=True)
class ApplicationDiagnosticFacts:
    """Optional Application-adapter facts without native resource identities."""

    application_instance_id: str
    kind: str
    connection: ConnectionDiagnosticFacts | None = None
    presentation: ApplicationPresentationDiagnosticFacts | None = None
    artifact_materialization: ApplicationArtifactMaterializationDiagnosticFacts | None = None

    def __post_init__(self) -> None:
        if (
            type(self.application_instance_id) is not str
            or not self.application_instance_id
            or len(self.application_instance_id) > _DIAGNOSTIC_ID_MAX_CHARS
            or type(self.kind) is not str
            or not self.kind
            or len(self.kind) > _DIAGNOSTIC_ID_MAX_CHARS
        ):
            raise ValueError("Application diagnostic identity exceeds the fixed bound")
        if self.connection is not None and type(self.connection) is not ConnectionDiagnosticFacts:
            raise TypeError("Application diagnostics must use the exact connection fact shape")
        if self.connection is not None and any(
            queue.name is QueueDiagnosticName.CHANNEL_INBOUND for queue in self.connection.queues
        ):
            raise ValueError("Channel inbound queue is not Application-scoped")
        if (
            self.presentation is not None
            and type(self.presentation) is not ApplicationPresentationDiagnosticFacts
        ):
            raise TypeError("application presentation diagnostics must use the typed fact shape")
        if (
            self.artifact_materialization is not None
            and type(self.artifact_materialization)
            is not ApplicationArtifactMaterializationDiagnosticFacts
        ):
            raise TypeError("application artifact diagnostics must use the typed fact shape")


@dataclass(frozen=True, slots=True)
class ApplicationPresentationDiagnosticFacts:
    """Redacted process-lifetime counters for configured A1 presentation."""

    invocation_count: int = 0
    success_count: int = 0
    omission_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    cancellation_count: int = 0
    cancellation_overrun_count: int = 0
    capacity_rejection_count: int = 0
    last_failure_code: ApplicationPresentationFailureCode | None = None

    def __post_init__(self) -> None:
        counts = (
            self.invocation_count,
            self.success_count,
            self.omission_count,
            self.failure_count,
            self.timeout_count,
            self.cancellation_count,
            self.cancellation_overrun_count,
            self.capacity_rejection_count,
        )
        if any(
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 0 <= count <= _DIAGNOSTIC_COUNTER_MAX
            for count in counts
        ):
            raise TypeError("application presentation counts must be non-negative integers")
        if self.success_count + self.omission_count + self.failure_count > self.invocation_count:
            raise ValueError("application presentation outcomes cannot exceed invocations")
        if self.timeout_count + self.cancellation_count > self.failure_count:
            raise ValueError("application presentation failure counts are inconsistent")
        if self.cancellation_overrun_count > self.timeout_count + self.cancellation_count:
            raise ValueError("application presentation overruns exceed cancellations")
        if self.capacity_rejection_count > self.failure_count:
            raise ValueError("application presentation capacity rejections exceed failures")
        if self.last_failure_code is not None and not isinstance(
            self.last_failure_code,
            ApplicationPresentationFailureCode,
        ):
            raise ValueError("application presentation failure code must use fixed vocabulary")


@dataclass(frozen=True, slots=True)
class ApplicationArtifactMaterializationDiagnosticFacts:
    """Redacted process-lifetime counters for configured App Server artifact A1."""

    invocation_count: int = 0
    success_count: int = 0
    omission_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    cancellation_count: int = 0
    cancellation_overrun_count: int = 0
    capacity_rejection_count: int = 0
    live_duplicate_count: int = 0
    last_failure_code: ApplicationArtifactMaterializationFailureCode | None = None

    def __post_init__(self) -> None:
        counts = (
            self.invocation_count,
            self.success_count,
            self.omission_count,
            self.failure_count,
            self.timeout_count,
            self.cancellation_count,
            self.cancellation_overrun_count,
            self.capacity_rejection_count,
            self.live_duplicate_count,
        )
        if any(
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 0 <= count <= _DIAGNOSTIC_COUNTER_MAX
            for count in counts
        ):
            raise TypeError("application artifact counts must be non-negative integers")
        if self.success_count + self.omission_count + self.failure_count > self.invocation_count:
            raise ValueError("application artifact outcomes cannot exceed invocations")
        if self.timeout_count + self.cancellation_count > self.failure_count:
            raise ValueError("application artifact failure counts are inconsistent")
        if self.cancellation_overrun_count > self.timeout_count + self.cancellation_count:
            raise ValueError("application artifact overruns exceed cancellations")
        if self.capacity_rejection_count > self.failure_count:
            raise ValueError("application artifact capacity rejections exceed failures")
        if self.last_failure_code is not None and not isinstance(
            self.last_failure_code,
            ApplicationArtifactMaterializationFailureCode,
        ):
            raise ValueError("application artifact failure code must use fixed vocabulary")


class DiagnosticsProvider(Protocol):
    """Optional structural seam; it is not a required Core adapter capability."""

    def diagnostic_facts(self) -> ApplicationDiagnosticFacts: ...


# The owner-qualified name is canonical for Applications; the historical name
# remains an exact alias so the transition facade preserves its public object.
ApplicationDiagnosticsProvider = DiagnosticsProvider

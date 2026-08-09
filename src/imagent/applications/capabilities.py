from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..interaction.media import AttachmentSourceKind
from ..interaction.operations import ContractViolation


class SupportLevel(StrEnum):
    NATIVE = "native"
    FALLBACK = "fallback"
    UNSUPPORTED = "unsupported"


class EventSequenceScope(StrEnum):
    NONE = "none"
    THREAD = "thread"
    APPLICATION = "application"


class ProjectMode(StrEnum):
    MANAGED = "managed"
    FLAT = "flat"
    FIXED = "fixed"


class ThreadDeletionCapability(StrEnum):
    UNSUPPORTED = "unsupported"
    ARCHIVE = "archive"
    PERMANENT = "permanent"


@dataclass(frozen=True, slots=True)
class ProjectCapabilities:
    mode: ProjectMode
    discovery: SupportLevel
    reading: SupportLevel
    creation: SupportLevel = SupportLevel.UNSUPPORTED
    deletion: SupportLevel = SupportLevel.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class ThreadCapabilities:
    listing: SupportLevel
    creation: SupportLevel
    reading: SupportLevel
    deletion: ThreadDeletionCapability = ThreadDeletionCapability.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    history: SupportLevel
    streaming: SupportLevel
    replay_from_cursor: SupportLevel
    interruption: SupportLevel
    interactive_requests: SupportLevel
    pending_request_snapshot: SupportLevel = SupportLevel.UNSUPPORTED
    native_thread_activation: SupportLevel = SupportLevel.UNSUPPORTED
    gap_detection: SupportLevel = SupportLevel.UNSUPPORTED
    event_sequence_scope: EventSequenceScope = EventSequenceScope.NONE


@dataclass(frozen=True, slots=True)
class ApplicationCapabilities:
    projects: ProjectCapabilities
    threads: ThreadCapabilities
    runtime: RuntimeCapabilities
    attachment_sources: tuple[AttachmentSourceKind, ...] = ()


def validate_application_capabilities(capabilities: ApplicationCapabilities) -> None:
    if len(set(capabilities.attachment_sources)) != len(capabilities.attachment_sources):
        raise ContractViolation("application attachment source capabilities must be unique")
    runtime = capabilities.runtime
    if (
        runtime.pending_request_snapshot is not SupportLevel.UNSUPPORTED
        and runtime.interactive_requests is SupportLevel.UNSUPPORTED
    ):
        raise ContractViolation("pending request snapshot requires interactive request support")
    if (
        runtime.gap_detection is not SupportLevel.UNSUPPORTED
        and runtime.event_sequence_scope is EventSequenceScope.NONE
    ):
        raise ContractViolation("gap detection requires a declared event sequence scope")
    projects = capabilities.projects
    if projects.mode is ProjectMode.MANAGED:
        if projects.discovery is not SupportLevel.NATIVE:
            raise ContractViolation("managed project mode requires native project discovery")
        if projects.reading is not SupportLevel.NATIVE:
            raise ContractViolation("managed project mode requires native project reads")
        if projects.creation is SupportLevel.FALLBACK:
            raise ContractViolation("managed project creation cannot use fallback support")
        if projects.deletion is SupportLevel.FALLBACK:
            raise ContractViolation("managed project deletion cannot use fallback support")
    else:
        if projects.discovery is not SupportLevel.FALLBACK:
            raise ContractViolation(
                f"{projects.mode.value} project discovery must be an adapter projection"
            )
        if projects.reading is not SupportLevel.FALLBACK:
            raise ContractViolation(
                f"{projects.mode.value} project reads must be an adapter projection"
            )
        if (
            projects.creation is not SupportLevel.UNSUPPORTED
            or projects.deletion is not SupportLevel.UNSUPPORTED
        ):
            raise ContractViolation(
                f"{projects.mode.value} project mode cannot advertise native management"
            )


__all__ = [
    "ApplicationCapabilities",
    "ProjectCapabilities",
    "ThreadCapabilities",
    "RuntimeCapabilities",
    "SupportLevel",
    "ProjectMode",
    "ThreadDeletionCapability",
    "EventSequenceScope",
    "validate_application_capabilities",
]

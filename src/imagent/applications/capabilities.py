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
    if not isinstance(capabilities, ApplicationCapabilities):
        raise ContractViolation("application capabilities must be ApplicationCapabilities")
    if not isinstance(capabilities.projects, ProjectCapabilities):
        raise ContractViolation("application project capabilities must be ProjectCapabilities")
    if not isinstance(capabilities.threads, ThreadCapabilities):
        raise ContractViolation("application Thread capabilities must be ThreadCapabilities")
    if not isinstance(capabilities.runtime, RuntimeCapabilities):
        raise ContractViolation("application runtime capabilities must be RuntimeCapabilities")
    if not isinstance(capabilities.attachment_sources, tuple) or any(
        not isinstance(source, AttachmentSourceKind) for source in capabilities.attachment_sources
    ):
        raise ContractViolation(
            "application attachment source capabilities must be AttachmentSourceKind values"
        )
    if len(set(capabilities.attachment_sources)) != len(capabilities.attachment_sources):
        raise ContractViolation("application attachment source capabilities must be unique")

    projects = capabilities.projects
    if not isinstance(projects.mode, ProjectMode):
        raise ContractViolation("project mode must be ProjectMode")
    for field_name in ("discovery", "reading", "creation", "deletion"):
        if not isinstance(getattr(projects, field_name), SupportLevel):
            raise ContractViolation(f"project {field_name} must be SupportLevel")

    threads = capabilities.threads
    for field_name in ("listing", "creation", "reading"):
        if not isinstance(getattr(threads, field_name), SupportLevel):
            raise ContractViolation(f"Thread {field_name} must be SupportLevel")
    if not isinstance(threads.deletion, ThreadDeletionCapability):
        raise ContractViolation("Thread deletion must be ThreadDeletionCapability")

    runtime = capabilities.runtime
    for field_name in (
        "history",
        "streaming",
        "replay_from_cursor",
        "interruption",
        "interactive_requests",
        "pending_request_snapshot",
        "native_thread_activation",
        "gap_detection",
    ):
        if not isinstance(getattr(runtime, field_name), SupportLevel):
            raise ContractViolation(f"runtime {field_name} must be SupportLevel")
    if not isinstance(runtime.event_sequence_scope, EventSequenceScope):
        raise ContractViolation("runtime event_sequence_scope must be EventSequenceScope")
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

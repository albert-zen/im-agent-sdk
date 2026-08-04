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
    project_operations = (
        projects.discovery,
        projects.reading,
        projects.creation,
        projects.deletion,
    )
    if projects.mode is ProjectMode.MANAGED:
        if projects.discovery is SupportLevel.UNSUPPORTED:
            raise ContractViolation("managed project mode requires project discovery")
        if projects.reading is SupportLevel.UNSUPPORTED:
            raise ContractViolation("managed project mode requires project reads")
    elif any(level is not SupportLevel.UNSUPPORTED for level in project_operations):
        raise ContractViolation(
            f"{projects.mode.value} project mode cannot advertise project operations"
        )

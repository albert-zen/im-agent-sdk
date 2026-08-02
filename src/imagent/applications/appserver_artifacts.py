from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import islice
from types import MappingProxyType
from typing import Protocol

from ..contracts import (
    AttachmentContent,
    AttachmentHandle,
    LocalPath,
    RemoteUrl,
    ThreadRef,
)
from ..diagnostics import (
    ApplicationArtifactMaterializationDiagnosticFacts,
    ApplicationArtifactMaterializationFailureCode,
)
from .appserver_mapping import normalized_item_type

_IDENTITY_MAX_CHARACTERS = 512
_METADATA_MAX_ITEMS = 16
_METADATA_KEY_MAX_CHARACTERS = 64
_METADATA_TEXT_MAX_CHARACTERS = 256


class AppServerArtifactSourceKind(StrEnum):
    LOCAL_PATH = "local_path"
    FILE_URL = "file_url"
    DATA_URL = "data_url"


class AppServerCompletedItemKind(StrEnum):
    AGENT_MESSAGE = "agent_message"
    IMAGE_GENERATION = "image_generation"
    DYNAMIC_TOOL_CALL = "dynamic_tool_call"
    OTHER = "other"


class AppServerCompletedItemPhase(StrEnum):
    COMMENTARY = "commentary"
    FINAL_ANSWER = "final_answer"
    OTHER = "other"
    NONE = "none"


class AppServerTurnTerminalStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class AppServerArtifactCandidate:
    """One stable untrusted native locator; it carries no access authority."""

    candidate_id: str
    source_kind: AppServerArtifactSourceKind
    locator: str

    def __post_init__(self) -> None:
        _require_identity(self.candidate_id, "artifact candidate identity", limit=1_024)
        if not isinstance(self.source_kind, AppServerArtifactSourceKind):
            raise TypeError("artifact candidate source must use the fixed vocabulary")
        if not isinstance(self.locator, str) or not self.locator:
            raise ValueError("artifact candidate locator must be non-empty text")


@dataclass(frozen=True, slots=True)
class AppServerCompletedItemFacts:
    """Bounded facts for one non-user App Server completed item."""

    item_id: str
    thread_ref: ThreadRef
    turn_id: str
    authoritative: bool
    kind: AppServerCompletedItemKind
    phase: AppServerCompletedItemPhase
    has_default_message: bool
    artifact_candidates: tuple[AppServerArtifactCandidate, ...] = ()

    def __post_init__(self) -> None:
        _require_identity(self.item_id, "App Server item identity")
        _require_identity(self.turn_id, "App Server Turn identity")
        _validate_thread_ref(self.thread_ref)
        if not isinstance(self.authoritative, bool) or not isinstance(
            self.has_default_message, bool
        ):
            raise TypeError("artifact item flags must be booleans")
        if not isinstance(self.kind, AppServerCompletedItemKind) or not isinstance(
            self.phase, AppServerCompletedItemPhase
        ):
            raise TypeError("artifact item kind and phase must use fixed vocabularies")
        if not isinstance(self.artifact_candidates, tuple) or not all(
            isinstance(candidate, AppServerArtifactCandidate)
            for candidate in self.artifact_candidates
        ):
            raise TypeError("artifact candidates must be a typed tuple")


@dataclass(frozen=True, slots=True)
class AppServerTurnTerminalFacts:
    """Bounded terminal association point after all completed native items."""

    thread_ref: ThreadRef
    turn_id: str
    authoritative: bool
    status: AppServerTurnTerminalStatus

    def __post_init__(self) -> None:
        _validate_thread_ref(self.thread_ref)
        _require_identity(self.turn_id, "App Server Turn identity")
        if not isinstance(self.authoritative, bool):
            raise TypeError("artifact terminal authority flag must be boolean")
        if not isinstance(self.status, AppServerTurnTerminalStatus):
            raise TypeError("artifact terminal status must use the fixed vocabulary")


@dataclass(frozen=True, slots=True)
class ApplicationArtifactMaterialization:
    """Consumer-owned materialization result without message/control authority."""

    attachments: tuple[AttachmentContent, ...]


class AppServerArtifactMaterializer(Protocol):
    async def materialize_completed_item(
        self,
        facts: AppServerCompletedItemFacts,
    ) -> ApplicationArtifactMaterialization | None: ...

    async def materialize_turn_terminal(
        self,
        facts: AppServerTurnTerminalFacts,
    ) -> ApplicationArtifactMaterialization | None: ...


@dataclass(frozen=True, slots=True)
class AppServerArtifactMaterializationLimits:
    timeout_seconds: float = 30.0
    max_candidates: int = 4
    max_candidate_locator_characters: int = 16 * 1024 * 1024
    max_output_attachments: int = 16
    max_output_string_characters: int = 65_536
    max_concurrency: int = 16
    max_seen_identities: int = 4_096

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("artifact materialization timeout must be finite and positive")
        for label, value in (
            ("candidate", self.max_candidates),
            ("candidate locator", self.max_candidate_locator_characters),
            ("output attachment", self.max_output_attachments),
            ("output string", self.max_output_string_characters),
            ("concurrency", self.max_concurrency),
            ("seen identity", self.max_seen_identities),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"artifact materialization {label} limit must be positive")


class ApplicationArtifactMaterializationError(ValueError):
    """A materializer returned output outside the typed finite contract."""


class ApplicationArtifactMaterializationTimeout(TimeoutError):
    def __init__(self, *, cancellation_overrun: bool) -> None:
        super().__init__("artifact materializer exceeded its configured lifetime")
        self.cancellation_overrun = cancellation_overrun


class ApplicationArtifactMaterializationCapacityError(RuntimeError):
    """The finite materializer task capacity is occupied."""


class ApplicationArtifactMaterializationCancelled(RuntimeError):
    """The materializer cancelled itself without cancelling its caller."""


class ApplicationArtifactMaterializationFailed(RuntimeError):
    """The materializer failed; consumer-controlled detail was discarded."""


class AppServerArtifactMaterializationRuntime:
    """Finite async invocation and redacted diagnostics for App Server artifact A1."""

    def __init__(self, limits: AppServerArtifactMaterializationLimits) -> None:
        self._limits = limits
        self._active_tasks: set[asyncio.Task[ApplicationArtifactMaterialization | None]] = set()
        self._invocation_count = 0
        self._success_count = 0
        self._omission_count = 0
        self._failure_count = 0
        self._timeout_count = 0
        self._cancellation_count = 0
        self._cancellation_overrun_count = 0
        self._capacity_rejection_count = 0
        self._live_duplicate_count = 0
        self._last_failure_code: ApplicationArtifactMaterializationFailureCode | None = None

    async def invoke(
        self,
        call: Callable[[], Awaitable[ApplicationArtifactMaterialization | None]],
    ) -> ApplicationArtifactMaterialization | None:
        self._invocation_count += 1
        if len(self._active_tasks) >= self._limits.max_concurrency:
            self._capacity_rejection_count += 1
            self._record_failure(ApplicationArtifactMaterializationFailureCode.CAPACITY_EXHAUSTED)
            raise ApplicationArtifactMaterializationCapacityError(
                "artifact materializer task capacity is exhausted"
            )
        task = asyncio.create_task(
            _invoke(call),
            name="imagent-appserver-artifact-materialization",
        )
        self._active_tasks.add(task)
        task.add_done_callback(self._finish_task)
        try:
            done, _ = await asyncio.wait((task,), timeout=self._limits.timeout_seconds)
            if not done:
                joined = await _cancel_and_join(
                    task,
                    timeout_seconds=self._limits.timeout_seconds,
                )
                raise ApplicationArtifactMaterializationTimeout(cancellation_overrun=not joined)
            if task.cancelled():
                self._cancellation_count += 1
                self._record_failure(ApplicationArtifactMaterializationFailureCode.CANCELLED)
                raise ApplicationArtifactMaterializationCancelled(
                    "artifact materializer cancelled its invocation"
                )
            output = task.result()
            if output is not None:
                output = _validate_materialization(output, self._limits)
        except ApplicationArtifactMaterializationError:
            self._record_failure(ApplicationArtifactMaterializationFailureCode.INVALID_OUTPUT)
            raise
        except ApplicationArtifactMaterializationTimeout as error:
            self._timeout_count += 1
            if error.cancellation_overrun:
                self._cancellation_overrun_count += 1
            self._record_failure(ApplicationArtifactMaterializationFailureCode.TIMED_OUT)
            raise
        except ApplicationArtifactMaterializationCancelled:
            raise
        except asyncio.CancelledError:
            joined = True
            if not task.done():
                joined = await _cancel_and_join(
                    task,
                    timeout_seconds=self._limits.timeout_seconds,
                )
            self._cancellation_count += 1
            if not joined:
                self._cancellation_overrun_count += 1
            self._record_failure(ApplicationArtifactMaterializationFailureCode.CANCELLED)
            raise
        except BaseException:
            if not task.done():
                await _cancel_and_join(task, timeout_seconds=self._limits.timeout_seconds)
            self._record_failure(ApplicationArtifactMaterializationFailureCode.MATERIALIZER_FAILED)
            raise ApplicationArtifactMaterializationFailed("artifact materializer failed") from None
        if output is None:
            self._omission_count += 1
        else:
            self._success_count += 1
        return output

    def record_live_duplicate(self) -> None:
        self._live_duplicate_count += 1

    def reject_invalid_facts(self) -> None:
        self._invocation_count += 1
        self._record_failure(ApplicationArtifactMaterializationFailureCode.INVALID_FACTS)

    def diagnostic_facts(self) -> ApplicationArtifactMaterializationDiagnosticFacts:
        return ApplicationArtifactMaterializationDiagnosticFacts(
            invocation_count=self._invocation_count,
            success_count=self._success_count,
            omission_count=self._omission_count,
            failure_count=self._failure_count,
            timeout_count=self._timeout_count,
            cancellation_count=self._cancellation_count,
            cancellation_overrun_count=self._cancellation_overrun_count,
            capacity_rejection_count=self._capacity_rejection_count,
            live_duplicate_count=self._live_duplicate_count,
            last_failure_code=self._last_failure_code,
        )

    async def close(self) -> None:
        tasks = tuple(self._active_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(
                tasks,
                timeout=self._limits.timeout_seconds,
            )
            for task in done:
                _consume_task_result(task)
            for task in pending:
                task.cancel()

    def _finish_task(
        self,
        task: asyncio.Task[ApplicationArtifactMaterialization | None],
    ) -> None:
        self._active_tasks.discard(task)
        _consume_task_result(task)

    def _record_failure(
        self,
        code: ApplicationArtifactMaterializationFailureCode,
    ) -> None:
        self._failure_count += 1
        self._last_failure_code = code


def appserver_completed_item_facts(
    item: Mapping[str, object],
    *,
    thread_ref: ThreadRef,
    turn_id: str,
    authoritative: bool,
    default_message_id: str | None,
    limits: AppServerArtifactMaterializationLimits,
) -> AppServerCompletedItemFacts:
    kind = _item_kind(item)
    phase = _item_phase(item)
    candidate_values = _artifact_candidate_values(item, kind=kind, limits=limits)
    item_id = _stable_item_id(item)
    candidates = tuple(
        AppServerArtifactCandidate(
            candidate_id=f"{item_id}:image:{source_index}",
            source_kind=source_kind,
            locator=locator,
        )
        for source_index, source_kind, locator in candidate_values
    )
    return AppServerCompletedItemFacts(
        item_id=item_id,
        thread_ref=thread_ref,
        turn_id=turn_id,
        authoritative=authoritative,
        kind=kind,
        phase=phase,
        has_default_message=default_message_id is not None,
        artifact_candidates=candidates,
    )


def _item_kind(item: Mapping[str, object]) -> AppServerCompletedItemKind:
    kind = normalized_item_type(item)
    if "agent" in kind or "assistant" in kind:
        return AppServerCompletedItemKind.AGENT_MESSAGE
    if kind == "imagegeneration":
        return AppServerCompletedItemKind.IMAGE_GENERATION
    if kind == "dynamictoolcall":
        return AppServerCompletedItemKind.DYNAMIC_TOOL_CALL
    return AppServerCompletedItemKind.OTHER


def _item_phase(item: Mapping[str, object]) -> AppServerCompletedItemPhase:
    phase = item.get("phase")
    if not isinstance(phase, str) or not phase:
        return AppServerCompletedItemPhase.NONE
    normalized = phase.replace("-", "_").casefold()
    if normalized == "commentary":
        return AppServerCompletedItemPhase.COMMENTARY
    if normalized in {"final", "final_answer", "finalanswer"}:
        return AppServerCompletedItemPhase.FINAL_ANSWER
    return AppServerCompletedItemPhase.OTHER


def _artifact_candidate_values(
    item: Mapping[str, object],
    *,
    kind: AppServerCompletedItemKind,
    limits: AppServerArtifactMaterializationLimits,
) -> tuple[tuple[int, AppServerArtifactSourceKind, str], ...]:
    if kind is AppServerCompletedItemKind.IMAGE_GENERATION:
        locator = item.get("savedPath")
        if (
            isinstance(locator, str)
            and locator
            and len(locator) <= limits.max_candidate_locator_characters
        ):
            return ((0, AppServerArtifactSourceKind.LOCAL_PATH, locator),)
        return ()
    if kind is not AppServerCompletedItemKind.DYNAMIC_TOOL_CALL:
        return ()
    content_items = item.get("contentItems")
    if not isinstance(content_items, list):
        return ()
    candidates: list[tuple[int, AppServerArtifactSourceKind, str]] = []
    locator_characters = 0
    for index, content in enumerate(content_items[: limits.max_candidates]):
        if not isinstance(content, Mapping) or content.get("type") != "inputImage":
            continue
        locator = content.get("imageUrl")
        if (
            not isinstance(locator, str)
            or not locator
            or len(locator) > limits.max_candidate_locator_characters
        ):
            continue
        if locator.startswith("data:image/"):
            source_kind = AppServerArtifactSourceKind.DATA_URL
        elif locator.startswith("file:"):
            source_kind = AppServerArtifactSourceKind.FILE_URL
        else:
            continue
        locator_characters += len(locator)
        if locator_characters > limits.max_candidate_locator_characters:
            break
        candidates.append((index, source_kind, locator))
    return tuple(candidates)


def _stable_item_id(item: Mapping[str, object]) -> str:
    native_id = item.get("id") or item.get("itemId")
    if isinstance(native_id, str) and 0 < len(native_id) <= _IDENTITY_MAX_CHARACTERS:
        return native_id
    raise ValueError("App Server artifact item requires a bounded native item identity")


def _validate_materialization(
    output: object,
    limits: AppServerArtifactMaterializationLimits,
) -> ApplicationArtifactMaterialization:
    if not isinstance(output, ApplicationArtifactMaterialization):
        raise ApplicationArtifactMaterializationError(
            "artifact materializer must return ApplicationArtifactMaterialization or None"
        )
    attachments = output.attachments
    if (
        not isinstance(attachments, tuple)
        or not attachments
        or len(attachments) > limits.max_output_attachments
    ):
        raise ApplicationArtifactMaterializationError(
            "artifact materializer returned unbounded attachments"
        )
    canonical: list[AttachmentContent] = []
    seen_ids: set[str] = set()
    string_characters = 0
    for attachment in attachments:
        if not isinstance(attachment, AttachmentContent):
            raise ApplicationArtifactMaterializationError(
                "artifact materializer returned a non-attachment item"
            )
        if attachment.attachment_id in seen_ids:
            raise ApplicationArtifactMaterializationError(
                "artifact materializer returned duplicate attachment identity"
            )
        seen_ids.add(attachment.attachment_id)
        values = [attachment.attachment_id, attachment.media_type]
        if attachment.filename is not None:
            values.append(attachment.filename)
        source = attachment.source
        if isinstance(source, LocalPath):
            values.append(source.path)
        elif isinstance(source, RemoteUrl):
            values.append(source.url)
        elif isinstance(source, AttachmentHandle):
            values.append(source.handle_id)
        else:
            raise ApplicationArtifactMaterializationError(
                "artifact materializer returned an unsupported attachment source"
            )
        if any(not isinstance(value, str) or not value for value in values):
            raise ApplicationArtifactMaterializationError(
                "artifact materializer returned an invalid attachment string"
            )
        string_characters += sum(len(value) for value in values)
        if string_characters > limits.max_output_string_characters:
            raise ApplicationArtifactMaterializationError(
                "artifact materializer exceeded the output string limit"
            )
        if attachment.size_bytes is not None and (
            not isinstance(attachment.size_bytes, int)
            or isinstance(attachment.size_bytes, bool)
            or attachment.size_bytes < 0
        ):
            raise ApplicationArtifactMaterializationError(
                "artifact materializer returned an invalid attachment size"
            )
        metadata, metadata_characters = _bounded_metadata(attachment.metadata)
        string_characters += metadata_characters
        if string_characters > limits.max_output_string_characters:
            raise ApplicationArtifactMaterializationError(
                "artifact materializer exceeded the output string limit"
            )
        canonical.append(replace(attachment, metadata=metadata))
    return ApplicationArtifactMaterialization(tuple(canonical))


def _bounded_metadata(metadata: Mapping[str, object]) -> tuple[Mapping[str, object], int]:
    if not isinstance(metadata, Mapping):
        raise ApplicationArtifactMaterializationError(
            "artifact attachment metadata must be a mapping"
        )
    keys = list(islice(metadata, _METADATA_MAX_ITEMS + 1))
    if len(keys) > _METADATA_MAX_ITEMS:
        raise ApplicationArtifactMaterializationError(
            "artifact attachment metadata exceeds its item limit"
        )
    copied: dict[str, object] = {}
    characters = 0
    for key in keys:
        if not isinstance(key, str) or not key or len(key) > _METADATA_KEY_MAX_CHARACTERS:
            raise ApplicationArtifactMaterializationError(
                "artifact attachment metadata key is invalid"
            )
        value = metadata[key]
        characters += len(key)
        if isinstance(value, str):
            if len(value) > _METADATA_TEXT_MAX_CHARACTERS:
                raise ApplicationArtifactMaterializationError(
                    "artifact attachment metadata text is too long"
                )
            characters += len(value)
        elif value is None or isinstance(value, bool):
            pass
        elif isinstance(value, int):
            if not -(2**63) <= value <= 2**63 - 1:
                raise ApplicationArtifactMaterializationError(
                    "artifact attachment metadata integer is out of range"
                )
        elif not (isinstance(value, float) and math.isfinite(value)):
            raise ApplicationArtifactMaterializationError(
                "artifact attachment metadata must contain bounded scalars"
            )
        copied[key] = value
    return MappingProxyType(copied), characters


def _require_identity(value: str, label: str, *, limit: int = _IDENTITY_MAX_CHARACTERS) -> None:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{label} must be non-empty and at most {limit} characters")


def _validate_thread_ref(thread_ref: ThreadRef) -> None:
    if not isinstance(thread_ref, ThreadRef):
        raise TypeError("artifact facts Thread must use ThreadRef")
    _require_identity(
        thread_ref.application_instance_id,
        "artifact Application identity",
    )
    _require_identity(thread_ref.native_thread_id, "artifact Thread identity")
    if thread_ref.project_ref is not None:
        _require_identity(
            thread_ref.project_ref.application_instance_id,
            "artifact Project Application identity",
        )
        _require_identity(
            thread_ref.project_ref.native_project_id,
            "artifact Project identity",
        )


async def _cancel_and_join(
    task: asyncio.Task[ApplicationArtifactMaterialization | None],
    *,
    timeout_seconds: float,
) -> bool:
    task.cancel()
    done, _ = await asyncio.wait((task,), timeout=timeout_seconds)
    if not done:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        return False
    _consume_task_result(task)
    return True


async def _invoke(
    call: Callable[[], Awaitable[ApplicationArtifactMaterialization | None]],
) -> ApplicationArtifactMaterialization | None:
    return await call()


def _consume_task_result(
    task: asyncio.Task[ApplicationArtifactMaterialization | None],
) -> None:
    try:
        task.result()
    except BaseException:
        pass

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from ..contracts import AgentMessage, MessageRole, TextContent, TextFormat, ThreadRef
from .appserver_mapping import item_text, normalized_item_type

PRESENTATION_TEXT_LIMIT = 8_000
PRESENTATION_LIST_LIMIT = 100
ARTIFACT_CANDIDATE_LIMIT = 4
ARTIFACT_LOCATOR_LIMIT = 16 * 1024 * 1024


class AppServerArtifactSourceKind(StrEnum):
    LOCAL_PATH = "local_path"
    FILE_URL = "file_url"
    DATA_URL = "data_url"


@dataclass(frozen=True, slots=True)
class AppServerArtifactCandidate:
    """One untrusted native artifact locator, bounded before consumer policy sees it."""

    candidate_id: str
    media_kind: str
    source_kind: AppServerArtifactSourceKind
    value: str


@dataclass(frozen=True, slots=True)
class AppServerPresentationItem:
    """Typed presentation facts from one completed App Server item."""

    item_id: str
    item_kind: str
    phase: str
    text: str
    command: str
    changed_paths: tuple[str, ...]
    artifact_candidates: tuple[AppServerArtifactCandidate, ...]


@dataclass(frozen=True, slots=True)
class AppServerPresentationContext:
    thread_ref: ThreadRef
    turn_id: str
    authoritative: bool


class AppServerPresentationHook(Protocol):
    """Consumer presentation policy inside the adapter's single ordered event path."""

    def present_completed_item(
        self,
        context: AppServerPresentationContext,
        item: AppServerPresentationItem,
        default_message: AgentMessage | None,
    ) -> AgentMessage | None: ...

    def present_turn_terminal(
        self,
        context: AppServerPresentationContext,
        status: str,
    ) -> AgentMessage | None: ...


class AppServerLivePresentationHook(Protocol):
    def present_live_message(
        self,
        context: AppServerPresentationContext,
        message: AgentMessage,
    ) -> AgentMessage | None: ...


class AppServerDeltaObserver(Protocol):
    def observe_delta(
        self,
        context: AppServerPresentationContext,
        delta: str,
    ) -> None: ...


def appserver_presentation_item(
    item: Mapping[str, object],
) -> AppServerPresentationItem:
    item_id = str(item.get("id") or item.get("itemId") or "")
    item_kind = normalized_item_type(item)
    changes = item.get("changes")
    changed_paths = (
        tuple(
            path
            for change in changes[:PRESENTATION_LIST_LIMIT]
            if isinstance(change, Mapping)
            and (path := bounded_text(change.get("path"), limit=1_000))
        )
        if isinstance(changes, list)
        else ()
    )
    return AppServerPresentationItem(
        item_id=item_id,
        item_kind=item_kind,
        phase=bounded_text(item.get("phase"), limit=100),
        text=bounded_text(item_text(item)),
        command=bounded_text(item.get("command")),
        changed_paths=changed_paths,
        artifact_candidates=appserver_artifact_candidates(item, item_id=item_id),
    )


def appserver_artifact_candidates(
    item: Mapping[str, object],
    *,
    item_id: str,
) -> tuple[AppServerArtifactCandidate, ...]:
    candidates: list[AppServerArtifactCandidate] = []
    item_kind = normalized_item_type(item)
    if item_kind == "imagegeneration":
        saved_path = bounded_text(item.get("savedPath"), limit=4_096)
        if saved_path:
            owner_id = _artifact_owner_id(item_id, item_kind, saved_path)
            candidates.append(
                AppServerArtifactCandidate(
                    candidate_id=f"{owner_id}:image:0",
                    media_kind="image",
                    source_kind=AppServerArtifactSourceKind.LOCAL_PATH,
                    value=saved_path,
                )
            )
    elif item_kind == "dynamictoolcall":
        content_items = item.get("contentItems")
        if isinstance(content_items, list):
            for index, content in enumerate(content_items[:ARTIFACT_CANDIDATE_LIMIT]):
                if not isinstance(content, Mapping) or content.get("type") != "inputImage":
                    continue
                value = str(content.get("imageUrl") or "")
                if not value or len(value) > ARTIFACT_LOCATOR_LIMIT:
                    continue
                if value.startswith("data:image/"):
                    source_kind = AppServerArtifactSourceKind.DATA_URL
                elif value.startswith("file:"):
                    source_kind = AppServerArtifactSourceKind.FILE_URL
                else:
                    continue
                owner_id = _artifact_owner_id(item_id, item_kind, value)
                candidates.append(
                    AppServerArtifactCandidate(
                        candidate_id=f"{owner_id}:image:{index}",
                        media_kind="image",
                        source_kind=source_kind,
                        value=value,
                    )
                )
    return tuple(candidates)


def _artifact_owner_id(item_id: str, item_kind: str, value: str) -> str:
    if item_id:
        return item_id
    digest = hashlib.sha256(f"{item_kind}\0{value}".encode()).hexdigest()
    return f"imagent:appserver-artifact:sha256:{digest}"


def appserver_completed_item_message(
    thread_ref: ThreadRef,
    item: Mapping[str, object],
    *,
    fallback_item_id: str | None = None,
    native_method: str = "item/completed",
    native_application: str = "appserver",
) -> AgentMessage | None:
    item_kind = normalized_item_type(item)
    text = ""
    kind = ""
    if "agent" in item_kind or "assistant" in item_kind:
        text = item_text(item)
        kind = "agent_message"
    elif item_kind == "commandexecution":
        command = bounded_text(item.get("command"))
        if command:
            text = f"Executed `{command}`"
            kind = "command_execution"
    elif item_kind == "filechange":
        changes = item.get("changes")
        paths = (
            tuple(
                bounded_text(change.get("path"), limit=1_000)
                for change in changes[:PRESENTATION_LIST_LIMIT]
                if isinstance(change, Mapping) and change.get("path")
            )
            if isinstance(changes, list)
            else ()
        )
        paths = tuple(path for path in paths if path)
        if paths:
            text = "\n".join(("Changed files:", *(f"- {path}" for path in paths)))
            kind = "file_change"
    if not text:
        return None
    item_id = str(item.get("id") or fallback_item_id or "") or f"live-{uuid.uuid4()}"
    metadata = {
        "native_application": native_application,
        "native_method": native_method,
        "native_item_kind": kind,
    }
    phase = str(item.get("phase") or "")
    if phase or kind == "agent_message":
        metadata["phase"] = phase
    return AgentMessage(
        agent_item_id=item_id,
        thread_ref=thread_ref,
        role=MessageRole.ASSISTANT,
        content=(TextContent(text[:PRESENTATION_TEXT_LIMIT], TextFormat.MARKDOWN),),
        created_at=datetime.now(UTC),
        metadata=metadata,
    )


def appserver_live_message(
    thread_ref: ThreadRef,
    *,
    method: str,
    params: Mapping[str, object],
    native_application: str = "appserver",
) -> AgentMessage | None:
    kind = ""
    text = ""
    if method == "turn/plan/updated":
        kind = "plan_updated"
        lines = ["[Plan update]"]
        explanation = bounded_text(params.get("explanation"))
        if explanation:
            lines.append(explanation)
        plan = params.get("plan")
        if isinstance(plan, list):
            for entry in plan[:PRESENTATION_LIST_LIMIT]:
                if not isinstance(entry, Mapping):
                    continue
                step = bounded_text(entry.get("step"), limit=1_000)
                status = bounded_text(entry.get("status"), limit=100)
                if step and status:
                    lines.append(f"[{status}] {step}")
        text = "\n".join(lines)
    elif method == "turn/diff/updated":
        kind = "diff_updated"
        lines = [bounded_text(params.get("summary")) or "Diff updated."]
        files = params.get("files")
        if isinstance(files, list):
            paths = tuple(
                path
                for value in files[:PRESENTATION_LIST_LIMIT]
                if (path := bounded_text(value, limit=1_000))
            )
            if paths:
                lines.extend(("Files:", *(f"- {path}" for path in paths)))
        text = "\n".join(lines)
    elif method == "thread/status/changed":
        kind = "thread_status_changed"
        status = params.get("status")
        if isinstance(status, Mapping):
            status = status.get("type") or status.get("status")
        text = f"Thread status changed: {bounded_text(status, limit=100) or 'updated'}."
    elif method == "thread/compacted":
        kind = "thread_compacted"
        summary = bounded_text(params.get("summary"))
        text = "Thread compacted." if not summary else f"Thread compacted. {summary}"
    elif method == "model/rerouted":
        kind = "model_rerouted"
        text = bounded_text(params.get("message")) or "Model rerouted."
    if not text:
        return None
    event_id = str(params.get("eventId") or params.get("event_id") or "")
    return AgentMessage(
        agent_item_id=event_id or f"live-{uuid.uuid4()}",
        thread_ref=thread_ref,
        role=MessageRole.SYSTEM,
        content=(TextContent(text[:PRESENTATION_TEXT_LIMIT], TextFormat.MARKDOWN),),
        created_at=datetime.now(UTC),
        metadata={
            "native_application": native_application,
            "native_method": method,
            "native_item_kind": kind,
            "live_only": True,
        },
    )


def bounded_text(value: object, *, limit: int = PRESENTATION_TEXT_LIMIT) -> str:
    return str(value or "").strip()[:limit]

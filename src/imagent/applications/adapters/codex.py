from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from ...interaction.messages import MessageRole
from ..contract import AgentMessage, ThreadRef
from ..events import AgentEventType
from ..presentation import (
    ApplicationPresentationLimits,
    ApplicationPresentationRuntime,
    CodexLiveActivityFacts,
    CodexLiveActivityKind,
    CodexLiveActivityMethod,
    CodexLiveActivityPresenter,
    CodexPlanStep,
)
from ..presentation.artifact_materialization import (
    AppServerArtifactMaterializationLimits,
    AppServerArtifactMaterializer,
)
from .appserver._base import AppServerClient, _AppServerApplicationAdapter
from .appserver.requests import map_appserver_request

__all__ = ["CodexApplicationAdapter"]


class CodexApplicationAdapter(_AppServerApplicationAdapter):
    def __init__(
        self,
        *,
        application_instance_id: str,
        client: AppServerClient,
        cwd: str,
        shared_filesystem_root: str | Path | None = None,
        event_buffer_max_pending: int = 1024,
        steer_active_turn: bool = True,
        thread_start_options: Mapping[str, object] | None = None,
        live_activity_presenter: CodexLiveActivityPresenter | None = None,
        presentation_limits: ApplicationPresentationLimits = ApplicationPresentationLimits(),
        artifact_materializer: AppServerArtifactMaterializer | None = None,
        artifact_materialization_limits: AppServerArtifactMaterializationLimits = (
            AppServerArtifactMaterializationLimits()
        ),
    ) -> None:
        self._live_activity_presenter = live_activity_presenter
        self._presentation_limits = presentation_limits
        self._seen_live_activity_ids: dict[tuple[str, str], None] = {}
        super().__init__(
            application_instance_id=application_instance_id,
            kind="codex",
            display_name="Codex",
            client=client,
            cwd=cwd,
            shared_filesystem_root=shared_filesystem_root,
            server_request_mapper=map_appserver_request,
            event_buffer_max_pending=event_buffer_max_pending,
            steer_active_turn=steer_active_turn,
            thread_start_options=thread_start_options,
            presentation_runtime=(
                ApplicationPresentationRuntime(presentation_limits)
                if live_activity_presenter is not None
                else None
            ),
            artifact_materializer=artifact_materializer,
            artifact_materialization_limits=artifact_materialization_limits,
        )

    async def _handle_notification(self, notification: dict) -> None:
        method = str(notification.get("method") or "")
        params = notification.get("params")
        if method == "serverRequest/resolved" or not isinstance(params, dict):
            await super()._handle_notification(notification)
            return
        thread_id = str(params.get("threadId") or "")
        if not thread_id:
            await super()._handle_notification(notification)
            return
        turn = params.get("turn")
        turn_id = str(
            params.get("turnId") or (turn.get("id") if isinstance(turn, dict) else "") or ""
        )
        facts = _codex_live_activity_facts(
            self._thread_ref(thread_id),
            turn_id=turn_id or None,
            method=method,
            params=params,
        )
        live_activity_presenter = self._live_activity_presenter
        if live_activity_presenter is not None and facts is not None:
            identity = (thread_id, facts.event_id)
            if identity in self._seen_live_activity_ids:
                return
            self._seen_live_activity_ids[identity] = None
            while len(self._seen_live_activity_ids) > self._presentation_limits.max_seen_identities:
                self._seen_live_activity_ids.pop(next(iter(self._seen_live_activity_ids)))
            runtime = self._presentation_runtime
            if runtime is None:
                raise RuntimeError("Codex live presenter runtime is not configured")
            presentation = await runtime.invoke(
                lambda: live_activity_presenter.present_live_activity(facts)
            )
            if presentation is not None:
                thread_ref = self._thread_ref(thread_id)
                message = AgentMessage(
                    agent_item_id=facts.event_id,
                    thread_ref=thread_ref,
                    role=MessageRole.SYSTEM,
                    content=presentation.content,
                    created_at=datetime.now(UTC),
                    metadata={
                        "native_application": self._summary.kind,
                        "native_method": facts.native_method.value,
                        "kind": facts.kind.value,
                        "live_only": True,
                    },
                )
                self._emit(
                    thread_id,
                    AgentEventType.MESSAGE_CREATED,
                    {"message": message},
                    event_id=facts.event_id,
                    thread_ref=thread_ref,
                    turn_id=turn_id or None,
                )
            return
        await super()._handle_notification(notification)


_CODEX_LIVE_METHODS = {
    CodexLiveActivityMethod.PLAN_UPDATED.value: (
        CodexLiveActivityMethod.PLAN_UPDATED,
        CodexLiveActivityKind.PLAN_UPDATED,
    ),
    CodexLiveActivityMethod.DIFF_UPDATED.value: (
        CodexLiveActivityMethod.DIFF_UPDATED,
        CodexLiveActivityKind.DIFF_UPDATED,
    ),
    CodexLiveActivityMethod.THREAD_STATUS_CHANGED.value: (
        CodexLiveActivityMethod.THREAD_STATUS_CHANGED,
        CodexLiveActivityKind.THREAD_STATUS_CHANGED,
    ),
    CodexLiveActivityMethod.THREAD_COMPACTED.value: (
        CodexLiveActivityMethod.THREAD_COMPACTED,
        CodexLiveActivityKind.THREAD_COMPACTED,
    ),
    CodexLiveActivityMethod.MODEL_REROUTED.value: (
        CodexLiveActivityMethod.MODEL_REROUTED,
        CodexLiveActivityKind.MODEL_REROUTED,
    ),
}


def _codex_live_activity_facts(
    thread_ref: ThreadRef,
    *,
    turn_id: str | None,
    method: str,
    params: Mapping[str, object],
) -> CodexLiveActivityFacts | None:
    classification = _CODEX_LIVE_METHODS.get(method)
    if classification is None:
        return None
    native_method, kind = classification
    event_id = str(params.get("eventId") or params.get("event_id") or "")
    if not event_id:
        event_id = f"imagent:appserver-live:{uuid.uuid4()}"
    summary: str | None = None
    changed_file_count: int | None = None
    plan: tuple[CodexPlanStep, ...] = ()
    if kind is CodexLiveActivityKind.PLAN_UPDATED:
        summary = _bounded_presentation_text(params.get("explanation"))
        native_plan = params.get("plan")
        if isinstance(native_plan, list):
            normalized_plan: list[CodexPlanStep] = []
            for entry in native_plan[:100]:
                if not isinstance(entry, Mapping):
                    continue
                status = _bounded_presentation_text(entry.get("status"), limit=128)
                step = _bounded_presentation_text(entry.get("step"))
                if status is not None and step is not None:
                    normalized_plan.append(CodexPlanStep(status=status, step=step))
            plan = tuple(normalized_plan)
    elif kind is CodexLiveActivityKind.DIFF_UPDATED:
        summary = _bounded_presentation_text(params.get("summary"))
        native_files = params.get("files")
        if isinstance(native_files, list):
            changed_file_count = min(len(native_files), 100)
    elif kind is CodexLiveActivityKind.THREAD_STATUS_CHANGED:
        status = params.get("status")
        if isinstance(status, Mapping):
            status = status.get("type") or status.get("status")
        summary = _bounded_presentation_text(status, limit=128)
    elif kind is CodexLiveActivityKind.THREAD_COMPACTED:
        summary = _bounded_presentation_text(params.get("summary"))
    else:
        summary = _bounded_presentation_text(params.get("message"))
    return CodexLiveActivityFacts(
        event_id=event_id,
        thread_ref=thread_ref,
        turn_id=turn_id,
        kind=kind,
        native_method=native_method,
        summary=summary,
        changed_file_count=changed_file_count,
        plan=plan,
    )


def _bounded_presentation_text(value: object, *, limit: int = 8_000) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] or None

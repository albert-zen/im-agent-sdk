from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from ...interaction.messages import MessageRole
from ..contract import (
    AcceptedTurn,
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    ApplicationInputOutcomeUnknown,
    InputContinuationPreference,
    InputDisposition,
    ThreadRef,
    ThreadStatus,
    TurnRef,
    TurnReplyCorrelationPolicy,
    TurnStatus,
)
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
from .appserver._base import (
    AppServerClient,
    _AppServerApplicationAdapter,
    _PreparedAppServerInput,
)
from .appserver.mapping import (
    AppServerEvent as _AppServerEvent,
)
from .appserver.mapping import (
    AppServerMappingError as _AppServerMappingError,
)
from .appserver.mapping import native_turn_id as _native_turn_id
from .appserver.mapping import (
    thread_status as _thread_status,
)
from .appserver.mapping import (
    turn_id as _turn_id,
)
from .appserver.mapping import (
    turn_status as _turn_status,
)
from .appserver.requests import map_appserver_request

__all__ = ["CodexApplicationAdapter"]


class _CodexSteerClient(Protocol):
    def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        text: str | None = None,
        **kwargs: object,
    ) -> Awaitable[Mapping[str, object]] | Mapping[str, object]: ...


class CodexApplicationAdapter(_AppServerApplicationAdapter):
    def __init__(
        self,
        *,
        application_instance_id: str,
        client: AppServerClient,
        workspace_id: str,
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
        self._steer_active_turn = steer_active_turn
        self._live_activity_presenter = live_activity_presenter
        self._presentation_limits = presentation_limits
        self._seen_live_activity_ids: dict[tuple[str, str], None] = {}
        super().__init__(
            application_instance_id=application_instance_id,
            kind="codex",
            display_name="Codex",
            client=client,
            workspace_id=workspace_id,
            cwd=cwd,
            shared_filesystem_root=shared_filesystem_root,
            server_request_mapper=map_appserver_request,
            event_buffer_max_pending=event_buffer_max_pending,
            thread_start_options=thread_start_options,
            presentation_runtime=(
                ApplicationPresentationRuntime(presentation_limits)
                if live_activity_presenter is not None
                else None
            ),
            artifact_materializer=artifact_materializer,
            artifact_materialization_limits=artifact_materialization_limits,
        )

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: Callable[[ApplicationInputDispatch], Awaitable[None]] | None = None,
    ) -> AcceptedTurn:
        self._require_own_thread(thread_ref)
        prepared = self._prepare_input(message)
        if not isinstance(continuation, InputContinuationPreference):
            raise ValueError("unknown input continuation preference")
        active_turn_id = None
        verified_native_scope: Mapping[str, object] | None = None
        if (
            self._steer_active_turn
            and continuation is InputContinuationPreference.PREFER_ACTIVE_TURN
        ):
            evidence = self._created_pre_input_evidence(thread_ref)
            if evidence is not None:
                native_thread, exact_pre_input = await self._read_created_pre_input_scope(
                    thread_ref
                )
                if exact_pre_input:
                    verified_native_scope = native_thread
                else:
                    active_turn_id = await self._read_active_turn_id(thread_ref)
            else:
                active_turn_id = await self._read_active_turn_id(thread_ref)
        if active_turn_id is None:
            return await self._start_prepared_input(
                thread_ref,
                message,
                prepared,
                before_dispatch,
                verified_native_scope=verified_native_scope,
            )
        expected_local_image_epoch = (
            await self._verified_local_image_epoch() if prepared.input_items is not None else None
        )
        steer_client = self._steer_client()
        if before_dispatch is not None:
            await before_dispatch(
                ApplicationInputDispatch(
                    thread_ref=thread_ref,
                    client_message_id=message.client_message_id,
                    disposition=InputDisposition.STEERED,
                    correlation_policy=TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
                    expected_turn_ref=TurnRef(thread_ref, active_turn_id),
                )
            )
        self._retire_created_pre_input(thread_ref)
        result = await self._steer_input(
            steer_client,
            thread_id=thread_ref.thread_id,
            turn_id=active_turn_id,
            prepared=prepared,
            expected_local_image_epoch=expected_local_image_epoch,
        )
        try:
            turn_id = _native_turn_id(result)
        except _AppServerMappingError as cause:
            raise ApplicationInputOutcomeUnknown(
                "turn/steer was accepted but its native Turn identity is unknown",
                cause,
            ) from cause
        if not turn_id:
            cause = RuntimeError("turn/steer did not return a turn id")
            raise ApplicationInputOutcomeUnknown(
                "turn/steer was accepted but its native Turn identity is unknown",
                cause,
            ) from cause
        return AcceptedTurn(
            turn_ref=TurnRef(thread_ref, turn_id),
            client_message_id=message.client_message_id,
            disposition=InputDisposition.STEERED,
            correlation_policy=TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
        )

    async def _read_active_turn_id(self, thread_ref: ThreadRef) -> str | None:
        thread = await self._require_native_thread_scope(
            thread_ref,
            include_turns=True,
        )
        turns = thread.get("turns")
        if not isinstance(turns, list):
            raise RuntimeError("active-Turn steering requires an authoritative native turn list")
        for turn in reversed(turns):
            if (
                not isinstance(turn, Mapping)
                or _turn_status(turn.get("status")) is not TurnStatus.RUNNING
            ):
                continue
            return _turn_id(turn)
        status_value = thread.get("status")
        if isinstance(status_value, Mapping):
            status_value = status_value.get("type") or status_value.get("status")
        if _thread_status(status_value) is ThreadStatus.RUNNING:
            raise RuntimeError("native thread is active but did not expose an active Turn identity")
        return None

    def _steer_client(self) -> _CodexSteerClient:
        steer_turn = getattr(self._client, "steer_turn", None)
        if not callable(steer_turn):
            raise RuntimeError("configured App Server client does not support turn/steer")
        return cast(_CodexSteerClient, self._client)

    async def _steer_input(
        self,
        steer_client: _CodexSteerClient,
        *,
        thread_id: str,
        turn_id: str,
        prepared: _PreparedAppServerInput,
        expected_local_image_epoch: int | None,
    ) -> Mapping[str, object]:
        native_text, kwargs = self._input_arguments(
            prepared,
            expected_local_image_epoch,
        )
        result = steer_client.steer_turn(
            thread_id,
            turn_id,
            native_text,
            **kwargs,
        )
        if not inspect.isawaitable(result):
            raise RuntimeError("configured App Server turn/steer did not return an awaitable")
        return await cast(Awaitable[Mapping[str, object]], result)

    async def _handle_mapped_notification(self, event: _AppServerEvent) -> None:
        method = event.method
        thread_id = event.thread_id
        live_activity_presenter = self._live_activity_presenter
        classification = _CODEX_LIVE_METHODS.get(method)
        if (
            live_activity_presenter is not None
            and classification is not None
            and thread_id is not None
        ):
            if event.event_id is None:
                raise _AppServerMappingError
            facts = _codex_live_activity_facts(
                self._thread_ref(thread_id),
                turn_id=event.turn_id,
                event_id=event.event_id,
                method=method,
                params=event.payload,
            )
            assert facts is not None
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
                    turn_id=event.turn_id,
                )
            return
        await super()._handle_mapped_notification(event)


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
    event_id: str,
    method: str,
    params: Mapping[str, object],
) -> CodexLiveActivityFacts | None:
    classification = _CODEX_LIVE_METHODS.get(method)
    if classification is None:
        return None
    native_method, kind = classification
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

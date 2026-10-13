from __future__ import annotations

import base64
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, Protocol, cast, overload

from ...interaction.media import (
    AttachmentContent,
    AttachmentSourceKind,
    LocalPath,
    read_local_attachment,
)
from ...interaction.messages import TextContent
from ..contract import (
    AcceptedTurn,
    AgentInput,
    ApplicationInputAcceptance,
    ApplicationInputDispatch,
    ApplicationInputOutcomeUnknown,
    ApplicationInputPlanHandler,
    InputContinuationPreference,
    InputDisposition,
    QueuedInputDispatch,
    QueuedInputReceipt,
    ThreadRef,
    ThreadStatus,
    TurnRef,
    TurnReplyCorrelationPolicy,
    TurnStatus,
    validate_queued_input_dispatch,
)
from ..presentation.artifact_materialization import (
    AppServerArtifactMaterializationLimits,
    AppServerArtifactMaterializer,
)
from .appserver._base import AppServerClient, _AppServerApplicationAdapter, _PreparedAppServerInput
from .appserver.client.handoff import AppServerResponse
from .appserver.mapping import (
    AppServerMappingError as _AppServerMappingError,
)
from .appserver.mapping import (
    native_turn_id as _native_turn_id,
)
from .appserver.mapping import (
    thread_status as _thread_status,
)
from .appserver.mapping import (
    turn_id as _turn_id,
)
from .appserver.mapping import (
    turn_status as _turn_status,
)
from .appserver.requests import map_zen_appserver_request
from .zen_queue import (
    _INLINE_IMAGE_CAPABILITY,
    _QUEUE_CAPABILITY,
    _native_input_capabilities,
    _queued_admission_receipt,
    _queued_input_fingerprint,
    _queued_receipt_from_items,
)

__all__ = ["ZenApplicationAdapter"]

_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_INPUT_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_INPUT_IMAGES = 16
_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


class _ZenSteerClient(Protocol):
    def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        text: str | None = None,
        **kwargs: object,
    ) -> Awaitable[Mapping[str, object]] | Mapping[str, object]: ...


class _ZenQueueClient(Protocol):
    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...

    async def call_with_dispatch_position(
        self,
        method: str,
        params: dict[str, Any],
    ) -> AppServerResponse: ...

    async def queue_zen_input(
        self,
        thread_id: str,
        client_message_id: str,
        *,
        input_items: list[dict[str, Any]],
        expected_connection_epoch: int,
    ) -> Mapping[str, object]: ...


class ZenApplicationAdapter(_AppServerApplicationAdapter):
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
        thread_creation_profiles: Mapping[str, Mapping[str, object]] | None = None,
        artifact_materializer: AppServerArtifactMaterializer | None = None,
        artifact_materialization_limits: AppServerArtifactMaterializationLimits = (
            AppServerArtifactMaterializationLimits()
        ),
    ) -> None:
        self._steer_active_turn = steer_active_turn
        super().__init__(
            application_instance_id=application_instance_id,
            kind="zen",
            display_name="Zen",
            client=client,
            workspace_id=workspace_id,
            cwd=cwd,
            shared_filesystem_root=shared_filesystem_root,
            server_request_mapper=map_zen_appserver_request,
            event_buffer_max_pending=event_buffer_max_pending,
            thread_start_options=thread_start_options,
            thread_creation_profiles=thread_creation_profiles,
            artifact_materializer=artifact_materializer,
            artifact_materialization_limits=artifact_materialization_limits,
        )

        if self._shared_filesystem_root is not None:
            self._summary = replace(
                self._summary,
                capabilities=replace(
                    self._summary.capabilities,
                    attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
                ),
            )

    def _prepare_input(self, message: AgentInput) -> _PreparedAppServerInput:
        attachments = tuple(part for part in message.content if isinstance(part, AttachmentContent))
        if not attachments:
            return super()._prepare_input(message)
        if len(attachments) > _MAX_INPUT_IMAGES:
            raise ValueError("Zen input image count exceeds its bound")
        total_bytes = 0
        for attachment in attachments:
            if attachment.media_type not in _IMAGE_MEDIA_TYPES:
                raise ValueError("Zen supports PNG, JPEG, GIF, and WebP image attachments only")
            if not isinstance(attachment.source, LocalPath):
                raise NotImplementedError("Zen image input requires rooted LocalPath bytes")
            size = attachment.size_bytes
            if type(size) is not int or not 0 < size <= _MAX_IMAGE_BYTES:
                raise ValueError("Zen image input requires a finite positive declared byte size")
            total_bytes += size
        if total_bytes > _MAX_INPUT_IMAGE_BYTES:
            raise ValueError("Zen input image bytes exceed the aggregate bound")
        items: list[Mapping[str, object]] = []
        for part in message.content:
            if isinstance(part, TextContent):
                items.append({"type": "text", "text": part.text})
            elif isinstance(part, AttachmentContent):
                assert part.size_bytes is not None
                payload = read_local_attachment(
                    part.source,
                    shared_filesystem_root=self._shared_filesystem_root,
                    consumer="Zen image input",
                    expected_size=part.size_bytes,
                    max_bytes=_MAX_IMAGE_BYTES,
                )
                items.append(
                    {
                        "type": "image",
                        "url": f"data:{part.media_type};base64,"
                        + base64.b64encode(payload).decode("ascii"),
                    }
                )
        return _PreparedAppServerInput(text="", input_items=tuple(items))

    @overload
    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: Literal[InputContinuationPreference.QUEUE],
        before_dispatch: Callable[[QueuedInputDispatch], Awaitable[None]] | None = None,
    ) -> QueuedInputReceipt: ...

    @overload
    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: Literal[
            InputContinuationPreference.PREFER_ACTIVE_TURN,
            InputContinuationPreference.START_NEW_TURN,
        ] = InputContinuationPreference.PREFER_ACTIVE_TURN,
        before_dispatch: Callable[[ApplicationInputDispatch], Awaitable[None]] | None = None,
    ) -> AcceptedTurn: ...

    @overload
    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = InputContinuationPreference.PREFER_ACTIVE_TURN,
        before_dispatch: ApplicationInputPlanHandler | None = None,
    ) -> ApplicationInputAcceptance: ...

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: (
            Callable[[ApplicationInputDispatch], Awaitable[None]]
            | Callable[[QueuedInputDispatch], Awaitable[None]]
            | None
        ) = None,
    ) -> ApplicationInputAcceptance:
        self._require_own_thread(thread_ref)
        if continuation is InputContinuationPreference.QUEUE:
            return await self._send_queued_input(
                thread_ref,
                message,
                cast(Callable[[QueuedInputDispatch], Awaitable[None]] | None, before_dispatch),
            )
        turn_dispatch = cast(
            Callable[[ApplicationInputDispatch], Awaitable[None]] | None, before_dispatch
        )
        prepared = self._prepare_input(message)
        if not isinstance(continuation, InputContinuationPreference):
            raise ValueError("unknown input continuation preference")
        active_turn_id = None
        expected_pre_input_evidence = None
        if (
            self._steer_active_turn
            and continuation is InputContinuationPreference.PREFER_ACTIVE_TURN
        ):
            evidence = self._created_pre_input_evidence(thread_ref)
            if evidence is not None:
                _native_thread, exact_pre_input = await self._read_created_pre_input_scope(
                    thread_ref,
                    expected_evidence=evidence,
                )
                if exact_pre_input:
                    expected_pre_input_evidence = evidence
                else:
                    active_turn_id = await self._read_active_turn_id(thread_ref)
            else:
                active_turn_id = await self._read_active_turn_id(thread_ref)
        if active_turn_id is None:
            return await self._start_prepared_input(
                thread_ref,
                message,
                prepared,
                turn_dispatch,
                expected_pre_input_evidence=expected_pre_input_evidence,
            )
        expected_local_image_epoch = (
            await self._verified_local_image_epoch() if prepared.uses_local_image_paths else None
        )
        steer_client = self._steer_client()
        if turn_dispatch is not None:
            await turn_dispatch(
                ApplicationInputDispatch(
                    thread_ref=thread_ref,
                    client_message_id=message.client_message_id,
                    disposition=InputDisposition.STEERED,
                    correlation_policy=TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
                    expected_turn_ref=TurnRef(thread_ref, active_turn_id),
                )
            )
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

    def _queue_client(self) -> _ZenQueueClient:
        for name in ("call", "call_with_dispatch_position", "queue_zen_input"):
            if not callable(getattr(self._client, name, None)):
                raise NotImplementedError("configured client does not support native queued input")
        return cast(_ZenQueueClient, self._client)

    async def _send_queued_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        before_dispatch: Callable[[QueuedInputDispatch], Awaitable[None]] | None,
    ) -> QueuedInputReceipt:
        client = self._queue_client()
        features = await client.call_with_dispatch_position("zen/initialize", {})
        capabilities = _native_input_capabilities(features.result)
        if _QUEUE_CAPABILITY not in capabilities:
            raise NotImplementedError("native Host does not support queued-input-admission-v1")
        has_images = any(isinstance(part, AttachmentContent) for part in message.content)
        if has_images and _INLINE_IMAGE_CAPABILITY not in capabilities:
            raise NotImplementedError("native Host does not support inline-image-input-v1")
        await self._require_native_thread_scope(thread_ref)
        prepared = self._prepare_input(message)
        items: list[dict[str, Any]] = []
        native_parts = prepared.input_items or tuple(
            {"type": "text", "text": part.text}
            for part in message.content
            if isinstance(part, TextContent)
        )
        for item in native_parts:
            if item.get("type") == "text":
                items.append(dict(item))
            elif item.get("type") == "image":
                url = item.get("url")
                if not isinstance(url, str) or not url.startswith("data:"):
                    raise ValueError("queued images require acquired inline bytes")
                header, encoded = url[5:].split(";base64,", 1)
                items.append({"type": "image_bytes", "mediaType": header, "dataBase64": encoded})
            else:
                raise ValueError("unsupported native queued input part")
        fingerprint = _queued_input_fingerprint(items)
        plan = QueuedInputDispatch(thread_ref, message.client_message_id, fingerprint)
        validate_queued_input_dispatch(plan)
        if before_dispatch is not None:
            await before_dispatch(plan)
        self._retire_created_pre_input(thread_ref)
        result = await client.queue_zen_input(
            thread_ref.thread_id,
            message.client_message_id,
            input_items=items,
            expected_connection_epoch=features.dispatch_position.connection_epoch,
        )
        try:
            return _queued_admission_receipt(
                result, thread_ref, message.client_message_id, fingerprint
            )
        except Exception as cause:
            # A parser failure after one native send cannot prove no admission.
            raise ApplicationInputOutcomeUnknown(
                "native queue response lacked valid canonical receipt evidence", cause
            ) from cause

    async def read_queued_input_receipt(
        self,
        thread_ref: ThreadRef,
        client_message_id: str,
    ) -> QueuedInputReceipt | None:
        """Read canonical evidence; this never resends input or resumes its queue."""

        self._require_own_thread(thread_ref)
        client = self._queue_client()
        result = await client.call("zen/thread/read", {"threadId": thread_ref.thread_id})
        thread = result.get("thread")
        if (
            not isinstance(thread, Mapping)
            or thread.get("id") != thread_ref.thread_id
            or not self._native_thread_matches_workspace(thread)
        ):
            raise ValueError("native queued input snapshot belongs to a different workspace")
        return _queued_receipt_from_items(thread.get("items"), thread_ref, client_message_id)

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

    def _steer_client(self) -> _ZenSteerClient:
        steer_turn = getattr(self._client, "steer_turn", None)
        if not callable(steer_turn):
            raise RuntimeError("configured App Server client does not support turn/steer")
        return cast(_ZenSteerClient, self._client)

    async def _steer_input(
        self,
        steer_client: _ZenSteerClient,
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

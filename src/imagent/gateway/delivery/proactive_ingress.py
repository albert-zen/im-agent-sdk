from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ...adapters import DeliverySubmissionConflict
from ...contracts import (
    ConversationDeliveryTarget,
    DeliveryIntent,
    DeliveryTarget,
    ProactiveDeliveryResult,
    ProjectRef,
    ThreadRef,
    ThreadRouteDeliveryTarget,
)
from ...interaction.media import AttachmentContent
from ...interaction.media_staging import (
    InlineArtifactStagingInput,
    create_inline_staging_directory,
    stage_inline_artifacts,
    validated_inline_artifact_size,
)
from ...interaction.messages import ConversationRef, TextContent, TextFormat
from ...interaction.operations import ContractViolation
from ...keyed_locks import KeyedLockRegistry
from .proactive import DeliveryRouteError
from .proactive_authorization import DeliveryAuthorizationError


class ProactiveDeliveryEndpoint(Protocol):
    async def authorize_proactive_target(
        self,
        target: DeliveryTarget,
        *,
        credential: str,
    ) -> None: ...

    async def deliver_proactively(
        self,
        intent: DeliveryIntent,
        *,
        credential: str,
    ) -> ProactiveDeliveryResult: ...


@dataclass(frozen=True, slots=True)
class DeliveryIngressResponse:
    status_code: int
    body: dict[str, object]


class ProactiveDeliveryJsonHandler:
    """Transport-neutral ingress for a consumer-owned authenticated local service.

    The host extracts its transport credential and passes it separately from
    the untrusted JSON body. Inline artifacts are materialized only beneath the
    configured staging root and removed after synchronous delivery returns.
    """

    def __init__(
        self,
        endpoint: ProactiveDeliveryEndpoint,
        *,
        staging_root: str | Path,
        max_inline_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if max_inline_bytes <= 0:
            raise ValueError("max_inline_bytes must be positive")
        self._endpoint = endpoint
        self._staging_root = Path(staging_root).resolve()
        self._max_inline_bytes = max_inline_bytes
        self._locks = KeyedLockRegistry()

    async def handle(
        self,
        payload: Mapping[str, object],
        *,
        credential: str,
    ) -> DeliveryIngressResponse:
        try:
            delivery_id = _required_string(payload, "deliveryId")
        except (ValueError, TypeError) as error:
            return _error_response(400, "invalid_delivery_request", error)
        async with self._locks.hold(delivery_id):
            staging_directory: Path | None = None
            try:
                target = _parse_target(_required_mapping(payload, "target"))
                await self._endpoint.authorize_proactive_target(
                    target,
                    credential=credential,
                )
                intent, staging_directory = await self._parse_intent(
                    payload,
                    target=target,
                )
                result = await self._endpoint.deliver_proactively(
                    intent,
                    credential=credential,
                )
            except DeliveryAuthorizationError as error:
                return _error_response(403, "delivery_unauthorized", error)
            except DeliveryRouteError as error:
                return _error_response(404, "delivery_route_unavailable", error)
            except DeliverySubmissionConflict as error:
                return _error_response(409, "delivery_id_conflict", error)
            except (ContractViolation, ValueError, TypeError) as error:
                return _error_response(400, "invalid_delivery_request", error)
            except OSError as error:
                return _error_response(503, "artifact_staging_failed", error)
            finally:
                if staging_directory is not None:
                    await asyncio.to_thread(
                        shutil.rmtree,
                        staging_directory,
                        True,
                    )
            return DeliveryIngressResponse(
                status_code=200,
                body=_result_json(result),
            )

    async def _parse_intent(
        self,
        payload: Mapping[str, object],
        *,
        target: DeliveryTarget,
    ) -> tuple[DeliveryIntent, Path | None]:
        delivery_id = _required_string(payload, "deliveryId")
        content_payload = _required_sequence(payload, "content")
        if not content_payload:
            raise ValueError("content must contain at least one item")

        parsed_content: list[TextContent | InlineArtifactStagingInput] = []
        inline_artifacts: list[InlineArtifactStagingInput] = []
        total_decoded_bytes = 0
        for index, raw_item in enumerate(content_payload):
            if not isinstance(raw_item, Mapping):
                raise TypeError(f"content[{index}] must be an object")
            item_type = _required_string(raw_item, "type")
            if item_type == "text":
                parsed_content.append(
                    TextContent(
                        _required_string(raw_item, "text"),
                        TextFormat(str(raw_item.get("format", TextFormat.PLAIN.value))),
                    )
                )
                continue
            if item_type != "inlineArtifact":
                raise ValueError(f"content[{index}].type is unsupported")
            artifact = _parse_inline_artifact(raw_item)
            remaining_bytes = self._max_inline_bytes - total_decoded_bytes
            decoded_size = validated_inline_artifact_size(
                artifact,
                max_bytes=remaining_bytes,
                declared_size_error=(f"content[{index}].sizeBytes does not match encoded content"),
            )
            total_decoded_bytes += decoded_size
            if total_decoded_bytes > self._max_inline_bytes:
                raise ValueError("inline artifact payload exceeds configured byte limit")
            inline_artifacts.append(artifact)
            parsed_content.append(artifact)

        staging_directory: Path | None = None
        staged_by_id: dict[str, AttachmentContent] = {}
        if inline_artifacts:
            staging_directory = await asyncio.to_thread(
                create_inline_staging_directory,
                self._staging_root,
            )
            try:
                staged = await asyncio.to_thread(
                    stage_inline_artifacts,
                    staging_directory,
                    inline_artifacts,
                )
            except BaseException:
                await asyncio.to_thread(shutil.rmtree, staging_directory, True)
                raise
            staged_by_id = {item.attachment_id: item for item in staged}
        content = tuple(
            item if isinstance(item, TextContent) else staged_by_id[item.attachment_id]
            for item in parsed_content
        )

        created_at_raw = payload.get("createdAt")
        created_at = (
            _parse_timestamp(created_at_raw) if created_at_raw is not None else datetime.now(UTC)
        )
        reply_to = _optional_string(payload, "replyTo")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be an object")
        return (
            DeliveryIntent(
                delivery_id=delivery_id,
                target=target,
                content=content,
                created_at=created_at,
                reply_to=reply_to,
                metadata=dict(metadata),
            ),
            staging_directory,
        )


def _parse_target(
    payload: Mapping[str, object],
) -> ConversationDeliveryTarget | ThreadRouteDeliveryTarget:
    kind = _required_string(payload, "kind")
    if kind == "conversation":
        return ConversationDeliveryTarget(
            ConversationRef(
                _required_string(payload, "channelInstanceId"),
                _required_string(payload, "nativeConversationId"),
            )
        )
    if kind != "threadRoutes":
        raise ValueError("target.kind is unsupported")
    project_payload = payload.get("projectRef")
    project_ref = None
    if project_payload is not None:
        if not isinstance(project_payload, Mapping):
            raise TypeError("target.projectRef must be an object")
        project_ref = ProjectRef(
            _required_string(project_payload, "applicationInstanceId"),
            _required_string(project_payload, "nativeProjectId"),
        )
    application_instance_id = _required_string(payload, "applicationInstanceId")
    if project_ref is not None and project_ref.application_instance_id != application_instance_id:
        raise ValueError("target Project and Thread must belong to the same Application")
    return ThreadRouteDeliveryTarget(
        ThreadRef(
            application_instance_id,
            _required_string(payload, "nativeThreadId"),
            project_ref,
        ),
        route_id=_optional_string(payload, "routeId"),
    )


def _parse_inline_artifact(payload: Mapping[str, object]) -> InlineArtifactStagingInput:
    size = payload.get("sizeBytes")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        raise ValueError("inlineArtifact.sizeBytes must be a non-negative integer")
    return InlineArtifactStagingInput(
        attachment_id=_required_string(payload, "attachmentId"),
        filename=_required_string(payload, "filename"),
        media_type=_required_string(payload, "mediaType"),
        encoded_content=_required_string(payload, "contentBase64"),
        declared_size=size,
    )


def _required_mapping(
    payload: Mapping[str, object],
    name: str,
) -> Mapping[str, object]:
    value = payload.get(name)
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    return value


def _required_sequence(
    payload: Mapping[str, object],
    name: str,
) -> Sequence[object]:
    value = payload.get(name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be an array")
    return value


def _required_string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(payload: Mapping[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be null or a non-empty string")
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("createdAt must be an RFC 3339 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("createdAt must include a timezone")
    return parsed


def _error_response(
    status_code: int,
    code: str,
    error: BaseException,
) -> DeliveryIngressResponse:
    return DeliveryIngressResponse(
        status_code=status_code,
        body={
            "error": {
                "code": code,
                "message": str(error) or type(error).__name__,
            }
        },
    )


def _result_json(result: ProactiveDeliveryResult) -> dict[str, object]:
    return {
        "deliveryId": result.delivery_id,
        "state": result.state.value,
        "error": result.error,
        "destinations": [
            {
                "deliveryId": destination.delivery_id,
                "routeId": destination.route_id,
                "state": destination.state.value,
                "replayed": destination.replayed,
                "error": destination.error,
                "receipt": (
                    None
                    if destination.receipt is None
                    else {
                        "status": destination.receipt.status.value,
                        "nativeMessageId": destination.receipt.native_message_id,
                        "detail": destination.receipt.detail,
                        "retryAfterSeconds": (destination.receipt.retry_after_seconds),
                        "items": [
                            {
                                "contentIndex": item.content_index,
                                "attachmentId": item.attachment_id,
                                "status": item.status.value,
                                "nativeMessageId": item.native_message_id,
                                "detail": item.detail,
                            }
                            for item in destination.receipt.items
                        ],
                        "segments": [
                            {
                                "segmentIndex": segment.segment_index,
                                "deliveryId": segment.delivery_id,
                                "sourceContentIndexes": list(segment.source_content_indexes),
                                "status": segment.status.value,
                                "nativeMessageId": segment.native_message_id,
                                "detail": segment.detail,
                                "retryAfterSeconds": segment.retry_after_seconds,
                            }
                            for segment in destination.receipt.segments
                        ],
                    }
                ),
            }
            for destination in result.destinations
        ],
    }

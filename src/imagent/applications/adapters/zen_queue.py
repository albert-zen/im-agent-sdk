"""Zen-owned bounded translation of native queued human input evidence."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping

from ...interaction.operations import ContractViolation
from ..contract import (
    QueuedInputReceipt,
    QueuedInputState,
    ThreadRef,
    TurnRef,
    validate_queued_input_receipt,
)

_QUEUE_CAPABILITY = "queued-input-admission-v1"
_INLINE_IMAGE_CAPABILITY = "inline-image-input-v1"
_MAX_SNAPSHOT_ITEMS = 10_000
_FINGERPRINT_DOMAIN = b"imagent:zen-queued-input:v1\0"


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ContractViolation("invalid native queue identity")
    return value


def _queued_input_fingerprint(parts: object) -> str:
    """Same semantic digest for acquired inline bytes and canonical attachments."""

    if not isinstance(parts, list) or not 1 <= len(parts) <= 64:
        raise ContractViolation("queued input requires a bounded content list")
    normalized: list[dict[str, object]] = []
    image_count = 0
    image_bytes = 0
    for part in parts:
        if not isinstance(part, Mapping):
            raise ContractViolation("invalid queued input part")
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            if part.get("skillSource") is not None:
                raise ContractViolation("queued SDK input cannot claim Host skill provenance")
            normalized.append({"type": "text", "text": part["text"]})
            continue
        kind = part.get("type")
        if kind == "image_bytes":
            encoded = part.get("dataBase64")
            if not isinstance(encoded, str) or len(encoded) > 4 * ((20 * 1024 * 1024 + 2) // 3):
                raise ContractViolation("queued image exceeds its encoded byte bound")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, UnicodeError) as cause:
                raise ContractViolation("invalid queued image encoding") from cause
            sha256 = hashlib.sha256(content).hexdigest()
            size = len(content)
            media_type = part.get("mediaType")
        elif kind == "image":
            ref = part.get("attachment")
            if not isinstance(ref, Mapping) or ref.get("type") != "attachment":
                raise ContractViolation("queued canonical image has no attachment identity")
            sha256, size, media_type = (
                ref.get("sha256"),
                ref.get("byteLength"),
                ref.get("mediaType"),
            )
            if not isinstance(sha256, str) or re.fullmatch("[0-9a-f]{64}", sha256) is None:
                raise ContractViolation("invalid queued image byte digest")
        else:
            raise ContractViolation("unsupported queued input part")
        if (
            not isinstance(media_type, str)
            or media_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}
            or type(size) is not int
            or not 0 < size <= 20 * 1024 * 1024
        ):
            raise ContractViolation("invalid queued image media or byte size")
        image_count += 1
        image_bytes += size
        if image_count > 16 or image_bytes > 32 * 1024 * 1024:
            raise ContractViolation("queued image aggregate capacity exceeded")
        normalized.append(
            {"type": "image", "sha256": sha256, "mediaType": media_type, "byteLength": size}
        )
    return hashlib.sha256(
        _FINGERPRINT_DOMAIN
        + json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _native_input_capabilities(result: Mapping[str, object]) -> frozenset[str]:
    _identifier(result.get("processEpoch"))
    capabilities = result.get("capabilities", [])
    if (
        not isinstance(capabilities, list)
        or len(capabilities) > 128
        or any(not isinstance(item, str) or not 0 < len(item) <= 128 for item in capabilities)
    ):
        raise ContractViolation("invalid native input capabilities")
    return frozenset(capabilities)


def _queued_admission_receipt(
    result: object,
    thread_ref: ThreadRef,
    client_message_id: str,
    input_fingerprint: str,
) -> QueuedInputReceipt:
    if not isinstance(result, Mapping):
        raise ContractViolation("native queue acknowledgement must be an object")
    admission = result.get("admission")
    if not isinstance(admission, Mapping):
        raise ContractViolation("native queue response has no admission receipt")
    client_id = _identifier(admission.get("clientId"))
    if client_id != client_message_id:
        raise ContractViolation("native queue response belongs to a different input")
    item_id = _identifier(admission.get("queuedItemId"))
    state = admission.get("state")
    if not isinstance(state, str) or state not in {"queued", "admitted"}:
        raise ContractViolation("invalid native queue admission state")
    turn_id = admission.get("turnId")
    if state == "admitted":
        turn_ref = TurnRef(thread_ref, _identifier(turn_id))
    else:
        if turn_id is not None:
            raise ContractViolation("queued admission cannot invent an execution Turn")
        turn_ref = None
    outer_turn_id = result.get("turnId")
    if outer_turn_id is not None and (turn_ref is None or outer_turn_id != turn_ref.turn_id):
        raise ContractViolation("native queue response has conflicting Turn identities")
    receipt = QueuedInputReceipt(
        thread_ref,
        client_id,
        item_id,
        QueuedInputState(state),
        turn_ref,
        input_fingerprint=input_fingerprint,
    )
    validate_queued_input_receipt(receipt)
    return receipt


def _queued_receipt_from_items(
    items: object,
    thread_ref: ThreadRef,
    client_message_id: str,
) -> QueuedInputReceipt | None:
    """Read positive canonical evidence only; absence never authorizes resend."""

    _identifier(client_message_id)
    if not isinstance(items, list) or len(items) > _MAX_SNAPSHOT_ITEMS:
        raise ContractViolation("native queue recovery requires a complete bounded item list")
    queued: Mapping[str, object] | None = None
    admitted: Mapping[str, object] | None = None
    cancelled: Mapping[str, object] | None = None
    identities: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or item.get("threadId") != thread_ref.thread_id:
            raise ContractViolation("native queue snapshot contains a foreign or invalid item")
        item_id = _identifier(item.get("id"))
        if item_id in identities:
            raise ContractViolation("native queue snapshot repeats a canonical item")
        identities.add(item_id)
        if item.get("clientId") != client_message_id:
            continue
        kind = item.get("type")
        if kind == "user_message_queued":
            if queued is not None or item.get("deliveryMode") is not None:
                raise ContractViolation("native queue identity conflicts with its admission mode")
            queued = item
        elif kind == "user_message":
            if admitted is not None:
                raise ContractViolation("native queue input has multiple admitted Turns")
            admitted = item
        elif kind == "user_message_queue_cancelled":
            if cancelled is not None:
                raise ContractViolation("native queue input has conflicting cancellation facts")
            cancelled = item
        # Host and cross-Agent source identities never establish human input.
    if queued is None:
        if admitted is not None or cancelled is not None:
            raise ContractViolation("native input has no original human queued lineage")
        return None
    queued_id = _identifier(queued.get("id"))
    fingerprint = _queued_input_fingerprint(queued.get("input"))
    if cancelled is not None:
        if admitted is not None or cancelled.get("queuedItemId") != queued_id:
            raise ContractViolation("native cancellation conflicts with queued input identity")
        receipt = QueuedInputReceipt(
            thread_ref,
            client_message_id,
            queued_id,
            QueuedInputState.CANCELLED,
            input_fingerprint=fingerprint,
        )
    elif admitted is not None:
        receipt = QueuedInputReceipt(
            thread_ref,
            client_message_id,
            queued_id,
            QueuedInputState.ADMITTED,
            TurnRef(thread_ref, _identifier(admitted.get("turnId"))),
            input_fingerprint=fingerprint,
        )
    else:
        receipt = QueuedInputReceipt(
            thread_ref,
            client_message_id,
            queued_id,
            QueuedInputState.QUEUED,
            input_fingerprint=fingerprint,
        )
    validate_queued_input_receipt(receipt)
    return receipt

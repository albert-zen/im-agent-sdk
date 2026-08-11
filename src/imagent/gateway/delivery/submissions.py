from __future__ import annotations

import hashlib
import json

from ...applications.contract import ThreadRef
from ...interaction.media import LocalPath, RemoteUrl
from ...interaction.messages import Content, ConversationRef, Metadata, TextContent
from ...interaction.operations import ContractViolation, require_identifier
from ..persistence.state_contracts import (
    DeliverySubmissionOrigin,
    _validate_conversation_ref,
)
from .proactive import (
    ConversationDeliveryTarget,
    DeliveryIntent,
    DeliveryTarget,
    validate_delivery_intent,
)


def _canonical_metadata(metadata: Metadata) -> object:
    try:
        return json.loads(
            json.dumps(
                dict(metadata),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as error:
        raise ContractViolation("delivery metadata must be JSON-compatible") from error


def derive_delivery_target_fingerprint(target: DeliveryTarget) -> str:
    if isinstance(target, ConversationDeliveryTarget):
        identity: object = [
            target.kind.value,
            target.conversation_ref.channel_instance_id,
            target.conversation_ref.native_conversation_id,
        ]
    else:
        identity = [
            target.kind.value,
            _thread_identity(target.thread_ref),
            target.route_id,
        ]
    return _sha256_identity("target", identity)


def derive_delivery_payload_fingerprint(intent: DeliveryIntent) -> str:
    validate_delivery_intent(intent)
    identity = {
        "content": [_content_identity(item) for item in intent.content],
        "reply_to": intent.reply_to,
        "metadata": _canonical_metadata(intent.metadata),
    }
    return _sha256_identity("payload", identity)


def derive_destination_delivery_id(
    root_submission_id: str,
    conversation_ref: ConversationRef,
) -> str:
    require_identifier(root_submission_id, "submission_id")
    _validate_conversation_ref(conversation_ref)
    return _sha256_identity(
        "destination",
        [
            root_submission_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
    )


def derive_delivery_submission_id(
    origin: DeliverySubmissionOrigin,
    principal_id: str,
    delivery_id: str,
) -> str:
    if not isinstance(origin, DeliverySubmissionOrigin):
        raise ContractViolation("delivery submission origin is invalid")
    require_identifier(principal_id, "principal_id")
    require_identifier(delivery_id, "delivery_id")
    # The admitting principal remains part of the durable reservation, but it
    # cannot be part of the lookup identity: credential rotation must never
    # turn one caller delivery ID into a second native execution.
    return _sha256_identity(
        "submission",
        [origin.value, delivery_id],
    )


def _content_identity(content: Content) -> object:
    if isinstance(content, TextContent):
        return {
            "kind": "text",
            "text": content.text,
            "format": content.format.value,
        }
    source = content.source
    if isinstance(source, LocalPath):
        digest = content.metadata.get("sha256")
        source_identity: object = (
            {"kind": source.kind.value, "sha256": digest}
            if isinstance(digest, str) and digest
            else {"kind": source.kind.value, "path": source.path}
        )
    elif isinstance(source, RemoteUrl):
        source_identity = {"kind": source.kind.value, "url": source.url}
    else:
        source_identity = {
            "kind": source.kind.value,
            "handle_id": source.handle_id,
        }
    return {
        "kind": "attachment",
        "attachment_id": content.attachment_id,
        "media_type": content.media_type,
        "source": source_identity,
        "filename": content.filename,
        "size_bytes": content.size_bytes,
        "metadata": _canonical_metadata(content.metadata),
    }


def _sha256_identity(label: str, identity: object) -> str:
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return f"imagent:delivery-{label}:sha256:{digest}"


def _thread_identity(thread_ref: ThreadRef) -> object:
    return [
        thread_ref.project_ref.application_instance_id,
        thread_ref.project_ref.project_id,
        thread_ref.thread_id,
    ]

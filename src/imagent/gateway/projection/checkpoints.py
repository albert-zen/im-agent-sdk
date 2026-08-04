"""Stable completion delivery identity owned by Gateway projection checkpoints."""

import hashlib
import json

from ...applications.contract import ThreadRef
from ...interaction.messages import ConversationRef


def derive_projection_delivery_id(
    conversation_ref: ConversationRef,
    thread_ref: ThreadRef,
    agent_item_id: str,
    *,
    segment_index: int = 0,
) -> str:
    """Derive one stable per-destination authoritative-item delivery identity."""
    identity = json.dumps(
        [
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
            thread_ref.application_instance_id,
            (
                thread_ref.project_ref.native_project_id
                if thread_ref.project_ref is not None
                else None
            ),
            thread_ref.native_thread_id,
            agent_item_id,
            segment_index,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:delivery:sha256:{digest}"

"""Historical input-dispatch validation helper owner."""

from __future__ import annotations

import hashlib
import json

from ..interaction.messages import ConversationRef
from ..interaction.operations import require_identifier


def derive_client_message_id(
    conversation: ConversationRef,
    channel_message_id: str,
) -> str:
    require_identifier(channel_message_id, "channel_message_id")
    identity = json.dumps(
        [
            conversation.channel_instance_id,
            conversation.native_conversation_id,
            channel_message_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:client-message:sha256:{digest}"

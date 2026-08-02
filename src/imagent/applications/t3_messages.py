from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from ..contracts import AgentMessage, MessageRole, TextContent, TextFormat, ThreadRef


def t3_agent_message(
    thread_ref: ThreadRef,
    message: Mapping[str, object],
) -> AgentMessage | None:
    role = {
        "user": MessageRole.USER,
        "assistant": MessageRole.ASSISTANT,
        "system": MessageRole.SYSTEM,
    }.get(str(message.get("role") or ""))
    text = str(message.get("text") or "").strip()
    message_id = str(message.get("id") or message.get("messageId") or "")
    if role is None or not text or not message_id:
        return None
    return AgentMessage(
        agent_item_id=message_id,
        thread_ref=thread_ref,
        role=role,
        content=(TextContent(text, TextFormat.MARKDOWN),),
        created_at=_parse_datetime(message.get("createdAt") or message.get("updatedAt")),
        metadata={
            "turn_id": str(message.get("turnId") or ""),
            "streaming": bool(message.get("streaming")),
            "native_application": "t3",
        },
    )


def t3_activity_message(
    thread_ref: ThreadRef,
    activity: Mapping[str, object],
) -> AgentMessage | None:
    kind = str(activity.get("kind") or "")
    if kind in {
        "approval.requested",
        "approval.resolved",
        "user-input.requested",
        "user-input.resolved",
    }:
        return None
    summary = str(activity.get("summary") or "").strip()
    payload_value = activity.get("payload")
    payload = payload_value if isinstance(payload_value, Mapping) else {}
    detail = str(
        payload.get("detail") or payload.get("message") or payload.get("summary") or ""
    ).strip()
    text = "\n\n".join(part for part in (summary, detail) if part)
    activity_id = str(activity.get("id") or "")
    if not text or not activity_id:
        return None
    return AgentMessage(
        agent_item_id=activity_id,
        thread_ref=thread_ref,
        role=MessageRole.ASSISTANT,
        content=(TextContent(text, TextFormat.MARKDOWN),),
        created_at=_parse_datetime(activity.get("createdAt")),
        metadata={
            "kind": kind,
            "turn_id": str(activity.get("turnId") or ""),
            "native_application": "t3",
            "source": "activity",
        },
    )


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)

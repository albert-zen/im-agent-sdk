from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from .contract import ThreadStatus, TurnStatus


def native_object(result: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = result.get(key)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"application result did not contain {key}")
    return value


def native_turn_id(result: Mapping[str, object]) -> str | None:
    turn = result.get("turn")
    if isinstance(turn, Mapping):
        return optional_string(turn.get("id") or turn.get("turnId"))
    return optional_string(result.get("turnId"))


def native_list(
    result: Mapping[str, object],
    *keys: str,
) -> tuple[Mapping[str, object], ...]:
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, Mapping))
    raise RuntimeError("application result did not contain a thread list")


def optional_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def thread_status(value: object) -> ThreadStatus:
    if isinstance(value, Mapping):
        value = value.get("type") or value.get("status")
    normalized = str(value or "").replace("-", "_").casefold()
    return {
        "idle": ThreadStatus.IDLE,
        "notloaded": ThreadStatus.IDLE,
        "running": ThreadStatus.RUNNING,
        "active": ThreadStatus.RUNNING,
        "inprogress": ThreadStatus.RUNNING,
        "in_progress": ThreadStatus.RUNNING,
        "waiting_for_approval": ThreadStatus.WAITING_FOR_APPROVAL,
        "waiting_for_input": ThreadStatus.WAITING_FOR_INPUT,
        "completed": ThreadStatus.COMPLETED,
        "failed": ThreadStatus.FAILED,
        "interrupted": ThreadStatus.INTERRUPTED,
    }.get(normalized, ThreadStatus.UNKNOWN)


def turn_status(value: object) -> TurnStatus:
    if isinstance(value, Mapping):
        value = value.get("type") or value.get("status")
    normalized = str(value or "").replace("-", "_").casefold()
    return {
        "idle": TurnStatus.IDLE,
        "running": TurnStatus.RUNNING,
        "active": TurnStatus.RUNNING,
        "inprogress": TurnStatus.RUNNING,
        "in_progress": TurnStatus.RUNNING,
        "working": TurnStatus.RUNNING,
        "completed": TurnStatus.COMPLETED,
        "failed": TurnStatus.FAILED,
        "error": TurnStatus.FAILED,
        "interrupted": TurnStatus.INTERRUPTED,
        "cancelled": TurnStatus.INTERRUPTED,
        "canceled": TurnStatus.INTERRUPTED,
    }.get(normalized, TurnStatus.UNKNOWN)


def turn_list(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    for key in ("turns", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, Mapping))
    thread = payload.get("thread")
    if isinstance(thread, Mapping):
        turns = thread.get("turns")
        if isinstance(turns, list):
            return tuple(item for item in turns if isinstance(item, Mapping))
    return ()


def turn_items(turn: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    items = turn.get("items")
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, Mapping))


def turn_id(turn: Mapping[str, object]) -> str:
    value = str(turn.get("id") or turn.get("turnId") or "")
    if not value:
        raise RuntimeError("native turn did not contain an id")
    return value


def normalized_item_type(item: Mapping[str, object]) -> str:
    return (
        str(item.get("type") or item.get("kind") or "").replace("_", "").replace("-", "").casefold()
    )


def is_agent_item(item: Mapping[str, object]) -> bool:
    item_type = normalized_item_type(item)
    return "agent" in item_type or "assistant" in item_type


def item_text(item: Mapping[str, object]) -> str:
    text = item.get("text")
    if isinstance(text, str):
        return text.strip()
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, Mapping) and part.get("text")
        ).strip()
    return ""


def turn_error(turn: Mapping[str, object]) -> str | None:
    error = turn.get("error")
    if isinstance(error, Mapping):
        return optional_string(error.get("message") or error.get("error"))
    return optional_string(error)


def turn_updated_at(turn: Mapping[str, object]) -> datetime | None:
    value = turn.get("updatedAt") or turn.get("completedAt") or turn.get("createdAt")
    return parse_optional_datetime(value)


def parse_datetime(value: object) -> datetime:
    return parse_optional_datetime(value) or datetime.now(UTC)


def parse_optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_unsupported_method_error(error: Exception) -> bool:
    if getattr(error, "code", None) == -32601:
        return True
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "method not found",
            "unknown method",
            "not implemented",
            "unsupported method",
            "requires experimentalapi",
            "experimentalapi capability",
            "no handler",
        )
    )

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ...contract import ThreadStatus, TurnStatus


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


@dataclass(slots=True)
class AppServerEvent:
    direction: str
    method: str
    category: str
    kind: str
    payload: dict[str, Any]
    thread_id: str = ""
    turn_id: str = ""
    item_id: str = ""
    request_id: str | None = None
    process_id: str | None = None
    watch_id: str | None = None


_EVENT_KINDS = {
    "item/started": "item_started",
    "item/mcpToolCall/progress": "mcp_tool_progress",
    "item/commandExecution/requestApproval": "approval_request",
    "item/fileChange/requestApproval": "approval_request",
    "item/permissions/requestApproval": "approval_request",
    "item/tool/requestUserInput": "question_request",
    "item/agentMessage/delta": "agent_delta",
    "item/reasoning/summaryTextDelta": "reasoning_summary_text_delta",
    "item/reasoning/summaryPartAdded": "reasoning_summary_part_added",
    "item/reasoning/textDelta": "reasoning_text_delta",
    "turn/started": "turn_started",
    "serverRequest/resolved": "request_resolved",
    "turn/plan/updated": "plan_updated",
    "turn/diff/updated": "diff_updated",
    "thread/name/updated": "thread_name_updated",
    "item/completed": "item_completed",
    "turn/completed": "turn_completed",
    "thread/status/changed": "thread_status_changed",
    "thread/goal/updated": "thread_goal_updated",
    "thread/goal/cleared": "thread_goal_cleared",
    "thread/compacted": "thread_compacted",
    "model/rerouted": "model_rerouted",
    "currentTime/read": "current_time_read",
    "configWarning": "config_warning",
    "deprecationNotice": "deprecation_notice",
}

SUPPORTED_SERVER_REQUEST_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/tool/requestUserInput",
        "item/permissions/requestApproval",
    }
)

EXPERIMENTAL_SUPPORTED_SERVER_REQUEST_METHODS = frozenset(
    {
        "currentTime/read",
    }
)

HOST_DELEGATED_SERVER_REQUEST_METHODS = frozenset(
    {
        # Dynamic tools are registered and implemented by the client that
        # created the native thread. A shared external App Server broadcasts
        # the request to every thread subscriber. A configured host may
        # translate native-mappable Desktop thread tools only when its
        # topology declares it to be their host; it never owns thread state.
        "item/tool/call",
    }
)

REJECTED_SERVER_REQUEST_METHODS = frozenset(
    {
        "mcpServer/elicitation/request",
        "account/chatgptAuthTokens/refresh",
        "attestation/generate",
        "applyPatchApproval",
        "execCommandApproval",
    }
)

SERVER_REQUEST_METHODS = (
    SUPPORTED_SERVER_REQUEST_METHODS
    | EXPERIMENTAL_SUPPORTED_SERVER_REQUEST_METHODS
    | HOST_DELEGATED_SERVER_REQUEST_METHODS
    | REJECTED_SERVER_REQUEST_METHODS
)

_CATEGORY_PREFIXES = (
    ("thread/realtime/", "realtime"),
    ("thread/", "thread"),
    ("turn/", "turn"),
    ("item/", "item"),
    ("rawResponseItem/", "item"),
    ("command/exec/", "command_exec"),
    ("command/", "command_exec"),
    ("fs/", "fs"),
    ("skills/", "skills"),
    ("app/", "app"),
    ("plugin/", "plugin"),
    ("mcpServer/", "mcp"),
    ("mcpTool", "mcp"),
    ("account/", "account"),
    ("currentTime/", "system"),
    ("config/", "config"),
    ("windowsSandbox/", "system"),
    ("windows/", "system"),
    ("model/", "system"),
    ("hook/", "system"),
    ("fuzzyFileSearch/", "system"),
)

_SYSTEM_METHODS = {"configWarning", "deprecationNotice", "error"}


def normalize_appserver_message(message: dict[str, Any]) -> AppServerEvent:
    method = str(message.get("method", ""))
    payload = message.get("params", {})
    if not isinstance(payload, dict):
        payload = {}
    item_value = payload.get("item")
    item: dict[str, Any] = item_value if isinstance(item_value, dict) else {}
    turn_value = payload.get("turn")
    turn: dict[str, Any] = turn_value if isinstance(turn_value, dict) else {}
    direction = "server_request" if method and "id" in message else "notification"
    request_id = payload.get("requestId") or payload.get("_request_id")
    if request_id is None and direction == "server_request":
        request_id = message.get("id")
    item_id = payload.get("itemId") or item.get("id")
    return AppServerEvent(
        direction=direction,
        method=method,
        category=_categorize_method(method),
        kind=_EVENT_KINDS.get(method, "unknown"),
        payload=payload,
        thread_id=str(payload.get("threadId", "") or ""),
        turn_id=str(payload.get("turnId") or turn.get("id") or ""),
        item_id=str(item_id or ""),
        request_id=str(request_id) if request_id is not None else None,
        process_id=_string_or_none(payload.get("processId")),
        watch_id=_string_or_none(payload.get("watchId")),
    )


def _categorize_method(method: str) -> str:
    if method in _SYSTEM_METHODS:
        return "system"
    for prefix, category in _CATEGORY_PREFIXES:
        if method.startswith(prefix):
            return category
    return "unknown"


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)

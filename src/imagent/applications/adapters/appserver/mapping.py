from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from ...contract import ThreadStatus, TurnStatus

MAX_NATIVE_TEXT_CHARACTERS = 16_384
MAX_NATIVE_COLLECTION_ITEMS = 256
MAX_NATIVE_MAPPING_KEYS = 64
MAX_NATIVE_MAPPING_KEY_CHARACTERS = 128
MAX_NATIVE_NESTING = 16
MAX_NATIVE_TOTAL_VALUES = 4_096
MAX_NATIVE_CONTENT_PARTS = 64
MAX_NATIVE_ID_CHARACTERS = 512
APP_SERVER_MAPPING_ERROR_MESSAGE = "invalid App Server native mapping"

_DISPATCH_POSITION_KEY = "imagent_dispatch_position"


class AppServerMappingError(ValueError):
    """A native App Server fact cannot safely become an internal fact."""

    def __init__(self) -> None:
        super().__init__(APP_SERVER_MAPPING_ERROR_MESSAGE)


@dataclass(slots=True)
class _NormalizationBudget:
    total_values: int = 0

    def visit(self) -> None:
        self.total_values += 1
        if self.total_values > MAX_NATIVE_TOTAL_VALUES:
            raise AppServerMappingError


def native_mapping(value: object) -> dict[str, object]:
    """Validate and copy one finite native JSON object."""

    budget = _NormalizationBudget()
    if not isinstance(value, Mapping):
        raise AppServerMappingError
    return _normalize_mapping(value, budget=budget, depth=1)


def native_object(result: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = native_mapping(result).get(key)
    if not isinstance(value, Mapping):
        raise AppServerMappingError
    return value


def native_turn_id(result: Mapping[str, object]) -> str | None:
    normalized = native_mapping(result)
    turn = normalized.get("turn")
    return _consistent_identity(
        _identity_from(normalized, "turnId"),
        _identity_from(turn, "id", "turnId") if isinstance(turn, Mapping) else None,
    )


def native_list(
    result: Mapping[str, object],
    *keys: str,
) -> tuple[Mapping[str, object], ...]:
    normalized = native_mapping(result)
    for key in keys:
        value = normalized.get(key)
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, Mapping))
    raise AppServerMappingError


def optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _native_text(value).strip() or None


def thread_status(value: object) -> ThreadStatus:
    normalized = _status_text(value)
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
    normalized = _status_text(value)
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
    normalized = native_mapping(payload)
    for key in ("turns", "data"):
        value = normalized.get(key)
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, Mapping))
    thread = normalized.get("thread")
    if isinstance(thread, Mapping):
        turns = thread.get("turns")
        if isinstance(turns, list):
            return tuple(item for item in turns if isinstance(item, Mapping))
    return ()


def turn_items(turn: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    items = native_mapping(turn).get("items")
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, Mapping))


def thread_id(thread: Mapping[str, object]) -> str:
    value = _identity_from(native_mapping(thread), "id", "threadId")
    if value is None:
        raise AppServerMappingError
    return value


def turn_id(turn: Mapping[str, object]) -> str:
    value = _identity_from(native_mapping(turn), "id", "turnId")
    if value is None:
        raise AppServerMappingError
    return value


def item_id(item: Mapping[str, object]) -> str:
    value = _identity_from(_native_item_mapping(item), "id", "itemId")
    if value is None:
        raise AppServerMappingError
    return value


def normalized_item_type(item: Mapping[str, object]) -> str:
    normalized = _native_item_mapping(item)
    value = normalized.get("type")
    if value is None:
        value = normalized.get("kind")
    if not isinstance(value, str):
        return ""
    return _native_text(value).replace("_", "").replace("-", "").casefold()


def is_agent_item(item: Mapping[str, object]) -> bool:
    item_type = normalized_item_type(item)
    return "agent" in item_type or "assistant" in item_type


def item_text(item: Mapping[str, object]) -> str:
    normalized = _native_item_mapping(item)
    text = normalized.get("text")
    if isinstance(text, str):
        return _native_text(text).strip()
    if text is not None:
        raise AppServerMappingError
    content = normalized.get("content")
    if isinstance(content, str):
        return _native_text(content).strip()
    if content is None:
        return ""
    if not isinstance(content, list):
        raise AppServerMappingError
    parts: list[str] = []
    total = 0
    for part in content:
        if not isinstance(part, Mapping):
            continue
        value = part.get("text")
        if value is None:
            continue
        text_part = _native_text(value)
        total += len(text_part)
        if parts:
            total += 1
        if total > MAX_NATIVE_TEXT_CHARACTERS:
            raise AppServerMappingError
        parts.append(text_part)
    return "\n".join(parts).strip()


def _native_item_mapping(item: Mapping[str, object]) -> dict[str, object]:
    _validate_content_aggregation(item.get("content"))
    return native_mapping(item)


def _validate_content_aggregation(value: object) -> None:
    """Reject an overlarge native content list before its generic JSON copy."""

    if value is None or isinstance(value, str):
        return
    if not isinstance(value, list) or len(value) > MAX_NATIVE_CONTENT_PARTS:
        raise AppServerMappingError
    total = 0
    has_part = False
    for part in value:
        if not isinstance(part, Mapping):
            continue
        text = part.get("text")
        if text is None:
            continue
        text_part = _native_text(text)
        if has_part:
            total += 1
        total += len(text_part)
        if total > MAX_NATIVE_TEXT_CHARACTERS:
            raise AppServerMappingError
        has_part = True


def turn_error(turn: Mapping[str, object]) -> str | None:
    error = native_mapping(turn).get("error")
    if isinstance(error, Mapping):
        return optional_string(error.get("message") or error.get("error"))
    return optional_string(error)


def turn_updated_at(turn: Mapping[str, object]) -> datetime | None:
    normalized = native_mapping(turn)
    value = (
        normalized.get("updatedAt") or normalized.get("completedAt") or normalized.get("createdAt")
    )
    return parse_optional_datetime(value)


def parse_datetime(value: object) -> datetime:
    return parse_optional_datetime(value) or datetime.now(UTC)


def parse_optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(_native_text(value).replace("Z", "+00:00"))
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


@dataclass(frozen=True, slots=True)
class AppServerEvent:
    direction: str
    method: str
    category: str
    kind: str
    payload: dict[str, object]
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    event_id: str | None = None
    request_id: str | int | None = None
    transport_request_id: str | int | None = None
    connection_epoch: int | None = None
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
_THREAD_REQUIRED_EVENT_METHODS = frozenset(
    {
        "item/agentMessage/delta",
        "item/completed",
        "turn/completed",
        "turn/plan/updated",
        "turn/diff/updated",
        "thread/status/changed",
        "thread/compacted",
        "model/rerouted",
    }
)
_TURN_REQUIRED_EVENT_METHODS = frozenset(
    {"item/agentMessage/delta", "item/completed", "turn/completed"}
)
_ITEM_REQUIRED_EVENT_METHODS = frozenset({"item/completed"})


def normalize_appserver_message(message: Mapping[str, object]) -> AppServerEvent:
    normalized_message = _normalize_appserver_message_root(message)
    method = normalized_message.get("method")
    if not isinstance(method, str) or not method:
        raise AppServerMappingError
    method = _native_text(method)
    if not method.strip():
        raise AppServerMappingError
    payload_value = normalized_message.get("params", {})
    if not isinstance(payload_value, Mapping):
        raise AppServerMappingError
    payload = dict(payload_value)
    if any(key in payload for key in ("_connection_epoch", "_transport_request_id", "_request_id")):
        raise AppServerMappingError
    message_epoch = _connection_epoch(normalized_message.get("_connection_epoch"))
    connection_epoch = message_epoch
    direction = "server_request" if "id" in normalized_message else "notification"
    transport_request_id: str | int | None = None
    request_id: str | int | None = None
    payload_request_id: str | int | None = None
    if "requestId" in payload:
        payload_request_id = _transport_request_id(payload.get("requestId"))
    if direction == "server_request":
        transport_request_id = _transport_request_id(normalized_message.get("id"))
        if payload_request_id is not None and payload_request_id != transport_request_id:
            raise AppServerMappingError
        request_id = transport_request_id

    thread = payload.get("thread")
    item = payload.get("item")
    turn = payload.get("turn")
    if thread is not None and not isinstance(thread, Mapping):
        thread = None
    if item is not None and not isinstance(item, Mapping):
        item = None
    if turn is not None and not isinstance(turn, Mapping):
        turn = None
    thread_id = _consistent_identity(
        _identity_from(payload, "threadId"),
        _identity_from(thread, "id", "threadId") if thread is not None else None,
    )
    turn_id = _consistent_identity(
        _identity_from(payload, "turnId"),
        _identity_from(turn, "id", "turnId") if turn is not None else None,
    )
    item_id = _consistent_identity(
        _identity_from(payload, "itemId"),
        _identity_from(item, "id", "itemId") if item is not None else None,
    )
    event_id = _identity_from(payload, "eventId", "event_id")
    if method == "serverRequest/resolved":
        request_id = _transport_request_id(payload_request_id)
    elif request_id is None:
        request_id = payload_request_id

    _validate_event_identity(
        method=method,
        direction=direction,
        payload=payload,
        thread_id=thread_id,
        turn_id=turn_id,
        item_id=item_id,
        request_id=request_id,
        transport_request_id=transport_request_id,
        connection_epoch=connection_epoch,
    )
    return AppServerEvent(
        direction=direction,
        method=method,
        category=_categorize_method(method),
        kind=_EVENT_KINDS.get(method, "unknown"),
        payload=payload,
        thread_id=thread_id,
        turn_id=turn_id,
        item_id=item_id,
        event_id=event_id,
        request_id=request_id,
        transport_request_id=transport_request_id,
        connection_epoch=connection_epoch,
        process_id=optional_string(payload.get("processId")),
        watch_id=optional_string(payload.get("watchId")),
    )


def derive_appserver_event_id(
    application_instance_id: str,
    *,
    project_id: str,
    event_type: str,
    thread_id: str | None = None,
    turn_id: str | None = None,
    item_id: str | None = None,
    request_id: str | int | None = None,
    connection_epoch: int | None = None,
) -> str:
    """Create a bounded canonical event identity from validated native IDs."""

    _required_identity(application_instance_id)
    _required_identity(project_id)
    _native_text(event_type)
    for identity in (thread_id, turn_id, item_id):
        if identity is not None:
            _required_identity(identity)
    if request_id is not None:
        _transport_request_id(request_id)
    if connection_epoch is not None:
        _connection_epoch(connection_epoch)
    encoded = json.dumps(
        [
            application_instance_id,
            project_id,
            event_type,
            thread_id,
            turn_id,
            item_id,
            request_id,
            connection_epoch,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"appserver:sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _normalize_appserver_message_root(message: Mapping[str, object]) -> dict[str, object]:
    budget = _NormalizationBudget()
    return _normalize_mapping(
        cast(Mapping[object, object], message),
        budget=budget,
        depth=1,
        ignored_keys=frozenset({_DISPATCH_POSITION_KEY}),
    )


def _normalize_native_value(
    value: object,
    *,
    budget: _NormalizationBudget,
    depth: int,
    item_context: bool = False,
) -> object:
    if isinstance(value, str):
        budget.visit()
        return _native_text(value)
    if value is None or isinstance(value, (bool, int)):
        budget.visit()
        return value
    if isinstance(value, float):
        budget.visit()
        if not math.isfinite(value):
            raise AppServerMappingError
        return value
    if isinstance(value, Mapping):
        return _normalize_mapping(
            value,
            budget=budget,
            depth=depth + 1,
            item_context=item_context,
        )
    if isinstance(value, list):
        return _normalize_list(
            value,
            budget=budget,
            depth=depth + 1,
            item_context=item_context,
        )
    raise AppServerMappingError


def _normalize_mapping(
    value: Mapping[object, object],
    *,
    budget: _NormalizationBudget,
    depth: int,
    ignored_keys: frozenset[str] = frozenset(),
    item_context: bool = False,
) -> dict[str, object]:
    budget.visit()
    if depth > MAX_NATIVE_NESTING:
        raise AppServerMappingError
    key_count = 0
    for key in value:
        if not isinstance(key, str) or not key or len(key) > MAX_NATIVE_MAPPING_KEY_CHARACTERS:
            raise AppServerMappingError
        if key in ignored_keys:
            continue
        key_count += 1
        if key_count > MAX_NATIVE_MAPPING_KEYS:
            raise AppServerMappingError
    if item_context:
        _validate_content_aggregation(value.get("content"))
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if key in ignored_keys:
            continue
        if not isinstance(key, str):
            raise AppServerMappingError
        if key == "items" and isinstance(item, list):
            normalized[key] = _normalize_list(
                item,
                budget=budget,
                depth=depth + 1,
                item_context=True,
            )
            continue
        normalized[key] = _normalize_native_value(
            item,
            budget=budget,
            depth=depth,
            item_context=key == "item",
        )
    return normalized


def _normalize_list(
    value: list[object],
    *,
    budget: _NormalizationBudget,
    depth: int,
    item_context: bool = False,
) -> list[object]:
    budget.visit()
    if depth > MAX_NATIVE_NESTING or len(value) > MAX_NATIVE_COLLECTION_ITEMS:
        raise AppServerMappingError
    return [
        _normalize_native_value(
            item,
            budget=budget,
            depth=depth,
            item_context=item_context,
        )
        for item in value
    ]


def _native_text(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_NATIVE_TEXT_CHARACTERS:
        raise AppServerMappingError
    return value


def _required_identity(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NATIVE_ID_CHARACTERS:
        raise AppServerMappingError
    return value


def _identity_from(
    mapping: Mapping[str, object] | None,
    *keys: str,
) -> str | None:
    if mapping is None:
        return None
    identity: str | None = None
    for key in keys:
        if key in mapping and mapping[key] is not None:
            candidate = _required_identity(mapping[key])
            identity = _consistent_identity(identity, candidate)
    return identity


def _consistent_identity(*identities: str | None) -> str | None:
    """Return one exact native identity, rejecting conflicting aliases."""

    identity: str | None = None
    for candidate in identities:
        if candidate is None:
            continue
        if identity is not None and candidate != identity:
            raise AppServerMappingError
        identity = candidate
    return identity


def _transport_request_id(value: object) -> str | int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AppServerMappingError
    if isinstance(value, str):
        return _required_identity(value)
    if len(str(value)) > MAX_NATIVE_ID_CHARACTERS:
        raise AppServerMappingError
    return value


def _connection_epoch(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AppServerMappingError
    return value


def _status_text(value: object) -> str:
    if isinstance(value, Mapping):
        normalized = native_mapping(value)
        value = normalized.get("type") or normalized.get("status")
    if not isinstance(value, str):
        return ""
    return _native_text(value).replace("-", "_").casefold()


def _validate_event_identity(
    *,
    method: str,
    direction: str,
    payload: Mapping[str, object],
    thread_id: str | None,
    turn_id: str | None,
    item_id: str | None,
    request_id: str | int | None,
    transport_request_id: str | int | None,
    connection_epoch: int | None,
) -> None:
    if method in _THREAD_REQUIRED_EVENT_METHODS and thread_id is None:
        raise AppServerMappingError
    if method in _TURN_REQUIRED_EVENT_METHODS and turn_id is None:
        raise AppServerMappingError
    if method in _ITEM_REQUIRED_EVENT_METHODS:
        if item_id is None or not isinstance(payload.get("item"), Mapping):
            raise AppServerMappingError
    if method == "serverRequest/resolved" and request_id is None:
        raise AppServerMappingError
    if direction == "server_request" and method in SUPPORTED_SERVER_REQUEST_METHODS:
        if (
            thread_id is None
            or turn_id is None
            or transport_request_id is None
            or connection_epoch is None
        ):
            raise AppServerMappingError


def _categorize_method(method: str) -> str:
    if method in _SYSTEM_METHODS:
        return "system"
    for prefix, category in _CATEGORY_PREFIXES:
        if method.startswith(prefix):
            return category
    return "unknown"

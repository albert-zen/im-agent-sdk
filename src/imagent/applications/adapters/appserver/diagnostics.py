from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice

from ....interaction.diagnostics import (
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
    DiagnosticFailureCode,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
)
from .mapping import (
    AppServerMappingError,
    native_mapping,
    normalize_appserver_message,
)

logger = logging.getLogger(__name__)

DEBUG_SCHEMA = "appserver.debug.v1"
DEFAULT_MAX_PREVIEW_CHARS = 240
MAX_PREVIEW_CHARS = 256
MAX_DEBUG_COLLECTION_COUNT = 64
MAX_DEBUG_SAMPLE_ITEMS = 4
MAX_DEBUG_SCALAR_CHARACTERS = 16_384
MAX_DEBUG_COUNTER = 1_000_000

_MANAGED_MEDIA_PATH_PATTERN = re.compile(
    r"(?:^|/)inbound-media(?=$|/|[^a-z0-9._-])",
    flags=re.IGNORECASE,
)
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?:^|[^a-z0-9._-])(?:[a-z]:/|//[^/\s]+/[^/\s]+)", flags=re.IGNORECASE
)
_UNIX_PATH_PATTERN = re.compile(r"(?:^|[^a-z0-9._/-])/(?:[^\s/]+(?:/[^\s]*)*)?")
_ENDPOINT_OR_USERINFO_PATTERN = re.compile(
    r"(?:\b[a-z][a-z0-9+.-]*://[^\s]+|(?:^|[\s\"'=])[^@\s/:]+:[^@\s/]*@[^\s/]+)",
    flags=re.IGNORECASE,
)

_KNOWN_TRANSPORT_SHAPES = frozenset({"response", "request", "notification", "unknown"})
_KNOWN_METHOD_CATEGORIES = frozenset(
    {
        "account",
        "app",
        "command_exec",
        "config",
        "fs",
        "item",
        "mcp",
        "plugin",
        "realtime",
        "skills",
        "system",
        "thread",
        "turn",
        "unknown",
    }
)
_KNOWN_METHOD_KINDS = frozenset(
    {
        "agent_delta",
        "approval_request",
        "config_warning",
        "current_time_read",
        "deprecation_notice",
        "diff_updated",
        "item_completed",
        "item_started",
        "mcp_tool_progress",
        "model_rerouted",
        "plan_updated",
        "question_request",
        "reasoning_summary_part_added",
        "reasoning_summary_text_delta",
        "reasoning_text_delta",
        "request_resolved",
        "thread_compacted",
        "thread_goal_cleared",
        "thread_goal_updated",
        "thread_name_updated",
        "thread_status_changed",
        "turn_completed",
        "turn_started",
        "unknown",
    }
)
_KNOWN_TRANSPORT_METHOD_CATEGORIES = _KNOWN_METHOD_CATEGORIES | frozenset({"invalid", "response"})
_KNOWN_TRANSPORT_METHOD_KINDS = _KNOWN_METHOD_KINDS | frozenset({"invalid", "none"})
_KNOWN_DIRECTIONS = frozenset({"notification", "response", "server_request", "unknown"})
_KNOWN_COMPONENTS = frozenset(
    {
        "appserver.client",
        "appserver.protocol",
        "appserver.requests",
        "appserver.stderr",
        "appserver.supervisor",
    }
)
_KNOWN_EVENTS = frozenset(
    {
        "appserver.connect.failed",
        "appserver.connect.health_probe_failed",
        "appserver.connect.health_probe_succeeded",
        "appserver.connect.spawn_stdio_succeeded",
        "appserver.connect.started",
        "appserver.connect.websocket_failed",
        "appserver.connect.websocket_retry_scheduled",
        "appserver.connect.websocket_succeeded",
        "appserver.connection.closed",
        "appserver.connection_reset_handler.failed",
        "appserver.dispatch.failed",
        "appserver.dispatch.overflow",
        "appserver.protocol.received",
        "appserver.protocol.sent",
        "appserver.reconnect.failed",
        "appserver.reconnect.scheduled",
        "appserver.reconnect.succeeded",
        "appserver.request.overload_retry_scheduled",
        "appserver.request_resolution.missing_thread_scope",
        "appserver.request_resolution.unscoped",
        "appserver.server_request.error_reply.failed",
        "appserver.shared_filesystem.verification_failed",
        "appserver.stderr.line",
        "appserver.stderr.read_failed",
        "appserver.thread_resume.lightweight_unsupported",
    }
)
_KNOWN_LEVELS = frozenset({"CRITICAL", "DEBUG", "ERROR", "INFO", "WARNING"})
_KNOWN_CONNECTION_MODES = frozenset({"disconnected", "external", "spawned-stdio"})
_KNOWN_CONNECTION_STATUSES = frozenset(
    {"connected", "connecting", "disconnected", "initializing", "reconnecting"}
)
_KNOWN_TRANSPORTS = frozenset({"stdio-jsonl", "tcp-websocket", "unix-websocket"})
_KNOWN_OWNERSHIPS = frozenset({"bridge-child", "external"})
_EVENT_FIELDS = frozenset(
    {"component", "event", "level", "message", "data", "connection_mode", "connection_epoch"}
)
_HEALTH_FIELDS = frozenset(
    {
        "connected",
        "ready",
        "status",
        "mode",
        "ownership",
        "transport",
        "connection_epoch",
        "reconnect_enabled",
        "local_image_paths",
        "retry_attempt",
        "retry_delay_s",
        "health_ok",
        "health_status_code",
        "error_type",
        "health_error_type",
    }
)


def summarize_transport_message(
    message: Mapping[str, object], *, max_preview_chars: int = DEFAULT_MAX_PREVIEW_CHARS
) -> dict[str, object]:
    """Return one fixed, content-free structural transport record."""

    preview_limit = _validated_max_preview_chars(max_preview_chars)
    if not isinstance(message, Mapping):
        return _transport_record(
            transport_shape="unknown",
            method_category="invalid",
            method_kind="invalid",
            direction="unknown",
            error_present=False,
            body=None,
            preview_limit=preview_limit,
        )
    transport_shape = _transport_shape(message)
    try:
        normalized_message = native_mapping(message)
    except AppServerMappingError:
        return _transport_record(
            transport_shape=transport_shape,
            method_category="invalid",
            method_kind="invalid",
            direction="unknown",
            error_present=False,
            body=None,
            preview_limit=preview_limit,
        )

    if transport_shape == "response":
        has_error = "error" in normalized_message
        return _transport_record(
            transport_shape=transport_shape,
            method_category="response",
            method_kind="none",
            direction="response",
            error_present=has_error,
            body=(
                normalized_message.get("error") if has_error else normalized_message.get("result")
            ),
            preview_limit=preview_limit,
        )

    if "method" not in normalized_message:
        return _transport_record(
            transport_shape=transport_shape,
            method_category="unknown",
            method_kind="unknown",
            direction="unknown",
            error_present=False,
            body=normalized_message,
            preview_limit=preview_limit,
        )

    try:
        event = normalize_appserver_message(normalized_message)
    except AppServerMappingError:
        return _transport_record(
            transport_shape=transport_shape,
            method_category="invalid",
            method_kind="invalid",
            direction="unknown",
            error_present=False,
            body=None,
            preview_limit=preview_limit,
        )
    return _transport_record(
        transport_shape=transport_shape,
        method_category=_known_category(
            event.category, _KNOWN_METHOD_CATEGORIES, fallback="unknown"
        ),
        method_kind=_known_category(event.kind, _KNOWN_METHOD_KINDS, fallback="unknown"),
        direction=_known_category(event.direction, _KNOWN_DIRECTIONS, fallback="unknown"),
        error_present=False,
        body=event.payload,
        preview_limit=preview_limit,
    )


def summarize_text(
    value: str, *, max_preview_chars: int = DEFAULT_MAX_PREVIEW_CHARS
) -> dict[str, object]:
    """Return text length/fingerprint facts without retaining a preview."""

    preview_limit = _validated_max_preview_chars(max_preview_chars)
    if not isinstance(value, str):
        raise TypeError("diagnostic text must be a string")
    text_length, text_length_capped = _capped_count(len(value), limit=MAX_DEBUG_SCALAR_CHARACTERS)
    sensitive_path = False
    fingerprint_redacted = text_length_capped
    if not text_length_capped:
        sensitive_path = _contains_sensitive_path_or_endpoint(value)
        fingerprint_redacted = sensitive_path
    return {
        "schema": DEBUG_SCHEMA,
        "record_type": "text",
        "text_length": text_length,
        "text_length_capped": text_length_capped,
        "text_sha256": None if fingerprint_redacted else _sha256_text(value),
        "fingerprint_redacted": fingerprint_redacted,
        "path_or_endpoint_redacted": sensitive_path,
        "preview_limit": preview_limit,
        "preview_emitted": False,
    }


def _transport_record(
    *,
    transport_shape: str,
    method_category: str,
    method_kind: str,
    direction: str,
    error_present: bool,
    body: object,
    preview_limit: int,
) -> dict[str, object]:
    return {
        "schema": DEBUG_SCHEMA,
        "record_type": "transport",
        "transport_shape": _known_category(
            transport_shape, _KNOWN_TRANSPORT_SHAPES, fallback="unknown"
        ),
        "method_category": _known_category(
            method_category, _KNOWN_TRANSPORT_METHOD_CATEGORIES, fallback="unknown"
        ),
        "method_kind": _known_category(
            method_kind, _KNOWN_TRANSPORT_METHOD_KINDS, fallback="unknown"
        ),
        "direction": _known_category(direction, _KNOWN_DIRECTIONS, fallback="unknown"),
        "error_present": error_present,
        "body": _summarize_value(body),
        "preview_limit": preview_limit,
        "preview_emitted": False,
    }


def _summarize_value(value: object) -> dict[str, object]:
    """Describe one untrusted value without copying or serializing it."""

    value_type = _value_type(value)
    scalar_length: int | None = None
    scalar_length_capped = False
    collection_count: int | None = None
    collection_count_capped = False
    samples: list[dict[str, object]] = []
    if isinstance(value, str):
        scalar_length, scalar_length_capped = _capped_count(
            len(value), limit=MAX_DEBUG_SCALAR_CHARACTERS
        )
    elif isinstance(value, Mapping):
        collection_count, collection_count_capped = _capped_count(
            len(value), limit=MAX_DEBUG_COLLECTION_COUNT
        )
        for key, item in islice(value.items(), MAX_DEBUG_SAMPLE_ITEMS):
            samples.append(_mapping_sample(key, item))
    elif isinstance(value, (list, tuple)):
        collection_count, collection_count_capped = _capped_count(
            len(value), limit=MAX_DEBUG_COLLECTION_COUNT
        )
        for item in islice(value, MAX_DEBUG_SAMPLE_ITEMS):
            samples.append(_sequence_sample(item))
    return {
        "value_type": value_type,
        "scalar_length": scalar_length,
        "scalar_length_capped": scalar_length_capped,
        "collection_count": collection_count,
        "collection_count_capped": collection_count_capped,
        "samples": samples,
    }


def _mapping_sample(key: object, value: object) -> dict[str, object]:
    key_length: int | None = None
    key_length_capped = False
    key_sha256: str | None = None
    key_redacted = False
    if isinstance(key, str):
        key_length, key_length_capped = _capped_count(len(key), limit=MAX_DEBUG_SCALAR_CHARACTERS)
        key_redacted = key_length_capped
        if not key_redacted:
            key_redacted = _contains_sensitive_path_or_endpoint(key)
        if not key_redacted:
            key_sha256 = _sha256_text(key)
    return {
        "key_type": _value_type(key),
        "key_sha256": key_sha256,
        "key_length": key_length,
        "key_length_capped": key_length_capped,
        "key_redacted": key_redacted,
        "value_type": _value_type(value),
        "value_length": _string_length(value),
        "value_length_capped": _string_length_capped(value),
    }


def _sequence_sample(value: object) -> dict[str, object]:
    return {
        "key_type": None,
        "key_sha256": None,
        "key_length": None,
        "key_length_capped": False,
        "key_redacted": False,
        "value_type": _value_type(value),
        "value_length": _string_length(value),
        "value_length_capped": _string_length_capped(value),
    }


def _string_length(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    return _capped_count(len(value), limit=MAX_DEBUG_SCALAR_CHARACTERS)[0]


def _string_length_capped(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return _capped_count(len(value), limit=MAX_DEBUG_SCALAR_CHARACTERS)[1]


def _value_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "text"
    if isinstance(value, Mapping):
        return "mapping"
    if isinstance(value, (list, tuple)):
        return "collection"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    return "other"


def _capped_count(count: int, *, limit: int) -> tuple[int, bool]:
    return min(max(0, count), limit), count > limit


def _validated_max_preview_chars(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_PREVIEW_CHARS:
        raise ValueError(
            f"max_preview_chars must be a positive integer no greater than {MAX_PREVIEW_CHARS}"
        )
    return value


def _transport_shape(message: Mapping[str, object]) -> str:
    if "id" in message and ("result" in message or "error" in message):
        return "response"
    if "id" in message and "method" in message:
        return "request"
    if "method" in message:
        return "notification"
    return "unknown"


def _known_category(value: object, allowed: frozenset[str], *, fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


def _contains_sensitive_path_or_endpoint(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    return (
        _MANAGED_MEDIA_PATH_PATTERN.search(normalized) is not None
        or _WINDOWS_PATH_PATTERN.search(normalized) is not None
        or _UNIX_PATH_PATTERN.search(normalized) is not None
        or _ENDPOINT_OR_USERINFO_PATTERN.search(normalized) is not None
    )


def _bounded_counter(value: object) -> tuple[int | None, bool]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, False
    return _capped_count(value, limit=MAX_DEBUG_COUNTER)


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _ignored_field_count(
    values: Mapping[str, object], known_fields: frozenset[str]
) -> tuple[int, bool]:
    count = 0
    for key in values:
        if key in known_fields:
            continue
        count += 1
        if count > MAX_DEBUG_COLLECTION_COUNT:
            return MAX_DEBUG_COLLECTION_COUNT, True
    return count, False


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


@dataclass(slots=True)
class AppServerDiagnosticState:
    """Process-local diagnostic counters kept outside the transport client."""

    notification_capacity: int
    server_request_capacity: int
    notification_overflow_count: int = 0
    server_request_overflow_count: int = 0
    last_failure_code: DiagnosticFailureCode | None = None

    def record_connect_failure(self) -> None:
        self.last_failure_code = DiagnosticFailureCode.CONNECT_FAILED

    def record_transport_failure(self) -> None:
        self.last_failure_code = DiagnosticFailureCode.TRANSPORT_FAILED

    def record_overflow(self, queue_name: QueueDiagnosticName) -> None:
        if queue_name is QueueDiagnosticName.SERVER_REQUEST:
            self.server_request_overflow_count += 1
            self.last_failure_code = DiagnosticFailureCode.SERVER_REQUEST_OVERFLOW
        else:
            self.notification_overflow_count += 1
            self.last_failure_code = DiagnosticFailureCode.NOTIFICATION_OVERFLOW

    @property
    def overflow_counts(self) -> tuple[int, int]:
        return self.notification_overflow_count, self.server_request_overflow_count

    def snapshot(
        self,
        *,
        transport_open: bool,
        initialized: bool,
        reconnecting: bool,
        connection_epoch: int,
        closing: bool,
        worker_running: bool,
        notification_depth: int,
        server_request_depth: int,
    ) -> ConnectionDiagnosticFacts:
        if transport_open and initialized:
            state = ConnectionDiagnosticState.READY
        elif reconnecting:
            state = ConnectionDiagnosticState.RECONNECTING
        elif transport_open:
            state = ConnectionDiagnosticState.CONNECTING
        else:
            state = ConnectionDiagnosticState.DISCONNECTED
        worker_degraded = reconnecting or (transport_open and not worker_running)
        worker_degraded = worker_degraded or (
            not transport_open and not closing and self.last_failure_code is not None
        )
        return ConnectionDiagnosticFacts(
            state=state,
            connection_epoch=connection_epoch,
            reconnect_count=max(0, connection_epoch - 1),
            worker_running=worker_running,
            worker_degraded=worker_degraded,
            last_failure_code=self.last_failure_code,
            queues=(
                QueueDiagnosticFacts(
                    name=QueueDiagnosticName.NOTIFICATION,
                    capacity=self.notification_capacity,
                    depth=notification_depth,
                    overflow_count=self.notification_overflow_count,
                ),
                QueueDiagnosticFacts(
                    name=QueueDiagnosticName.SERVER_REQUEST,
                    capacity=self.server_request_capacity,
                    depth=server_request_depth,
                    overflow_count=self.server_request_overflow_count,
                ),
            ),
        )


def emit_event(**event: object) -> None:
    """Log a fixed, redacted internal event record without defining OTel."""

    logger.debug("app-server event: %s", _event_record(event))


def _event_record(event: Mapping[str, object]) -> dict[str, object]:
    connection_epoch, connection_epoch_capped = _bounded_counter(event.get("connection_epoch"))
    ignored_field_count, ignored_field_count_capped = _ignored_field_count(event, _EVENT_FIELDS)
    return {
        "schema": DEBUG_SCHEMA,
        "record_type": "event",
        "component": _known_category(event.get("component"), _KNOWN_COMPONENTS, fallback="other"),
        "event": _known_category(event.get("event"), _KNOWN_EVENTS, fallback="appserver.other"),
        "level": _known_category(event.get("level", "DEBUG"), _KNOWN_LEVELS, fallback="DEBUG"),
        "connection_mode": _known_category(
            event.get("connection_mode"), _KNOWN_CONNECTION_MODES, fallback="other"
        ),
        "connection_epoch": connection_epoch,
        "connection_epoch_capped": connection_epoch_capped,
        "message": _summarize_value(event.get("message")),
        "data": _summarize_value(event.get("data")),
        "ignored_field_count": ignored_field_count,
        "ignored_field_count_capped": ignored_field_count_capped,
    }


def mark_appserver_health(**state: object) -> None:
    """Log a fixed redacted health record separate from ADR-0014 facts."""

    logger.debug("app-server health: %s", _health_record(state))


def _health_record(state: Mapping[str, object]) -> dict[str, object]:
    connection_epoch, connection_epoch_capped = _bounded_counter(state.get("connection_epoch"))
    retry_attempt, retry_attempt_capped = _bounded_counter(state.get("retry_attempt"))
    ignored_field_count, ignored_field_count_capped = _ignored_field_count(state, _HEALTH_FIELDS)
    health_ok = _optional_bool(state.get("health_ok"))
    return {
        "schema": DEBUG_SCHEMA,
        "record_type": "health",
        "connected": _optional_bool(state.get("connected")),
        "ready": _optional_bool(state.get("ready")),
        "status": _known_category(
            state.get("status"), _KNOWN_CONNECTION_STATUSES, fallback="other"
        ),
        "connection_mode": _known_category(
            state.get("mode"), _KNOWN_CONNECTION_MODES, fallback="other"
        ),
        "transport": _known_category(state.get("transport"), _KNOWN_TRANSPORTS, fallback="other"),
        "ownership": _known_category(state.get("ownership"), _KNOWN_OWNERSHIPS, fallback="other"),
        "connection_epoch": connection_epoch,
        "connection_epoch_capped": connection_epoch_capped,
        "reconnect_enabled": _optional_bool(state.get("reconnect_enabled")),
        "local_image_paths": _optional_bool(state.get("local_image_paths")),
        "retry_attempt": retry_attempt,
        "retry_attempt_capped": retry_attempt_capped,
        "retry_scheduled": retry_attempt is not None,
        "health": "ok" if health_ok is True else "failed" if health_ok is False else "unknown",
        "failure_present": (
            state.get("error_type") is not None or state.get("health_error_type") is not None
        ),
        "ignored_field_count": ignored_field_count,
        "ignored_field_count_capped": ignored_field_count_capped,
    }

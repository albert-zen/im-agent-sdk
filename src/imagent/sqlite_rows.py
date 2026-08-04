from __future__ import annotations

import sqlite3
from datetime import datetime

from .adapters import ProjectionCheckpointConflict, ProjectionRouteConflict
from .contracts import (
    ApplicationRef,
    ConversationBinding,
    ProjectRef,
    ThreadProjectionRoute,
    ThreadRef,
    TurnReplyCorrelation,
    validate_binding,
    validate_projection_route,
    validate_turn_reply_correlation,
)
from .interaction.messages import ConversationRef


def binding_from_row(row: sqlite3.Row) -> ConversationBinding:
    application_id = row["application_instance_id"]
    project_id = row["project_id"]
    thread_id = row["thread_id"]
    if application_id is None and (project_id is not None or thread_id is not None):
        raise ValueError("binding scope requires an application")
    application_ref = None
    if application_id is not None:
        application_ref = ApplicationRef(required_text(application_id, "application_instance_id"))
    project_ref = (
        ProjectRef(
            application_ref.application_instance_id,
            required_text(project_id, "project_id"),
        )
        if application_ref is not None and project_id is not None
        else None
    )
    thread_ref = (
        ThreadRef(
            application_instance_id=application_ref.application_instance_id,
            native_thread_id=required_text(thread_id, "thread_id"),
            project_ref=project_ref,
        )
        if application_ref is not None and thread_id is not None
        else None
    )
    binding = ConversationBinding(
        conversation_ref=ConversationRef(
            required_text(row["channel_instance_id"], "channel_instance_id"),
            required_text(row["native_conversation_id"], "native_conversation_id"),
        ),
        application_ref=application_ref,
        project_ref=project_ref,
        thread_ref=thread_ref,
        revision=required_integer(row["revision"], "revision"),
        updated_at=decode_datetime(row["updated_at"], "updated_at"),
    )
    validate_binding(binding)
    return binding


def thread_storage_key(thread_ref: ThreadRef) -> tuple[str, str, str]:
    return (
        thread_ref.application_instance_id,
        (thread_ref.project_ref.native_project_id if thread_ref.project_ref is not None else ""),
        thread_ref.native_thread_id,
    )


def merge_projection_route(
    existing: ThreadProjectionRoute | None,
    replacement: ThreadProjectionRoute,
) -> ThreadProjectionRoute:
    if existing is None:
        return replacement
    if (
        existing.route_id != replacement.route_id
        or existing.thread_ref != replacement.thread_ref
        or existing.conversation_ref != replacement.conversation_ref
    ):
        raise ProjectionRouteConflict(
            f"route ID belongs to different endpoints: {replacement.route_id}"
        )
    if replacement.checkpoint_agent_item_id is not None:
        if (
            replacement.checkpoint_agent_item_id != existing.checkpoint_agent_item_id
            or replacement.checkpointed_at != existing.checkpointed_at
        ):
            raise ProjectionCheckpointConflict(
                f"route refresh cannot change checkpoint: {replacement.route_id}"
            )
        return replacement
    if existing.checkpoint_agent_item_id is None:
        return replacement
    return ThreadProjectionRoute(
        route_id=replacement.route_id,
        thread_ref=replacement.thread_ref,
        conversation_ref=replacement.conversation_ref,
        reply_to_message_id=replacement.reply_to_message_id,
        checkpoint_agent_item_id=existing.checkpoint_agent_item_id,
        checkpointed_at=existing.checkpointed_at,
        updated_at=replacement.updated_at,
    )


def projection_route_from_row(row: sqlite3.Row) -> ThreadProjectionRoute:
    application_id = required_text(row["application_instance_id"], "application_instance_id")
    project_id = empty_storage_text(row["project_id"], "project_id")
    project_ref = ProjectRef(application_id, project_id) if project_id else None
    route = ThreadProjectionRoute(
        route_id=required_text(row["route_id"], "route_id"),
        thread_ref=ThreadRef(
            application_instance_id=application_id,
            native_thread_id=required_text(row["thread_id"], "thread_id"),
            project_ref=project_ref,
        ),
        conversation_ref=ConversationRef(
            channel_instance_id=required_text(row["channel_instance_id"], "channel_instance_id"),
            native_conversation_id=required_text(
                row["native_conversation_id"], "native_conversation_id"
            ),
        ),
        reply_to_message_id=optional_text(row["reply_to_message_id"], "reply_to_message_id"),
        checkpoint_agent_item_id=optional_text(
            row["checkpoint_agent_item_id"], "checkpoint_agent_item_id"
        ),
        checkpointed_at=(
            decode_datetime(row["checkpointed_at"], "checkpointed_at")
            if row["checkpointed_at"] is not None
            else None
        ),
        updated_at=decode_datetime(row["updated_at"], "updated_at"),
    )
    validate_projection_route(route)
    return route


def turn_reply_correlation_from_row(
    row: sqlite3.Row,
) -> TurnReplyCorrelation:
    application_id = required_text(row["application_instance_id"], "application_instance_id")
    project_id = empty_storage_text(row["project_id"], "project_id")
    project_ref = ProjectRef(application_id, project_id) if project_id else None
    correlation = TurnReplyCorrelation(
        correlation_id=required_text(row["correlation_id"], "correlation_id"),
        thread_ref=ThreadRef(
            application_instance_id=application_id,
            native_thread_id=required_text(row["thread_id"], "thread_id"),
            project_ref=project_ref,
        ),
        turn_id=required_text(row["turn_id"], "turn_id"),
        client_message_id=required_text(row["client_message_id"], "client_message_id"),
        conversation_ref=ConversationRef(
            channel_instance_id=required_text(row["channel_instance_id"], "channel_instance_id"),
            native_conversation_id=required_text(
                row["native_conversation_id"], "native_conversation_id"
            ),
        ),
        reply_to_message_id=required_text(row["reply_to_message_id"], "reply_to_message_id"),
        created_at=decode_datetime(row["created_at"], "created_at"),
    )
    validate_turn_reply_correlation(correlation)
    return correlation


def required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty SQLite text value")
    return value


def optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return required_text(value, label)


def empty_storage_text(value: object, label: str) -> str | None:
    """Decode the current NOT NULL empty-string sentinel for an absent Project."""

    if value == "":
        return None
    return required_text(value, label)


def required_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an SQLite integer")
    return value


def decode_datetime(value: object, label: str) -> datetime:
    try:
        decoded = datetime.fromisoformat(required_text(value, label))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO timestamp") from error
    if decoded.tzinfo is None or decoded.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return decoded

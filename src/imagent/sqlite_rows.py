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
)
from .interaction.messages import ConversationRef


def binding_from_row(row: sqlite3.Row) -> ConversationBinding:
    application_id = row["application_instance_id"]
    project_id = row["project_id"]
    thread_id = row["thread_id"]
    application_ref = ApplicationRef(str(application_id)) if application_id is not None else None
    project_ref = (
        ProjectRef(str(application_id), str(project_id))
        if application_id is not None and project_id is not None
        else None
    )
    thread_ref = (
        ThreadRef(
            application_instance_id=str(application_id),
            native_thread_id=str(thread_id),
            project_ref=project_ref,
        )
        if application_id is not None and thread_id is not None
        else None
    )
    return ConversationBinding(
        conversation_ref=ConversationRef(
            str(row["channel_instance_id"]),
            str(row["native_conversation_id"]),
        ),
        application_ref=application_ref,
        project_ref=project_ref,
        thread_ref=thread_ref,
        revision=int(row["revision"]),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


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
    application_id = str(row["application_instance_id"])
    project_id = str(row["project_id"])
    project_ref = ProjectRef(application_id, project_id) if project_id else None
    return ThreadProjectionRoute(
        route_id=str(row["route_id"]),
        thread_ref=ThreadRef(
            application_instance_id=application_id,
            native_thread_id=str(row["thread_id"]),
            project_ref=project_ref,
        ),
        conversation_ref=ConversationRef(
            channel_instance_id=str(row["channel_instance_id"]),
            native_conversation_id=str(row["native_conversation_id"]),
        ),
        reply_to_message_id=(
            str(row["reply_to_message_id"]) if row["reply_to_message_id"] is not None else None
        ),
        checkpoint_agent_item_id=(
            str(row["checkpoint_agent_item_id"])
            if row["checkpoint_agent_item_id"] is not None
            else None
        ),
        checkpointed_at=(
            datetime.fromisoformat(str(row["checkpointed_at"]))
            if row["checkpointed_at"] is not None
            else None
        ),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def turn_reply_correlation_from_row(
    row: sqlite3.Row,
) -> TurnReplyCorrelation:
    application_id = str(row["application_instance_id"])
    project_id = str(row["project_id"])
    project_ref = ProjectRef(application_id, project_id) if project_id else None
    return TurnReplyCorrelation(
        correlation_id=str(row["correlation_id"]),
        thread_ref=ThreadRef(
            application_instance_id=application_id,
            native_thread_id=str(row["thread_id"]),
            project_ref=project_ref,
        ),
        turn_id=str(row["turn_id"]),
        client_message_id=str(row["client_message_id"]),
        conversation_ref=ConversationRef(
            channel_instance_id=str(row["channel_instance_id"]),
            native_conversation_id=str(row["native_conversation_id"]),
        ),
        reply_to_message_id=str(row["reply_to_message_id"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )

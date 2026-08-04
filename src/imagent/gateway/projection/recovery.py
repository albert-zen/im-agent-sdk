from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ...adapters import AgentApplicationAdapter
from ...applications.capabilities import SupportLevel
from ...applications.events import AgentEvent
from ...contracts import (
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    GetThreadHistory,
    GetTurnCatchup,
    ThreadHistory,
    ThreadHistoryRead,
    ThreadProjectionRoute,
    ThreadRef,
    TurnCatchup,
    TurnCatchupRead,
)
from ...events import CursorExpired
from ...interaction.messages import ConversationRef
from ...projections import (
    AuthoritativeProjectionSlice,
    ProjectedAgentMessage,
    derive_projection_route_id,
)


class RecoveryMode(StrEnum):
    REPLAY = "replay"
    AUTHORITATIVE = "authoritative"


@dataclass(frozen=True, slots=True)
class ThreadRecovery:
    mode: RecoveryMode
    events: AsyncIterator[AgentEvent]
    history: ThreadHistory | None = None
    catchup: TurnCatchup | None = None
    cursor_expired: bool = False


ExecuteApplication = Callable[
    [ApplicationOperation],
    Awaitable[ApplicationOperationResult],
]


class ProjectionRecoveryUnavailable(RuntimeError):
    """The Application could not provide an authoritative recovery read."""


async def recover_thread(
    application: AgentApplicationAdapter,
    thread_ref: ThreadRef,
    *,
    recovery_id: str,
    after_cursor: str | None = None,
    history_limit: int = 20,
    catchup_limit: int = 20,
) -> ThreadRecovery:
    """Resume native replay or establish live-first authoritative reconciliation."""
    if history_limit < 1:
        raise ValueError("history_limit must be positive")
    if catchup_limit < 1:
        raise ValueError("catchup_limit must be positive")
    if thread_ref.application_instance_id != (application.summary.ref.application_instance_id):
        raise ValueError("recovery Thread belongs to a different application")

    cursor_expired = False
    if (
        after_cursor is not None
        and application.summary.capabilities.runtime.replay_from_cursor
        is not SupportLevel.UNSUPPORTED
    ):
        try:
            events = application.subscribe_thread(
                thread_ref,
                after_cursor=after_cursor,
            )
        except CursorExpired:
            cursor_expired = True
        else:
            return ThreadRecovery(mode=RecoveryMode.REPLAY, events=events)

    events = application.subscribe_thread(thread_ref)
    created_at = datetime.now(UTC)
    try:
        history_result = await application.execute(
            GetThreadHistory(
                operation_id=f"{recovery_id}:thread.history",
                application_ref=application.summary.ref,
                thread_ref=thread_ref,
                limit=history_limit,
                page=1,
                created_at=created_at,
            )
        )
        catchup_result = await application.execute(
            GetTurnCatchup(
                operation_id=f"{recovery_id}:turn.catchup",
                application_ref=application.summary.ref,
                thread_ref=thread_ref,
                limit=catchup_limit,
                created_at=created_at,
            )
        )
        if isinstance(history_result, ApplicationOperationFailed):
            raise RuntimeError(
                f"authoritative history recovery failed: {history_result.error.message}"
            )
        if isinstance(catchup_result, ApplicationOperationFailed):
            raise RuntimeError(
                f"authoritative catch-up recovery failed: {catchup_result.error.message}"
            )
        if not isinstance(history_result, ThreadHistoryRead) or not isinstance(
            catchup_result,
            TurnCatchupRead,
        ):
            raise RuntimeError("authoritative recovery returned an incompatible result")
    except BaseException:
        await _close(events)
        raise
    return ThreadRecovery(
        mode=RecoveryMode.AUTHORITATIVE,
        events=events,
        history=history_result.history,
        catchup=catchup_result.catchup,
        cursor_expired=cursor_expired,
    )


async def read_bounded_authoritative_projection(
    application: AgentApplicationAdapter,
    route: ThreadProjectionRoute,
    *,
    execute_application: ExecuteApplication,
    baseline_history_limit: int,
    recovery_history_page_size: int,
    recovery_max_pages: int,
    catchup_limit: int,
    projection_item_limit: int,
    require_checkpoint: bool = False,
) -> AuthoritativeProjectionSlice:
    """Read a recent baseline or scan newest pages toward one route checkpoint."""
    if projection_item_limit < 1:
        raise ValueError("projection_item_limit must be positive")
    thread_ref = route.thread_ref
    checkpoint = route.checkpoint_agent_item_id
    history_pages: list[tuple[ProjectedAgentMessage, ...]] = []
    recovery_scope = derive_projection_route_id(
        thread_ref,
        ConversationRef("gateway", "authoritative-recovery"),
    )
    history_limit = baseline_history_limit if checkpoint is None else recovery_history_page_size
    max_pages = 1 if checkpoint is None else recovery_max_pages
    page = 1
    checkpoint_found = False
    gap = "checkpoint_missing" if checkpoint is None and require_checkpoint else None
    while page <= max_pages:
        history_result = await execute_application(
            GetThreadHistory(
                operation_id=f"{recovery_scope}:thread.history:{page}",
                application_ref=application.summary.ref,
                thread_ref=thread_ref,
                limit=history_limit,
                page=page,
                created_at=datetime.now(UTC),
            )
        )
        if not isinstance(history_result, ThreadHistoryRead):
            raise ProjectionRecoveryUnavailable("history_unavailable")
        page_messages = tuple(
            ProjectedAgentMessage(
                message=message,
                turn_id=turn.turn_id,
            )
            for turn in history_result.history.turns
            for message in turn.agent_messages
        )
        history_pages.append(page_messages)
        if checkpoint is not None and any(
            item.message.agent_item_id == checkpoint for item in page_messages
        ):
            checkpoint_found = True
            break
        if checkpoint is None:
            break
        if not history_result.history.has_older:
            gap = "checkpoint_missing"
            break
        if page == max_pages:
            gap = "checkpoint_out_of_window"
            break
        page += 1
    catchup_result = await execute_application(
        GetTurnCatchup(
            operation_id=f"{recovery_scope}:turn.catchup",
            application_ref=application.summary.ref,
            thread_ref=thread_ref,
            limit=catchup_limit,
            created_at=datetime.now(UTC),
        )
    )
    messages: list[ProjectedAgentMessage] = []
    for history_page in reversed(history_pages):
        messages.extend(history_page)
    if checkpoint_found and checkpoint is not None:
        checkpoint_index = next(
            index for index, item in enumerate(messages) if item.message.agent_item_id == checkpoint
        )
        messages = messages[checkpoint_index + 1 :]
    if isinstance(catchup_result, TurnCatchupRead):
        messages.extend(
            ProjectedAgentMessage(
                message=message,
                turn_id=catchup_result.catchup.turn_id,
            )
            for message in catchup_result.catchup.messages
        )
    else:
        raise ProjectionRecoveryUnavailable("catchup_unavailable")
    deduplicated: list[ProjectedAgentMessage] = []
    seen: set[str] = set()
    for item in messages:
        if item.message.agent_item_id in seen:
            continue
        seen.add(item.message.agent_item_id)
        deduplicated.append(item)
    if len(deduplicated) > projection_item_limit:
        gap = gap or "projection_window_truncated"
        deduplicated = (
            deduplicated[:projection_item_limit]
            if checkpoint_found
            else deduplicated[-projection_item_limit:]
        )
    return AuthoritativeProjectionSlice(
        messages=tuple(deduplicated),
        pages_read=len(history_pages),
        checkpoint_found=checkpoint_found,
        gap=gap,
    )


async def _close(events: AsyncIterator[AgentEvent]) -> None:
    close = getattr(events, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .adapters import AgentApplicationAdapter
from .contracts import (
    AgentEvent,
    ApplicationOperationFailed,
    GetThreadHistory,
    GetTurnCatchup,
    SupportLevel,
    ThreadHistory,
    ThreadHistoryRead,
    ThreadRef,
    TurnCatchup,
    TurnCatchupRead,
)
from .events import CursorExpired


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


async def recover_thread(
    application: AgentApplicationAdapter,
    thread_ref: ThreadRef,
    *,
    recovery_id: str,
    after_cursor: str | None = None,
) -> ThreadRecovery:
    """Resume native replay or establish live-first authoritative reconciliation."""
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
                limit=20,
                page=1,
                created_at=created_at,
            )
        )
        catchup_result = await application.execute(
            GetTurnCatchup(
                operation_id=f"{recovery_id}:turn.catchup",
                application_ref=application.summary.ref,
                thread_ref=thread_ref,
                limit=20,
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


async def _close(events: AsyncIterator[AgentEvent]) -> None:
    close = getattr(events, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result

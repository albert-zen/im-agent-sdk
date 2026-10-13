"""Bounded queued-status rendering with fixed Gateway-owned identities."""

from __future__ import annotations

import asyncio
from datetime import datetime

from ...interaction.messages import ConversationRef, OutboundMessage, TextContent, TextFormat
from ..queued_input import QueuedInputNoticeReason, QueuedInputPresenter, QueuedInputStatus


class QueuedInputPresentationRuntime:
    def __init__(
        self,
        presenter: QueuedInputPresenter,
        *,
        timeout_seconds: float,
        max_items: int,
        max_text_characters: int,
        max_concurrency: int,
    ) -> None:
        self._presenter = presenter
        self._timeout = timeout_seconds
        self._max_items = max_items
        self._max_text = max_text_characters
        self._max_concurrency = max_concurrency
        self._tasks: set[asyncio.Task[OutboundMessage]] = set()
        self._closed = False

    async def present(
        self,
        status: QueuedInputStatus,
        *,
        reason: QueuedInputNoticeReason,
        conversation_ref: ConversationRef,
        delivery_id: str,
    ) -> OutboundMessage:
        if self._closed or len(self._tasks) >= self._max_concurrency:
            raise RuntimeError("queued presenter closed or at capacity")
        task = asyncio.create_task(
            self._presenter.present_queued_input(
                status,
                reason=reason,
                conversation_ref=conversation_ref,
                delivery_id=delivery_id,
                reply_to_message_id=status.message_id,
            ),
            name="imagent-queued-presentation",
        )
        self._tasks.add(task)
        task.add_done_callback(self._finish)
        try:
            done, _ = await asyncio.wait((task,), timeout=self._timeout)
            if not done:
                raise TimeoutError("queued presenter exceeded its bounded lifetime")
            output = task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait((task,), timeout=self._timeout)
        if not isinstance(output, OutboundMessage) or (
            output.conversation_ref != conversation_ref
            or output.delivery_id != delivery_id
            or output.reply_to != status.message_id
        ):
            raise ValueError("queued presenter changed fixed delivery identity")
        if (
            not isinstance(output.content, tuple)
            or not output.content
            or len(output.content) > self._max_items
            or any(
                not isinstance(part, TextContent)
                or not isinstance(part.text, str)
                or not part.text.strip()
                or not isinstance(part.format, TextFormat)
                for part in output.content
            )
        ):
            raise ValueError("queued presenter requires bounded nonempty typed text")
        if (
            sum(len(part.text) for part in output.content if isinstance(part, TextContent))
            > self._max_text
        ):
            raise ValueError("queued presenter exceeded text bound")
        if (
            not isinstance(output.metadata, dict)
            or output.metadata
            or not isinstance(output.created_at, datetime)
        ):
            raise ValueError("queued presenter supplied invalid metadata or creation time")
        return OutboundMessage(
            delivery_id=delivery_id,
            conversation_ref=conversation_ref,
            content=tuple(output.content),
            created_at=output.created_at,
            reply_to=status.message_id,
        )

    def _finish(self, task: asyncio.Task[OutboundMessage]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self._timeout)
            if pending:
                raise TimeoutError("queued presenter did not join during shutdown")

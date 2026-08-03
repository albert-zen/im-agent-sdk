from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import Protocol

from .contracts import Content, InboundMessage, TextContent
from .diagnostics import (
    InboundContentTransformerDiagnosticFacts,
    InboundContentTransformFailureCode,
)
from .interaction.media import AttachmentContent


class InboundContentTransformer(Protocol):
    """Replay-safe I1 replacement of verified, unconsumed inbound content."""

    async def transform_content(
        self,
        message: InboundMessage,
    ) -> tuple[Content, ...]: ...


class InboundContentTransformationError(ValueError):
    """The I1 transformer returned content outside its bounded typed contract."""


class InboundContentTransformationTimeout(TimeoutError):
    """Gateway cancelled and joined I1 after its configured lifetime."""

    def __init__(self, message: str, *, cancellation_overrun: bool) -> None:
        super().__init__(message)
        self.cancellation_overrun = cancellation_overrun


class InboundContentTransformationCapacityError(RuntimeError):
    """The configured finite I1 task capacity is already occupied."""


class InboundContentTransformRuntime:
    """One configured I1 invocation boundary and its redacted local facts."""

    def __init__(
        self,
        transformer: InboundContentTransformer,
        *,
        timeout_seconds: float,
        max_items: int,
        max_concurrency: int,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("inbound content transform timeout must be finite and positive")
        if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 1:
            raise ValueError("inbound content transform item limit must be positive")
        if (
            not isinstance(max_concurrency, int)
            or isinstance(max_concurrency, bool)
            or max_concurrency < 1
        ):
            raise ValueError("inbound content transform concurrency must be positive")
        self._transformer = transformer
        self._timeout_seconds = timeout_seconds
        self._max_items = max_items
        self._max_concurrency = max_concurrency
        self._active_tasks: set[asyncio.Task[tuple[Content, ...]]] = set()
        self._invocation_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._timeout_count = 0
        self._cancellation_count = 0
        self._cancellation_overrun_count = 0
        self._capacity_rejection_count = 0
        self._last_failure_code: InboundContentTransformFailureCode | None = None

    async def transform(self, message: InboundMessage) -> tuple[Content, ...]:
        self._invocation_count += 1
        if len(self._active_tasks) >= self._max_concurrency:
            self._capacity_rejection_count += 1
            self._record_failure(InboundContentTransformFailureCode.CAPACITY_EXHAUSTED)
            raise InboundContentTransformationCapacityError(
                "inbound content transformer task capacity is exhausted"
            )
        try:
            content = await transform_inbound_content(
                self._transformer,
                message,
                timeout_seconds=self._timeout_seconds,
                max_items=self._max_items,
                on_task_created=self._track_task,
            )
        except InboundContentTransformationError:
            self._record_failure(InboundContentTransformFailureCode.INVALID_OUTPUT)
            raise
        except InboundContentTransformationTimeout as error:
            self._timeout_count += 1
            if error.cancellation_overrun:
                self._cancellation_overrun_count += 1
            self._record_failure(InboundContentTransformFailureCode.TIMED_OUT)
            raise
        except asyncio.CancelledError:
            self._cancellation_count += 1
            self._record_failure(InboundContentTransformFailureCode.CANCELLED)
            raise
        except BaseException:
            self._record_failure(InboundContentTransformFailureCode.TRANSFORMER_FAILED)
            raise
        self._success_count += 1
        return content

    def diagnostic_facts(self) -> InboundContentTransformerDiagnosticFacts:
        return InboundContentTransformerDiagnosticFacts(
            invocation_count=self._invocation_count,
            success_count=self._success_count,
            failure_count=self._failure_count,
            timeout_count=self._timeout_count,
            cancellation_count=self._cancellation_count,
            cancellation_overrun_count=self._cancellation_overrun_count,
            capacity_rejection_count=self._capacity_rejection_count,
            last_failure_code=self._last_failure_code,
        )

    def _record_failure(self, code: InboundContentTransformFailureCode) -> None:
        self._failure_count += 1
        self._last_failure_code = code

    def _track_task(self, task: asyncio.Task[tuple[Content, ...]]) -> None:
        self._active_tasks.add(task)
        task.add_done_callback(self._finish_task)

    def _finish_task(self, task: asyncio.Task[tuple[Content, ...]]) -> None:
        self._active_tasks.discard(task)
        _consume_task_result(task)

    async def close(self) -> None:
        """Bound shutdown cleanup for any transformer tasks that overran cancellation."""

        tasks = tuple(self._active_tasks)
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        done, pending = await asyncio.wait(tasks, timeout=self._timeout_seconds)
        for task in done:
            self._finish_task(task)
        for task in pending:
            task.cancel()


async def transform_inbound_content(
    transformer: InboundContentTransformer,
    message: InboundMessage,
    *,
    timeout_seconds: float,
    max_items: int,
    on_task_created: Callable[[asyncio.Task[tuple[Content, ...]]], None] | None = None,
) -> tuple[Content, ...]:
    """Invoke I1 under its finite lifetime and validate its content-only result."""

    task = asyncio.create_task(
        transformer.transform_content(message),
        name="imagent-inbound-content",
    )
    if on_task_created is not None:
        on_task_created(task)
    try:
        done, _ = await asyncio.wait((task,), timeout=timeout_seconds)
        if not done:
            joined = await _cancel_and_join(task, timeout_seconds=timeout_seconds)
            raise InboundContentTransformationTimeout(
                "inbound content transformer exceeded its configured lifetime",
                cancellation_overrun=not joined,
            )
        content = task.result()
    except InboundContentTransformationTimeout:
        raise
    except BaseException:
        if not task.done():
            await _cancel_and_join(task, timeout_seconds=timeout_seconds)
        raise
    if not isinstance(content, tuple):
        raise InboundContentTransformationError("inbound content transformer must return a tuple")
    if not content:
        raise InboundContentTransformationError(
            "inbound content transformer returned empty content"
        )
    if len(content) > max_items:
        raise InboundContentTransformationError(
            "inbound content transformer exceeded the configured item limit"
        )
    if not all(isinstance(item, (TextContent, AttachmentContent)) for item in content):
        raise InboundContentTransformationError(
            "inbound content transformer returned an unsupported content item"
        )
    return content


async def _cancel_and_join(
    task: asyncio.Task[tuple[Content, ...]],
    *,
    timeout_seconds: float,
) -> bool:
    task.cancel()
    done, _ = await asyncio.wait((task,), timeout=timeout_seconds)
    if not done:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        return False
    try:
        task.result()
    except BaseException:
        # Cleanup cannot replace the caller's cancellation or the SDK deadline.
        pass
    return True


def _consume_task_result(task: asyncio.Task[tuple[Content, ...]]) -> None:
    try:
        task.result()
    except BaseException:
        pass

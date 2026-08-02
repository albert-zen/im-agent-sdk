from __future__ import annotations

import asyncio
import math
from typing import Protocol

from .contracts import AttachmentContent, Content, InboundMessage, TextContent
from .diagnostics import (
    InboundContentTransformerDiagnosticFacts,
    InboundContentTransformFailureCode,
)


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


class InboundContentTransformRuntime:
    """One configured I1 invocation boundary and its redacted local facts."""

    def __init__(
        self,
        transformer: InboundContentTransformer,
        *,
        timeout_seconds: float,
        max_items: int,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("inbound content transform timeout must be finite and positive")
        if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 1:
            raise ValueError("inbound content transform item limit must be positive")
        self._transformer = transformer
        self._timeout_seconds = timeout_seconds
        self._max_items = max_items
        self._invocation_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._timeout_count = 0
        self._cancellation_count = 0
        self._cancellation_overrun_count = 0
        self._last_failure_code: InboundContentTransformFailureCode | None = None

    async def transform(self, message: InboundMessage) -> tuple[Content, ...]:
        self._invocation_count += 1
        try:
            content = await transform_inbound_content(
                self._transformer,
                message,
                timeout_seconds=self._timeout_seconds,
                max_items=self._max_items,
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
            last_failure_code=self._last_failure_code,
        )

    def _record_failure(self, code: InboundContentTransformFailureCode) -> None:
        self._failure_count += 1
        self._last_failure_code = code


async def transform_inbound_content(
    transformer: InboundContentTransformer,
    message: InboundMessage,
    *,
    timeout_seconds: float,
    max_items: int,
) -> tuple[Content, ...]:
    """Invoke I1 under its finite lifetime and validate its content-only result."""

    task = asyncio.create_task(
        transformer.transform_content(message),
        name="imagent-inbound-content",
    )
    try:
        done, _ = await asyncio.wait((task,), timeout=timeout_seconds)
        if not done:
            joined = await _cancel_and_join(task, timeout_seconds=timeout_seconds)
            raise InboundContentTransformationTimeout(
                "inbound content transformer exceeded its configured lifetime",
                cancellation_overrun=not joined,
            )
        content = task.result()
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

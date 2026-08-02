from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import islice
from types import MappingProxyType
from typing import Protocol

from .contracts import AttachmentContent, OutboundMessage, TextContent
from .diagnostics import (
    OutboundPresentationDiagnosticFacts,
    OutboundPresentationFailureCode,
)


class ProjectionPresentationOrigin(StrEnum):
    """Whether one projection decision is recoverable from native history."""

    AUTHORITATIVE = "authoritative"
    LIVE_ONLY = "live_only"


@dataclass(frozen=True, slots=True)
class OutboundPresentationContext:
    """Bounded O1 facts for one already-routed projection destination."""

    origin: ProjectionPresentationOrigin

    def __post_init__(self) -> None:
        if not isinstance(self.origin, ProjectionPresentationOrigin):
            raise ValueError("outbound presentation origin must use the fixed vocabulary")


class OutboundPresentationPolicy(Protocol):
    """Replay-safe per-destination projection presentation policy."""

    async def present(
        self,
        message: OutboundMessage,
        context: OutboundPresentationContext,
    ) -> OutboundMessage | None: ...


class OutboundPresentationError(ValueError):
    """O1 changed fixed routing authority or returned unbounded content."""


class OutboundPresentationTimeout(TimeoutError):
    def __init__(self, message: str, *, cancellation_overrun: bool) -> None:
        super().__init__(message)
        self.cancellation_overrun = cancellation_overrun


class OutboundPresentationCapacityError(RuntimeError):
    """The configured finite O1 task capacity is occupied."""


class OutboundPresentationRuntime:
    """Bound O1 invocation without claim, checkpoint, or delivery authority."""

    def __init__(
        self,
        policy: OutboundPresentationPolicy,
        *,
        timeout_seconds: float,
        max_items: int,
        max_text_characters: int,
        max_concurrency: int,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("outbound presentation timeout must be finite and positive")
        if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 1:
            raise ValueError("outbound presentation item limit must be positive")
        if (
            not isinstance(max_text_characters, int)
            or isinstance(max_text_characters, bool)
            or max_text_characters < 1
        ):
            raise ValueError("outbound presentation text limit must be positive")
        if (
            not isinstance(max_concurrency, int)
            or isinstance(max_concurrency, bool)
            or max_concurrency < 1
        ):
            raise ValueError("outbound presentation concurrency must be positive")
        self._policy = policy
        self._timeout_seconds = timeout_seconds
        self._max_items = max_items
        self._max_text_characters = max_text_characters
        self._max_concurrency = max_concurrency
        self._active_tasks: set[asyncio.Task[OutboundMessage | None]] = set()
        self._invocation_count = 0
        self._delivery_count = 0
        self._suppression_count = 0
        self._failure_count = 0
        self._timeout_count = 0
        self._cancellation_count = 0
        self._cancellation_overrun_count = 0
        self._capacity_rejection_count = 0
        self._last_failure_code: OutboundPresentationFailureCode | None = None

    async def present(
        self,
        message: OutboundMessage,
        context: OutboundPresentationContext,
    ) -> OutboundMessage | None:
        self._invocation_count += 1
        if len(self._active_tasks) >= self._max_concurrency:
            self._capacity_rejection_count += 1
            self._record_failure(OutboundPresentationFailureCode.CAPACITY_EXHAUSTED)
            raise OutboundPresentationCapacityError(
                "outbound presentation policy task capacity is exhausted"
            )
        try:
            message = _immutable_outbound_message(
                message,
                max_items=self._max_items,
                max_text_characters=self._max_text_characters,
            )
        except OutboundPresentationError:
            self._record_failure(OutboundPresentationFailureCode.INVALID_OUTPUT)
            raise
        task = asyncio.create_task(
            self._policy.present(message, context),
            name="imagent-outbound-presentation",
        )
        self._track_task(task)
        try:
            done, _ = await asyncio.wait((task,), timeout=self._timeout_seconds)
            if not done:
                joined = await _cancel_and_join(task, timeout_seconds=self._timeout_seconds)
                raise OutboundPresentationTimeout(
                    "outbound presentation policy exceeded its configured lifetime",
                    cancellation_overrun=not joined,
                )
            output = task.result()
            if output is not None:
                output = validate_outbound_presentation(
                    output,
                    original=message,
                    max_items=self._max_items,
                    max_text_characters=self._max_text_characters,
                )
        except OutboundPresentationError:
            self._record_failure(OutboundPresentationFailureCode.INVALID_OUTPUT)
            raise
        except OutboundPresentationTimeout as error:
            self._timeout_count += 1
            if error.cancellation_overrun:
                self._cancellation_overrun_count += 1
            self._record_failure(OutboundPresentationFailureCode.TIMED_OUT)
            raise
        except asyncio.CancelledError:
            joined = True
            if not task.done():
                joined = await _cancel_and_join(task, timeout_seconds=self._timeout_seconds)
            self._cancellation_count += 1
            if not joined:
                self._cancellation_overrun_count += 1
            self._record_failure(OutboundPresentationFailureCode.CANCELLED)
            raise
        except BaseException:
            if not task.done():
                await _cancel_and_join(task, timeout_seconds=self._timeout_seconds)
            self._record_failure(OutboundPresentationFailureCode.POLICY_FAILED)
            raise
        if output is None:
            self._suppression_count += 1
        else:
            self._delivery_count += 1
        return output

    def diagnostic_facts(self) -> OutboundPresentationDiagnosticFacts:
        return OutboundPresentationDiagnosticFacts(
            invocation_count=self._invocation_count,
            delivery_count=self._delivery_count,
            suppression_count=self._suppression_count,
            failure_count=self._failure_count,
            timeout_count=self._timeout_count,
            cancellation_count=self._cancellation_count,
            cancellation_overrun_count=self._cancellation_overrun_count,
            capacity_rejection_count=self._capacity_rejection_count,
            last_failure_code=self._last_failure_code,
        )

    async def close(self) -> None:
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

    def _track_task(self, task: asyncio.Task[OutboundMessage | None]) -> None:
        self._active_tasks.add(task)
        task.add_done_callback(self._finish_task)

    def _finish_task(self, task: asyncio.Task[OutboundMessage | None]) -> None:
        self._active_tasks.discard(task)
        _consume_task_result(task)

    def _record_failure(self, code: OutboundPresentationFailureCode) -> None:
        self._failure_count += 1
        self._last_failure_code = code


def validate_outbound_presentation(
    output: object,
    *,
    original: OutboundMessage,
    max_items: int,
    max_text_characters: int,
) -> OutboundMessage:
    output = _immutable_outbound_message(
        output,
        max_items=max_items,
        max_text_characters=max_text_characters,
    )
    if (
        output.delivery_id != original.delivery_id
        or output.conversation_ref != original.conversation_ref
        or output.reply_to != original.reply_to
        or output.created_at != original.created_at
    ):
        raise OutboundPresentationError(
            "outbound presentation policy cannot change delivery routing identity"
        )
    original_attachments = tuple(
        item for item in original.content if isinstance(item, AttachmentContent)
    )
    output_attachments = tuple(
        item for item in output.content if isinstance(item, AttachmentContent)
    )
    if output_attachments != original_attachments:
        raise OutboundPresentationError(
            "outbound presentation policy cannot change attachment authority"
        )
    return output


def _immutable_outbound_message(
    message: object,
    *,
    max_items: int,
    max_text_characters: int,
) -> OutboundMessage:
    if not isinstance(message, OutboundMessage):
        raise OutboundPresentationError(
            "outbound presentation policy must receive and return OutboundMessage"
        )
    if not isinstance(message.content, tuple) or not message.content:
        raise OutboundPresentationError("outbound presentation content must be non-empty")
    if len(message.content) > max_items:
        raise OutboundPresentationError(
            "outbound presentation content exceeds the configured item limit"
        )
    if not all(isinstance(item, (TextContent, AttachmentContent)) for item in message.content):
        raise OutboundPresentationError(
            "outbound presentation contains an unsupported content item"
        )
    if sum(len(item.text) for item in message.content if isinstance(item, TextContent)) > (
        max_text_characters
    ):
        raise OutboundPresentationError("outbound presentation exceeds the configured text limit")
    content = tuple(
        replace(item, metadata=_bounded_metadata(item.metadata))
        if isinstance(item, AttachmentContent)
        else item
        for item in message.content
    )
    return replace(
        message,
        content=content,
        metadata=_bounded_metadata(message.metadata),
    )


def _bounded_metadata(metadata: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(metadata, Mapping):
        raise OutboundPresentationError("outbound presentation metadata must be a mapping")
    keys = list(islice(metadata, 17))
    if len(keys) > 16:
        raise OutboundPresentationError("outbound presentation metadata exceeds 16 items")
    copied: dict[str, object] = {}
    for key in keys:
        if not isinstance(key, str) or not key or len(key) > 64:
            raise OutboundPresentationError("outbound presentation metadata key is invalid")
        value = metadata[key]
        if isinstance(value, str):
            if len(value) > 256:
                raise OutboundPresentationError("outbound presentation metadata text is too long")
        elif value is None or isinstance(value, bool):
            pass
        elif isinstance(value, int):
            if not -(2**63) <= value <= 2**63 - 1:
                raise OutboundPresentationError(
                    "outbound presentation metadata integer is out of range"
                )
        elif not (isinstance(value, float) and math.isfinite(value)):
            raise OutboundPresentationError(
                "outbound presentation metadata must contain bounded scalars"
            )
        copied[key] = value
    return MappingProxyType(copied)


async def _cancel_and_join(
    task: asyncio.Task[OutboundMessage | None],
    *,
    timeout_seconds: float,
) -> bool:
    task.cancel()
    done, _ = await asyncio.wait((task,), timeout=timeout_seconds)
    if not done:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        return False
    _consume_task_result(task)
    return True


def _consume_task_result(task: asyncio.Task[OutboundMessage | None]) -> None:
    try:
        task.result()
    except BaseException:
        pass

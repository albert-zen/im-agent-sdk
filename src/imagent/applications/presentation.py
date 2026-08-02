from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from ..contracts import TextContent, TextFormat, ThreadRef
from ..diagnostics import (
    ApplicationPresentationDiagnosticFacts,
    ApplicationPresentationFailureCode,
)

_IDENTITY_MAX_CHARACTERS = 512
_KIND_MAX_CHARACTERS = 128
_FACT_TEXT_MAX_CHARACTERS = 8_000
_FACT_ITEMS_MAX = 100


class CodexLiveActivityKind(StrEnum):
    PLAN_UPDATED = "plan_updated"
    DIFF_UPDATED = "diff_updated"
    THREAD_STATUS_CHANGED = "thread_status_changed"
    THREAD_COMPACTED = "thread_compacted"
    MODEL_REROUTED = "model_rerouted"


class CodexLiveActivityMethod(StrEnum):
    PLAN_UPDATED = "turn/plan/updated"
    DIFF_UPDATED = "turn/diff/updated"
    THREAD_STATUS_CHANGED = "thread/status/changed"
    THREAD_COMPACTED = "thread/compacted"
    MODEL_REROUTED = "model/rerouted"


@dataclass(frozen=True, slots=True)
class CodexPlanStep:
    status: str
    step: str

    def __post_init__(self) -> None:
        _validate_bounded_text(self.status, "Codex plan status", limit=_KIND_MAX_CHARACTERS)
        _validate_bounded_text(self.step, "Codex plan step", limit=_FACT_TEXT_MAX_CHARACTERS)


@dataclass(frozen=True, slots=True)
class CodexLiveActivityFacts:
    event_id: str
    thread_ref: ThreadRef
    turn_id: str | None
    kind: CodexLiveActivityKind
    native_method: CodexLiveActivityMethod
    summary: str | None = None
    details: tuple[str, ...] = ()
    plan: tuple[CodexPlanStep, ...] = ()

    def __post_init__(self) -> None:
        _validate_bounded_text(
            self.event_id, "Codex event identity", limit=_IDENTITY_MAX_CHARACTERS
        )
        if self.turn_id is not None:
            _validate_bounded_text(
                self.turn_id,
                "Codex Turn identity",
                limit=_IDENTITY_MAX_CHARACTERS,
            )
        if not isinstance(self.kind, CodexLiveActivityKind) or not isinstance(
            self.native_method, CodexLiveActivityMethod
        ):
            raise TypeError("Codex live activity kind and method must use fixed vocabularies")
        if self.summary is not None:
            _validate_bounded_text(
                self.summary,
                "Codex live summary",
                limit=_FACT_TEXT_MAX_CHARACTERS,
            )
        _validate_text_tuple(self.details, "Codex live details")
        if not isinstance(self.plan, tuple) or len(self.plan) > _FACT_ITEMS_MAX:
            raise ValueError("Codex live plan must be a bounded tuple")
        if not all(isinstance(item, CodexPlanStep) for item in self.plan):
            raise TypeError("Codex live plan entries must be CodexPlanStep values")


@dataclass(frozen=True, slots=True)
class T3ActivityFacts:
    activity_id: str
    thread_ref: ThreadRef
    turn_id: str
    kind: str
    summary: str | None
    detail: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_bounded_text(
            self.activity_id, "T3 activity identity", limit=_IDENTITY_MAX_CHARACTERS
        )
        _validate_bounded_text(self.turn_id, "T3 Turn identity", limit=_IDENTITY_MAX_CHARACTERS)
        _validate_bounded_text(self.kind, "T3 activity kind", limit=_KIND_MAX_CHARACTERS)
        for label, value in (
            ("T3 activity summary", self.summary),
            ("T3 activity detail", self.detail),
        ):
            if value is not None:
                _validate_bounded_text(value, label, limit=_FACT_TEXT_MAX_CHARACTERS)
        if not isinstance(self.created_at, datetime):
            raise TypeError("T3 activity creation time must be a datetime")


@dataclass(frozen=True, slots=True)
class ApplicationTextPresentation:
    content: tuple[TextContent, ...]


class CodexLiveActivityPresenter(Protocol):
    async def present_live_activity(
        self,
        facts: CodexLiveActivityFacts,
    ) -> ApplicationTextPresentation | None: ...


class T3ActivityPresenter(Protocol):
    async def present_activity(
        self,
        facts: T3ActivityFacts,
    ) -> ApplicationTextPresentation | None: ...


@dataclass(frozen=True, slots=True)
class ApplicationPresentationLimits:
    timeout_seconds: float = 30.0
    max_items: int = 16
    max_text_characters: int = 16_384
    max_concurrency: int = 16
    max_seen_identities: int = 4_096

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("application presentation timeout must be finite and positive")
        for label, value in (
            ("item", self.max_items),
            ("text", self.max_text_characters),
            ("concurrency", self.max_concurrency),
            ("seen identity", self.max_seen_identities),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"application presentation {label} limit must be positive")


class ApplicationPresentationError(ValueError):
    """An A1 presenter returned content outside its bounded text contract."""


class ApplicationPresentationTimeout(TimeoutError):
    def __init__(self, *, cancellation_overrun: bool) -> None:
        super().__init__("application presenter exceeded its configured lifetime")
        self.cancellation_overrun = cancellation_overrun


class ApplicationPresentationCapacityError(RuntimeError):
    """The configured finite A1 task capacity is occupied."""


class ApplicationPresentationRuntime:
    """Bound one concrete adapter's A1 invocations and redacted facts."""

    def __init__(self, limits: ApplicationPresentationLimits) -> None:
        self._limits = limits
        self._active_tasks: set[asyncio.Task[ApplicationTextPresentation | None]] = set()
        self._invocation_count = 0
        self._success_count = 0
        self._omission_count = 0
        self._failure_count = 0
        self._timeout_count = 0
        self._cancellation_count = 0
        self._cancellation_overrun_count = 0
        self._capacity_rejection_count = 0
        self._last_failure_code: ApplicationPresentationFailureCode | None = None

    async def invoke(
        self,
        call: Callable[[], Awaitable[ApplicationTextPresentation | None]],
    ) -> ApplicationTextPresentation | None:
        self._invocation_count += 1
        if len(self._active_tasks) >= self._limits.max_concurrency:
            self._capacity_rejection_count += 1
            self._record_failure(ApplicationPresentationFailureCode.CAPACITY_EXHAUSTED)
            raise ApplicationPresentationCapacityError(
                "application presentation task capacity is exhausted"
            )
        task = asyncio.create_task(
            _invoke(call),
            name="imagent-application-presentation",
        )
        self._track_task(task)
        try:
            done, _ = await asyncio.wait((task,), timeout=self._limits.timeout_seconds)
            if not done:
                joined = await _cancel_and_join(task, timeout_seconds=self._limits.timeout_seconds)
                raise ApplicationPresentationTimeout(cancellation_overrun=not joined)
            output = task.result()
            if output is not None:
                output = _validate_output(output, self._limits)
        except ApplicationPresentationError:
            self._record_failure(ApplicationPresentationFailureCode.INVALID_OUTPUT)
            raise
        except ApplicationPresentationTimeout as error:
            self._timeout_count += 1
            if error.cancellation_overrun:
                self._cancellation_overrun_count += 1
            self._record_failure(ApplicationPresentationFailureCode.TIMED_OUT)
            raise
        except asyncio.CancelledError:
            joined = True
            if not task.done():
                joined = await _cancel_and_join(task, timeout_seconds=self._limits.timeout_seconds)
            self._cancellation_count += 1
            if not joined:
                self._cancellation_overrun_count += 1
            self._record_failure(ApplicationPresentationFailureCode.CANCELLED)
            raise
        except BaseException:
            if not task.done():
                await _cancel_and_join(task, timeout_seconds=self._limits.timeout_seconds)
            self._record_failure(ApplicationPresentationFailureCode.PRESENTER_FAILED)
            raise
        if output is None:
            self._omission_count += 1
        else:
            self._success_count += 1
        return output

    def diagnostic_facts(self) -> ApplicationPresentationDiagnosticFacts:
        return ApplicationPresentationDiagnosticFacts(
            invocation_count=self._invocation_count,
            success_count=self._success_count,
            omission_count=self._omission_count,
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
        done, pending = await asyncio.wait(tasks, timeout=self._limits.timeout_seconds)
        for task in done:
            self._finish_task(task)
        for task in pending:
            task.cancel()

    def _track_task(self, task: asyncio.Task[ApplicationTextPresentation | None]) -> None:
        self._active_tasks.add(task)
        task.add_done_callback(self._finish_task)

    def _finish_task(self, task: asyncio.Task[ApplicationTextPresentation | None]) -> None:
        self._active_tasks.discard(task)
        _consume_task_result(task)

    def _record_failure(self, code: ApplicationPresentationFailureCode) -> None:
        self._failure_count += 1
        self._last_failure_code = code


def _validate_output(
    output: ApplicationTextPresentation,
    limits: ApplicationPresentationLimits,
) -> ApplicationTextPresentation:
    if not isinstance(output, ApplicationTextPresentation):
        raise ApplicationPresentationError(
            "application presenter must return ApplicationTextPresentation or None"
        )
    content = output.content
    if not isinstance(content, tuple) or not content or len(content) > limits.max_items:
        raise ApplicationPresentationError("application presenter returned unbounded content")
    canonical: list[TextContent] = []
    total = 0
    for item in content:
        if (
            not isinstance(item, TextContent)
            or not isinstance(item.text, str)
            or not item.text.strip()
            or not isinstance(item.format, TextFormat)
        ):
            raise ApplicationPresentationError(
                "application presenter returned invalid text content"
            )
        total += len(item.text)
        if total > limits.max_text_characters:
            raise ApplicationPresentationError(
                "application presenter exceeded the configured text limit"
            )
        canonical.append(TextContent(item.text, item.format))
    return ApplicationTextPresentation(tuple(canonical))


def _validate_bounded_text(value: str, label: str, *, limit: int) -> None:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{label} must be non-empty and at most {limit} characters")


def _validate_text_tuple(values: tuple[str, ...], label: str) -> None:
    if not isinstance(values, tuple) or len(values) > _FACT_ITEMS_MAX:
        raise ValueError(f"{label} must be a bounded tuple")
    for value in values:
        _validate_bounded_text(value, label, limit=_FACT_TEXT_MAX_CHARACTERS)


async def _cancel_and_join(
    task: asyncio.Task[ApplicationTextPresentation | None],
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


async def _invoke(
    call: Callable[[], Awaitable[ApplicationTextPresentation | None]],
) -> ApplicationTextPresentation | None:
    return await call()


def _consume_task_result(task: asyncio.Task[ApplicationTextPresentation | None]) -> None:
    try:
        task.result()
    except BaseException:
        pass

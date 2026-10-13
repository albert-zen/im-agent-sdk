from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from ...applications.contract import ApplicationInputOutcomeUnknown, ApplicationInputRejected
from ...interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
    TextFormat,
)
from ..admission import ClaimedInbound
from ..diagnostics import (
    InboundFailurePresentationFailureCode,
    InboundFailurePresenterDiagnosticFacts,
)
from ..persistence.repository_contracts import IdempotencyRepository
from ..persistence.state_contracts import QueuedInputOrigin, validate_queued_input_origin
from ..persistence.store import (
    EffectReceiptCapacityError,
    GatewayStoreSession,
    QueuedInputFenceConflict,
    StaleRuntimeFence,
)
from .dispatch import InputPostAcceptanceError


class InboundFailurePhase(StrEnum):
    PRE_ACCEPTANCE = "pre_acceptance"
    OUTCOME_UNKNOWN = "outcome_unknown"
    TURN_ENDED = "turn_ended"
    THREAD_BUSY = "thread_busy"
    POST_ACCEPTANCE = "post_acceptance"


class InboundFailurePresenter(Protocol):
    """Render one bounded failure phase for Gateway-fixed origin identity."""

    async def present_failure(
        self,
        phase: InboundFailurePhase,
        *,
        conversation_ref: ConversationRef,
        delivery_id: str,
        reply_to_message_id: str,
    ) -> OutboundMessage: ...


class InboundFailurePresentationError(ValueError):
    """The I2 presenter changed fixed identity or returned invalid content."""


class InboundFailurePresentationTimeout(TimeoutError):
    def __init__(self, message: str, *, cancellation_overrun: bool) -> None:
        super().__init__(message)
        self.cancellation_overrun = cancellation_overrun


class InboundFailurePresentationCapacityError(RuntimeError):
    """The configured finite I2 render-task capacity is occupied."""


@dataclass(frozen=True, slots=True)
class InboundFailurePresentation:
    phase: InboundFailurePhase
    conversation_ref: ConversationRef
    delivery_id: str
    reply_to_message_id: str


class InboundFailurePresentationRuntime:
    """Bound I2 rendering without claim or delivery authority."""

    def __init__(
        self,
        presenter: InboundFailurePresenter,
        *,
        timeout_seconds: float,
        max_items: int,
        max_text_characters: int,
        max_concurrency: int,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("inbound failure presentation timeout must be finite and positive")
        if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 1:
            raise ValueError("inbound failure presentation item limit must be positive")
        if (
            not isinstance(max_text_characters, int)
            or isinstance(max_text_characters, bool)
            or max_text_characters < 1
        ):
            raise ValueError("inbound failure presentation text limit must be positive")
        if (
            not isinstance(max_concurrency, int)
            or isinstance(max_concurrency, bool)
            or max_concurrency < 1
        ):
            raise ValueError("inbound failure presentation concurrency must be positive")
        self._presenter = presenter
        self._timeout_seconds = timeout_seconds
        self._max_items = max_items
        self._max_text_characters = max_text_characters
        self._max_concurrency = max_concurrency
        self._active_tasks: set[asyncio.Task[OutboundMessage]] = set()
        self._invocation_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._timeout_count = 0
        self._cancellation_count = 0
        self._cancellation_overrun_count = 0
        self._capacity_rejection_count = 0
        self._last_failure_code: InboundFailurePresentationFailureCode | None = None

    async def present(self, facts: InboundFailurePresentation) -> OutboundMessage:
        self._invocation_count += 1
        if len(self._active_tasks) >= self._max_concurrency:
            self._capacity_rejection_count += 1
            self._record_failure(InboundFailurePresentationFailureCode.CAPACITY_EXHAUSTED)
            raise InboundFailurePresentationCapacityError(
                "inbound failure presenter task capacity is exhausted"
            )
        task = asyncio.create_task(
            self._presenter.present_failure(
                facts.phase,
                conversation_ref=facts.conversation_ref,
                delivery_id=facts.delivery_id,
                reply_to_message_id=facts.reply_to_message_id,
            ),
            name="imagent-inbound-failure-presentation",
        )
        self._track_task(task)
        try:
            done, _ = await asyncio.wait((task,), timeout=self._timeout_seconds)
            if not done:
                joined = await _cancel_and_join(
                    task,
                    timeout_seconds=self._timeout_seconds,
                )
                raise InboundFailurePresentationTimeout(
                    "inbound failure presenter exceeded its configured lifetime",
                    cancellation_overrun=not joined,
                )
            output = task.result()
            output = _validate_presentation(
                output,
                facts,
                max_items=self._max_items,
                max_text_characters=self._max_text_characters,
            )
        except InboundFailurePresentationError:
            self._record_failure(InboundFailurePresentationFailureCode.INVALID_OUTPUT)
            raise
        except InboundFailurePresentationTimeout as error:
            self._timeout_count += 1
            if error.cancellation_overrun:
                self._cancellation_overrun_count += 1
            self._record_failure(InboundFailurePresentationFailureCode.TIMED_OUT)
            raise
        except asyncio.CancelledError:
            joined = True
            if not task.done():
                joined = await _cancel_and_join(task, timeout_seconds=self._timeout_seconds)
            self._cancellation_count += 1
            if not joined:
                self._cancellation_overrun_count += 1
            self._record_failure(InboundFailurePresentationFailureCode.CANCELLED)
            raise
        except BaseException:
            if not task.done():
                await _cancel_and_join(task, timeout_seconds=self._timeout_seconds)
            self._record_failure(InboundFailurePresentationFailureCode.PRESENTER_FAILED)
            raise
        self._success_count += 1
        return output

    def diagnostic_facts(self) -> InboundFailurePresenterDiagnosticFacts:
        return InboundFailurePresenterDiagnosticFacts(
            invocation_count=self._invocation_count,
            success_count=self._success_count,
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

    def _track_task(self, task: asyncio.Task[OutboundMessage]) -> None:
        self._active_tasks.add(task)
        task.add_done_callback(self._finish_task)

    def _finish_task(self, task: asyncio.Task[OutboundMessage]) -> None:
        self._active_tasks.discard(task)
        _consume_task_result(task)

    def _record_failure(self, code: InboundFailurePresentationFailureCode) -> None:
        self._failure_count += 1
        self._last_failure_code = code


class InboundEffectFence:
    """One claim owner with an ordinary and an atomic queued transition."""

    def __init__(
        self,
        claimed: ClaimedInbound,
        repository: IdempotencyRepository,
        session: GatewayStoreSession | None = None,
    ) -> None:
        self._claimed = claimed
        self._repository = repository
        self._session = session
        self.entered = False
        self.started = False
        self.origin: QueuedInputOrigin | None = None

    async def __call__(self) -> None:
        self.entered = True
        await self._repository.mark_side_effect_started(
            self._claimed.scope, self._claimed.key, owner_token=self._claimed.owner_token
        )
        self.started = True

    async def queue(self, origin: QueuedInputOrigin) -> None:
        if self._session is None:
            raise RuntimeError("queued input requires a coherent GatewayStore")
        validate_queued_input_origin(origin)
        self.origin = origin
        self.entered = True
        try:
            await self._session.fence_queued_input(
                origin,
                claim_scope=self._claimed.scope,
                claim_key=self._claimed.key,
                owner_token=self._claimed.owner_token,
            )
        except (EffectReceiptCapacityError, QueuedInputFenceConflict, StaleRuntimeFence):
            # These typed store outcomes prove the atomic fence was rejected.
            # Generic failures/cancellation can lose a committed acknowledgement
            # and must retain conservative unknown classification instead.
            self.entered = False
            self.origin = None
            raise
        self.started = True


async def handle_claimed_inbound(
    claimed: ClaimedInbound,
    *,
    process: Callable[[InboundEffectFence], Awaitable[None]],
    session: GatewayStoreSession | None = None,
    on_queued_unknown: Callable[[QueuedInputOrigin], Awaitable[None]] | None = None,
    idempotency: IdempotencyRepository,
    presentation: InboundFailurePresentationRuntime | None,
    deliver: Callable[[OutboundMessage], Awaitable[object]],
) -> None:
    fence = InboundEffectFence(claimed, idempotency, session)

    try:
        await process(fence)
    except InputPostAcceptanceError as error:
        try:
            await idempotency.complete(
                claimed.scope,
                claimed.key,
                owner_token=claimed.owner_token,
            )
        except BaseException as terminal_error:
            raise terminal_error from error.cause
        if presentation is None:
            raise error.cause.with_traceback(error.cause.__traceback__) from None
        await _present_failure(
            claimed.message,
            InboundFailurePhase.POST_ACCEPTANCE,
            presentation,
            deliver,
        )
    except ApplicationInputRejected as error:
        # Authoritative negative acceptance proves no canonical input append.
        # Complete even without presentation; the same IM input is not replayed.
        await idempotency.complete(
            claimed.scope,
            claimed.key,
            owner_token=claimed.owner_token,
        )
        if presentation is None:
            raise
        await _present_failure(
            claimed.message,
            InboundFailurePhase(error.reason),
            presentation,
            deliver,
        )
    except ApplicationInputOutcomeUnknown as error:
        if fence.origin is not None and on_queued_unknown is not None:
            await on_queued_unknown(fence.origin)
            return
        if presentation is None:
            raise error.cause.with_traceback(error.cause.__traceback__) from None
        await _present_failure(
            claimed.message,
            InboundFailurePhase.OUTCOME_UNKNOWN,
            presentation,
            deliver,
        )
    except asyncio.CancelledError:
        if not fence.entered:
            await idempotency.release(
                claimed.scope,
                claimed.key,
                owner_token=claimed.owner_token,
            )
        raise
    except BaseException as error:
        if fence.entered:
            if (
                fence.origin is not None
                and on_queued_unknown is not None
                and isinstance(error, Exception)
            ):
                await on_queued_unknown(fence.origin)
                return
            if presentation is None or not isinstance(error, Exception):
                raise
            await _present_failure(
                claimed.message,
                InboundFailurePhase.OUTCOME_UNKNOWN,
                presentation,
                deliver,
            )
            return
        if presentation is None or not isinstance(error, Exception):
            await idempotency.release(
                claimed.scope,
                claimed.key,
                owner_token=claimed.owner_token,
            )
            raise
        await idempotency.complete(
            claimed.scope,
            claimed.key,
            owner_token=claimed.owner_token,
        )
        await _present_failure(
            claimed.message,
            InboundFailurePhase.PRE_ACCEPTANCE,
            presentation,
            deliver,
        )
    else:
        await idempotency.complete(
            claimed.scope,
            claimed.key,
            owner_token=claimed.owner_token,
        )


async def _present_failure(
    message: InboundMessage,
    phase: InboundFailurePhase,
    presentation: InboundFailurePresentationRuntime,
    deliver: Callable[[OutboundMessage], Awaitable[object]],
) -> None:
    facts = InboundFailurePresentation(
        phase=phase,
        conversation_ref=message.conversation_ref,
        delivery_id=_failure_delivery_id(message),
        reply_to_message_id=message.message_id,
    )
    await deliver(await presentation.present(facts))


def _failure_delivery_id(message: InboundMessage) -> str:
    return (
        f"imagent:gateway:{message.conversation_ref.channel_instance_id}:"
        f"{message.conversation_ref.native_conversation_id}:"
        f"{message.message_id}:inbound-failure"
    )


def _validate_presentation(
    output: OutboundMessage,
    facts: InboundFailurePresentation,
    *,
    max_items: int,
    max_text_characters: int,
) -> OutboundMessage:
    if not isinstance(output, OutboundMessage):
        raise InboundFailurePresentationError(
            "inbound failure presenter must return an OutboundMessage"
        )
    if output.conversation_ref != facts.conversation_ref:
        raise InboundFailurePresentationError(
            "inbound failure presenter changed the Conversation identity"
        )
    if output.delivery_id != facts.delivery_id:
        raise InboundFailurePresentationError(
            "inbound failure presenter changed the delivery identity"
        )
    if output.reply_to != facts.reply_to_message_id:
        raise InboundFailurePresentationError(
            "inbound failure presenter changed the reply identity"
        )
    content = output.content
    if not isinstance(content, tuple) or not content or len(content) > max_items:
        raise InboundFailurePresentationError(
            "inbound failure presenter returned unbounded content"
        )
    text_content: list[TextContent] = []
    for item in content:
        if not isinstance(item, TextContent):
            raise InboundFailurePresentationError(
                "inbound failure presenter must return text-only content"
            )
        if not isinstance(item.text, str) or not isinstance(item.format, TextFormat):
            raise InboundFailurePresentationError(
                "inbound failure presenter returned invalid text content"
            )
        text_content.append(item)
    if any(not item.text.strip() for item in text_content):
        raise InboundFailurePresentationError(
            "inbound failure presenter returned empty text content"
        )
    if sum(len(item.text) for item in text_content) > max_text_characters:
        raise InboundFailurePresentationError(
            "inbound failure presenter exceeded the configured text limit"
        )
    if not isinstance(output.metadata, dict) or output.metadata:
        raise InboundFailurePresentationError(
            "inbound failure presenter cannot attach arbitrary metadata"
        )
    if not isinstance(output.created_at, datetime):
        raise InboundFailurePresentationError(
            "inbound failure presenter returned an invalid creation time"
        )
    return OutboundMessage(
        delivery_id=facts.delivery_id,
        conversation_ref=facts.conversation_ref,
        content=tuple(text_content),
        created_at=output.created_at,
        reply_to=facts.reply_to_message_id,
    )


async def _cancel_and_join(
    task: asyncio.Task[OutboundMessage],
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


def _consume_task_result(task: asyncio.Task[OutboundMessage]) -> None:
    try:
        task.result()
    except BaseException:
        pass

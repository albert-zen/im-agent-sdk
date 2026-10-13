"""Lease-fenced original input association; native owns all queue execution."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import cast

from ...applications.contract import (
    AgentApplicationAdapter,
    InputContinuationPreference,
    QueuedInputDispatch,
    QueuedInputReceipt,
    QueuedInputRecovery,
    QueuedInputState,
    ThreadRef,
    validate_queued_input_dispatch,
)
from ...applications.queued_input import (
    MAX_QUEUED_INPUT_READ_IDS,
    QueuedInputControl,
    QueuedInputNoticeKind,
    QueuedInputObserver,
    QueuedInputSnapshotReader,
)
from ...interaction.messages import ConversationRef, InboundMessage
from ...interaction.operations import OperationErrorCode
from ..outcomes import Failed, Succeeded
from ..persistence.effects import ActionError, ActionErrorCode, EffectValue, KnownNativeOutcome
from ..persistence.state_contracts import (
    QueuedInputCorrelation,
    QueuedInputOrigin,
    TurnReplyCorrelation,
)
from ..persistence.store import GatewayStoreSession
from ..projection.request_correlation import derive_turn_reply_correlation_id
from ..queued_input import QueuedInputNoticeReason, QueuedInputStatus, QueuedInputStatusKind

logger = logging.getLogger(__name__)


def _actor_fingerprint(actor: str) -> str:
    return hashlib.sha256(b"imagent:queued-actor:v1\0" + actor.encode("utf-8")).hexdigest()


class QueuedInputRuntime:
    """Persist only original associations and canonical positive receipt evidence."""

    def __init__(
        self,
        session: GatewayStoreSession,
        applications: Mapping[str, AgentApplicationAdapter],
        *,
        publish: Callable[[QueuedInputCorrelation, QueuedInputNoticeReason, str], Awaitable[None]],
        max_threads: int,
        lifecycle_timeout: float,
        retry_initial: float,
        retry_max: float,
    ) -> None:
        self._session = session
        self._applications = applications
        self._publish = publish
        self._max_threads = max_threads
        self._lifecycle_timeout = lifecycle_timeout
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._workers: dict[ThreadRef, asyncio.Task[None]] = {}
        self._ready: dict[ThreadRef, asyncio.Future[None]] = {}
        self._closed = False

    def origin(
        self,
        plan: QueuedInputDispatch,
        message: InboundMessage,
        *,
        thread_ref: ThreadRef,
        binding_generation: int,
        client_message_id: str,
    ) -> QueuedInputOrigin:
        validate_queued_input_dispatch(plan)
        if plan.thread_ref != thread_ref or plan.client_message_id != client_message_id:
            raise ValueError("queued plan changed original Thread or client identity")
        return QueuedInputOrigin(
            conversation_ref=message.conversation_ref,
            message_id=message.message_id,
            actor_fingerprint=_actor_fingerprint(message.sender),
            thread_ref=thread_ref,
            binding_generation=binding_generation,
            client_message_id=client_message_id,
            input_fingerprint=plan.input_fingerprint,
            continuation=InputContinuationPreference.QUEUE,
        )

    async def accept(
        self, origin: QueuedInputOrigin, receipt: QueuedInputReceipt
    ) -> QueuedInputCorrelation:
        correlation = None
        if receipt.state is QueuedInputState.ADMITTED:
            if receipt.turn_ref is None:
                raise ValueError("admitted queued receipt lacks original Turn")
            correlation = TurnReplyCorrelation(
                correlation_id=derive_turn_reply_correlation_id(receipt.turn_ref),
                turn_ref=receipt.turn_ref,
                client_message_id=origin.client_message_id,
                conversation_ref=origin.conversation_ref,
                reply_to_message_id=origin.message_id,
                created_at=datetime.now(UTC),
            )
        return await self._session.merge_queued_input_receipt(
            origin, receipt, turn_correlation=correlation
        )

    async def reconcile_thread(
        self, thread_ref: ThreadRef, *, publish: bool = False
    ) -> frozenset[str]:
        rows = await self._session.list_queued_inputs(thread_ref)
        if not rows:
            return frozenset()
        missing: set[str] = set()
        application = self._applications.get(thread_ref.project_ref.application_instance_id)
        if not callable(getattr(application, "read_queued_input_receipt", None)):
            raise NotImplementedError("Application cannot recover queued input receipts")
        recovery = cast(QueuedInputRecovery, application)
        for offset in range(0, len(rows), MAX_QUEUED_INPUT_READ_IDS):
            cohort = rows[offset : offset + MAX_QUEUED_INPUT_READ_IDS]
            if isinstance(application, QueuedInputSnapshotReader):
                receipts = await application.read_queued_input_receipts(
                    thread_ref, tuple(row.origin.client_message_id for row in cohort)
                )
                if not isinstance(receipts, tuple) or len(receipts) != len(cohort):
                    raise ValueError(
                        "queued snapshot did not preserve the exact requested identities"
                    )
            else:
                receipts = tuple(
                    [
                        await recovery.read_queued_input_receipt(
                            thread_ref, row.origin.client_message_id
                        )
                        for row in cohort
                    ]
                )
            for row, receipt in zip(cohort, receipts, strict=True):
                if receipt is None:
                    missing.add(row.origin.client_message_id)
                else:
                    merged = await self.accept(row.origin, receipt)
                    if publish and merged.receipt != row.receipt:
                        await self._notify_status(merged, live=True)

        return frozenset(missing)

    async def _notify_status(self, row: QueuedInputCorrelation, *, live: bool = False) -> None:
        status = self.status(row)
        reason = (
            QueuedInputNoticeReason.ADMISSION
            if status.state in (QueuedInputStatusKind.UNKNOWN, QueuedInputStatusKind.PENDING)
            else QueuedInputNoticeReason.STATE_CHANGED
        )
        if live:
            await self._publish_live_notice(row, reason, status.state.value)
        else:
            await self._publish(row, reason, status.state.value)

    async def _publish_live_notice(
        self, row: QueuedInputCorrelation, reason: QueuedInputNoticeReason, notice_id: str
    ) -> None:
        try:
            await self._publish(row, reason, notice_id)
        except Exception as error:
            # Product rendering/delivery does not own native observation. Do
            # not log callback text or retain it as durable native failure.
            logger.warning("Queued notice failed; observation continues (%s)", type(error).__name__)

    async def notify_admission(self, origin: QueuedInputOrigin) -> None:
        row = await self._session.get_queued_input(origin.conversation_ref, origin.message_id)
        if row is not None:
            await self._notify_status(row)

    async def ensure_observing(self, thread_ref: ThreadRef) -> None:
        if self._closed:
            raise RuntimeError("queued observer is closed")
        task = self._workers.get(thread_ref)
        if task is not None and task.done():
            self._workers.pop(thread_ref, None)
            self._ready.pop(thread_ref, None)
            task.exception() if not task.cancelled() else None
            task = None
        if task is None:
            if len(self._workers) >= self._max_threads:
                raise RuntimeError("queued observer Thread capacity exhausted")
            ready = asyncio.get_running_loop().create_future()
            ready.add_done_callback(
                lambda future: future.exception() if not future.cancelled() else None
            )
            self._ready[thread_ref] = ready
            self._workers[thread_ref] = asyncio.create_task(
                self._observe(thread_ref, ready), name="imagent-queued-observer"
            )
        await asyncio.shield(self._ready[thread_ref])

    async def _observe(self, thread_ref: ThreadRef, ready: asyncio.Future[None]) -> None:
        application = self._applications[thread_ref.project_ref.application_instance_id]
        try:
            if not isinstance(application, QueuedInputObserver):
                raise NotImplementedError("Application cannot observe queued input admission")
            failures = 0
            while not self._closed:
                subscription = None
                try:
                    subscription = await application.subscribe_queued_inputs(thread_ref)
                    await self.reconcile_thread(thread_ref, publish=ready.done())
                    failures = 0
                    if not ready.done():
                        ready.set_result(None)
                    async for notice in subscription:
                        if notice.thread_ref != thread_ref:
                            raise ValueError("queue observer substituted Thread scope")
                        await self.reconcile_thread(thread_ref, publish=True)
                        if notice.kind is QueuedInputNoticeKind.ATTEMPT_FAILED:
                            rows = await self._session.list_queued_inputs(thread_ref)
                            for row in rows:
                                receipt = row.receipt
                                if (
                                    receipt is not None
                                    and receipt.state is QueuedInputState.QUEUED
                                    and receipt.client_message_id == notice.client_message_id
                                    and receipt.queued_item_id == notice.queued_item_id
                                ):
                                    await self._publish_live_notice(
                                        row,
                                        QueuedInputNoticeReason.ATTEMPT_FAILED,
                                        notice.notice_id,
                                    )
                except Exception as error:
                    if not ready.done():
                        raise
                    # Only observation is retried. No input or execution Resume
                    # authority exists here; projection remains canonically fenced.
                    failures += 1
                    logger.warning("Queued observation recovering (%s)", type(error).__name__)
                finally:
                    if subscription is not None:
                        await subscription.aclose()
                delay = min(self._retry_initial * (2 ** min(failures, 10)), self._retry_max)
                await asyncio.sleep(delay)
        except BaseException as error:
            if not ready.done():
                ready.set_exception(error)
            raise

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._workers.values())
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self._lifecycle_timeout)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                raise TimeoutError("queued observer did not join during shutdown")
        self._workers.clear()
        self._ready.clear()

    async def restore(self) -> None:
        threads = dict.fromkeys(
            row.origin.thread_ref for row in await self._session.list_queued_inputs()
        )
        for thread in threads:
            await self.ensure_observing(thread)
            await self.reconcile_thread(thread)

    async def ensure_turn(self, thread_ref: ThreadRef, turn_id: str) -> None:
        if await self._session.get_turn_reply_correlation(thread_ref, turn_id) is not None:
            return
        rows = await self._session.list_queued_inputs(thread_ref)
        if not rows:
            return
        missing = await self.reconcile_thread(thread_ref)
        if await self._session.get_turn_reply_correlation(thread_ref, turn_id) is not None:
            return
        unresolved = await self._session.list_queued_inputs(thread_ref)
        if missing or any(row.receipt is None for row in unresolved):
            raise RuntimeError("queued input origin is unknown; Turn projection is fenced")
        # A complete canonical read established every original queued lineage.
        # An unrelated native Turn is not one of these queued inputs.

    async def read(
        self, conversation_ref: ConversationRef, actor: str, message_id: str
    ) -> Succeeded[QueuedInputStatus] | Failed[ActionError]:
        row = await self._session.get_queued_input(conversation_ref, message_id)
        if row is None:
            return Failed(
                ActionError(ActionErrorCode.NATIVE_REJECTED, OperationErrorCode.NOT_FOUND)
            )
        if row.origin.actor_fingerprint != _actor_fingerprint(actor):
            return Failed(
                ActionError(
                    ActionErrorCode.NATIVE_REJECTED, OperationErrorCode.UNAUTHORIZED_DESTINATION
                )
            )
        try:
            missing = await self.reconcile_thread(row.origin.thread_ref)
        except Exception:
            return Failed(ActionError(ActionErrorCode.NATIVE_OUTCOME_UNKNOWN))
        current = await self._session.get_queued_input(conversation_ref, message_id)
        assert current is not None
        if current.origin.client_message_id in missing:
            return Succeeded(QueuedInputStatus(message_id, QueuedInputStatusKind.UNKNOWN))
        return Succeeded(self.status(current))

    async def cancel(
        self,
        conversation_ref: ConversationRef,
        actor: str,
        message_id: str,
        *,
        reconcile_only: bool,
    ) -> KnownNativeOutcome | None:
        read = await self.read(conversation_ref, actor, message_id)
        if isinstance(read, Failed):
            return read if not reconcile_only else None
        if read.value.state is QueuedInputStatusKind.CANCELLED:
            return Succeeded(EffectValue())
        if read.value.state is QueuedInputStatusKind.ADMITTED:
            return Failed(ActionError(ActionErrorCode.QUEUE_ALREADY_ADMITTED))
        if read.value.state is QueuedInputStatusKind.UNKNOWN:
            return (
                None
                if reconcile_only
                else Failed(ActionError(ActionErrorCode.NATIVE_OUTCOME_UNKNOWN))
            )
        if reconcile_only:
            return None
        row = await self._session.get_queued_input(conversation_ref, message_id)
        if row is None or row.receipt is None:
            raise RuntimeError("queued cancellation lacks canonical lineage")
        application = self._applications.get(
            row.origin.thread_ref.project_ref.application_instance_id
        )
        if not isinstance(application, QueuedInputControl):
            return Failed(ActionError(ActionErrorCode.UNSUPPORTED))
        receipt = await application.cancel_queued_input_receipt(row.receipt)
        merged = await self.accept(row.origin, receipt)
        if merged.receipt is not None and merged.receipt.state is QueuedInputState.CANCELLED:
            return Succeeded(EffectValue())
        if merged.receipt is not None and merged.receipt.state is QueuedInputState.ADMITTED:
            return Failed(ActionError(ActionErrorCode.QUEUE_ALREADY_ADMITTED))
        raise RuntimeError("queued cancellation did not establish a terminal admission")

    @staticmethod
    def status(row: QueuedInputCorrelation) -> QueuedInputStatus:
        state = QueuedInputStatusKind.UNKNOWN
        if row.receipt is not None:
            state = {
                QueuedInputState.QUEUED: QueuedInputStatusKind.PENDING,
                QueuedInputState.ADMITTED: QueuedInputStatusKind.ADMITTED,
                QueuedInputState.CANCELLED: QueuedInputStatusKind.CANCELLED,
            }[row.receipt.state]
        return QueuedInputStatus(row.origin.message_id, state)

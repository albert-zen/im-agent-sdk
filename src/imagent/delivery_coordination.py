from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

from .adapters import ChannelAdapter
from .contracts import (
    ConversationRef,
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySegmentReceipt,
    DeliverySegmentStatus,
    OutboundMessage,
    validate_delivery_receipt_for_content,
)
from .delivery_planning import (
    DeliveryPlan,
    DeliveryPlanner,
    DeliveryPlanningError,
    PlannedDeliverySegment,
)
from .interaction.media import AttachmentContent
from .keyed_locks import KeyedLockRegistry


@dataclass(frozen=True, slots=True)
class DeliveryHandle:
    admitted: bool
    _future: asyncio.Future[DeliveryReceipt]
    _task: asyncio.Task[None] | None = None

    async def result(self) -> DeliveryReceipt:
        try:
            receipt = await asyncio.shield(self._future)
            if self._task is not None:
                await asyncio.shield(self._task)
            return receipt
        except asyncio.CancelledError:
            # Awaited delivery owns its worker lifetime. Join cancellation so
            # callers may safely release temporary artifacts afterwards.
            await self.cancel()
            raise

    async def cancel(self) -> None:
        task = self._task
        if task is None:
            if not self._future.done():
                self._future.cancel()
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class DeliveryCoordinatorConfig:
    max_pending: int = 256
    max_pending_per_destination: int = 32
    max_concurrent_destinations: int = 16
    max_source_items_per_delivery: int = 256
    max_segments_per_delivery: int = 256
    max_attempts: int = 1
    retry_initial_seconds: float = 0.25
    retry_max_seconds: float = 5.0
    backpressure_retry_after_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_pending < 1:
            raise ValueError("max_pending must be positive")
        if self.max_pending_per_destination < 1:
            raise ValueError("max_pending_per_destination must be positive")
        if self.max_concurrent_destinations < 1:
            raise ValueError("max_concurrent_destinations must be positive")
        if self.max_source_items_per_delivery < 1:
            raise ValueError("max_source_items_per_delivery must be positive")
        if self.max_segments_per_delivery < 1:
            raise ValueError("max_segments_per_delivery must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not math.isfinite(self.retry_initial_seconds) or self.retry_initial_seconds < 0:
            raise ValueError("retry_initial_seconds must be finite and non-negative")
        if not math.isfinite(self.retry_max_seconds):
            raise ValueError("retry_max_seconds must be finite")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("retry_max_seconds cannot be smaller than retry_initial_seconds")
        if (
            not math.isfinite(self.backpressure_retry_after_seconds)
            or self.backpressure_retry_after_seconds < 0
        ):
            raise ValueError("backpressure_retry_after_seconds must be finite and non-negative")


class DeliveryCoordinator:
    """Bounded, ordered execution of deterministic delivery plans."""

    def __init__(
        self,
        *,
        planner: DeliveryPlanner | None = None,
        config: DeliveryCoordinatorConfig | None = None,
    ) -> None:
        self._planner = planner or DeliveryPlanner()
        self._config = config or DeliveryCoordinatorConfig()
        self._destination_locks = KeyedLockRegistry()
        self._execution_slots = asyncio.Semaphore(self._config.max_concurrent_destinations)
        self._pending = 0
        self._pending_by_destination: dict[ConversationRef, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def planner(self) -> DeliveryPlanner:
        return self._planner

    def preflight(
        self,
        channel: ChannelAdapter,
        message: OutboundMessage,
    ) -> DeliveryPlan:
        """Build the exact bounded plan used by admission and execution."""

        return self._planner.plan(
            message,
            channel.capabilities.delivery,
            max_source_items=self._config.max_source_items_per_delivery,
            max_segments=self._config.max_segments_per_delivery,
        )

    def validate_source_item_count(self, count: int) -> None:
        """Reject oversized logical messages before validation or hashing walks them."""

        limit = self._config.max_source_items_per_delivery
        if count > limit:
            raise DeliveryPlanningError(f"delivery exceeds source item limit ({limit})")

    def start(self) -> None:
        """Open a quiescent Coordinator for a new owning Gateway lifecycle."""

        if self._pending or self._tasks:
            raise RuntimeError("delivery coordinator cannot start with pending work")
        self._closed = False

    def submit(
        self,
        channel: ChannelAdapter,
        message: OutboundMessage,
    ) -> DeliveryHandle:
        if self._closed:
            raise RuntimeError("delivery coordinator is closed")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[DeliveryReceipt] = loop.create_future()
        if not self._reserve(message.conversation_ref):
            future.set_result(self._backpressure_receipt(message))
            return DeliveryHandle(admitted=False, _future=future)

        try:
            plan = self.preflight(channel, message)
        except BaseException:
            self._release(message.conversation_ref)
            raise

        try:
            task = asyncio.create_task(
                self._run_submission(channel, plan, future),
                name=f"imagent-delivery:{message.conversation_ref.channel_instance_id}",
            )
        except BaseException:
            self._release(message.conversation_ref)
            raise
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return DeliveryHandle(admitted=True, _future=future, _task=task)

    async def deliver(
        self,
        channel: ChannelAdapter,
        message: OutboundMessage,
    ) -> DeliveryReceipt:
        handle = self.submit(channel, message)
        try:
            return await handle.result()
        except asyncio.CancelledError:
            await handle.cancel()
            raise

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run_submission(
        self,
        channel: ChannelAdapter,
        plan: DeliveryPlan,
        future: asyncio.Future[DeliveryReceipt],
    ) -> None:
        try:
            async with self._destination_locks.hold(plan.conversation_ref):
                receipt = await self._execute(channel, plan)
                if not future.done():
                    future.set_result(receipt)
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        except BaseException as error:
            if not future.done():
                future.set_exception(error)
        finally:
            self._release(plan.conversation_ref)

    def _reserve(self, conversation_ref: ConversationRef) -> bool:
        destination_pending = self._pending_by_destination.get(conversation_ref, 0)
        if (
            self._pending >= self._config.max_pending
            or destination_pending >= self._config.max_pending_per_destination
        ):
            return False
        self._pending += 1
        self._pending_by_destination[conversation_ref] = destination_pending + 1
        return True

    def _release(self, conversation_ref: ConversationRef) -> None:
        self._pending -= 1
        remaining = self._pending_by_destination.get(conversation_ref, 1) - 1
        if remaining > 0:
            self._pending_by_destination[conversation_ref] = remaining
        else:
            self._pending_by_destination.pop(conversation_ref, None)

    def _backpressure_receipt(self, message: OutboundMessage) -> DeliveryReceipt:
        detail = "delivery coordinator is at bounded capacity"
        return DeliveryReceipt(
            status=DeliveryReceiptStatus.RETRYABLE_FAILURE,
            detail=detail,
            retry_after_seconds=self._config.backpressure_retry_after_seconds,
        )

    async def _execute(
        self,
        channel: ChannelAdapter,
        plan: DeliveryPlan,
    ) -> DeliveryReceipt:
        completed: list[tuple[PlannedDeliverySegment, DeliveryReceipt]] = []
        for segment in plan.segments:
            receipt = await self._send_segment(channel, segment)
            completed.append((segment, receipt))
            if receipt.status is not DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM:
                break
        return _aggregate_receipts(plan, completed)

    async def _send_segment(
        self,
        channel: ChannelAdapter,
        segment: PlannedDeliverySegment,
    ) -> DeliveryReceipt:
        attempt = 1
        while True:
            try:
                async with self._execution_slots:
                    receipt = await channel.send(segment.message)
                validate_delivery_receipt_for_content(
                    receipt,
                    segment.message.content,
                )
            except BaseException as error:
                if isinstance(
                    error,
                    (KeyboardInterrupt, SystemExit, asyncio.CancelledError),
                ):
                    raise
                return DeliveryReceipt(
                    status=DeliveryReceiptStatus.UNKNOWN,
                    detail=str(error) or type(error).__name__,
                )
            if (
                receipt.status is not DeliveryReceiptStatus.RETRYABLE_FAILURE
                or attempt >= self._config.max_attempts
            ):
                return receipt
            if receipt.retry_after_seconds is not None:
                # Native retry-after is a minimum. Return it rather than
                # retrying early when consumer policy cannot wait that long.
                if receipt.retry_after_seconds > self._config.retry_max_seconds:
                    return receipt
                delay = receipt.retry_after_seconds
            else:
                delay = min(
                    self._config.retry_initial_seconds * (2 ** (attempt - 1)),
                    self._config.retry_max_seconds,
                )
            await asyncio.sleep(delay)
            attempt += 1


def _aggregate_receipts(
    plan: DeliveryPlan,
    completed: list[tuple[PlannedDeliverySegment, DeliveryReceipt]],
) -> DeliveryReceipt:
    item_outcomes: dict[int, list[DeliveryItemReceipt]] = {}
    segment_receipts: list[DeliverySegmentReceipt] = []
    for segment, receipt in completed:
        native_items = {item.content_index: item for item in receipt.items}
        for local_index, source_index in enumerate(segment.source_content_indexes):
            native_item = native_items.get(local_index)
            source_content = segment.message.content[local_index]
            item_outcomes.setdefault(source_index, []).append(
                DeliveryItemReceipt(
                    content_index=source_index,
                    status=(
                        native_item.status
                        if native_item is not None
                        else _item_status_from_receipt(receipt.status)
                    ),
                    attachment_id=(
                        source_content.attachment_id
                        if isinstance(source_content, AttachmentContent)
                        else None
                    ),
                    native_message_id=(
                        native_item.native_message_id if native_item is not None else None
                    ),
                    detail=(native_item.detail if native_item is not None else receipt.detail),
                )
            )
        segment_receipts.append(
            DeliverySegmentReceipt(
                segment_index=segment.segment_index,
                delivery_id=segment.message.delivery_id,
                source_content_indexes=segment.source_content_indexes,
                status=_segment_status_from_receipt(receipt.status),
                native_message_id=receipt.native_message_id,
                detail=receipt.detail,
                retry_after_seconds=receipt.retry_after_seconds,
            )
        )
    for segment in plan.segments[len(completed) :]:
        for local_index, source_index in enumerate(segment.source_content_indexes):
            source_content = segment.message.content[local_index]
            item_outcomes.setdefault(source_index, []).append(
                DeliveryItemReceipt(
                    content_index=source_index,
                    status=DeliveryItemStatus.SKIPPED,
                    attachment_id=(
                        source_content.attachment_id
                        if isinstance(source_content, AttachmentContent)
                        else None
                    ),
                    detail="not attempted after an earlier segment failed",
                )
            )
        segment_receipts.append(
            DeliverySegmentReceipt(
                segment_index=segment.segment_index,
                delivery_id=segment.message.delivery_id,
                source_content_indexes=segment.source_content_indexes,
                status=DeliverySegmentStatus.SKIPPED,
                detail="not attempted after an earlier segment failed",
            )
        )

    items = tuple(
        _aggregate_item_outcomes(index, item_outcomes[index]) for index in sorted(item_outcomes)
    )
    receipts = [receipt for _, receipt in completed]
    accepted_before_failure = any(
        receipt.status is DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM for receipt in receipts[:-1]
    )
    final_status = receipts[-1].status
    if final_status is DeliveryReceiptStatus.UNKNOWN:
        status = DeliveryReceiptStatus.UNKNOWN
    elif final_status is DeliveryReceiptStatus.RETRYABLE_FAILURE:
        status = (
            DeliveryReceiptStatus.UNKNOWN
            if accepted_before_failure
            else DeliveryReceiptStatus.RETRYABLE_FAILURE
        )
    elif final_status is DeliveryReceiptStatus.REJECTED_BY_PLATFORM:
        status = (
            DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM
            if accepted_before_failure
            else DeliveryReceiptStatus.REJECTED_BY_PLATFORM
        )
    else:
        status = DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM

    return DeliveryReceipt(
        status=status,
        native_message_id=(receipts[0].native_message_id if len(receipts) == 1 else None),
        detail=_aggregate_detail(
            status=status,
            final_detail=receipts[-1].detail,
            accepted_before_failure=accepted_before_failure,
            all_segments_attempted=len(receipts) == len(plan.segments),
            items=items,
        ),
        items=items,
        segments=tuple(segment_receipts),
        retry_after_seconds=(
            receipts[-1].retry_after_seconds
            if status is DeliveryReceiptStatus.RETRYABLE_FAILURE
            else None
        ),
    )


def _item_status_from_receipt(status: DeliveryReceiptStatus) -> DeliveryItemStatus:
    if status is DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM:
        return DeliveryItemStatus.ACCEPTED
    if status is DeliveryReceiptStatus.REJECTED_BY_PLATFORM:
        return DeliveryItemStatus.REJECTED
    if status is DeliveryReceiptStatus.RETRYABLE_FAILURE:
        return DeliveryItemStatus.RETRYABLE_FAILURE
    return DeliveryItemStatus.UNKNOWN


def _segment_status_from_receipt(status: DeliveryReceiptStatus) -> DeliverySegmentStatus:
    return DeliverySegmentStatus(status.value)


def _aggregate_item_outcomes(
    content_index: int,
    outcomes: list[DeliveryItemReceipt],
) -> DeliveryItemReceipt:
    statuses = {outcome.status for outcome in outcomes}
    if statuses == {DeliveryItemStatus.ACCEPTED}:
        status = DeliveryItemStatus.ACCEPTED
    elif DeliveryItemStatus.ACCEPTED in statuses:
        status = DeliveryItemStatus.UNKNOWN
    elif DeliveryItemStatus.UNKNOWN in statuses:
        status = DeliveryItemStatus.UNKNOWN
    elif DeliveryItemStatus.RETRYABLE_FAILURE in statuses:
        status = DeliveryItemStatus.RETRYABLE_FAILURE
    elif DeliveryItemStatus.REJECTED in statuses:
        status = DeliveryItemStatus.REJECTED
    else:
        status = DeliveryItemStatus.SKIPPED
    attachment_ids = {
        outcome.attachment_id for outcome in outcomes if outcome.attachment_id is not None
    }
    native_ids = {
        outcome.native_message_id for outcome in outcomes if outcome.native_message_id is not None
    }
    details = [outcome.detail for outcome in outcomes if outcome.detail]
    return DeliveryItemReceipt(
        content_index=content_index,
        status=status,
        attachment_id=(next(iter(attachment_ids)) if len(attachment_ids) == 1 else None),
        native_message_id=(next(iter(native_ids)) if len(native_ids) == 1 else None),
        detail=(
            "; ".join(dict.fromkeys(details))
            if details
            else (
                "source content spans segments with different outcomes"
                if len(statuses) > 1
                else None
            )
        ),
    )


def _aggregate_detail(
    *,
    status: DeliveryReceiptStatus,
    final_detail: str | None,
    accepted_before_failure: bool,
    all_segments_attempted: bool,
    items: tuple[DeliveryItemReceipt, ...],
) -> str | None:
    if status is DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM:
        if all_segments_attempted and all(
            item.status is DeliveryItemStatus.ACCEPTED for item in items
        ):
            return None
        return "delivery completed partially; inspect item and segment receipts"
    if final_detail:
        return final_detail
    if status is DeliveryReceiptStatus.UNKNOWN:
        return (
            "delivery outcome is partial or ambiguous after an accepted prefix"
            if accepted_before_failure
            else "delivery outcome is unknown"
        )
    if status is DeliveryReceiptStatus.RETRYABLE_FAILURE:
        return "delivery failed without acceptance and may be retried"
    return "delivery was rejected by the platform"

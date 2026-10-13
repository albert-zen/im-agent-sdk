"""Adversarial queue admission through public Gateway and coherent real stores."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast, overload
from unittest.mock import patch

from imagent import Gateway, MemoryGatewayStore, ProjectionPolicy, SQLiteGatewayStore
from imagent.applications.contract import (
    AcceptedTurn,
    AgentInput,
    ApplicationInputAcceptance,
    ApplicationInputDispatchHandler,
    ApplicationInputOutcomeUnknown,
    ApplicationInputPlanHandler,
    ApplicationSummary,
    InputContinuationPreference,
    QueuedInputDispatch,
    QueuedInputReceipt,
    QueuedInputState,
    ThreadRef,
    TurnRef,
)
from imagent.applications.events import AgentEvent
from imagent.applications.operations import ApplicationOperation, ApplicationOperationResult
from imagent.applications.queued_input import (
    QueuedInputNotice,
    QueuedInputNoticeKind,
    QueuedInputSubscription,
)
from imagent.applications.requests import InteractiveRequest
from imagent.gateway import (
    GatewayExtensions,
    GatewayLimits,
    QueuedInputNoticeReason,
    QueuedInputStatus,
    QueuedInputStatusKind,
)
from imagent.gateway.admission import inbound_idempotency_identity
from imagent.gateway.input import InboundFailurePhase, InboundFailurePresenter
from imagent.gateway.outcomes import Failed, OutcomeUnknown, Succeeded
from imagent.gateway.persistence.effects import ActionErrorCode
from imagent.gateway.persistence.repository_contracts import IdempotencyClaimStatus
from imagent.gateway.persistence.state_contracts import QueuedInputCorrelation
from imagent.gateway.persistence.store import GatewayStore, GatewayStoreSession
from imagent.interaction.controllers.request_presentation import (
    MarkdownRequestPresenter,
    RequestPresenter,
)
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
)
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _GatewayInputOptions(TypedDict, total=False):
    input_continuation: InputContinuationPreference


class _Subscription:
    def __init__(self) -> None:
        self.items: asyncio.Queue[QueuedInputNotice] = asyncio.Queue()
        self.closed = False

    def __aiter__(self) -> _Subscription:
        return self

    async def __anext__(self) -> QueuedInputNotice:
        if self.closed:
            raise StopAsyncIteration
        return await self.items.get()

    async def aclose(self) -> None:
        self.closed = True


class _QueueApplication:
    """Strict optional queue ports around the reference resource/event adapter."""

    def __init__(self) -> None:
        self.base = FakeAgentApplicationAdapter("queue-app")
        self.continuations: list[InputContinuationPreference] = []
        self.native_inputs: list[AgentInput] = []
        self.canonical: dict[tuple[ThreadRef, str], QueuedInputReceipt] = {}
        self.subscriptions: dict[ThreadRef, list[_Subscription]] = {}
        self.reads: list[tuple[ThreadRef, tuple[str, ...]]] = []
        self.cancel_calls: list[QueuedInputReceipt] = []
        self.hide_evidence = False
        self.hidden_identities: set[str] = set()
        self.hide_after_reads: int | None = None
        self.snapshot_change: Callable[[tuple[QueuedInputReceipt | None, ...]], object] | None = (
            None
        )
        self.acknowledgement = "normal"
        self.cancel_acknowledgement = "normal"
        self.before_plan: Callable[[], Awaitable[None]] | None = None
        self.after_commit: Callable[[QueuedInputReceipt], Awaitable[None]] | None = None
        self.plan_change: Callable[[QueuedInputDispatch], QueuedInputDispatch] | None = None
        self.receipt_change: Callable[[QueuedInputReceipt], QueuedInputReceipt] | None = None

    @property
    def summary(self) -> ApplicationSummary:
        return self.base.summary

    async def start(self) -> None:
        await self.base.start()

    async def stop(self) -> None:
        await self.base.stop()

    async def execute(self, operation: ApplicationOperation) -> ApplicationOperationResult:
        return await self.base.execute(operation)

    async def list_pending_requests(self) -> tuple[InteractiveRequest, ...]:
        return await self.base.list_pending_requests()

    def subscribe_thread(
        self, thread_ref: ThreadRef, after_cursor: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        return self.base.subscribe_thread(thread_ref, after_cursor)

    @overload
    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: Literal[
            InputContinuationPreference.PREFER_ACTIVE_TURN,
            InputContinuationPreference.START_NEW_TURN,
        ] = InputContinuationPreference.PREFER_ACTIVE_TURN,
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn: ...

    @overload
    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference,
        before_dispatch: ApplicationInputPlanHandler | None = None,
    ) -> ApplicationInputAcceptance: ...

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = InputContinuationPreference.PREFER_ACTIVE_TURN,
        before_dispatch: ApplicationInputPlanHandler
        | ApplicationInputDispatchHandler
        | None = None,
    ) -> ApplicationInputAcceptance:
        self.continuations.append(continuation)
        if continuation is not InputContinuationPreference.QUEUE:
            return await self.base.send_input(
                thread_ref,
                message,
                continuation=continuation,
                before_dispatch=before_dispatch,
            )
        assert before_dispatch is not None
        fingerprint = hashlib.sha256(repr(message.content).encode()).hexdigest()
        plan = QueuedInputDispatch(thread_ref, message.client_message_id, fingerprint)
        if self.plan_change is not None:
            plan = self.plan_change(plan)
        if self.before_plan is not None:
            await self.before_plan()
        await cast(ApplicationInputPlanHandler, before_dispatch)(plan)
        self.native_inputs.append(message)
        receipt = QueuedInputReceipt(
            thread_ref,
            message.client_message_id,
            f"queue-item-{len(self.native_inputs)}",
            QueuedInputState.QUEUED,
            input_fingerprint=fingerprint,
        )
        self.canonical[(thread_ref, message.client_message_id)] = receipt
        if self.after_commit is not None:
            await self.after_commit(receipt)
        if self.acknowledgement == "lost":
            raise ApplicationInputOutcomeUnknown(
                "synthetic lost response", OSError("lost response")
            )
        if self.acknowledgement == "malformed":
            return cast(ApplicationInputAcceptance, {"admission": None})
        return self.receipt_change(receipt) if self.receipt_change is not None else receipt

    async def read_queued_input_receipt(
        self, thread_ref: ThreadRef, client_message_id: str
    ) -> QueuedInputReceipt | None:
        return (await self.read_queued_input_receipts(thread_ref, (client_message_id,)))[0]

    async def read_queued_input_receipts(
        self, thread_ref: ThreadRef, client_message_ids: tuple[str, ...]
    ) -> tuple[QueuedInputReceipt | None, ...]:
        self.reads.append((thread_ref, client_message_ids))
        if self.hide_evidence or (
            self.hide_after_reads is not None and len(self.reads) >= self.hide_after_reads
        ):
            return tuple(None for _ in client_message_ids)
        result = tuple(
            None
            if identity in self.hidden_identities
            else self.canonical.get((thread_ref, identity))
            for identity in client_message_ids
        )
        return (
            cast(tuple[QueuedInputReceipt | None, ...], self.snapshot_change(result))
            if self.snapshot_change is not None
            else result
        )

    async def subscribe_queued_inputs(self, thread_ref: ThreadRef) -> QueuedInputSubscription:
        subscription = _Subscription()
        self.subscriptions.setdefault(thread_ref, []).append(subscription)
        return subscription

    def notify(
        self,
        receipt: QueuedInputReceipt,
        *,
        kind: QueuedInputNoticeKind = QueuedInputNoticeKind.CHANGED,
        notice_id: str = "canonical-change",
    ) -> None:
        for subscription in self.subscriptions.get(receipt.thread_ref, ()):
            if not subscription.closed:
                subscription.items.put_nowait(
                    QueuedInputNotice(
                        receipt.thread_ref,
                        receipt.client_message_id,
                        receipt.queued_item_id,
                        kind,
                        notice_id,
                    )
                )

    async def cancel_queued_input_receipt(self, receipt: QueuedInputReceipt) -> QueuedInputReceipt:
        self.cancel_calls.append(receipt)
        terminal = replace(receipt, state=QueuedInputState.CANCELLED)
        if self.cancel_acknowledgement != "no-evidence":
            self.canonical[(receipt.thread_ref, receipt.client_message_id)] = terminal
        if self.cancel_acknowledgement != "normal":
            raise OSError("lost cancellation acknowledgement")
        return terminal


class _RecordingStore:
    """Observe the actual coherent session without changing any store behavior."""

    def __init__(self, delegate: GatewayStore) -> None:
        self.delegate = delegate
        self.session: GatewayStoreSession | None = None

    @property
    def max_effect_receipts(self) -> int:
        return self.delegate.max_effect_receipts

    async def acquire_runtime(
        self, *, gateway_id: str, owner_token: str, lease_duration_seconds: float
    ) -> GatewayStoreSession:
        self.session = await self.delegate.acquire_runtime(
            gateway_id=gateway_id,
            owner_token=owner_token,
            lease_duration_seconds=lease_duration_seconds,
        )
        return self.session

    async def close(self) -> None:
        await self.delegate.close()


class _Presenter:
    def __init__(self) -> None:
        self.calls: list[tuple[QueuedInputStatus, QueuedInputNoticeReason]] = []
        self.change: Callable[[OutboundMessage], OutboundMessage] | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.block = False

    async def present_queued_input(
        self,
        status: QueuedInputStatus,
        *,
        reason: QueuedInputNoticeReason,
        conversation_ref: ConversationRef,
        delivery_id: str,
        reply_to_message_id: str,
    ) -> OutboundMessage:
        self.calls.append((status, reason))
        self.entered.set()
        if self.block:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        output = OutboundMessage(
            delivery_id,
            conversation_ref,
            (TextContent(f"queue:{status.state.value}"),),
            datetime.now(UTC),
            reply_to=reply_to_message_id,
        )
        return self.change(output) if self.change is not None else output


class _FailurePresenter:
    def __init__(self) -> None:
        self.phases: list[InboundFailurePhase] = []

    async def present_failure(
        self,
        phase: InboundFailurePhase,
        *,
        conversation_ref: ConversationRef,
        delivery_id: str,
        reply_to_message_id: str,
    ) -> OutboundMessage:
        self.phases.append(phase)
        return OutboundMessage(
            delivery_id,
            conversation_ref,
            (TextContent(phase.value),),
            datetime.now(UTC),
            reply_to=reply_to_message_id,
        )


class QueuedGatewayFencingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="queue-fencing-")
        self.app = _QueueApplication()
        self.conversation = ConversationRef("channel", "original")
        self.gateways: list[Gateway] = []

    async def asyncTearDown(self) -> None:
        for gateway in reversed(self.gateways):
            await gateway.stop()
        self.directory.cleanup()

    async def start(
        self,
        *,
        sqlite: bool = False,
        capacity: int = 4096,
        mode: InputContinuationPreference | None = InputContinuationPreference.QUEUE,
        presenter: _Presenter | None = None,
        limits: GatewayLimits = GatewayLimits(),
        request_presenter: RequestPresenter | None = None,
        projection_policy: ProjectionPolicy = ProjectionPolicy.FOREGROUND_ONLY,
        failure_presenter: InboundFailurePresenter | None = None,
    ) -> Gateway:
        delegate = (
            SQLiteGatewayStore(
                Path(self.directory.name) / "bridge.sqlite3", max_effect_receipts=capacity
            )
            if sqlite
            else MemoryGatewayStore(max_effect_receipts=capacity)
        )
        self.store = _RecordingStore(delegate)
        self.channel = FakeChannelAdapter("channel")
        kwargs: _GatewayInputOptions = {} if mode is None else {"input_continuation": mode}
        gateway = Gateway(
            gateway_id="fencing",
            channels=[self.channel],
            applications=[self.app],
            store=self.store,
            projection_policy=projection_policy,
            extensions=GatewayExtensions(
                queued_input_presenter=presenter,
                request_presenter=request_presenter,
                inbound_failure_presenter=failure_presenter,
            ),
            limits=limits,
            **kwargs,
        )
        self.gateways.append(gateway)
        await gateway.start()
        self.gateway = gateway
        self.actions = gateway.actions(self.conversation, actor="original-actor")
        return gateway

    async def bind(self, *, conversation: ConversationRef | None = None) -> ThreadRef:
        actions = self.gateway.actions(conversation or self.conversation, actor="original-actor")
        result = await actions.create_and_bind_thread(
            self.app.base.default_project_ref, action_id="create"
        )
        assert isinstance(result, Succeeded) and isinstance(result.value.ref, ThreadRef), result
        return result.value.ref

    async def emit(
        self, message_id: str, *, text: str = "body", conversation: ConversationRef | None = None
    ) -> None:
        await self.channel.emit_message(
            InboundMessage(
                message_id,
                conversation or self.conversation,
                "original-actor",
                (TextContent(text),),
                datetime.now(UTC),
            )
        )

    async def row(self, message_id: str) -> QueuedInputCorrelation:
        assert self.store.session is not None
        row = await self.store.session.get_queued_input(self.conversation, message_id)
        assert row is not None
        return row

    async def status(self, message_id: str) -> QueuedInputStatus:
        result = await self.actions.read_queued_input(message_id)
        assert isinstance(result, Succeeded), result
        return result.value

    async def assert_claim_fenced(self, message_id: str) -> None:
        assert self.store.session is not None
        scope, key = inbound_idempotency_identity(self.conversation, message_id)
        status = await self.store.session.claim(scope, key, owner_token="adversarial-retry")
        self.assertIsNot(status, IdempotencyClaimStatus.ACQUIRED)

    async def test_lost_response_unknown_survives_fresh_sqlite_and_changed_mode(self) -> None:
        await self.start(sqlite=True)
        await self.bind()
        self.app.acknowledgement = "lost"
        self.app.hide_evidence = True
        with self.assertRaisesRegex(OSError, "lost response"):
            await self.emit("lost")
        original = await self.row("lost")
        self.assertIsNone(original.receipt)
        self.assertIs((await self.status("lost")).state, QueuedInputStatusKind.UNKNOWN)
        await self.assert_claim_fenced("lost")
        await self.emit("lost", text="changed content")
        await self.gateway.stop()
        await self.start(sqlite=True, mode=InputContinuationPreference.PREFER_ACTIVE_TURN)
        self.assertEqual((await self.row("lost")).origin, original.origin)
        await self.emit("lost", text="changed after restart and mode switch")
        self.assertEqual(len(self.app.native_inputs), 1)
        self.assertEqual(self.app.continuations, [InputContinuationPreference.QUEUE])
        await self.assert_claim_fenced("lost")
        self.app.hide_evidence = False
        self.assertIs((await self.status("lost")).state, QueuedInputStatusKind.PENDING)
        self.assertEqual((await self.row("lost")).origin, original.origin)
        await self.assert_claim_fenced("lost")

    async def test_malformed_ack_never_reopens_original_claim(self) -> None:
        await self.start()
        await self.bind()
        self.app.acknowledgement = "malformed"
        self.app.hide_evidence = True
        with self.assertRaises(RuntimeError):
            await self.emit("malformed")
        self.assertIsNone((await self.row("malformed")).receipt)
        await self.assert_claim_fenced("malformed")
        await self.emit("malformed", text="changed body")
        self.assertEqual(len(self.app.native_inputs), 1)
        self.app.hide_evidence = False
        self.assertIs((await self.status("malformed")).state, QueuedInputStatusKind.PENDING)

    async def test_configured_queue_does_not_change_omitted_gateway_default(self) -> None:
        await self.start(mode=None)
        await self.bind()
        await self.emit("ordinary")
        self.assertEqual(self.app.continuations, [InputContinuationPreference.PREFER_ACTIVE_TURN])
        self.assertEqual(self.app.native_inputs, [])
        assert self.store.session is not None
        self.assertEqual(await self.store.session.list_queued_inputs(), ())

    async def test_unknown_cancel_reconciles_without_second_native_cancel_or_input(self) -> None:
        await self.start()
        await self.bind()
        await self.emit("cancel")
        self.app.cancel_acknowledgement = "lost"
        self.app.hide_evidence = False
        first = await self.actions.cancel_queued_input("cancel", action_id="cancel-once")
        self.assertIsInstance(first, OutcomeUnknown)
        second = await self.actions.cancel_queued_input("cancel", action_id="cancel-once")
        self.assertIsInstance(second, Succeeded)
        self.assertIs((await self.status("cancel")).state, QueuedInputStatusKind.CANCELLED)
        self.assertEqual(len(self.app.cancel_calls), 1)
        self.assertEqual(len(self.app.native_inputs), 1)
        await self.assert_claim_fenced("cancel")

    async def test_missing_cancellation_evidence_stays_unknown_on_same_action_retry(self) -> None:
        await self.start()
        await self.bind()
        await self.emit("cancel")
        self.app.cancel_acknowledgement = "no-evidence"
        first = await self.actions.cancel_queued_input("cancel", action_id="cancel-once")
        second = await self.actions.cancel_queued_input("cancel", action_id="cancel-once")
        self.assertIsInstance(first, OutcomeUnknown)
        self.assertIsInstance(second, OutcomeUnknown)
        self.assertIs((await self.status("cancel")).state, QueuedInputStatusKind.PENDING)
        self.assertEqual(len(self.app.cancel_calls), 1)
        self.assertEqual(len(self.app.native_inputs), 1)
        await self.assert_claim_fenced("cancel")

    async def test_canonical_admission_before_queued_ack_is_monotonic(self) -> None:
        await self.start()
        thread = await self.bind()
        assert self.store.session is not None
        session = self.store.session

        async def admit_before_ack(receipt: QueuedInputReceipt) -> None:
            admitted = replace(
                receipt,
                state=QueuedInputState.ADMITTED,
                turn_ref=TurnRef(thread, "original-turn"),
            )
            self.app.canonical[(thread, receipt.client_message_id)] = admitted
            self.app.notify(admitted)
            async with asyncio.timeout(1):
                while True:
                    row = await session.get_queued_input(self.conversation, "event-first")
                    if row is not None and row.receipt == admitted:
                        break
                    await asyncio.sleep(0)

        self.app.after_commit = admit_before_ack
        await self.emit("event-first")
        row = await self.row("event-first")
        assert row.receipt is not None
        self.assertIs(row.receipt.state, QueuedInputState.ADMITTED)
        correlation = await session.get_turn_reply_correlation(thread, "original-turn")
        assert correlation is not None
        self.assertEqual(correlation.reply_to_message_id, "event-first")
        self.assertEqual(len(self.app.native_inputs), 1)
        await self.assert_claim_fenced("event-first")

    async def test_absent_canonical_evidence_fences_approval_first_delivery(self) -> None:
        await self.start(request_presenter=MarkdownRequestPresenter())
        thread = await self.bind()
        await self.emit("queued-origin")
        self.app.hide_evidence = True
        before_reads = len(self.app.reads)
        await self.app.base.open_approval_request(thread, turn_id="uncorrelated-turn")
        async with asyncio.timeout(1):
            while len(self.app.reads) == before_reads:
                await asyncio.sleep(0)
        await asyncio.sleep(0.03)
        self.assertEqual(
            self.channel.sent,
            [],
            "missing canonical queue evidence must not route an uncorrelated approval",
        )
        self.assertIs((await self.status("queued-origin")).state, QueuedInputStatusKind.UNKNOWN)
        self.assertEqual(len(self.app.native_inputs), 1)

    async def test_recovery_rejects_foreign_scope_client_and_payload_fingerprint(self) -> None:
        await self.start()
        thread = await self.bind()
        self.app.acknowledgement = "lost"
        with self.assertRaises(OSError):
            await self.emit("unknown-origin")
        original = await self.row("unknown-origin")
        key = (thread, original.origin.client_message_id)
        canonical = self.app.canonical[key]
        wrong = (
            replace(canonical, thread_ref=ThreadRef(thread.project_ref, "foreign-thread")),
            replace(canonical, client_message_id="foreign-client"),
            replace(canonical, input_fingerprint="f" * 64),
        )
        for receipt in wrong:
            with self.subTest(receipt=receipt):
                self.app.canonical[key] = receipt
                result = await self.actions.read_queued_input("unknown-origin")
                self.assertIsInstance(result, Failed)
                assert isinstance(result, Failed)
                self.assertIs(result.error.code, ActionErrorCode.NATIVE_OUTCOME_UNKNOWN)
                self.assertEqual(await self.row("unknown-origin"), original)
                await self.assert_claim_fenced("unknown-origin")
        self.assertEqual(len(self.app.native_inputs), 1)

    async def test_bad_dispatch_scope_is_rejected_before_native_effect_and_claim_can_retry(
        self,
    ) -> None:
        await self.start()
        await self.bind()
        self.app.plan_change = lambda plan: replace(plan, client_message_id="foreign-client")
        with self.assertRaisesRegex(ValueError, "original Thread or client"):
            await self.emit("bad-plan")
        self.assertEqual(self.app.native_inputs, [])
        assert self.store.session is not None
        self.assertIsNone(await self.store.session.get_queued_input(self.conversation, "bad-plan"))
        self.app.plan_change = None
        await self.emit("bad-plan")
        self.assertEqual(len(self.app.native_inputs), 1)
        await self.assert_claim_fenced("bad-plan")

    async def test_binding_generation_change_before_fence_prevents_native_send(self) -> None:
        await self.start(projection_policy=ProjectionPolicy.REMEMBERED_LAST_RECIPIENT)
        thread = await self.bind()

        async def change_generation() -> None:
            result = await self.actions.clear_thread(action_id="clear-during-plan")
            self.assertIsInstance(result, Succeeded)
            result = await self.actions.bind_thread(thread, action_id="rebind-during-plan")
            self.assertIsInstance(result, Succeeded)

        self.app.before_plan = change_generation
        with self.assertRaises(RuntimeError):
            await asyncio.wait_for(self.emit("stale-generation"), 1)
        self.assertEqual(self.app.native_inputs, [])
        assert self.store.session is not None
        self.assertIsNone(
            await self.store.session.get_queued_input(self.conversation, "stale-generation")
        )

    async def test_invalid_presenter_output_never_delivers_or_reopens_input(self) -> None:
        changes: tuple[Callable[[OutboundMessage], OutboundMessage], ...] = (
            lambda output: replace(output, delivery_id="substituted-id"),
            lambda output: replace(output, conversation_ref=ConversationRef("channel", "other")),
            lambda output: replace(output, reply_to="different-message"),
            lambda output: cast(OutboundMessage, "not a typed message"),
            lambda output: replace(output, content=()),
            lambda output: replace(output, content=(TextContent("x" * 33),)),
            lambda output: replace(output, content=(TextContent("a"), TextContent("b"))),
            lambda output: replace(output, metadata={"native_client": "must-not-leak"}),
        )
        presenter = _Presenter()
        await self.start(
            presenter=presenter,
            limits=GatewayLimits(
                queued_input_present_max_text_characters=32, queued_input_present_max_items=1
            ),
        )
        await self.bind()
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                presenter.change = change
                message_id = f"invalid-presenter-{index}"
                with self.assertRaises(ValueError):
                    await self.emit(message_id)
                self.assertEqual(self.channel.sent, [])
                self.assertIs((await self.status(message_id)).state, QueuedInputStatusKind.PENDING)
                await self.emit(message_id, text="changed replay")
                await self.assert_claim_fenced(message_id)
                self.assertEqual(len(self.app.native_inputs), index + 1)

    async def test_presenter_timeout_cancels_task_and_preserves_single_input(self) -> None:
        presenter = _Presenter()
        presenter.block = True
        await self.start(
            presenter=presenter,
            limits=GatewayLimits(queued_input_present_timeout_seconds=0.02),
        )
        await self.bind()
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.emit("timed-out"), 0.5)
        self.assertTrue(presenter.cancelled.is_set())
        self.assertEqual(self.channel.sent, [])
        self.assertEqual(len(self.app.native_inputs), 1)
        await self.emit("timed-out")
        await self.assert_claim_fenced("timed-out")

    async def test_caller_cancellation_during_presentation_does_not_resend(self) -> None:
        presenter = _Presenter()
        presenter.block = True
        await self.start(presenter=presenter)
        await self.bind()
        task = asyncio.create_task(self.emit("cancel-presenter"))
        await asyncio.wait_for(presenter.entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(presenter.cancelled.is_set())
        self.assertEqual(self.channel.sent, [])
        await self.emit("cancel-presenter")
        self.assertEqual(len(self.app.native_inputs), 1)
        await self.assert_claim_fenced("cancel-presenter")

    async def test_request_first_read_establishes_original_turn_without_queue_notice(self) -> None:
        await self.start(request_presenter=MarkdownRequestPresenter())
        thread = await self.bind()
        await self.emit("approval-origin")
        original = await self.row("approval-origin")
        assert original.receipt is not None
        admitted = replace(
            original.receipt,
            state=QueuedInputState.ADMITTED,
            turn_ref=TurnRef(thread, "approval-turn"),
        )
        self.app.canonical[(thread, original.origin.client_message_id)] = admitted
        await self.app.base.open_approval_request(thread, turn_id="approval-turn")
        async with asyncio.timeout(1):
            while not self.channel.sent:
                await asyncio.sleep(0)
        self.assertEqual(self.channel.sent[0].reply_to, "approval-origin")
        self.assertEqual((await self.row("approval-origin")).receipt, admitted)
        self.assertEqual(len(self.app.native_inputs), 1)

    async def test_foreign_actor_and_conversation_cannot_even_read_native_evidence(self) -> None:
        await self.start()
        await self.bind()
        await self.emit("private-origin")
        reads = len(self.app.reads)
        for actions in (
            self.gateway.actions(self.conversation, actor="other-actor"),
            self.gateway.actions(ConversationRef("channel", "other"), actor="original-actor"),
        ):
            denied_read = await actions.read_queued_input("private-origin")
            self.assertIsInstance(denied_read, Failed)
            denied_cancel = await actions.cancel_queued_input("private-origin", action_id="denied")
            self.assertIsInstance(denied_cancel, Failed)
            self.assertEqual(len(self.app.reads), reads)
            self.assertEqual(self.app.cancel_calls, [])
        self.assertEqual(len(self.app.native_inputs), 1)

    async def test_rebinding_during_presentation_drops_obsolete_notice(self) -> None:
        presenter = _Presenter()
        presenter.block = True
        await self.start(presenter=presenter)
        await self.bind()
        sending = asyncio.create_task(self.emit("obsolete-notice"))
        await asyncio.wait_for(presenter.entered.wait(), 1)
        result = await self.actions.clear_thread(action_id="clear-before-render-finishes")
        self.assertIsInstance(result, Succeeded)
        presenter.release.set()
        await sending
        self.assertEqual(self.channel.sent, [])
        self.assertIs((await self.status("obsolete-notice")).state, QueuedInputStatusKind.PENDING)
        cancelled = await self.actions.cancel_queued_input("obsolete-notice", action_id="cancel")
        self.assertIsInstance(cancelled, Succeeded)
        self.assertEqual(len(self.app.cancel_calls), 1)
        self.assertEqual(len(self.app.native_inputs), 1)

    async def test_presenter_capacity_bounds_tasks_across_conversations(self) -> None:
        presenter = _Presenter()
        presenter.block = True
        await self.start(
            presenter=presenter,
            limits=GatewayLimits(queued_input_present_max_concurrency=1),
        )
        await self.bind()
        other = ConversationRef("channel", "independent")
        await self.bind(conversation=other)
        sending = asyncio.create_task(self.emit("held-render"))
        await asyncio.wait_for(presenter.entered.wait(), 1)
        try:
            with self.assertRaisesRegex(RuntimeError, "at capacity"):
                await self.emit("capacity-render", conversation=other)
            self.assertEqual(len(presenter.calls), 1)
            self.assertEqual(len(self.app.native_inputs), 2)
            await self.emit("capacity-render", conversation=other, text="changed replay")
            self.assertEqual(len(self.app.native_inputs), 2)
        finally:
            presenter.release.set()
            await sending
        self.assertEqual(len(presenter.calls), 1)
        self.assertEqual(len(self.channel.sent), 1)

    async def test_stop_cancels_presenter_and_retained_actions_cannot_cancel_native(self) -> None:
        presenter = _Presenter()
        presenter.block = True
        await self.start(presenter=presenter)
        await self.bind()
        actions = self.actions
        sending = asyncio.create_task(self.emit("stop-render"))
        await asyncio.wait_for(presenter.entered.wait(), 1)
        await asyncio.wait_for(self.gateway.stop(), 1)
        with self.assertRaises(asyncio.CancelledError):
            await sending
        self.assertTrue(presenter.cancelled.is_set())
        with self.assertRaises(RuntimeError):
            await actions.cancel_queued_input("stop-render", action_id="after-stop")
        self.assertEqual(self.app.cancel_calls, [])
        self.assertEqual(len(self.app.native_inputs), 1)
        self.assertEqual(self.channel.sent, [])
        self.assertTrue(
            all(
                subscription.closed
                for items in self.app.subscriptions.values()
                for subscription in items
            )
        )

    async def test_shutdown_before_queue_fence_prevents_late_native_send(self) -> None:
        await self.start()
        await self.bind()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def pause_before_fence() -> None:
            entered.set()
            await release.wait()

        self.app.before_plan = pause_before_fence
        sending = asyncio.create_task(self.emit("stale-runtime"))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(self.gateway.stop(), 1)
        release.set()
        with self.assertRaises(RuntimeError):
            await sending
        self.assertEqual(self.app.native_inputs, [])
        self.assertEqual(self.app.cancel_calls, [])

    async def test_cancellation_after_native_fence_never_reopens_original_input(self) -> None:
        await self.start()
        await self.bind()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def pause_after_commit(_receipt: QueuedInputReceipt) -> None:
            entered.set()
            await release.wait()

        self.app.after_commit = pause_after_commit
        sending = asyncio.create_task(self.emit("cancelled-inflight"))
        await asyncio.wait_for(entered.wait(), 1)
        sending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await sending
        self.assertIsNone((await self.row("cancelled-inflight")).receipt)
        await self.assert_claim_fenced("cancelled-inflight")
        await self.emit("cancelled-inflight", text="edited after cancellation")
        self.assertEqual(len(self.app.native_inputs), 1)
        self.assertIs(
            (await self.status("cancelled-inflight")).state, QueuedInputStatusKind.PENDING
        )

    async def test_attempt_failed_notice_is_ephemeral_and_pending_remains_canonical(self) -> None:
        presenter = _Presenter()
        await self.start(presenter=presenter)
        thread = await self.bind()
        await self.emit("failed-attempt")
        original = await self.row("failed-attempt")
        assert original.receipt is not None
        self.app.notify(
            original.receipt,
            kind=QueuedInputNoticeKind.ATTEMPT_FAILED,
            notice_id="startup-failed-one",
        )
        async with asyncio.timeout(1):
            while not any(
                reason is QueuedInputNoticeReason.ATTEMPT_FAILED for _, reason in presenter.calls
            ):
                await asyncio.sleep(0)
        self.assertEqual(await self.row("failed-attempt"), original)
        self.assertIs((await self.status("failed-attempt")).state, QueuedInputStatusKind.PENDING)
        self.assertEqual(len(self.app.native_inputs), 1)
        self.assertEqual(len(self.app.subscriptions[thread]), 1)

    async def test_presenter_failure_does_not_silently_kill_queue_observation(self) -> None:
        presenter = _Presenter()
        await self.start(presenter=presenter)
        thread = await self.bind()
        await self.emit("observer-must-survive")
        original = await self.row("observer-must-survive")
        assert original.receipt is not None
        presenter.change = lambda output: replace(output, delivery_id="bad-extension-id")
        self.app.notify(
            original.receipt,
            kind=QueuedInputNoticeKind.ATTEMPT_FAILED,
            notice_id="bad-presentation",
        )
        async with asyncio.timeout(1):
            while len(presenter.calls) < 2:
                await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        presenter.change = None
        admitted = replace(
            original.receipt,
            state=QueuedInputState.ADMITTED,
            turn_ref=TurnRef(thread, "later-admitted"),
        )
        self.app.canonical[(thread, original.origin.client_message_id)] = admitted
        self.app.notify(admitted, notice_id="later-valid-change")
        await asyncio.sleep(0.05)
        current = await self.row("observer-must-survive")
        self.assertEqual(
            current.receipt,
            admitted,
            "a bounded presenter validation failure must not terminate canonical queue observation",
        )
        self.assertTrue(
            any(status.state is QueuedInputStatusKind.ADMITTED for status, _ in presenter.calls)
        )

    async def test_mixed_missing_snapshot_only_makes_that_original_input_unknown(self) -> None:
        await self.start()
        await self.bind()
        await self.emit("missing")
        await self.emit("present")
        missing = await self.row("missing")
        present = await self.row("present")
        self.app.hidden_identities.add(missing.origin.client_message_id)
        self.assertIs((await self.status("missing")).state, QueuedInputStatusKind.UNKNOWN)
        self.assertIs((await self.status("present")).state, QueuedInputStatusKind.PENDING)
        self.assertEqual(await self.row("missing"), missing)
        self.assertEqual(await self.row("present"), present)
        denied = await self.actions.cancel_queued_input("missing", action_id="no-evidence-cancel")
        self.assertIsInstance(denied, Failed)
        assert isinstance(denied, Failed)
        self.assertIs(denied.error.code, ActionErrorCode.NATIVE_OUTCOME_UNKNOWN)
        self.assertEqual(self.app.cancel_calls, [])
        cancelled = await self.actions.cancel_queued_input("present", action_id="known-cancel")
        self.assertIsInstance(cancelled, Succeeded)
        self.assertEqual(len(self.app.cancel_calls), 1)
        self.assertEqual(
            self.app.cancel_calls[0].client_message_id, present.origin.client_message_id
        )
        await self.assert_claim_fenced("missing")
        await self.assert_claim_fenced("present")
        self.assertEqual(len(self.app.native_inputs), 2)

    async def test_evidence_disappearing_after_cancel_preflight_prevents_native_cancel(
        self,
    ) -> None:
        await self.start()
        await self.bind()
        await self.emit("cancel-read-race")
        original = await self.row("cancel-read-race")
        self.app.hide_after_reads = len(self.app.reads) + 2
        result = await self.actions.cancel_queued_input("cancel-read-race", action_id="cancel-race")
        self.assertEqual(
            self.app.cancel_calls,
            [],
            "missing current canonical evidence at invocation must not cancel from cached lineage",
        )
        self.assertNotIsInstance(result, Succeeded)
        self.assertEqual(await self.row("cancel-read-race"), original)
        await self.assert_claim_fenced("cancel-read-race")
        self.assertEqual(len(self.app.native_inputs), 1)

    async def test_admission_presenter_failure_is_post_acceptance_and_claim_terminal(self) -> None:
        presenter = _Presenter()
        presenter.change = lambda output: replace(output, reply_to="changed")
        failure = _FailurePresenter()
        await self.start(presenter=presenter, failure_presenter=failure)
        await self.bind()
        await self.emit("accepted-before-render-failure")
        self.assertEqual(failure.phases, [InboundFailurePhase.POST_ACCEPTANCE])
        self.assertEqual(len(presenter.calls), 1)
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(self.channel.sent[0].reply_to, "accepted-before-render-failure")
        self.assertIs(
            (await self.status("accepted-before-render-failure")).state,
            QueuedInputStatusKind.PENDING,
        )
        assert self.store.session is not None
        scope, key = inbound_idempotency_identity(
            self.conversation, "accepted-before-render-failure"
        )
        self.assertIs(
            await self.store.session.claim(scope, key, owner_token="would-be-retry"),
            IdempotencyClaimStatus.ALREADY_COMPLETED,
        )
        await self.emit("accepted-before-render-failure", text="changed body")
        self.assertEqual(len(self.app.native_inputs), 1)
        self.assertEqual(len(presenter.calls), 1)

    async def test_malformed_partial_or_reordered_snapshot_never_creates_receipt(self) -> None:
        await self.start()
        await self.bind()
        self.app.acknowledgement = "lost"
        for identity in ("first-unknown", "second-unknown"):
            with self.assertRaises(OSError):
                await self.emit(identity)
        first = await self.row("first-unknown")
        second = await self.row("second-unknown")
        malformed: tuple[Callable[[tuple[QueuedInputReceipt | None, ...]], object], ...] = (
            lambda rows: list(rows),
            lambda rows: rows[:1],
            lambda rows: (*rows, rows[0]),
            lambda rows: tuple(reversed(rows)),
        )
        for change in malformed:
            self.app.snapshot_change = change
            result = await self.actions.read_queued_input("first-unknown")
            self.assertIsInstance(result, Failed)
            assert isinstance(result, Failed)
            self.assertIs(result.error.code, ActionErrorCode.NATIVE_OUTCOME_UNKNOWN)
            self.assertEqual(await self.row("first-unknown"), first)
            self.assertEqual(await self.row("second-unknown"), second)
        self.assertEqual(len(self.app.native_inputs), 2)
        await self.assert_claim_fenced("first-unknown")
        await self.assert_claim_fenced("second-unknown")

    async def test_transient_live_canonical_failure_reinstalls_observation_without_send(
        self,
    ) -> None:
        presenter = _Presenter()
        await self.start(presenter=presenter)
        thread = await self.bind()
        await self.emit("live-recovery")
        original = await self.row("live-recovery")
        assert original.receipt is not None
        admitted = replace(
            original.receipt,
            state=QueuedInputState.ADMITTED,
            turn_ref=TurnRef(thread, "recovered-turn"),
        )
        self.app.canonical[(thread, admitted.client_message_id)] = admitted
        presenter.entered.clear()

        def unavailable_once(rows):
            self.app.snapshot_change = None
            raise OSError("temporary canonical transport loss")

        self.app.snapshot_change = unavailable_once
        self.app.notify(admitted)
        await asyncio.wait_for(presenter.entered.wait(), 0.5)
        recovered = (await self.row("live-recovery")).receipt
        assert recovered is not None
        self.assertIs(recovered.state, QueuedInputState.ADMITTED)
        self.assertEqual(presenter.calls[-1][0].state, QueuedInputStatusKind.ADMITTED)
        self.assertEqual(len(self.app.native_inputs), 1)
        self.assertEqual(len(self.app.subscriptions[thread]), 2)

    async def test_proven_queue_capacity_rejection_uses_pre_acceptance_not_unknown(self) -> None:
        for sqlite in (False, True):
            with self.subTest(sqlite=sqlite):
                presenter = _Presenter()
                failure = _FailurePresenter()
                await self.start(
                    sqlite=sqlite, capacity=1, presenter=presenter, failure_presenter=failure
                )
                await self.bind()
                await self.emit("over-capacity")
                self.assertEqual(failure.phases, [InboundFailurePhase.PRE_ACCEPTANCE])
                self.assertEqual(presenter.calls, [])
                self.assertEqual(self.app.native_inputs, [])
                assert self.store.session is not None
                self.assertIsNone(
                    await self.store.session.get_queued_input(self.conversation, "over-capacity")
                )
                scope, key = inbound_idempotency_identity(self.conversation, "over-capacity")
                self.assertIs(
                    await self.store.session.claim(scope, key, owner_token="late"),
                    IdempotencyClaimStatus.ALREADY_COMPLETED,
                )
                await self.gateway.stop()

    async def test_committed_queue_fence_ack_loss_remains_unknown_without_native_send(self) -> None:
        for sqlite in (False, True):
            with self.subTest(sqlite=sqlite):
                presenter = _Presenter()
                failure = _FailurePresenter()
                await self.start(sqlite=sqlite, presenter=presenter, failure_presenter=failure)
                await self.bind()
                assert self.store.session is not None
                original_fence = self.store.session.fence_queued_input

                async def committed_then_lost(*args, **kwargs):
                    await original_fence(*args, **kwargs)
                    raise OSError("synthetic lost fence acknowledgement")

                with patch.object(self.store.session, "fence_queued_input", committed_then_lost):
                    await self.emit("lost-store-ack")
                self.assertEqual(failure.phases, [])
                self.assertEqual(presenter.calls[-1][0].state, QueuedInputStatusKind.UNKNOWN)
                self.assertIsNone((await self.row("lost-store-ack")).receipt)
                self.assertEqual(self.app.native_inputs, [])
                await self.assert_claim_fenced("lost-store-ack")
                await self.emit("lost-store-ack", text="changed")
                self.assertEqual(self.app.native_inputs, [])
                await self.gateway.stop()

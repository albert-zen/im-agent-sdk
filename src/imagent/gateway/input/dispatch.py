"""Canonical Gateway input dispatch and acceptance ordering."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from ...applications.contract import (
    AcceptedTurn,
    AgentApplicationAdapter,
    AgentInput,
    ApplicationInputDispatch,
    InputContinuationPreference,
    ThreadRef,
)
from ...applications.events import AgentEvent, EventBufferOverflow
from ...interaction.messages import Content, ConversationRef, InboundMessage
from ...interaction.operations import (
    OperationErrorCode,
    _MappedOperationError,
    require_identifier,
)

logger = logging.getLogger(__name__)

NativeSideEffectFence = Callable[[], Awaitable[None]]


class MissingBindingError(_MappedOperationError):
    """Ordinary input has no explicit complete Application/Project/Thread binding."""

    operation_error_code = OperationErrorCode.MISSING_BINDING

    def __init__(self) -> None:
        super().__init__("ordinary input requires an explicit Application/Project/Thread binding")


class OrderedProjectionEventApplier(Protocol):
    """Apply one normalized event once the acceptance-ordering gate releases it."""

    async def apply_ordered_event(self, event: AgentEvent) -> None: ...

    async def recover_ordering_gap(self, thread_ref: ThreadRef, *, gap_code: str) -> None: ...

    def has_observing_worker(self, thread_ref: ThreadRef) -> bool: ...


class InputDispatchCorrelator(Protocol):
    """Authorize and correlate the exact facts owned by request correlation."""

    async def authorize_input_dispatch(
        self,
        dispatch: ApplicationInputDispatch,
        *,
        thread_ref: ThreadRef,
        client_message_id: str,
    ) -> None: ...

    async def correlate_accepted_turn(
        self,
        accepted: AcceptedTurn,
        dispatch: ApplicationInputDispatch,
        *,
        thread_ref: ThreadRef,
        client_message_id: str,
        conversation_ref: ConversationRef,
        reply_to_message_id: str,
    ) -> None: ...


class InputPostAcceptanceError(RuntimeError):
    """Bridge post-processing failed after the Application accepted input."""

    def __init__(self, accepted_turn: AcceptedTurn, cause: BaseException) -> None:
        super().__init__(
            "Agent input was accepted before bridge post-processing failed: "
            f"{accepted_turn.turn_ref.turn_id}"
        )
        self.accepted_turn = accepted_turn
        self.cause = cause


class TurnAcceptanceBufferOverflow(EventBufferOverflow):
    def __init__(self, *, max_pending: int) -> None:
        super().__init__("turn_acceptance_buffer_overflow", max_pending=max_pending)


class InputDispatchRejected(RuntimeError):
    """Application input was rejected by a Gateway invariant before dispatch."""


class TurnAcceptanceOrderingGate:
    """Bound normalized events until all pending input acceptance resolves."""

    def __init__(self, *, max_pending: int) -> None:
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending < 1:
            raise ValueError("turn acceptance event maximum must be a positive integer")
        self._max_pending = max_pending
        self._event_locks: dict[ThreadRef, asyncio.Lock] = {}
        self._pending: dict[ThreadRef, int] = {}
        self._ready: dict[ThreadRef, asyncio.Event] = {}
        self._buffered_events: dict[ThreadRef, list[AgentEvent]] = {}
        self._buffered_event_overflows: set[ThreadRef] = set()

    def begin_acceptance(self, thread_ref: ThreadRef) -> None:
        if self._pending.get(thread_ref, 0) == 0:
            self._ready[thread_ref] = asyncio.Event()
        self._pending[thread_ref] = self._pending.get(thread_ref, 0) + 1

    async def finish_acceptance(
        self,
        thread_ref: ThreadRef,
        *,
        event_applier: OrderedProjectionEventApplier,
    ) -> None:
        pending = self._pending.get(thread_ref, 0)
        if pending < 1:
            raise RuntimeError("Turn acceptance ordering was not started")
        if pending > 1:
            self._pending[thread_ref] = pending - 1
            return
        self._pending.pop(thread_ref, None)
        ready = self._ready.pop(thread_ref, None)
        if ready is not None:
            ready.set()
        try:
            await self._drain_buffered_events(
                thread_ref,
                event_applier=event_applier,
            )
        finally:
            self._clear_acceptance_entries(
                thread_ref,
                event_applier=event_applier,
            )

    async def handle_event(
        self,
        event: AgentEvent,
        *,
        event_applier: OrderedProjectionEventApplier,
    ) -> None:
        thread_ref = event.thread_ref
        if thread_ref is None:
            return
        lock = self._event_locks.setdefault(thread_ref, asyncio.Lock())
        async with lock:
            if self._pending.get(thread_ref, 0) > 0:
                buffered = self._buffered_events.setdefault(thread_ref, [])
                if len(buffered) >= self._max_pending:
                    buffered.clear()
                    self._buffered_event_overflows.add(thread_ref)
                    raise TurnAcceptanceBufferOverflow(max_pending=self._max_pending)
                buffered.append(event)
                return
            await event_applier.apply_ordered_event(event)

    async def wait_for_acceptance(self, thread_ref: ThreadRef) -> None:
        ready = self._ready.get(thread_ref)
        if ready is not None:
            await ready.wait()

    def worker_finished(self, thread_ref: ThreadRef) -> None:
        if self._pending.get(thread_ref, 0) == 0:
            self._event_locks.pop(thread_ref, None)

    def reset(self) -> None:
        self._event_locks.clear()
        self._pending.clear()
        self._ready.clear()
        self._buffered_events.clear()
        self._buffered_event_overflows.clear()

    async def _drain_buffered_events(
        self,
        thread_ref: ThreadRef,
        *,
        event_applier: OrderedProjectionEventApplier,
    ) -> None:
        lock = self._event_locks.setdefault(thread_ref, asyncio.Lock())
        async with lock:
            if self._pending.get(thread_ref, 0) > 0:
                return
            if thread_ref in self._buffered_event_overflows:
                self._buffered_event_overflows.remove(thread_ref)
                self._buffered_events.pop(thread_ref, None)
                raise TurnAcceptanceBufferOverflow(max_pending=self._max_pending)
            events = self._buffered_events.pop(thread_ref, [])
            try:
                for event in events:
                    await event_applier.apply_ordered_event(event)
            except BaseException as ordered_event_error:
                try:
                    await event_applier.recover_ordering_gap(
                        thread_ref,
                        gap_code="turn_acceptance_ordering_drain_failed",
                    )
                except BaseException as recovery_error:
                    ordered_event_error.add_note(
                        "Ordering-gap recovery scheduling also failed after "
                        f"ordered event application: {recovery_error!r}"
                    )
                    logger.exception(
                        "Ordering-gap recovery scheduling failed while preserving "
                        "the ordered-event error",
                        exc_info=recovery_error,
                    )
                raise

    def _clear_acceptance_entries(
        self,
        thread_ref: ThreadRef,
        *,
        event_applier: OrderedProjectionEventApplier,
    ) -> None:
        if self._pending.get(thread_ref, 0) > 0:
            return
        self._ready.pop(thread_ref, None)
        self._buffered_events.pop(thread_ref, None)
        self._buffered_event_overflows.discard(thread_ref)
        if not event_applier.has_observing_worker(thread_ref):
            self._event_locks.pop(thread_ref, None)


class InputDispatchRuntime:
    """Authorize and correlate one verified inbound message around native dispatch."""

    def __init__(
        self,
        *,
        correlator: InputDispatchCorrelator,
        acceptance_gate: TurnAcceptanceOrderingGate,
        event_applier: OrderedProjectionEventApplier,
    ) -> None:
        self._correlator = correlator
        self._acceptance_gate = acceptance_gate
        self._event_applier = event_applier

    async def dispatch(
        self,
        application: AgentApplicationAdapter,
        thread_ref: ThreadRef,
        message: InboundMessage,
        *,
        content: tuple[Content, ...],
        before_application_send: NativeSideEffectFence | None = None,
    ) -> AcceptedTurn:
        client_message_id = derive_client_message_id(
            message.conversation_ref,
            message.message_id,
        )
        agent_input = AgentInput(
            client_message_id=client_message_id,
            content=content,
            sender=message.sender,
        )
        self._acceptance_gate.begin_acceptance(thread_ref)
        accepted: AcceptedTurn | None = None
        authorized_dispatch: ApplicationInputDispatch | None = None
        primary_error: BaseException | None = None

        async def authorize_dispatch(dispatch: ApplicationInputDispatch) -> None:
            nonlocal authorized_dispatch
            if authorized_dispatch is not None:
                raise InputDispatchRejected("Application input dispatch was declared twice")
            try:
                await self._correlator.authorize_input_dispatch(
                    dispatch,
                    thread_ref=thread_ref,
                    client_message_id=agent_input.client_message_id,
                )
            except ValueError as error:
                raise InputDispatchRejected(str(error)) from error
            authorized_dispatch = dispatch
            if before_application_send is not None:
                await before_application_send()

        try:
            accepted = await application.send_input(
                thread_ref,
                agent_input,
                continuation=InputContinuationPreference.PREFER_ACTIVE_TURN,
                before_dispatch=authorize_dispatch,
            )
            if authorized_dispatch is None:
                raise RuntimeError("Application accepted input without declaring dispatch")
            await self._correlator.correlate_accepted_turn(
                accepted,
                authorized_dispatch,
                thread_ref=thread_ref,
                client_message_id=agent_input.client_message_id,
                conversation_ref=message.conversation_ref,
                reply_to_message_id=message.message_id,
            )
        except BaseException as error:
            primary_error = error
        finally:
            try:
                await self._acceptance_gate.finish_acceptance(
                    thread_ref,
                    event_applier=self._event_applier,
                )
            except BaseException as drain_error:
                if primary_error is None:
                    primary_error = drain_error
                else:
                    primary_error.add_note(
                        f"Buffered-event draining also failed after input handling: {drain_error!r}"
                    )
                    logger.exception(
                        "Buffered-event draining failed while preserving the primary input error",
                        exc_info=drain_error,
                    )
        if primary_error is not None:
            if accepted is not None:
                raise InputPostAcceptanceError(accepted, primary_error) from primary_error
            raise primary_error
        if accepted is None:
            raise RuntimeError("Application input completed without an AcceptedTurn")
        return accepted


def derive_client_message_id(
    conversation: ConversationRef,
    channel_message_id: str,
) -> str:
    """Derive the replay-stable Gateway client identity for one native message."""

    require_identifier(channel_message_id, "channel_message_id")
    identity = json.dumps(
        [
            conversation.channel_instance_id,
            conversation.native_conversation_id,
            channel_message_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:client-message:sha256:{digest}"

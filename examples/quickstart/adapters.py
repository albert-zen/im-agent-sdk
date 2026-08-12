"""Small local adapters that implement the SDK's public Channel/Application ports."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from imagent.applications.capabilities import (
    ApplicationCapabilities,
    EventSequenceScope,
    ProjectCapabilities,
    ProjectMode,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
)
from imagent.applications.contract import (
    AcceptedTurn,
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    ApplicationInputDispatchHandler,
    ApplicationRef,
    ApplicationSummary,
    InputContinuationPreference,
    InputDisposition,
    Page,
    ProjectRef,
    ProjectSummary,
    ThreadHistory,
    ThreadRef,
    ThreadStatus,
    ThreadSummary,
    TurnCatchup,
    TurnHistoryEntry,
    TurnRef,
    TurnReplyCorrelationPolicy,
    TurnStatus,
    fingerprint_canonical_workspace_root,
)
from imagent.applications.events import AgentEvent, AgentEventType
from imagent.applications.operations import (
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    CreateProject,
    CreateThread,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetThreadStatus,
    GetTurnCatchup,
    ListProjects,
    ListThreads,
    ProjectCreated,
    ProjectRead,
    ProjectsListed,
    ThreadCreated,
    ThreadHistoryRead,
    ThreadRead,
    ThreadsListed,
    ThreadStatusRead,
    TurnCatchupRead,
    validate_application_operation,
    validate_application_operation_result,
)
from imagent.applications.requests import InteractiveRequest
from imagent.interaction.channels import (
    ChannelCapabilities,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySupportLevel,
    InboundAdmissionHandler,
    MessageHandler,
)
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    MessageRole,
    OutboundMessage,
    TextContent,
)
from imagent.interaction.operations import operation_error, require_identifier


def _now() -> datetime:
    return datetime.now(UTC)


class LocalChannel:
    """Bounded local transport seam; replace this with a native Channel adapter."""

    def __init__(self, channel_instance_id: str = "quickstart-channel") -> None:
        self._id = channel_instance_id
        self._capabilities = ChannelCapabilities(
            markdown=DeliverySupportLevel.NATIVE,
            reply_references=DeliverySupportLevel.NATIVE,
        )
        self._on_message: MessageHandler | None = None
        self._on_admission: InboundAdmissionHandler | None = None
        self._sent: list[OutboundMessage] = []
        self._changed = asyncio.Condition()

    @property
    def channel_instance_id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> ChannelCapabilities:
        return self._capabilities

    @property
    def sent(self) -> tuple[OutboundMessage, ...]:
        return tuple(self._sent)

    async def start(
        self,
        on_message: MessageHandler,
        on_admission: InboundAdmissionHandler | None = None,
    ) -> None:
        self._on_message = on_message
        self._on_admission = on_admission

    async def stop(self) -> None:
        self._on_message = None
        self._on_admission = None

    async def receive_text(
        self,
        conversation_ref: ConversationRef,
        *,
        actor: str,
        message_id: str,
        text: str,
    ) -> None:
        """Local native ingress: authenticate first, then request Gateway admission."""

        if self._on_message is None:
            raise RuntimeError("local Channel is not running")
        require_identifier(actor, "authenticated actor")
        message = InboundMessage(
            message_id=message_id,
            conversation_ref=conversation_ref,
            sender=actor,
            content=(TextContent(text),),
            created_at=_now(),
        )
        if self._on_admission is None:
            await self._on_message(message)
            return
        admission = await self._on_admission(conversation_ref, message_id)
        if admission is not None:
            await admission.deliver(message)

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        if self._on_message is None:
            raise RuntimeError("local Channel is not running")
        async with self._changed:
            if len(self._sent) >= 16:
                raise RuntimeError("local Channel delivery record capacity is exhausted")
            self._sent.append(message)
            self._changed.notify_all()
        return DeliveryReceipt(
            status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            native_message_id=f"local-delivery-{len(self._sent)}",
        )

    async def wait_for_delivery(self, *, timeout_seconds: float = 3.0) -> OutboundMessage:
        async with asyncio.timeout(timeout_seconds):
            async with self._changed:
                while not self._sent:
                    await self._changed.wait()
                return self._sent[-1]


class _Subscription(AsyncIterator[AgentEvent]):
    def __init__(self, owner: LocalEchoApplication, thread_ref: ThreadRef) -> None:
        self._owner = owner
        self._thread_ref = thread_ref
        self._queue: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=16)
        self._closed = False

    def __aiter__(self) -> _Subscription:
        return self

    async def __anext__(self) -> AgentEvent:
        if self._closed:
            raise StopAsyncIteration
        try:
            return await self._queue.get()
        except asyncio.CancelledError:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            self._owner.remove_subscription(self._thread_ref, self)

    def publish(self, event: AgentEvent) -> None:
        if self._closed:
            return
        if self._queue.full():
            raise RuntimeError("local Application event capacity is exhausted")
        self._queue.put_nowait(event)


class LocalEchoApplication:
    """Process-local Agent authority; the SDK never persists this state."""

    def __init__(self, application_instance_id: str = "quickstart-agent") -> None:
        capabilities = ApplicationCapabilities(
            projects=ProjectCapabilities(
                mode=ProjectMode.MANAGED,
                discovery=SupportLevel.NATIVE,
                reading=SupportLevel.NATIVE,
                creation=SupportLevel.NATIVE,
            ),
            threads=ThreadCapabilities(
                listing=SupportLevel.NATIVE,
                creation=SupportLevel.NATIVE,
                reading=SupportLevel.NATIVE,
            ),
            runtime=RuntimeCapabilities(
                history=SupportLevel.NATIVE,
                streaming=SupportLevel.NATIVE,
                replay_from_cursor=SupportLevel.UNSUPPORTED,
                interruption=SupportLevel.UNSUPPORTED,
                interactive_requests=SupportLevel.UNSUPPORTED,
                event_sequence_scope=EventSequenceScope.NONE,
            ),
        )
        self._summary = ApplicationSummary(
            ref=ApplicationRef(application_instance_id),
            kind="quickstart-echo",
            display_name="Quickstart Echo Application",
            capabilities=capabilities,
        )
        self._projects: dict[ProjectRef, ProjectSummary] = {}
        self._threads: dict[ThreadRef, ThreadSummary] = {}
        self._history: dict[ThreadRef, list[TurnHistoryEntry]] = {}
        self._project_actions: dict[str, ProjectSummary] = {}
        self._thread_actions: dict[str, ThreadSummary] = {}
        self._subscriptions: dict[ThreadRef, set[_Subscription]] = {}
        self._next_project = 1
        self._next_thread = 1
        self._next_turn = 1

    @property
    def summary(self) -> ApplicationSummary:
        return self._summary

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        for subscriptions in tuple(self._subscriptions.values()):
            for subscription in tuple(subscriptions):
                await subscription.aclose()

    async def list_pending_requests(self) -> tuple[InteractiveRequest, ...]:
        return ()

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if after_cursor is not None:
            raise ValueError("the quickstart Application does not advertise cursor replay")
        if thread_ref not in self._threads:
            raise KeyError(thread_ref)
        subscription = _Subscription(self, thread_ref)
        self._subscriptions.setdefault(thread_ref, set()).add(subscription)
        return subscription

    def remove_subscription(self, thread_ref: ThreadRef, subscription: _Subscription) -> None:
        subscriptions = self._subscriptions.get(thread_ref)
        if subscriptions is not None:
            subscriptions.discard(subscription)
            if not subscriptions:
                self._subscriptions.pop(thread_ref, None)

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn:
        del continuation
        turn_ref = TurnRef(thread_ref, f"turn-{self._next_turn}")
        self._next_turn += 1
        dispatch = ApplicationInputDispatch(
            thread_ref=thread_ref,
            client_message_id=message.client_message_id,
            disposition=InputDisposition.STARTED,
            correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
        )
        if before_dispatch is not None:
            await before_dispatch(dispatch)
        user_text = " ".join(item.text for item in message.content if isinstance(item, TextContent))
        response = AgentMessage(
            agent_item_id=f"{turn_ref.turn_id}:assistant",
            thread_ref=thread_ref,
            role=MessageRole.ASSISTANT,
            content=(TextContent(f"Echo: {user_text}"),),
            created_at=_now(),
            client_message_id=message.client_message_id,
        )
        user_message = AgentMessage(
            agent_item_id=f"{turn_ref.turn_id}:user",
            thread_ref=thread_ref,
            role=MessageRole.USER,
            content=message.content,
            created_at=_now(),
            client_message_id=message.client_message_id,
        )
        self._history[thread_ref].append(
            TurnHistoryEntry(
                turn_ref=turn_ref,
                status=TurnStatus.COMPLETED,
                user_message=user_message,
                agent_messages=(response,),
            )
        )
        self._threads[thread_ref] = replace(
            self._threads[thread_ref], status=ThreadStatus.COMPLETED, updated_at=_now()
        )
        self._publish(thread_ref, turn_ref, response)
        return AcceptedTurn(
            turn_ref=turn_ref,
            client_message_id=message.client_message_id,
            disposition=InputDisposition.STARTED,
            correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
        )

    def _publish(self, thread_ref: ThreadRef, turn_ref: TurnRef, message: AgentMessage) -> None:
        event = AgentEvent(
            event_id=f"event-{turn_ref.turn_id}",
            application_instance_id=self.summary.ref.application_instance_id,
            project_ref=thread_ref.project_ref,
            thread_ref=thread_ref,
            turn_ref=turn_ref,
            type=AgentEventType.MESSAGE_COMPLETED,
            data={"message": message},
            created_at=_now(),
        )
        for subscription in tuple(self._subscriptions.get(thread_ref, ())):
            subscription.publish(event)

    async def execute(self, operation: ApplicationOperation) -> ApplicationOperationResult:
        try:
            validate_application_operation(operation)
            result = self._execute(operation)
            validate_application_operation_result(operation, result)
            return result
        except Exception as error:
            return ApplicationOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                error=operation_error(error),
                completed_at=_now(),
            )

    def _execute(self, operation: ApplicationOperation) -> ApplicationOperationResult:
        completed_at = _now()
        if operation.application_ref != self.summary.ref:
            raise ValueError("operation belongs to another Application")
        if isinstance(operation, CreateProject):
            project = self._project_actions.get(operation.operation_id)
            if project is None:
                if len(self._projects) >= 4:
                    raise ValueError("quickstart Project capacity is exhausted")
                root = str(Path(operation.cwd).resolve())
                project = ProjectSummary(
                    ref=ProjectRef(
                        self.summary.ref.application_instance_id,
                        f"project-{self._next_project}",
                    ),
                    display_name=operation.display_name or "Quickstart workspace",
                    root_path=root,
                    workspace_root_fingerprint=fingerprint_canonical_workspace_root(root),
                )
                self._next_project += 1
                self._projects[project.ref] = project
                self._project_actions[operation.operation_id] = project
            return ProjectCreated(
                operation_id=operation.operation_id, completed_at=completed_at, project=project
            )
        if isinstance(operation, GetProject):
            return ProjectRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                project=self._projects[operation.project_ref],
            )
        if isinstance(operation, ListProjects):
            return ProjectsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                projects=Page(tuple(self._projects.values())),
            )
        if isinstance(operation, CreateThread):
            thread = self._thread_actions.get(operation.operation_id)
            if thread is None:
                if operation.project_ref not in self._projects:
                    raise KeyError(operation.project_ref)
                if len(self._threads) >= 8:
                    raise ValueError("quickstart Thread capacity is exhausted")
                thread = ThreadSummary(
                    ref=ThreadRef(operation.project_ref, f"thread-{self._next_thread}"),
                    status=ThreadStatus.IDLE,
                    title=operation.title,
                    updated_at=completed_at,
                )
                self._next_thread += 1
                self._threads[thread.ref] = thread
                self._history[thread.ref] = []
                self._thread_actions[operation.operation_id] = thread
            return ThreadCreated(
                operation_id=operation.operation_id, completed_at=completed_at, thread=thread
            )
        if isinstance(operation, GetThread):
            return ThreadRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread=self._threads[operation.thread_ref],
            )
        if isinstance(operation, ListThreads):
            threads = tuple(
                thread
                for thread in self._threads.values()
                if thread.ref.project_ref == operation.project_ref
            )
            return ThreadsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                threads=Page(threads),
            )
        if isinstance(operation, GetThreadStatus):
            return ThreadStatusRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                thread_ref=operation.thread_ref,
                thread_status=self._threads[operation.thread_ref].status,
            )
        if isinstance(operation, GetThreadHistory):
            return ThreadHistoryRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                history=ThreadHistory(
                    thread_ref=operation.thread_ref,
                    turns=tuple(self._history[operation.thread_ref][-operation.limit :]),
                ),
            )
        if isinstance(operation, GetTurnCatchup):
            latest = self._history[operation.thread_ref][-1:]
            turn = latest[0] if latest else None
            return TurnCatchupRead(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                catchup=TurnCatchup(
                    thread_ref=operation.thread_ref,
                    turn_ref=None if turn is None else turn.turn_ref,
                    status=TurnStatus.IDLE if turn is None else turn.status,
                    messages=() if turn is None else turn.agent_messages,
                ),
            )
        raise NotImplementedError(operation.type.value)


def outbound_text(message: OutboundMessage) -> str:
    return " ".join(item.text for item in message.content if isinstance(item, TextContent))


__all__ = ["LocalChannel", "LocalEchoApplication", "outbound_text"]

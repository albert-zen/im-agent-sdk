from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

from imagent.contracts import (
    AcceptedTurn,
    ActivateNativeThread,
    AgentEvent,
    AgentEventType,
    AgentInput,
    AgentMessage,
    ApplicationCapabilities,
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    ApplicationRef,
    ApplicationSummary,
    ApprovalRequest,
    ChannelCapabilities,
    CreateThread,
    DeleteThread,
    DeliveryReceipt,
    EventSequenceScope,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetThreadStatus,
    GetTurnCatchup,
    InterruptTurn,
    ListProjects,
    ListThreads,
    MessageRole,
    NativeThreadActivated,
    OutboundMessage,
    Page,
    ProjectCapabilities,
    ProjectMode,
    ProjectRead,
    ProjectRef,
    ProjectsListed,
    ProjectSummary,
    RequestChoice,
    RequestDuplicateError,
    RequestRef,
    RequestResolution,
    RequestResolutionStatus,
    RequestResolvedError,
    RequestResponded,
    RequestResponse,
    RequestStaleError,
    RespondRequest,
    RuntimeCapabilities,
    SupportLevel,
    TextContent,
    TextFormat,
    ThreadCapabilities,
    ThreadCreated,
    ThreadDeleted,
    ThreadDeletionCapability,
    ThreadDeletionMode,
    ThreadHistory,
    ThreadHistoryRead,
    ThreadRead,
    ThreadRef,
    ThreadsListed,
    ThreadSnapshot,
    ThreadStatus,
    ThreadStatusRead,
    ThreadSummary,
    TurnCatchup,
    TurnCatchupRead,
    TurnHistoryEntry,
    TurnInterrupted,
    TurnStatus,
    UserInputQuestion,
    UserInputRequest,
    derive_request_response_shape,
    operation_error,
    validate_application_operation,
    validate_application_operation_result,
    validate_request_response,
)
from imagent.events import CursorExpired, EventBroadcaster


def make_capabilities(project_mode: ProjectMode) -> ApplicationCapabilities:
    project_support = (
        SupportLevel.NATIVE if project_mode is ProjectMode.MANAGED else SupportLevel.UNSUPPORTED
    )
    return ApplicationCapabilities(
        projects=ProjectCapabilities(
            mode=project_mode,
            discovery=project_support,
            reading=project_support,
        ),
        threads=ThreadCapabilities(
            listing=SupportLevel.NATIVE,
            creation=SupportLevel.NATIVE,
            reading=SupportLevel.NATIVE,
            deletion=ThreadDeletionCapability.PERMANENT,
        ),
        runtime=RuntimeCapabilities(
            history=SupportLevel.NATIVE,
            streaming=SupportLevel.NATIVE,
            replay_from_cursor=SupportLevel.NATIVE,
            interruption=SupportLevel.NATIVE,
            interactive_requests=SupportLevel.NATIVE,
            pending_request_snapshot=SupportLevel.NATIVE,
            native_thread_activation=SupportLevel.NATIVE,
            gap_detection=SupportLevel.NATIVE,
            event_sequence_scope=EventSequenceScope.THREAD,
        ),
    )


class FakeChannelAdapter:
    def __init__(self, channel_instance_id: str = "fake-channel") -> None:
        self._channel_instance_id = channel_instance_id
        self._capabilities = ChannelCapabilities()
        self.started = False
        self.sent: list[OutboundMessage] = []

    @property
    def channel_instance_id(self) -> str:
        return self._channel_instance_id

    @property
    def capabilities(self) -> ChannelCapabilities:
        return self._capabilities

    async def start(self, on_message, on_operation) -> None:
        self.started = True
        self.on_message = on_message
        self.on_operation = on_operation

    async def stop(self) -> None:
        self.started = False

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        self.sent.append(message)
        return DeliveryReceipt(
            status="accepted_by_platform",
            native_message_id=f"sent-{len(self.sent)}",
        )


class FakeAgentApplicationAdapter:
    def __init__(
        self,
        application_instance_id: str = "fake-agent",
        project_mode: ProjectMode = ProjectMode.MANAGED,
        event_history_limit: int = 100,
    ) -> None:
        if event_history_limit < 1:
            raise ValueError("event_history_limit must be positive")
        self._application_id = application_instance_id
        self._capabilities = make_capabilities(project_mode)
        self._summary = ApplicationSummary(
            ref=ApplicationRef(application_instance_id),
            kind="fake",
            display_name="Fake Agent",
            capabilities=self._capabilities,
        )
        self._projects: dict[ProjectRef, ProjectSummary] = {}
        if project_mode is ProjectMode.MANAGED:
            ref = ProjectRef(application_instance_id, "contract-project")
            self._projects[ref] = ProjectSummary(ref=ref, display_name="Contract Project")
        self._threads: dict[ThreadRef, ThreadSummary] = {}
        self._inputs: list[tuple[ThreadRef, AgentInput]] = []
        self._turn_history: dict[ThreadRef, list[TurnHistoryEntry]] = {}
        self._events = EventBroadcaster[ThreadRef, AgentEvent]()
        self._event_epoch = str(uuid.uuid4())
        self._event_history_limit = event_history_limit
        self._sequences: dict[ThreadRef, int] = {}
        self._event_history: dict[ThreadRef, list[AgentEvent]] = {}
        self._next_thread = 1
        self.activated_threads: list[ThreadRef] = []
        self._open_requests: dict[RequestRef, ApprovalRequest | UserInputRequest] = {}
        self._resolved_requests: set[RequestRef] = set()
        self.request_responses: dict[RequestRef, RequestResponse] = {}
        self._next_request = 1

    @property
    def summary(self) -> ApplicationSummary:
        return self._summary

    @property
    def capabilities(self) -> ApplicationCapabilities:
        return self._capabilities

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def list_pending_requests(
        self,
    ) -> tuple[ApprovalRequest | UserInputRequest, ...]:
        return tuple(self._open_requests.values())

    async def list_projects(self, cursor=None) -> Page[ProjectSummary]:
        return Page(tuple(self._projects.values()))

    async def execute(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        try:
            validate_application_operation(operation)
            result = await self._execute(operation)
            validate_application_operation_result(operation, result)
            return result
        except Exception as error:
            return ApplicationOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=datetime.now(UTC),
                error=operation_error(error),
            )

    async def _execute(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        now = datetime.now(UTC)
        if isinstance(operation, ListProjects):
            return ProjectsListed(
                operation_id=operation.operation_id,
                completed_at=now,
                projects=await self.list_projects(operation.cursor),
            )
        if isinstance(operation, GetProject):
            return ProjectRead(
                operation_id=operation.operation_id,
                completed_at=now,
                project=await self.get_project(operation.project_ref),
            )
        if isinstance(operation, ListThreads):
            return ThreadsListed(
                operation_id=operation.operation_id,
                completed_at=now,
                threads=await self.list_threads(operation.project_ref, operation.cursor),
            )
        if isinstance(operation, CreateThread):
            return ThreadCreated(
                operation_id=operation.operation_id,
                completed_at=now,
                thread=await self.create_thread(operation.project_ref, operation.title),
            )
        if isinstance(operation, GetThread):
            return ThreadRead(
                operation_id=operation.operation_id,
                completed_at=now,
                thread=await self.get_thread(operation.thread_ref),
            )
        if isinstance(operation, ActivateNativeThread):
            await self.get_thread(operation.thread_ref)
            self.activated_threads.append(operation.thread_ref)
            return NativeThreadActivated(
                operation_id=operation.operation_id,
                completed_at=now,
                thread_ref=operation.thread_ref,
            )
        if isinstance(operation, DeleteThread):
            if operation.mode is not ThreadDeletionMode.PERMANENT:
                raise NotImplementedError("fake adapter supports permanent deletion")
            await self.delete_thread(operation.thread_ref)
            return ThreadDeleted(
                operation_id=operation.operation_id,
                completed_at=now,
                thread_ref=operation.thread_ref,
                mode=ThreadDeletionMode.PERMANENT,
            )
        if isinstance(operation, GetThreadStatus):
            return ThreadStatusRead(
                operation_id=operation.operation_id,
                completed_at=now,
                thread_ref=operation.thread_ref,
                thread_status=await self.get_thread_status(operation.thread_ref),
            )
        if isinstance(operation, GetTurnCatchup):
            turns = self._turn_history.get(operation.thread_ref, [])
            latest = turns[-1] if turns else None
            return TurnCatchupRead(
                operation_id=operation.operation_id,
                completed_at=now,
                catchup=TurnCatchup(
                    thread_ref=operation.thread_ref,
                    turn_id=latest.turn_id if latest is not None else None,
                    status=latest.status if latest is not None else TurnStatus.IDLE,
                    messages=latest.agent_messages if latest is not None else (),
                ),
            )
        if isinstance(operation, GetThreadHistory):
            turns = self._turn_history.get(operation.thread_ref, [])
            end = max(0, len(turns) - ((operation.page - 1) * operation.limit))
            start = max(0, end - operation.limit)
            return ThreadHistoryRead(
                operation_id=operation.operation_id,
                completed_at=now,
                history=ThreadHistory(
                    thread_ref=operation.thread_ref,
                    turns=tuple(turns[start:end]),
                    page=operation.page,
                    has_older=start > 0,
                ),
            )
        if isinstance(operation, InterruptTurn):
            await self.interrupt_turn(operation.thread_ref, operation.turn_id)
            return TurnInterrupted(
                operation_id=operation.operation_id,
                completed_at=now,
                thread_ref=operation.thread_ref,
                turn_id=operation.turn_id,
            )
        if isinstance(operation, RespondRequest):
            await self.respond_request(operation.request_ref, operation.response)
            return RequestResponded(
                operation_id=operation.operation_id,
                completed_at=now,
                request_ref=operation.request_ref,
            )
        raise NotImplementedError(operation.type.value)

    async def get_project(self, project_ref: ProjectRef) -> ProjectSummary:
        return self._projects[project_ref]

    async def list_threads(self, project_ref=None, cursor=None) -> Page[ThreadSummary]:
        return Page(
            tuple(
                thread
                for thread in self._threads.values()
                if project_ref is None or thread.ref.project_ref == project_ref
            )
        )

    async def create_thread(self, project_ref=None, title=None) -> ThreadSummary:
        ref = ThreadRef(
            application_instance_id=self._application_id,
            native_thread_id=f"thread-{self._next_thread}",
            project_ref=project_ref,
        )
        self._next_thread += 1
        summary = ThreadSummary(
            ref=ref,
            status=ThreadStatus.IDLE,
            title=title,
            updated_at=datetime.now(UTC),
        )
        self._threads[ref] = summary
        self._turn_history[ref] = []
        return summary

    async def get_thread(self, thread_ref: ThreadRef) -> ThreadSummary:
        return self._threads[thread_ref]

    async def delete_thread(self, thread_ref: ThreadRef) -> None:
        del self._threads[thread_ref]

    async def read_thread(self, thread_ref: ThreadRef, cursor=None) -> ThreadSnapshot:
        return ThreadSnapshot(thread=self._threads[thread_ref], messages=(), cursor=cursor)

    async def send_input(self, thread_ref: ThreadRef, message: AgentInput) -> AcceptedTurn:
        self._inputs.append((thread_ref, message))
        turn_id = f"turn-{len(self._inputs)}"
        self._threads[thread_ref] = replace(
            self._threads[thread_ref],
            status=ThreadStatus.RUNNING,
            updated_at=datetime.now(UTC),
        )
        agent_messages: list[AgentMessage] = []
        for index, phase in enumerate(("commentary", "final_answer"), start=1):
            agent_message = AgentMessage(
                agent_item_id=f"{turn_id}:message:{index}",
                thread_ref=thread_ref,
                role=MessageRole.ASSISTANT,
                content=(TextContent(f"fake {phase}", TextFormat.MARKDOWN),),
                created_at=datetime.now(UTC),
                metadata={"phase": phase},
            )
            agent_messages.append(agent_message)
            self._publish(
                thread_ref,
                AgentEventType.MESSAGE_COMPLETED,
                turn_id,
                {"message": agent_message},
            )
        self._publish(
            thread_ref,
            AgentEventType.TURN_COMPLETED,
            turn_id,
            {"status": "completed"},
        )
        self._threads[thread_ref] = replace(
            self._threads[thread_ref],
            status=ThreadStatus.COMPLETED,
            updated_at=datetime.now(UTC),
        )
        self._turn_history[thread_ref].append(
            TurnHistoryEntry(
                turn_id=turn_id,
                status=TurnStatus.COMPLETED,
                user_message=AgentMessage(
                    agent_item_id=f"{turn_id}:user",
                    thread_ref=thread_ref,
                    role=MessageRole.USER,
                    content=message.content,
                    created_at=datetime.now(UTC),
                    client_message_id=message.client_message_id,
                ),
                agent_messages=tuple(agent_messages),
            )
        )
        return AcceptedTurn(
            thread_ref=thread_ref,
            turn_id=turn_id,
            client_message_id=message.client_message_id,
        )

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor=None,
    ) -> AsyncIterator[AgentEvent]:
        if thread_ref not in self._threads:
            raise KeyError(thread_ref)
        initial: tuple[AgentEvent, ...] = ()
        if after_cursor is not None:
            history = self._event_history.get(thread_ref, [])
            for index, event in enumerate(history):
                if event.cursor == after_cursor:
                    initial = tuple(history[index + 1 :])
                    break
            else:
                raise CursorExpired("fake replay cursor expired or belongs to another epoch")
        return self._events.subscribe(thread_ref, initial=initial)

    def _publish(
        self,
        thread_ref: ThreadRef,
        event_type: AgentEventType,
        turn_id: str,
        data,
        *,
        request: ApprovalRequest | UserInputRequest | None = None,
        request_resolution: RequestResolution | None = None,
    ) -> None:
        sequence = self._sequences.get(thread_ref, 0) + 1
        self._sequences[thread_ref] = sequence
        message = data.get("message")
        if isinstance(message, AgentMessage):
            native_identity = f"message:{message.agent_item_id}"
        elif request is not None:
            native_identity = f"request:{request.request_ref.native_request_id}:{event_type.value}"
        elif request_resolution is not None:
            native_identity = (
                f"request:{request_resolution.request_ref.native_request_id}:{event_type.value}"
            )
        else:
            native_identity = f"turn:{turn_id}:{event_type.value}"
        event = AgentEvent(
            event_id=(
                f"{self._application_id}:thread:{thread_ref.native_thread_id}:{native_identity}"
            ),
            application_instance_id=self._application_id,
            sequence=sequence,
            sequence_epoch=self._event_epoch,
            type=event_type,
            data=data,
            created_at=datetime.now(UTC),
            thread_ref=thread_ref,
            turn_id=turn_id,
            cursor=(f"fake:{self._event_epoch}:{thread_ref.native_thread_id}:{sequence}"),
            request=request,
            request_resolution=request_resolution,
        )
        history = self._event_history.setdefault(thread_ref, [])
        history.append(event)
        del history[: -self._event_history_limit]
        self._events.publish(thread_ref, event)

    async def get_thread_status(self, thread_ref: ThreadRef) -> ThreadStatus:
        return self._threads[thread_ref].status

    async def interrupt_turn(self, thread_ref: ThreadRef, turn_id=None) -> None:
        self._threads[thread_ref] = replace(
            self._threads[thread_ref],
            status=ThreadStatus.INTERRUPTED,
        )

    async def open_approval_request(
        self,
        thread_ref: ThreadRef,
        *,
        turn_id: str,
        prompt: str = "Approve the requested action?",
        choices: tuple[RequestChoice, ...] = (
            RequestChoice("accept", "Approve once"),
            RequestChoice("accept_for_session", "Approve for session"),
            RequestChoice("decline", "Deny"),
            RequestChoice("cancel", "Cancel"),
        ),
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            request_ref=self._new_request_ref(),
            thread_ref=thread_ref,
            turn_id=turn_id,
            prompt=prompt,
            choices=choices,
        )
        self._open_requests[request.request_ref] = request
        self._publish(
            thread_ref,
            AgentEventType.REQUEST_OPENED,
            turn_id,
            {},
            request=request,
        )
        return request

    async def open_user_input_request(
        self,
        thread_ref: ThreadRef,
        *,
        turn_id: str,
        questions: tuple[UserInputQuestion, ...],
        prompt: str | None = None,
    ) -> UserInputRequest:
        request = UserInputRequest(
            request_ref=self._new_request_ref(),
            thread_ref=thread_ref,
            turn_id=turn_id,
            questions=questions,
            prompt=prompt,
        )
        self._open_requests[request.request_ref] = request
        self._publish(
            thread_ref,
            AgentEventType.REQUEST_OPENED,
            turn_id,
            {},
            request=request,
        )
        return request

    async def resolve_request(
        self,
        request_ref: RequestRef,
        *,
        status: RequestResolutionStatus = RequestResolutionStatus.RESOLVED,
    ) -> None:
        request = self._open_requests.pop(request_ref, None)
        if request is None:
            raise RequestStaleError("fake request is not pending")
        self._resolved_requests.add(request_ref)
        resolution = RequestResolution(
            request_ref=request_ref,
            status=status,
            resolved_at=datetime.now(UTC),
        )
        self._publish(
            request.thread_ref,
            AgentEventType.REQUEST_RESOLVED,
            request.turn_id,
            {},
            request_resolution=resolution,
        )

    async def respond_request(
        self,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> None:
        if request_ref in self.request_responses:
            raise RequestDuplicateError("fake request already has a response")
        request = self._open_requests.get(request_ref)
        if request is None:
            if request_ref in self._resolved_requests:
                raise RequestResolvedError("fake request is resolved")
            raise RequestStaleError("fake request is not pending")
        validate_request_response(
            response,
            derive_request_response_shape(request),
        )
        self.request_responses[request_ref] = response

    def _new_request_ref(self) -> RequestRef:
        request_ref = RequestRef(
            application_ref=self._summary.ref,
            native_request_id=f"{self._event_epoch}:request-{self._next_request}",
        )
        self._next_request += 1
        return request_ref

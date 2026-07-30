from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .adapters import (
    AgentApplicationAdapter,
    BindingRepository,
    ChannelAdapter,
    IdempotencyRepository,
)
from .bindings import BindingConflict
from .contracts import (
    AcceptedTurn,
    AgentEventType,
    AgentInput,
    AgentMessage,
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    ApplicationsListed,
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationThread,
    ContractError,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    CreateThread,
    GatewayOperation,
    GatewayOperationFailed,
    GatewayOperationResult,
    GetProject,
    GetThread,
    InboundMessage,
    ListApplications,
    OperationErrorCode,
    OutboundMessage,
    ProjectMode,
    ProjectRead,
    SelectApplication,
    TextContent,
    TextFormat,
    ThreadCreated,
    ThreadRead,
    derive_client_message_id,
    operation_error,
    validate_application_operation,
    validate_application_operation_result,
    validate_gateway_operation,
    validate_gateway_operation_result,
)
from .controllers import ControllerActions, InboundController
from .storage import InMemoryIdempotencyRepository

logger = logging.getLogger(__name__)


class ImAgentGateway:
    """Channel/application orchestration independent from one interaction grammar."""

    def __init__(
        self,
        *,
        channels: list[ChannelAdapter],
        applications: list[AgentApplicationAdapter],
        bindings: BindingRepository,
        idempotency: IdempotencyRepository | None = None,
        controller: InboundController | None = None,
    ) -> None:
        self._channels = {channel.channel_instance_id: channel for channel in channels}
        self._applications = {
            application.summary.ref.application_instance_id: application
            for application in applications
        }
        self._bindings = bindings
        self._idempotency = idempotency or InMemoryIdempotencyRepository()
        self._controller = controller
        self._locks: dict[object, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        for application in self._applications.values():
            await application.start()
        for channel in self._channels.values():
            await channel.start(self._handle_message, self._handle_operation)

    async def stop(self) -> None:
        for channel in reversed(tuple(self._channels.values())):
            await channel.stop()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for application in reversed(tuple(self._applications.values())):
            await application.stop()

    async def execute_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        """Route one typed application operation without mutating a binding."""
        try:
            validate_application_operation(operation)
            application = self._applications[operation.application_ref.application_instance_id]
            result = await application.execute(operation)
            validate_application_operation_result(operation, result)
            return result
        except Exception as error:
            return ApplicationOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=datetime.now(UTC),
                error=_contract_error(error),
            )

    async def execute_gateway(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        """Execute one typed Gateway operation under Conversation serialization."""
        lock = self._locks.setdefault(operation.conversation_ref, asyncio.Lock())
        async with lock:
            return await self._execute_gateway_locked(operation)

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None:
        return await self._bindings.get(conversation_ref)

    async def _execute_gateway_locked(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        try:
            validate_gateway_operation(operation)
            result = await self._apply_gateway_operation(operation)
            validate_gateway_operation_result(operation, result)
            return result
        except _GatewayActionError as error:
            contract_error = error.error
        except Exception as error:
            contract_error = _contract_error(error)
        return GatewayOperationFailed(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=datetime.now(UTC),
            error=contract_error,
        )

    async def _apply_gateway_operation(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        completed_at = datetime.now(UTC)
        if isinstance(operation, ListApplications):
            return ApplicationsListed(
                operation_id=operation.operation_id,
                completed_at=completed_at,
                applications=tuple(
                    application.summary for application in self._applications.values()
                ),
            )
        if isinstance(operation, SelectApplication):
            self._require_application(operation.application_ref.application_instance_id)
            binding = await self._bindings.put(
                ConversationBinding(
                    conversation_ref=operation.conversation_ref,
                    application_ref=operation.application_ref,
                ),
                expected_revision=operation.expected_revision,
            )
            return ConversationBound(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=completed_at,
                binding=binding,
            )
        if isinstance(operation, BindConversationToProject):
            application = self._require_application(operation.project_ref.application_instance_id)
            read = await self.execute_application(
                GetProject(
                    operation_id=f"{operation.operation_id}:validate-project",
                    application_ref=application.summary.ref,
                    project_ref=operation.project_ref,
                    created_at=operation.created_at,
                )
            )
            if isinstance(read, ApplicationOperationFailed):
                raise _GatewayActionError(read.error)
            if not isinstance(read, ProjectRead):
                raise RuntimeError("project.get returned an incompatible result")
            binding = await self._bindings.put(
                ConversationBinding(
                    conversation_ref=operation.conversation_ref,
                    application_ref=application.summary.ref,
                    project_ref=read.project.ref,
                ),
                expected_revision=operation.expected_revision,
            )
            return ConversationBound(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=completed_at,
                binding=binding,
            )
        if isinstance(operation, BindConversationToThread):
            application = self._require_application(operation.thread_ref.application_instance_id)
            read = await self.execute_application(
                GetThread(
                    operation_id=f"{operation.operation_id}:validate-thread",
                    application_ref=application.summary.ref,
                    thread_ref=operation.thread_ref,
                    created_at=operation.created_at,
                )
            )
            if isinstance(read, ApplicationOperationFailed):
                raise _GatewayActionError(read.error)
            if not isinstance(read, ThreadRead):
                raise RuntimeError("thread.get returned an incompatible result")
            binding = await self._bindings.put(
                ConversationBinding(
                    conversation_ref=operation.conversation_ref,
                    application_ref=application.summary.ref,
                    project_ref=read.thread.ref.project_ref,
                    thread_ref=read.thread.ref,
                ),
                expected_revision=operation.expected_revision,
            )
            return ConversationBound(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=completed_at,
                binding=binding,
            )
        if isinstance(operation, ClearConversationThread):
            current = await self._bindings.get(operation.conversation_ref)
            if current is None:
                raise ValueError("Conversation has no binding")
            binding = await self._bindings.put(
                ConversationBinding(
                    conversation_ref=current.conversation_ref,
                    application_ref=current.application_ref,
                    project_ref=current.project_ref,
                ),
                expected_revision=operation.expected_revision,
            )
            return ConversationBound(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=completed_at,
                binding=binding,
            )
        raise NotImplementedError(operation.type.value)

    async def _handle_message(self, message: InboundMessage) -> None:
        scope = f"inbound:{message.conversation_ref.channel_instance_id}"
        key = f"{message.conversation_ref.native_conversation_id}:{message.message_id}"
        if not await self._idempotency.claim(scope, key):
            return
        try:
            await self._process_message(message)
        except BaseException:
            await self._idempotency.release(scope, key)
            raise
        await self._idempotency.complete(scope, key)

    async def _process_message(self, message: InboundMessage) -> None:
        lock = self._locks.setdefault(message.conversation_ref, asyncio.Lock())
        async with lock:
            if self._controller is not None:
                outputs = await self._controller.handle(
                    message,
                    _LockedControllerActions(self),
                )
                if outputs is not None:
                    for output in outputs:
                        if output.conversation_ref != message.conversation_ref:
                            raise ValueError(
                                "Controller output belongs to a different Conversation"
                            )
                        await self._deliver_outbound(output)
                    return
            binding = await self._bindings.get(message.conversation_ref)
            application = self._bound_application(binding)
            if binding is None or binding.application_ref is None:
                application = self._single_application_or_none()
                if application is None:
                    await self._deliver_error(message, "No Agent application is selected.")
                    return
                selection = await self._execute_gateway_locked(
                    SelectApplication(
                        operation_id=_operation_id(message, "application.select"),
                        conversation_ref=message.conversation_ref,
                        actor=message.sender,
                        application_ref=application.summary.ref,
                        expected_revision=binding.revision if binding is not None else None,
                        created_at=message.created_at,
                    )
                )
                if not isinstance(selection, ConversationBound):
                    await self._deliver_operation_error(message, selection)
                    return
                binding = selection.binding
            if application is None:
                raise RuntimeError("bound Agent application is unavailable")
            if binding.thread_ref is None:
                if (
                    application.summary.capabilities.projects.mode is ProjectMode.MANAGED
                    and binding.project_ref is None
                ):
                    await self._deliver_error(message, "No project is selected.")
                    return
                result = await self.execute_application(
                    CreateThread(
                        operation_id=_operation_id(message, "thread.create"),
                        application_ref=application.summary.ref,
                        project_ref=binding.project_ref,
                        created_at=message.created_at,
                    )
                )
                if not isinstance(result, ThreadCreated):
                    await self._deliver_operation_error(message, result)
                    return
                bound = await self._execute_gateway_locked(
                    BindConversationToThread(
                        operation_id=_operation_id(
                            message,
                            "conversation.bind_thread",
                        ),
                        conversation_ref=message.conversation_ref,
                        actor=message.sender,
                        thread_ref=result.thread.ref,
                        expected_revision=binding.revision,
                        created_at=message.created_at,
                    )
                )
                if not isinstance(bound, ConversationBound):
                    await self._deliver_operation_error(message, bound)
                    return
                binding = bound.binding
            thread_ref = binding.thread_ref
            if thread_ref is None:
                raise RuntimeError("thread binding was not established")
            accepted = await application.send_input(
                thread_ref,
                AgentInput(
                    client_message_id=derive_client_message_id(
                        message.conversation_ref,
                        message.message_id,
                    ),
                    content=message.content,
                    sender=message.sender,
                    metadata={"channel_message_id": message.message_id},
                ),
            )
            task = asyncio.create_task(
                self._project_turn(
                    message=message,
                    application=application,
                    accepted=accepted,
                )
            )
            self._tasks.add(task)
            task.add_done_callback(self._finish_task)

    def _finish_task(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.exception(
                "Agent event projection failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _handle_operation(self, operation: GatewayOperation) -> None:
        result = await self.execute_gateway(operation)
        if isinstance(result, GatewayOperationFailed):
            logger.warning(
                "Gateway operation %s failed: %s",
                operation.operation_id,
                result.error.message,
            )

    async def _project_turn(
        self,
        *,
        message: InboundMessage,
        application: AgentApplicationAdapter,
        accepted: AcceptedTurn,
    ) -> None:
        async for event in application.subscribe_thread(accepted.thread_ref):
            if event.turn_id not in {None, accepted.turn_id}:
                continue
            if event.type is AgentEventType.MESSAGE_COMPLETED:
                agent_message = event.data.get("message")
                if isinstance(agent_message, AgentMessage):
                    await self._deliver_agent_message(message, agent_message)
            if event.type in {
                AgentEventType.TURN_COMPLETED,
                AgentEventType.TURN_FAILED,
                AgentEventType.TURN_INTERRUPTED,
            }:
                return

    async def _deliver_agent_message(
        self,
        inbound: InboundMessage,
        agent_message: AgentMessage,
    ) -> None:
        delivery_id = (
            f"imagent:delivery:{agent_message.thread_ref.native_thread_id}:"
            f"{agent_message.agent_item_id}"
        )
        await self._deliver_outbound(
            OutboundMessage(
                delivery_id=delivery_id,
                conversation_ref=inbound.conversation_ref,
                content=tuple(
                    TextContent(item.text, TextFormat.MARKDOWN)
                    if isinstance(item, TextContent)
                    else item
                    for item in agent_message.content
                ),
                created_at=agent_message.created_at,
                reply_to=inbound.message_id,
            )
        )

    async def _deliver_error(
        self,
        inbound: InboundMessage,
        text: str,
    ) -> None:
        await self._deliver_outbound(
            OutboundMessage(
                delivery_id=(
                    f"imagent:gateway:{inbound.conversation_ref.channel_instance_id}:"
                    f"{inbound.conversation_ref.native_conversation_id}:"
                    f"{inbound.message_id}:error"
                ),
                conversation_ref=inbound.conversation_ref,
                content=(TextContent(f"**Error:** {text}", TextFormat.MARKDOWN),),
                created_at=datetime.now(UTC),
                reply_to=inbound.message_id,
            )
        )

    async def _deliver_operation_error(
        self,
        inbound: InboundMessage,
        result: ApplicationOperationResult | GatewayOperationResult,
    ) -> None:
        error = (
            result.error
            if isinstance(result, (ApplicationOperationFailed, GatewayOperationFailed))
            else None
        )
        await self._deliver_error(
            inbound,
            error.message if error is not None else "Operation returned an incompatible result.",
        )

    async def _deliver_outbound(self, message: OutboundMessage) -> None:
        channel = self._channels[message.conversation_ref.channel_instance_id]
        scope = f"outbound:{message.conversation_ref.channel_instance_id}"
        if not await self._idempotency.claim(scope, message.delivery_id):
            return
        try:
            await channel.send(message)
        except BaseException:
            await self._idempotency.release(scope, message.delivery_id)
            raise
        await self._idempotency.complete(scope, message.delivery_id)

    def _bound_application(
        self,
        binding: ConversationBinding | None,
    ) -> AgentApplicationAdapter | None:
        if binding is None or binding.application_ref is None:
            return None
        try:
            return self._applications[binding.application_ref.application_instance_id]
        except KeyError as error:
            raise RuntimeError("bound Agent application is not registered") from error

    def _single_application_or_none(self) -> AgentApplicationAdapter | None:
        if len(self._applications) != 1:
            return None
        return next(iter(self._applications.values()))

    def _require_application(
        self,
        application_instance_id: str,
    ) -> AgentApplicationAdapter:
        try:
            return self._applications[application_instance_id]
        except KeyError as error:
            raise KeyError(
                f"Agent application is not registered: {application_instance_id}"
            ) from error


class _LockedControllerActions(ControllerActions):
    def __init__(self, gateway: ImAgentGateway) -> None:
        self._gateway = gateway

    async def execute_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        return await self._gateway.execute_application(operation)

    async def execute_gateway(
        self,
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        return await self._gateway._execute_gateway_locked(operation)

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None:
        return await self._gateway.get_binding(conversation_ref)


class _GatewayActionError(RuntimeError):
    def __init__(self, error: ContractError) -> None:
        super().__init__(error.message)
        self.error = error


def _contract_error(error: Exception) -> ContractError:
    if isinstance(error, BindingConflict):
        return operation_error(error, code=OperationErrorCode.CONFLICT)
    return operation_error(error)


def _operation_id(message: InboundMessage, operation_type: str) -> str:
    return f"imagent:operation:{message.message_id}:{operation_type}"

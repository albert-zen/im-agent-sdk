"""Private scoped-action composition for the optional inbound Controller."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime

from ..applications.contract import AgentApplicationAdapter, ApplicationSummary
from ..applications.operations import ApplicationOperation, ApplicationOperationResult
from ..applications.requests import RequestRef, RequestResponse
from ..interaction.messages import ConversationRef
from .actions import ConversationActions, _new_conversation_actions
from .effect_execution import GatewayEffectExecutor
from .persistence.effects import KnownNativeOutcome
from .persistence.state_contracts import ConversationBinding

ApplicationExecutor = Callable[[ApplicationOperation], Awaitable[ApplicationOperationResult]]
BindingReader = Callable[[ConversationRef], Awaitable[ConversationBinding | None]]
EffectFence = Callable[[], Awaitable[None]]


class _ScopedControllerActionRuntime:
    """Adapt exact Gateway capabilities to C without exposing them to consumers."""

    def __init__(
        self,
        *,
        gateway_id: str,
        applications: Mapping[str, AgentApplicationAdapter],
        execute_application: ApplicationExecutor,
        get_binding: BindingReader,
        effects: GatewayEffectExecutor,
    ) -> None:
        self._gateway_id = gateway_id
        self._applications = applications
        self._execute_application = execute_application
        self._get_binding = get_binding
        self._effects = effects

    def actions(
        self,
        conversation_ref: ConversationRef,
        *,
        actor: str,
        foreground_route: bool,
        inbound_message_id: str,
        inbound_created_at: datetime,
        enter_effect_fence: EffectFence,
    ) -> ConversationActions:
        return _new_conversation_actions(
            conversation_ref,
            actor=actor,
            gateway_id=self._gateway_id,
            runtime=self,
            effects=self._effects,
            foreground_route=foreground_route,
            inbound_message_id=inbound_message_id,
            inbound_created_at=inbound_created_at,
            enter_effect_fence=enter_effect_fence,
        )

    def list_applications(self) -> tuple[ApplicationSummary, ...]:
        return tuple(application.summary for application in self._applications.values())

    async def execute_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        return await self._execute_application(operation)

    async def reconcile_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult | None:
        del operation
        return None

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None:
        return await self._get_binding(conversation_ref)

    async def authorize_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> None:
        del conversation_ref, request_ref, response
        raise NotImplementedError("request-response action wiring belongs to DAG block G")

    async def invoke_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        operation_id: str,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> KnownNativeOutcome:
        del conversation_ref, operation_id, request_ref, response
        raise NotImplementedError("request-response action wiring belongs to DAG block G")

    async def reconcile_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        operation_id: str,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> KnownNativeOutcome | None:
        del conversation_ref, operation_id, request_ref, response
        raise NotImplementedError("request-response action wiring belongs to DAG block G")

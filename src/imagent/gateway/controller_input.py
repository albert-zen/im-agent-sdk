"""Private scoped-action composition for canonical Gateway consumers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime

from ..applications.contract import AgentApplicationAdapter, ApplicationRef, ApplicationSummary
from ..applications.operations import ApplicationOperation, ApplicationOperationResult
from ..applications.requests import RequestRef, RequestResponse
from ..interaction.messages import ConversationRef
from ..interaction.operations import OperationErrorCode
from .actions import (
    ApplicationActions,
    ConversationActions,
    _new_application_actions,
    _new_conversation_actions,
)
from .effect_execution import (
    GatewayEffectExecutor,
    NativeEffectInvoker,
    NativeEffectPreflight,
    NativeEffectReconciler,
    StoreEffectCommitFence,
    StoreEffectPreflight,
    WorkflowEffectCommitFence,
)
from .persistence.effects import (
    ActionError,
    ActionErrorCode,
    ActionOutcome,
    CreateBindingWorkflowRequest,
    KnownNativeOutcome,
    NativeMutationRequest,
    StoreMutationRequest,
)
from .persistence.state_contracts import ConversationBinding
from .projection.observation import (
    ProjectionRuntimeUnavailableError,
    ProjectionWorkerCapacityError,
)

ApplicationExecutor = Callable[[ApplicationOperation], Awaitable[ApplicationOperationResult]]
BindingReader = Callable[[ConversationRef], Awaitable[ConversationBinding | None]]
EffectFence = Callable[[], Awaitable[None]]
ProjectionRouteReconciler = Callable[[str | None, object | None], Awaitable[object | None]]
ProjectionRouteReconciliationValidator = Callable[[object], None]
ProjectionRouteBootstrapBegin = Callable[[str], Awaitable[object]]
ProjectionRouteBootstrapComplete = Callable[[object, bool | None], None]
ProjectionRouteBootstrapAbort = Callable[[object, bool], Awaitable[None]]
ProjectionRouteCommitFence = Callable[[object], AbstractAsyncContextManager[None]]


class _LifecycleBoundGatewayEffectExecutor:
    """Fence one D-owned executor without adding persistence or route authority."""

    def __init__(self, delegate: GatewayEffectExecutor) -> None:
        self._delegate = delegate
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    async def replay_store_mutation(
        self,
        request: StoreMutationRequest,
    ) -> ActionOutcome | None:
        self._require_active()
        return await self._delegate.replay_store_mutation(request)

    async def execute_store_mutation(
        self,
        request: StoreMutationRequest,
        *,
        preflight: StoreEffectPreflight | None = None,
        commit_fence: StoreEffectCommitFence | None = None,
    ) -> ActionOutcome:
        self._require_active()
        return await self._delegate.execute_store_mutation(
            request,
            preflight=preflight,
            commit_fence=commit_fence,
        )

    async def execute_native_mutation(
        self,
        request: NativeMutationRequest,
        *,
        invoke: NativeEffectInvoker,
        preflight: NativeEffectPreflight | None = None,
        reconcile: NativeEffectReconciler | None = None,
    ) -> ActionOutcome:
        self._require_active()
        return await self._delegate.execute_native_mutation(
            request,
            invoke=invoke,
            preflight=preflight,
            reconcile=reconcile,
        )

    async def execute_create_binding_workflow(
        self,
        request: CreateBindingWorkflowRequest,
        *,
        invoke: NativeEffectInvoker,
        preflight: NativeEffectPreflight | None = None,
        reconcile: NativeEffectReconciler | None = None,
        commit_fence: WorkflowEffectCommitFence | None = None,
    ) -> ActionOutcome:
        self._require_active()
        return await self._delegate.execute_create_binding_workflow(
            request,
            invoke=invoke,
            preflight=preflight,
            reconcile=reconcile,
            commit_fence=commit_fence,
        )

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("Gateway scoped action runtime is not active")


class _ScopedControllerActionRuntime:
    """Adapt exact Gateway capabilities to C without exposing private seams."""

    def __init__(
        self,
        *,
        gateway_id: str,
        applications: Mapping[str, AgentApplicationAdapter],
        execute_application: ApplicationExecutor,
        get_binding: BindingReader,
        effects: GatewayEffectExecutor,
        reconcile_projection_route: ProjectionRouteReconciler,
        validate_projection_route: ProjectionRouteReconciliationValidator,
        begin_projection_route: ProjectionRouteBootstrapBegin,
        complete_projection_route: ProjectionRouteBootstrapComplete,
        abort_projection_route: ProjectionRouteBootstrapAbort,
        fence_projection_route_commit: ProjectionRouteCommitFence,
    ) -> None:
        self._gateway_id = gateway_id
        self._applications = applications
        self._execute_application = execute_application
        self._get_binding = get_binding
        self._effects = _LifecycleBoundGatewayEffectExecutor(effects)
        self._active = True
        self._reconcile_projection_route = reconcile_projection_route
        self._validate_projection_route = validate_projection_route
        self._begin_projection_route = begin_projection_route
        self._complete_projection_route = complete_projection_route
        self._abort_projection_route = abort_projection_route
        self._fence_projection_route_commit = fence_projection_route_commit

    def actions(
        self,
        conversation_ref: ConversationRef,
        *,
        actor: str,
        foreground_route: bool,
        inbound_message_id: str | None = None,
        inbound_created_at: datetime | None = None,
        enter_effect_fence: EffectFence | None = None,
    ) -> ConversationActions:
        self._require_active()
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

    def application(
        self,
        application_ref: ApplicationRef,
        *,
        principal: str,
    ) -> ApplicationActions:
        self._require_active()
        return _new_application_actions(
            application_ref,
            principal=principal,
            gateway_id=self._gateway_id,
            runtime=self,
            effects=self._effects,
        )

    def deactivate(self) -> None:
        self._active = False
        self._effects.deactivate()

    def list_applications(self) -> tuple[ApplicationSummary, ...]:
        self._require_active()
        return tuple(application.summary for application in self._applications.values())

    async def execute_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        self._require_active()
        return await self._execute_application(operation)

    async def reconcile_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult | None:
        self._require_active()
        del operation
        return None

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None:
        self._require_active()
        return await self._get_binding(conversation_ref)

    async def reconcile_projection_route(
        self,
        route_id: str | None,
        action_lease: object | None,
    ) -> object | ActionError | None:
        try:
            return await self._reconcile_projection_route(route_id, action_lease)
        except ProjectionWorkerCapacityError:
            return ActionError(
                ActionErrorCode.CAPACITY_EXHAUSTED,
                OperationErrorCode.CAPACITY_EXHAUSTED,
            )
        except ProjectionRuntimeUnavailableError:
            return ActionError(ActionErrorCode.STALE_RUNTIME)
        except Exception:
            return ActionError(
                ActionErrorCode.NATIVE_REJECTED,
                OperationErrorCode.ADAPTER_FAILURE,
            )

    def validate_projection_route(self, receipt: object) -> ActionError | None:
        try:
            self._validate_projection_route(receipt)
        except ProjectionRuntimeUnavailableError:
            return ActionError(ActionErrorCode.STALE_RUNTIME)
        except Exception:
            return ActionError(
                ActionErrorCode.NATIVE_REJECTED,
                OperationErrorCode.ADAPTER_FAILURE,
            )
        return None

    async def begin_projection_route(self, route_id: str) -> object | ActionError:
        try:
            return await self._begin_projection_route(route_id)
        except ProjectionRuntimeUnavailableError:
            return ActionError(ActionErrorCode.STALE_RUNTIME)

    def complete_projection_route(
        self,
        lease: object,
        reconciled: bool | None,
    ) -> None:
        self._complete_projection_route(lease, reconciled)

    async def abort_projection_route(
        self,
        lease: object,
        route_absent: bool = False,
    ) -> None:
        await self._abort_projection_route(lease, route_absent)

    @asynccontextmanager
    async def fence_projection_route_commit(
        self,
        lease: object,
    ) -> AsyncIterator[ActionError | None]:
        try:
            async with self._fence_projection_route_commit(lease):
                yield None
        except ProjectionRuntimeUnavailableError:
            yield ActionError(ActionErrorCode.STALE_RUNTIME)

    async def authorize_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> None:
        self._require_active()
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
        self._require_active()
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
        self._require_active()
        del conversation_ref, operation_id, request_ref, response
        raise NotImplementedError("request-response action wiring belongs to DAG block G")

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("Gateway scoped action runtime is not active")

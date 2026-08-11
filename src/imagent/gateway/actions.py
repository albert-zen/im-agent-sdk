"""Conversation- and principal-scoped v1 consumer action surfaces."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol, TypeAlias, TypeVar
from uuid import uuid4

from ..applications.capabilities import SupportLevel, ThreadDeletionCapability
from ..applications.contract import (
    ApplicationRef,
    ApplicationSummary,
    Page,
    ProjectRef,
    ProjectSummary,
    ThreadHistory,
    ThreadRef,
    ThreadStatus,
    ThreadSummary,
    TurnCatchup,
    TurnRef,
    validate_application_summary,
    validate_project_ref,
    validate_thread_ref,
    validate_turn_ref,
)
from ..applications.operations import (
    ActivateNativeThread,
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    CreateProject,
    CreateThread,
    DeleteProject,
    DeleteThread,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetThreadStatus,
    GetTurnCatchup,
    InterruptTurn,
    ListProjects,
    ListThreads,
    NativeThreadActivated,
    ProjectCreated,
    ProjectDeleted,
    ProjectRead,
    ProjectsListed,
    ThreadCreated,
    ThreadDeleted,
    ThreadDeletionMode,
    ThreadHistoryRead,
    ThreadRead,
    ThreadsListed,
    ThreadStatusRead,
    TurnCatchupRead,
    TurnInterrupted,
    validate_application_operation,
    validate_application_operation_result,
)
from ..applications.requests import (
    ApprovalResponse,
    RequestRef,
    RequestResponse,
    UserInputResponse,
)
from ..interaction.media import AttachmentContent, AttachmentHandle, LocalPath, RemoteUrl
from ..interaction.messages import Content, ConversationRef, TextContent
from ..interaction.operations import (
    ContractViolation,
    OperationErrorCode,
    operation_error,
    require_identifier,
)
from .effect_execution import GatewayEffectExecutor, StoreEffectPreflight
from .outcomes import Failed, Outcome, OutcomeUnknown, Partial, Succeeded
from .persistence.effects import (
    ActionError,
    ActionErrorCode,
    ActionFingerprint,
    ActionIdentity,
    ActionOutcome,
    BindingClearScope,
    BindingTarget,
    CreateBindingWorkflowKind,
    CreateBindingWorkflowRequest,
    EffectCategory,
    EffectValue,
    FingerprintPayload,
    KnownNativeOutcome,
    NativeMutationRequest,
    RouteDeleteCondition,
    StableReference,
    StoreMutationPlan,
    StoreMutationRequest,
    derive_action_fingerprint,
)
from .persistence.state_contracts import (
    ConversationBinding,
    ThreadProjectionRoute,
    validate_binding,
)
from .projection.request_correlation import RespondToRequest
from .routing.bindings import (
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationApplication,
    ClearConversationProject,
    ClearConversationThread,
)
from .routing.operations import (
    _MAX_APPLICATION_LIST_ITEMS,
    ApplicationsListed,
    ListApplications,
    SelectApplication,
    validate_gateway_operation,
    validate_gateway_operation_result,
)
from .routing.projection_routes import (
    ClearThreadObservation,
    ObserveThread,
    derive_projection_route_id,
)

T = TypeVar("T")
ReadOutcome: TypeAlias = Succeeded[T] | Failed[ActionError]
EffectFence: TypeAlias = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ActionValue:
    """Minimal public value returned by durable mutations and workflows."""

    ref: ApplicationRef | ProjectRef | ThreadRef | TurnRef | RequestRef | None = None
    conversation_ref: ConversationRef | None = None
    binding_generation: int | None = None
    route_id: str | None = None


ActionResult: TypeAlias = Outcome[ActionValue, ActionError]


class _ActionRuntime(Protocol):
    def list_applications(self) -> tuple[ApplicationSummary, ...]: ...

    async def execute_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult: ...

    async def reconcile_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult | None: ...

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None: ...

    async def begin_projection_route(self, route_id: str) -> object | ActionError: ...

    def complete_projection_route(
        self,
        lease: object,
        reconciled: bool | None,
    ) -> None: ...

    async def reconcile_projection_route(
        self,
        route_id: str | None,
        action_lease: object | None,
    ) -> object | ActionError | None: ...

    def validate_projection_route(self, receipt: object) -> ActionError | None: ...

    async def authorize_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> None: ...

    async def invoke_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        operation_id: str,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> KnownNativeOutcome: ...

    async def reconcile_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        operation_id: str,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> KnownNativeOutcome | None: ...


class _CommandInvocationFacts(Protocol):
    @property
    def conversation_ref(self) -> ConversationRef: ...

    @property
    def message_id(self) -> str: ...

    @property
    def actor(self) -> str: ...

    @property
    def created_at(self) -> datetime: ...


@dataclass(slots=True)
class _InboundFence:
    message_id: str
    created_at: datetime
    enter: EffectFence
    entered: bool = False


@dataclass(frozen=True, slots=True)
class _ActionContext:
    gateway_id: str
    runtime: _ActionRuntime = field(repr=False)
    effects: GatewayEffectExecutor = field(repr=False)
    principal_id: str
    conversation_ref: ConversationRef | None = None
    foreground_route: bool = True
    inbound_fence: _InboundFence | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, init=False)
class ApplicationActions:
    """Native Application actions frozen to one Application and principal."""

    application_ref: ApplicationRef
    principal: str
    _context: _ActionContext = field(repr=False)

    def __new__(cls) -> ApplicationActions:
        raise TypeError("ApplicationActions is created by Gateway composition")

    @classmethod
    def _create(
        cls,
        application_ref: ApplicationRef,
        *,
        principal: str,
        context: _ActionContext,
    ) -> ApplicationActions:
        _validate_application_scope(application_ref, principal, context)
        value = object.__new__(cls)
        object.__setattr__(value, "application_ref", application_ref)
        object.__setattr__(value, "principal", principal)
        object.__setattr__(value, "_context", context)
        return value

    async def get_application(self) -> ReadOutcome[ApplicationSummary]:
        applications = _validated_application_discovery(self._context.runtime)
        summary = next(
            (item for item in applications if item.ref == self.application_ref),
            None,
        )
        if summary is None:
            return Failed(
                ActionError(ActionErrorCode.NATIVE_REJECTED, OperationErrorCode.NOT_FOUND)
            )
        return Succeeded(summary)

    async def list_projects(
        self,
        *,
        query: str | None = None,
        cursor: str | None = None,
    ) -> ReadOutcome[Page[ProjectSummary]]:
        result = await self._execute_read(
            ListProjects(
                operation_id=_read_operation_id("project.list"),
                application_ref=self.application_ref,
                query=query,
                cursor=cursor,
                created_at=_now(),
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            return Failed(_application_error(result))
        if not isinstance(result, ProjectsListed):
            raise RuntimeError("project.list returned an incompatible result")
        return Succeeded(result.projects)

    async def get_project(
        self,
        project_ref: ProjectRef,
    ) -> ReadOutcome[ProjectSummary]:
        self._require_project(project_ref)
        result = await self._execute_read(
            GetProject(
                operation_id=_read_operation_id("project.get"),
                application_ref=self.application_ref,
                project_ref=project_ref,
                created_at=_now(),
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            return Failed(_application_error(result))
        if not isinstance(result, ProjectRead):
            raise RuntimeError("project.get returned an incompatible result")
        return Succeeded(result.project)

    async def list_threads(
        self,
        project_ref: ProjectRef,
        *,
        query: str | None = None,
        cursor: str | None = None,
    ) -> ReadOutcome[Page[ThreadSummary]]:
        self._require_project(project_ref)
        result = await self._execute_read(
            ListThreads(
                operation_id=_read_operation_id("thread.list"),
                application_ref=self.application_ref,
                project_ref=project_ref,
                query=query,
                cursor=cursor,
                created_at=_now(),
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            return Failed(_application_error(result))
        if not isinstance(result, ThreadsListed):
            raise RuntimeError("thread.list returned an incompatible result")
        return Succeeded(result.threads)

    async def get_thread(self, thread_ref: ThreadRef) -> ReadOutcome[ThreadSummary]:
        self._require_thread(thread_ref)
        result = await self._execute_read(
            GetThread(
                operation_id=_read_operation_id("thread.get"),
                application_ref=self.application_ref,
                thread_ref=thread_ref,
                created_at=_now(),
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            return Failed(_application_error(result))
        if not isinstance(result, ThreadRead):
            raise RuntimeError("thread.get returned an incompatible result")
        return Succeeded(result.thread)

    async def get_thread_status(self, thread_ref: ThreadRef) -> ReadOutcome[ThreadStatus]:
        self._require_thread(thread_ref)
        result = await self._execute_read(
            GetThreadStatus(
                operation_id=_read_operation_id("thread.status"),
                application_ref=self.application_ref,
                thread_ref=thread_ref,
                created_at=_now(),
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            return Failed(_application_error(result))
        if not isinstance(result, ThreadStatusRead):
            raise RuntimeError("thread.status returned an incompatible result")
        return Succeeded(result.thread_status)

    async def read_history(
        self,
        thread_ref: ThreadRef,
        *,
        limit: int = 3,
        page: int = 1,
    ) -> ReadOutcome[ThreadHistory]:
        self._require_thread(thread_ref)
        result = await self._execute_read(
            GetThreadHistory(
                operation_id=_read_operation_id("thread.history"),
                application_ref=self.application_ref,
                thread_ref=thread_ref,
                limit=limit,
                page=page,
                created_at=_now(),
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            return Failed(_application_error(result))
        if not isinstance(result, ThreadHistoryRead):
            raise RuntimeError("thread.history returned an incompatible result")
        return Succeeded(result.history)

    async def read_turn_catchup(
        self,
        thread_ref: ThreadRef,
        *,
        limit: int = 5,
    ) -> ReadOutcome[TurnCatchup]:
        self._require_thread(thread_ref)
        result = await self._execute_read(
            GetTurnCatchup(
                operation_id=_read_operation_id("turn.catchup"),
                application_ref=self.application_ref,
                thread_ref=thread_ref,
                limit=limit,
                created_at=_now(),
            )
        )
        if isinstance(result, ApplicationOperationFailed):
            return Failed(_application_error(result))
        if not isinstance(result, TurnCatchupRead):
            raise RuntimeError("turn.catchup returned an incompatible result")
        return Succeeded(result.catchup)

    async def create_project(
        self,
        *,
        cwd: str,
        action_id: str,
        display_name: str | None = None,
    ) -> ActionResult:
        payload: FingerprintPayload = (("cwd", cwd), ("display_name", display_name))
        return await self._native(
            "project.create",
            action_id,
            payload,
            lambda phase_id: CreateProject(
                operation_id=phase_id,
                application_ref=self.application_ref,
                cwd=cwd,
                display_name=display_name,
                created_at=_now(),
            ),
            ProjectCreated,
        )

    async def delete_project(
        self,
        project_ref: ProjectRef,
        *,
        action_id: str,
    ) -> ActionResult:
        self._require_project(project_ref)
        return await self._native(
            "project.delete",
            action_id,
            _ref_payload(project_ref),
            lambda phase_id: DeleteProject(
                operation_id=phase_id,
                application_ref=self.application_ref,
                project_ref=project_ref,
                created_at=_now(),
            ),
            ProjectDeleted,
        )

    async def create_thread(
        self,
        project_ref: ProjectRef,
        *,
        action_id: str,
        title: str | None = None,
        initial_context: tuple[Content, ...] = (),
    ) -> ActionResult:
        self._require_project(project_ref)
        validate_application_operation(
            CreateThread(
                operation_id="imagent:validation",
                application_ref=self.application_ref,
                project_ref=project_ref,
                title=title,
                initial_context=initial_context,
                created_at=_now(),
            )
        )
        initial_context = _snapshot_content(initial_context)
        payload: FingerprintPayload = (
            *_ref_payload(project_ref),
            ("title", title),
            ("initial_context", _content_fingerprint(initial_context)),
        )
        return await self._native(
            "thread.create",
            action_id,
            payload,
            lambda phase_id: CreateThread(
                operation_id=phase_id,
                application_ref=self.application_ref,
                project_ref=project_ref,
                title=title,
                initial_context=initial_context,
                created_at=_now(),
            ),
            ThreadCreated,
        )

    async def activate_native_thread(
        self,
        thread_ref: ThreadRef,
        *,
        action_id: str,
    ) -> ActionResult:
        self._require_thread(thread_ref)
        return await self._native(
            "thread.activate_native",
            action_id,
            _ref_payload(thread_ref),
            lambda phase_id: ActivateNativeThread(
                operation_id=phase_id,
                application_ref=self.application_ref,
                thread_ref=thread_ref,
                created_at=_now(),
            ),
            NativeThreadActivated,
        )

    async def delete_thread(
        self,
        thread_ref: ThreadRef,
        *,
        mode: ThreadDeletionMode,
        action_id: str,
    ) -> ActionResult:
        self._require_thread(thread_ref)
        payload: FingerprintPayload = (*_ref_payload(thread_ref), ("mode", mode.value))
        return await self._native(
            "thread.delete",
            action_id,
            payload,
            lambda phase_id: DeleteThread(
                operation_id=phase_id,
                application_ref=self.application_ref,
                thread_ref=thread_ref,
                mode=mode,
                created_at=_now(),
            ),
            ThreadDeleted,
        )

    async def interrupt_turn(
        self,
        thread_ref: ThreadRef,
        *,
        action_id: str,
        turn_ref: TurnRef | None = None,
    ) -> ActionResult:
        self._require_thread(thread_ref)
        if turn_ref is not None:
            validate_turn_ref(turn_ref)
            if turn_ref.thread_ref != thread_ref:
                raise ContractViolation("Turn belongs to another Thread")
        payload: FingerprintPayload = (
            *_ref_payload(thread_ref),
            ("turn_id", turn_ref.turn_id if turn_ref is not None else None),
        )
        return await self._native(
            "turn.interrupt",
            action_id,
            payload,
            lambda phase_id: InterruptTurn(
                operation_id=phase_id,
                application_ref=self.application_ref,
                thread_ref=thread_ref,
                turn_ref=turn_ref,
                created_at=_now(),
            ),
            TurnInterrupted,
        )

    async def _execute_read(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        validate_application_operation(operation)
        result = await self._context.runtime.execute_application(operation)
        validate_application_operation_result(operation, result)
        return result

    async def _native(
        self,
        action_kind: str,
        action_id: str,
        payload: FingerprintPayload,
        operation: Callable[[str], ApplicationOperation],
        success_type: type[object],
    ) -> ActionResult:
        validation_operation = operation("imagent:validation")
        validate_application_operation(validation_operation)
        semantic_payload = payload
        if all(name != "application_id" for name, _ in semantic_payload):
            semantic_payload = (
                ("application_id", self.application_ref.application_instance_id),
                *semantic_payload,
            )
        fingerprint = _fingerprint(
            self._context,
            action_kind,
            action_id,
            semantic_payload,
        )
        request = NativeMutationRequest(fingerprint=fingerprint)

        async def preflight() -> ActionError | None:
            return await _preflight_application_mutation(
                self._context.runtime,
                validation_operation,
                fingerprint.phase_id("preflight"),
            )

        async def invoke(phase_id: str) -> KnownNativeOutcome:
            native_operation = operation(phase_id)
            result = await self._context.runtime.execute_application(native_operation)
            validate_application_operation_result(native_operation, result)
            return _known_native_result(
                result,
                success_type,
            )

        async def reconcile(phase_id: str) -> KnownNativeOutcome | None:
            native_operation = operation(phase_id)
            result = await self._context.runtime.reconcile_application(native_operation)
            if result is None:
                return None
            validate_application_operation_result(native_operation, result)
            return _known_native_result(result, success_type)

        outcome = await self._context.effects.execute_native_mutation(
            request,
            invoke=invoke,
            preflight=preflight,
            reconcile=reconcile,
        )
        return _public_outcome(outcome)

    def _require_project(self, project_ref: ProjectRef) -> None:
        validate_project_ref(project_ref)
        if project_ref.application_instance_id != self.application_ref.application_instance_id:
            raise ContractViolation("Project belongs to another ApplicationActions scope")

    def _require_thread(self, thread_ref: ThreadRef) -> None:
        validate_thread_ref(thread_ref)
        self._require_project(thread_ref.project_ref)


@dataclass(frozen=True, slots=True, init=False)
class ConversationActions:
    """All product actions frozen to one authenticated Conversation and actor."""

    conversation_ref: ConversationRef
    actor: str
    _context: _ActionContext = field(repr=False)

    def __new__(cls) -> ConversationActions:
        raise TypeError("ConversationActions is created by Gateway composition")

    @classmethod
    def _create(
        cls,
        conversation_ref: ConversationRef,
        *,
        actor: str,
        context: _ActionContext,
    ) -> ConversationActions:
        _validate_conversation_scope(conversation_ref, actor, context)
        value = object.__new__(cls)
        object.__setattr__(value, "conversation_ref", conversation_ref)
        object.__setattr__(value, "actor", actor)
        object.__setattr__(value, "_context", context)
        return value

    async def list_applications(self) -> ReadOutcome[tuple[ApplicationSummary, ...]]:
        operation = ListApplications(
            operation_id=_read_operation_id("application.list"),
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        applications = _validated_application_discovery(self._context.runtime)
        result = ApplicationsListed(
            operation_id=operation.operation_id,
            completed_at=_now(),
            applications=applications,
        )
        validate_gateway_operation_result(operation, result)
        return Succeeded(applications)

    async def get_application(
        self,
        application_ref: ApplicationRef,
    ) -> ReadOutcome[ApplicationSummary]:
        return await self._application(application_ref).get_application()

    async def get_binding(self) -> ConversationBinding | None:
        binding = await self._context.runtime.get_binding(self.conversation_ref)
        if binding is None:
            return None
        validate_binding(binding)
        if binding.conversation_ref != self.conversation_ref:
            raise ContractViolation("binding belongs to another Conversation")
        return binding

    async def list_projects(
        self,
        application_ref: ApplicationRef,
        *,
        query: str | None = None,
        cursor: str | None = None,
    ) -> ReadOutcome[Page[ProjectSummary]]:
        return await self._application(application_ref).list_projects(query=query, cursor=cursor)

    async def get_project(self, project_ref: ProjectRef) -> ReadOutcome[ProjectSummary]:
        return await self._application(_application_ref(project_ref)).get_project(project_ref)

    async def list_threads(
        self,
        project_ref: ProjectRef,
        *,
        query: str | None = None,
        cursor: str | None = None,
    ) -> ReadOutcome[Page[ThreadSummary]]:
        return await self._application(_application_ref(project_ref)).list_threads(
            project_ref,
            query=query,
            cursor=cursor,
        )

    async def get_thread(self, thread_ref: ThreadRef) -> ReadOutcome[ThreadSummary]:
        return await self._application(_application_ref(thread_ref)).get_thread(thread_ref)

    async def get_thread_status(self, thread_ref: ThreadRef) -> ReadOutcome[ThreadStatus]:
        return await self._application(_application_ref(thread_ref)).get_thread_status(thread_ref)

    async def read_history(
        self,
        thread_ref: ThreadRef,
        *,
        limit: int = 3,
        page: int = 1,
    ) -> ReadOutcome[ThreadHistory]:
        return await self._application(_application_ref(thread_ref)).read_history(
            thread_ref,
            limit=limit,
            page=page,
        )

    async def read_turn_catchup(
        self,
        thread_ref: ThreadRef,
        *,
        limit: int = 5,
    ) -> ReadOutcome[TurnCatchup]:
        return await self._application(_application_ref(thread_ref)).read_turn_catchup(
            thread_ref,
            limit=limit,
        )

    async def create_project(
        self,
        application_ref: ApplicationRef,
        *,
        cwd: str,
        action_id: str,
        display_name: str | None = None,
    ) -> ActionResult:
        return await self._application(application_ref).create_project(
            cwd=cwd,
            action_id=action_id,
            display_name=display_name,
        )

    async def delete_project(
        self,
        project_ref: ProjectRef,
        *,
        action_id: str,
    ) -> ActionResult:
        return await self._application(_application_ref(project_ref)).delete_project(
            project_ref,
            action_id=action_id,
        )

    async def create_thread(
        self,
        project_ref: ProjectRef,
        *,
        action_id: str,
        title: str | None = None,
        initial_context: tuple[Content, ...] = (),
    ) -> ActionResult:
        return await self._application(_application_ref(project_ref)).create_thread(
            project_ref,
            action_id=action_id,
            title=title,
            initial_context=initial_context,
        )

    async def activate_native_thread(
        self,
        thread_ref: ThreadRef,
        *,
        action_id: str,
    ) -> ActionResult:
        return await self._application(_application_ref(thread_ref)).activate_native_thread(
            thread_ref,
            action_id=action_id,
        )

    async def delete_thread(
        self,
        thread_ref: ThreadRef,
        *,
        mode: ThreadDeletionMode,
        action_id: str,
    ) -> ActionResult:
        return await self._application(_application_ref(thread_ref)).delete_thread(
            thread_ref,
            mode=mode,
            action_id=action_id,
        )

    async def interrupt_turn(
        self,
        thread_ref: ThreadRef,
        *,
        action_id: str,
        turn_ref: TurnRef | None = None,
    ) -> ActionResult:
        return await self._application(_application_ref(thread_ref)).interrupt_turn(
            thread_ref,
            action_id=action_id,
            turn_ref=turn_ref,
        )

    async def select_application(
        self,
        application_ref: ApplicationRef,
        *,
        action_id: str,
        expected_generation: int | None = None,
    ) -> ActionResult:
        operation = SelectApplication(
            operation_id=action_id,
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            application_ref=application_ref,
            expected_generation=expected_generation,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        target = BindingTarget(
            operation.conversation_ref,
            application_ref=operation.application_ref,
        )
        return await self._store_binding(
            operation.type.value,
            operation.operation_id,
            target,
            (("application_id", operation.application_ref.application_instance_id),),
            operation.expected_generation,
            preflight_resource=operation.application_ref,
        )

    async def select_project(
        self,
        project_ref: ProjectRef,
        *,
        action_id: str,
        expected_generation: int | None = None,
    ) -> ActionResult:
        operation = BindConversationToProject(
            operation_id=action_id,
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            project_ref=project_ref,
            expected_generation=expected_generation,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        target = BindingTarget(
            operation.conversation_ref,
            application_ref=_application_ref(operation.project_ref),
            project_ref=operation.project_ref,
        )
        return await self._store_binding(
            operation.type.value,
            operation.operation_id,
            target,
            _ref_payload(operation.project_ref),
            operation.expected_generation,
            preflight_resource=operation.project_ref,
        )

    async def bind_thread(
        self,
        thread_ref: ThreadRef,
        *,
        action_id: str,
        expected_generation: int | None = None,
    ) -> ActionResult:
        operation = BindConversationToThread(
            operation_id=action_id,
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            thread_ref=thread_ref,
            expected_generation=expected_generation,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        target = BindingTarget(
            operation.conversation_ref,
            application_ref=_application_ref(operation.thread_ref),
            project_ref=operation.thread_ref.project_ref,
            thread_ref=operation.thread_ref,
        )
        route = None
        if self._context.foreground_route:
            route = ThreadProjectionRoute(
                route_id=derive_projection_route_id(
                    operation.thread_ref,
                    operation.conversation_ref,
                ),
                thread_ref=operation.thread_ref,
                conversation_ref=operation.conversation_ref,
            )
        return await self._store_binding(
            operation.type.value,
            operation.operation_id,
            target,
            (
                *_ref_payload(operation.thread_ref),
                ("foreground_route", self._context.foreground_route),
            ),
            operation.expected_generation,
            route_upsert=route,
            preflight_resource=operation.thread_ref,
            require_streaming=self._context.foreground_route,
        )

    async def clear_thread(
        self,
        *,
        action_id: str,
        expected_generation: int | None = None,
    ) -> ActionResult:
        operation = ClearConversationThread(
            operation_id=action_id,
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            expected_generation=expected_generation,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        return await self._store_binding(
            operation.type.value,
            operation.operation_id,
            None,
            (),
            operation.expected_generation,
            binding_clear=BindingClearScope.THREAD,
        )

    async def clear_project(
        self,
        *,
        action_id: str,
        expected_generation: int | None = None,
    ) -> ActionResult:
        operation = ClearConversationProject(
            operation_id=action_id,
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            expected_generation=expected_generation,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        return await self._store_binding(
            operation.type.value,
            operation.operation_id,
            None,
            (),
            operation.expected_generation,
            binding_clear=BindingClearScope.PROJECT,
        )

    async def clear_application(
        self,
        *,
        action_id: str,
        expected_generation: int | None = None,
    ) -> ActionResult:
        operation = ClearConversationApplication(
            operation_id=action_id,
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            expected_generation=expected_generation,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        return await self._store_binding(
            operation.type.value,
            operation.operation_id,
            None,
            (),
            operation.expected_generation,
            binding_clear=BindingClearScope.APPLICATION,
        )

    async def observe_thread(
        self,
        thread_ref: ThreadRef,
        *,
        action_id: str,
        reply_to_message_id: str | None = None,
    ) -> ActionResult:
        operation = ObserveThread(
            operation_id=action_id,
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            thread_ref=thread_ref,
            reply_to_message_id=reply_to_message_id,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        route = ThreadProjectionRoute(
            route_id=derive_projection_route_id(
                operation.thread_ref,
                operation.conversation_ref,
            ),
            thread_ref=operation.thread_ref,
            conversation_ref=operation.conversation_ref,
            reply_to_message_id=operation.reply_to_message_id,
        )
        payload: FingerprintPayload = (
            *_ref_payload(operation.thread_ref),
            ("reply_to_message_id", operation.reply_to_message_id),
        )
        request = StoreMutationRequest(
            fingerprint=_fingerprint(
                self._context,
                operation.type.value,
                operation.operation_id,
                payload,
            ),
            plan=StoreMutationPlan(
                conversation_ref=self.conversation_ref,
                route_upsert=route,
            ),
        )

        async def preflight() -> ActionError | None:
            return await _preflight_application_resource(
                self._context.runtime,
                operation.thread_ref,
                _read_operation_id("preflight.thread.get"),
                require_streaming=True,
            )

        return _public_outcome(
            await self._execute_store_mutation(
                request,
                preflight=preflight,
                bootstrap_route_id=route.route_id,
            )
        )

    async def clear_observation(
        self,
        thread_ref: ThreadRef,
        *,
        action_id: str,
    ) -> ActionResult:
        operation = ClearThreadObservation(
            operation_id=action_id,
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            thread_ref=thread_ref,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        route_id = derive_projection_route_id(
            operation.thread_ref,
            operation.conversation_ref,
        )
        request = StoreMutationRequest(
            fingerprint=_fingerprint(
                self._context,
                operation.type.value,
                operation.operation_id,
                (
                    *_ref_payload(operation.thread_ref),
                    ("foreground_route", self._context.foreground_route),
                ),
            ),
            plan=StoreMutationPlan(
                conversation_ref=self.conversation_ref,
                route_delete_id=route_id,
                route_delete_condition=(
                    RouteDeleteCondition.UNLESS_BOUND_TO_ROUTE_THREAD
                    if self._context.foreground_route
                    else None
                ),
            ),
        )
        return _public_outcome(await self._execute_store_mutation(request))

    async def respond_request(
        self,
        request_ref: RequestRef,
        response: RequestResponse,
        *,
        action_id: str,
    ) -> ActionResult:
        validate_gateway_operation(
            RespondToRequest(
                operation_id=action_id,
                conversation_ref=self.conversation_ref,
                actor=self.actor,
                request_ref=request_ref,
                response=response,
                created_at=_now(),
            )
        )
        response = _snapshot_request_response(response)
        operation = RespondToRequest(
            operation_id=action_id,
            conversation_ref=self.conversation_ref,
            actor=self.actor,
            request_ref=request_ref,
            response=response,
            created_at=_now(),
        )
        validate_gateway_operation(operation)
        payload = _response_payload(operation.request_ref, operation.response)
        request = NativeMutationRequest(
            fingerprint=_fingerprint(
                self._context,
                operation.type.value,
                operation.operation_id,
                payload,
            ),
            category=EffectCategory.REQUEST_RESPONSE,
        )

        async def preflight() -> ActionError | None:
            application_error = _preflight_request_application(
                self._context.runtime,
                operation.request_ref,
            )
            if application_error is not None:
                return application_error
            try:
                await self._context.runtime.authorize_request_response(
                    operation.conversation_ref,
                    request_ref=operation.request_ref,
                    response=operation.response,
                )
            except Exception as error:
                return _exception_action_error(error)
            return None

        async def invoke(phase_id: str) -> KnownNativeOutcome:
            return _validated_request_outcome(
                await self._context.runtime.invoke_request_response(
                    operation.conversation_ref,
                    operation_id=phase_id,
                    request_ref=operation.request_ref,
                    response=operation.response,
                ),
                operation.request_ref,
            )

        async def reconcile(phase_id: str) -> KnownNativeOutcome | None:
            result = await self._context.runtime.reconcile_request_response(
                operation.conversation_ref,
                operation_id=phase_id,
                request_ref=operation.request_ref,
                response=operation.response,
            )
            return (
                None
                if result is None
                else _validated_request_outcome(result, operation.request_ref)
            )

        outcome = await self._context.effects.execute_native_mutation(
            request,
            invoke=invoke,
            preflight=preflight,
            reconcile=reconcile,
        )
        return _public_outcome(outcome)

    async def create_and_select_project(
        self,
        application_ref: ApplicationRef,
        *,
        cwd: str,
        action_id: str,
        display_name: str | None = None,
    ) -> ActionResult:
        require_identifier(application_ref.application_instance_id, "application_instance_id")

        def operation(phase_id: str) -> CreateProject:
            return CreateProject(
                operation_id=phase_id,
                application_ref=application_ref,
                cwd=cwd,
                display_name=display_name,
                created_at=_now(),
            )

        validate_application_operation(operation("imagent:validation"))
        payload: FingerprintPayload = (
            ("application_id", application_ref.application_instance_id),
            ("cwd", cwd),
            ("display_name", display_name),
            ("foreground_route", self._context.foreground_route),
        )
        request = CreateBindingWorkflowRequest(
            fingerprint=_fingerprint(
                self._context,
                "create_and_select_project",
                action_id,
                payload,
            ),
            conversation_ref=self.conversation_ref,
            kind=CreateBindingWorkflowKind.CREATE_AND_SELECT_PROJECT,
            application_ref=application_ref,
            foreground_route=self._context.foreground_route,
        )

        return await self._workflow(request, operation, ProjectCreated)

    async def create_and_bind_thread(
        self,
        project_ref: ProjectRef,
        *,
        action_id: str,
        title: str | None = None,
        initial_context: tuple[Content, ...] = (),
    ) -> ActionResult:
        validate_project_ref(project_ref)
        validate_application_operation(
            CreateThread(
                operation_id="imagent:validation",
                application_ref=_application_ref(project_ref),
                project_ref=project_ref,
                title=title,
                initial_context=initial_context,
                created_at=_now(),
            )
        )
        initial_context = _snapshot_content(initial_context)
        payload: FingerprintPayload = (
            *_ref_payload(project_ref),
            ("title", title),
            ("initial_context", _content_fingerprint(initial_context)),
            ("foreground_route", self._context.foreground_route),
        )
        request = CreateBindingWorkflowRequest(
            fingerprint=_fingerprint(
                self._context,
                "create_and_bind_thread",
                action_id,
                payload,
            ),
            conversation_ref=self.conversation_ref,
            kind=CreateBindingWorkflowKind.CREATE_AND_BIND_THREAD,
            application_ref=_application_ref(project_ref),
            project_ref=project_ref,
            foreground_route=self._context.foreground_route,
        )

        def operation(phase_id: str) -> CreateThread:
            return CreateThread(
                operation_id=phase_id,
                application_ref=_application_ref(project_ref),
                project_ref=project_ref,
                title=title,
                initial_context=initial_context,
                created_at=_now(),
            )

        return await self._workflow(request, operation, ThreadCreated)

    async def _store_binding(
        self,
        action_kind: str,
        action_id: str,
        target: BindingTarget | None,
        payload: FingerprintPayload,
        expected_generation: int | None,
        *,
        binding_clear: BindingClearScope | None = None,
        route_upsert: ThreadProjectionRoute | None = None,
        preflight_resource: ApplicationRef | ProjectRef | ThreadRef | None = None,
        require_streaming: bool = False,
    ) -> ActionResult:
        if expected_generation is not None:
            if not isinstance(expected_generation, int) or isinstance(expected_generation, bool):
                raise ContractViolation("expected_generation must be a non-negative integer")
            if expected_generation < 0:
                raise ContractViolation("expected_generation cannot be negative")
        semantic_payload: FingerprintPayload = (
            *payload,
            ("expected_generation", expected_generation),
        )
        request = StoreMutationRequest(
            fingerprint=_fingerprint(
                self._context,
                action_kind,
                action_id,
                semantic_payload,
            ),
            plan=StoreMutationPlan(
                conversation_ref=self.conversation_ref,
                binding_target=target,
                binding_clear=binding_clear,
                route_upsert=route_upsert,
                expected_generation=expected_generation,
            ),
        )

        async def preflight() -> ActionError | None:
            if preflight_resource is None:
                return None
            return await _preflight_application_resource(
                self._context.runtime,
                preflight_resource,
                _read_operation_id("preflight.resource.get"),
                require_streaming=require_streaming,
            )

        return _public_outcome(
            await self._execute_store_mutation(
                request,
                preflight=preflight if preflight_resource is not None else None,
                bootstrap_route_id=(route_upsert.route_id if route_upsert is not None else None),
            )
        )

    async def _workflow(
        self,
        request: CreateBindingWorkflowRequest,
        operation: Callable[[str], ApplicationOperation],
        success_type: type[object],
    ) -> ActionResult:
        validation_operation = operation("imagent:validation")
        validate_application_operation(validation_operation)

        async def preflight() -> ActionError | None:
            return await _preflight_application_mutation(
                self._context.runtime,
                validation_operation,
                request.fingerprint.phase_id("preflight"),
                require_streaming=(
                    request.kind is CreateBindingWorkflowKind.CREATE_AND_BIND_THREAD
                    and request.foreground_route
                ),
            )

        async def invoke(phase_id: str) -> KnownNativeOutcome:
            native_operation = operation(phase_id)
            result = await self._context.runtime.execute_application(native_operation)
            validate_application_operation_result(native_operation, result)
            return _known_native_result(
                result,
                success_type,
            )

        async def reconcile(phase_id: str) -> KnownNativeOutcome | None:
            native_operation = operation(phase_id)
            result = await self._context.runtime.reconcile_application(native_operation)
            if result is None:
                return None
            validate_application_operation_result(native_operation, result)
            return _known_native_result(result, success_type)

        outcome = await self._reconcile_projection_route(
            await self._context.effects.execute_create_binding_workflow(
                request,
                invoke=invoke,
                preflight=preflight,
                reconcile=reconcile,
            )
        )
        return _public_outcome(outcome)

    async def _reconcile_projection_route(
        self,
        outcome: ActionOutcome,
        action_lease: object | None = None,
    ) -> ActionOutcome:
        if not isinstance(outcome, Succeeded):
            return outcome
        task = asyncio.create_task(
            self._context.runtime.reconcile_projection_route(
                outcome.value.route_id,
                action_lease,
            )
        )
        try:
            reconciliation = await asyncio.shield(task)
        except asyncio.CancelledError:
            # The route mutation is already terminal.  Join the process-local
            # convergence attempt so a single caller cancellation cannot leave
            # a consumed command durably routed but inactive.
            reconciliation = await task
        if isinstance(reconciliation, ActionError):
            return Partial(outcome.value, reconciliation)
        if reconciliation is not None:
            error = self._context.runtime.validate_projection_route(reconciliation)
            if error is not None:
                return Partial(outcome.value, error)
        return outcome

    async def _execute_store_mutation(
        self,
        request: StoreMutationRequest,
        *,
        preflight: StoreEffectPreflight | None = None,
        bootstrap_route_id: str | None = None,
    ) -> ActionOutcome:
        if bootstrap_route_id is not None:
            replay = await self._context.effects.replay_store_mutation(request)
            if replay is not None:
                return await self._reconcile_projection_route(replay)
        bootstrap_lease_or_error = (
            await self._context.runtime.begin_projection_route(bootstrap_route_id)
            if bootstrap_route_id is not None
            else None
        )
        if isinstance(bootstrap_lease_or_error, ActionError):
            replay = await self._context.effects.replay_store_mutation(request)
            if replay is not None:
                return await self._reconcile_projection_route(replay)
            return Failed(bootstrap_lease_or_error)
        bootstrap_lease = bootstrap_lease_or_error
        reconciled: bool | None = None
        try:
            durable_outcome = await self._context.effects.execute_store_mutation(
                request,
                preflight=preflight,
            )
            if not isinstance(durable_outcome, Succeeded):
                return durable_outcome
            reconciled = False
            outcome = await self._reconcile_projection_route(
                durable_outcome,
                bootstrap_lease,
            )
            reconciled = isinstance(outcome, Succeeded)
            return outcome
        finally:
            if bootstrap_lease is not None:
                self._context.runtime.complete_projection_route(
                    bootstrap_lease,
                    reconciled,
                )

    async def _enter_effectful_command(self, invocation: _CommandInvocationFacts) -> None:
        if invocation.conversation_ref != self.conversation_ref or invocation.actor != self.actor:
            raise ValueError("command invocation is outside this ConversationActions scope")
        fence = self._context.inbound_fence
        if fence is None:
            return
        if fence.entered:
            raise RuntimeError("effectful command fence may be entered only once")
        if invocation.message_id != fence.message_id or invocation.created_at != fence.created_at:
            raise ValueError("command invocation does not match the owned inbound message")
        fence.entered = True
        await fence.enter()

    def _application(self, application_ref: ApplicationRef) -> ApplicationActions:
        return ApplicationActions._create(
            application_ref,
            principal=self.actor,
            context=self._context,
        )


def _new_application_actions(
    application_ref: ApplicationRef,
    *,
    principal: str,
    gateway_id: str,
    runtime: _ActionRuntime,
    effects: GatewayEffectExecutor,
) -> ApplicationActions:
    context = _ActionContext(
        gateway_id=gateway_id,
        runtime=runtime,
        effects=effects,
        principal_id=principal,
    )
    return ApplicationActions._create(application_ref, principal=principal, context=context)


def _new_conversation_actions(
    conversation_ref: ConversationRef,
    *,
    actor: str,
    gateway_id: str,
    runtime: _ActionRuntime,
    effects: GatewayEffectExecutor,
    foreground_route: bool,
    inbound_message_id: str | None = None,
    inbound_created_at: datetime | None = None,
    enter_effect_fence: EffectFence | None = None,
) -> ConversationActions:
    inbound_values = (inbound_message_id, inbound_created_at, enter_effect_fence)
    if any(value is None for value in inbound_values) and any(
        value is not None for value in inbound_values
    ):
        raise ValueError("inbound action fencing requires all owned message facts")
    fence = None
    if (
        inbound_message_id is not None
        and inbound_created_at is not None
        and enter_effect_fence is not None
    ):
        fence = _InboundFence(inbound_message_id, inbound_created_at, enter_effect_fence)
    context = _ActionContext(
        gateway_id=gateway_id,
        runtime=runtime,
        effects=effects,
        principal_id=actor,
        conversation_ref=conversation_ref,
        foreground_route=foreground_route,
        inbound_fence=fence,
    )
    return ConversationActions._create(conversation_ref, actor=actor, context=context)


def _validate_application_scope(
    application_ref: ApplicationRef,
    principal: str,
    context: _ActionContext,
) -> None:
    require_identifier(application_ref.application_instance_id, "application_instance_id")
    require_identifier(principal, "principal")
    if principal != context.principal_id:
        raise ValueError("ApplicationActions principal does not match its execution scope")


def _validate_conversation_scope(
    conversation_ref: ConversationRef,
    actor: str,
    context: _ActionContext,
) -> None:
    require_identifier(conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(conversation_ref.native_conversation_id, "native_conversation_id")
    require_identifier(actor, "actor")
    if context.conversation_ref != conversation_ref or context.principal_id != actor:
        raise ValueError("ConversationActions facts do not match its execution scope")


def _fingerprint(
    context: _ActionContext,
    action_kind: str,
    action_id: str,
    payload: FingerprintPayload,
) -> ActionFingerprint:
    return derive_action_fingerprint(
        ActionIdentity(
            gateway_id=context.gateway_id,
            principal_id=context.principal_id,
            action_kind=action_kind,
            action_id=action_id,
            conversation_ref=context.conversation_ref,
        ),
        payload,
    )


def _known_native_result(
    result: ApplicationOperationResult,
    success_type: type[object],
) -> KnownNativeOutcome:
    if isinstance(result, ApplicationOperationFailed):
        return Failed(_application_error(result))
    if not isinstance(result, success_type):
        raise RuntimeError("native Application mutation returned an incompatible result")
    return Succeeded(EffectValue(reference=_stable_reference_from_result(result)))


def _validated_request_outcome(
    outcome: KnownNativeOutcome,
    request_ref: RequestRef,
) -> KnownNativeOutcome:
    if isinstance(outcome, Failed):
        return outcome
    if not isinstance(outcome, Succeeded):
        raise RuntimeError("request response returned an open native outcome")
    value = outcome.value
    if (
        value.reference is None
        or value.reference.to_value() != request_ref
        or value.conversation_ref is not None
        or value.binding_generation is not None
        or value.route_id is not None
    ):
        raise RuntimeError("request response returned an incompatible result")
    return outcome


def _stable_reference_from_result(result: object) -> StableReference:
    if isinstance(result, ProjectCreated):
        value = result.project.ref
    elif isinstance(result, ProjectDeleted):
        value = result.project_ref
    elif isinstance(result, ThreadCreated):
        value = result.thread.ref
    elif isinstance(result, (NativeThreadActivated, ThreadDeleted)):
        value = result.thread_ref
    elif isinstance(result, TurnInterrupted):
        value = result.turn_ref or result.thread_ref
    else:
        raise RuntimeError("native result has no stable public reference")
    return StableReference.from_value(value)


def _application_error(result: ApplicationOperationFailed) -> ActionError:
    try:
        operation_error_code = OperationErrorCode(result.error.code)
    except ValueError:
        operation_error_code = OperationErrorCode.ADAPTER_FAILURE
    return _action_error(operation_error_code)


def _exception_action_error(error: Exception) -> ActionError:
    projected = operation_error(error)
    try:
        operation_error_code = OperationErrorCode(projected.code)
    except ValueError:
        operation_error_code = OperationErrorCode.ADAPTER_FAILURE
    return _action_error(operation_error_code)


def _action_error(operation_error_code: OperationErrorCode) -> ActionError:
    code = {
        OperationErrorCode.UNSUPPORTED: ActionErrorCode.UNSUPPORTED,
        OperationErrorCode.CONFLICT: ActionErrorCode.CONFLICT,
        OperationErrorCode.CAPACITY_EXHAUSTED: ActionErrorCode.CAPACITY_EXHAUSTED,
    }.get(operation_error_code, ActionErrorCode.NATIVE_REJECTED)
    return ActionError(code, operation_error_code)


def _public_outcome(outcome: ActionOutcome) -> ActionResult:
    if isinstance(outcome, Succeeded):
        return Succeeded(_public_value(outcome.value))
    if isinstance(outcome, Failed):
        return outcome
    if isinstance(outcome, Partial):
        return Partial(_public_value(outcome.value), outcome.error)
    if isinstance(outcome, OutcomeUnknown):
        return outcome
    raise RuntimeError("effect executor returned an open outcome variant")


def _public_value(value: EffectValue) -> ActionValue:
    return ActionValue(
        ref=value.reference.to_value() if value.reference is not None else None,
        conversation_ref=value.conversation_ref,
        binding_generation=value.binding_generation,
        route_id=value.route_id,
    )


def _application_ref(value: ProjectRef | ThreadRef) -> ApplicationRef:
    project_ref = value.project_ref if isinstance(value, ThreadRef) else value
    return ApplicationRef(project_ref.application_instance_id)


def _validated_application_discovery(
    runtime: _ActionRuntime,
) -> tuple[ApplicationSummary, ...]:
    applications = runtime.list_applications()
    if not isinstance(applications, tuple):
        raise ContractViolation("application discovery must return an applications tuple")
    if len(applications) > _MAX_APPLICATION_LIST_ITEMS:
        raise ContractViolation(
            f"application.list returned more than {_MAX_APPLICATION_LIST_ITEMS} applications"
        )
    for application in applications:
        validate_application_summary(application)
    return applications


def _preflight_application_summary(
    runtime: _ActionRuntime,
    application_ref: ApplicationRef,
) -> ApplicationSummary | ActionError:
    try:
        applications = _validated_application_discovery(runtime)
    except Exception as error:
        return _exception_action_error(error)
    summary = next(
        (application for application in applications if application.ref == application_ref),
        None,
    )
    if summary is None:
        return _action_error(OperationErrorCode.NOT_FOUND)
    return summary


async def _preflight_application_resource(
    runtime: _ActionRuntime,
    resource: ApplicationRef | ProjectRef | ThreadRef,
    operation_id: str,
    *,
    require_streaming: bool = False,
) -> ActionError | None:
    application_ref = (
        resource if isinstance(resource, ApplicationRef) else _application_ref(resource)
    )
    summary = _preflight_application_summary(runtime, application_ref)
    if isinstance(summary, ActionError):
        return summary
    if require_streaming and summary.capabilities.runtime.streaming is SupportLevel.UNSUPPORTED:
        return _action_error(OperationErrorCode.UNSUPPORTED)
    if isinstance(resource, ApplicationRef):
        return None
    return await _preflight_resource_under_application(
        runtime,
        summary,
        resource,
        operation_id,
    )


async def _preflight_resource_under_application(
    runtime: _ActionRuntime,
    application: ApplicationSummary,
    resource: ProjectRef | ThreadRef,
    operation_id: str,
) -> ActionError | None:
    if application.capabilities.projects.reading is SupportLevel.UNSUPPORTED:
        return _action_error(OperationErrorCode.UNSUPPORTED)
    project_ref = resource.project_ref if isinstance(resource, ThreadRef) else resource
    project_error = await _preflight_project_read(
        runtime,
        application.ref,
        project_ref,
        f"{operation_id}:project",
    )
    if project_error is not None or isinstance(resource, ProjectRef):
        return project_error
    if application.capabilities.threads.reading is SupportLevel.UNSUPPORTED:
        return _action_error(OperationErrorCode.UNSUPPORTED)
    return await _preflight_thread_read(
        runtime,
        application.ref,
        resource,
        f"{operation_id}:thread",
    )


async def _preflight_project_read(
    runtime: _ActionRuntime,
    application_ref: ApplicationRef,
    project_ref: ProjectRef,
    operation_id: str,
) -> ActionError | None:
    operation = GetProject(
        operation_id=operation_id,
        application_ref=application_ref,
        project_ref=project_ref,
        created_at=_now(),
    )
    try:
        validate_application_operation(operation)
        result = await runtime.execute_application(operation)
        validate_application_operation_result(operation, result)
    except Exception as error:
        return _exception_action_error(error)
    if isinstance(result, ApplicationOperationFailed):
        return _application_error(result)
    if not isinstance(result, ProjectRead):
        return _action_error(OperationErrorCode.ADAPTER_FAILURE)
    return None


async def _preflight_thread_read(
    runtime: _ActionRuntime,
    application_ref: ApplicationRef,
    thread_ref: ThreadRef,
    operation_id: str,
) -> ActionError | None:
    operation = GetThread(
        operation_id=operation_id,
        application_ref=application_ref,
        thread_ref=thread_ref,
        created_at=_now(),
    )
    try:
        validate_application_operation(operation)
        result = await runtime.execute_application(operation)
        validate_application_operation_result(operation, result)
    except Exception as error:
        return _exception_action_error(error)
    if isinstance(result, ApplicationOperationFailed):
        return _application_error(result)
    if not isinstance(result, ThreadRead):
        return _action_error(OperationErrorCode.ADAPTER_FAILURE)
    return None


async def _preflight_application_mutation(
    runtime: _ActionRuntime,
    operation: ApplicationOperation,
    operation_id: str,
    *,
    require_streaming: bool = False,
) -> ActionError | None:
    summary = _preflight_application_summary(runtime, operation.application_ref)
    if isinstance(summary, ActionError):
        return summary
    capability_error = _mutation_capability_error(summary, operation)
    if capability_error is not None:
        return capability_error
    if require_streaming and summary.capabilities.runtime.streaming is SupportLevel.UNSUPPORTED:
        return _action_error(OperationErrorCode.UNSUPPORTED)
    if isinstance(operation, (DeleteProject, CreateThread)):
        return await _preflight_resource_under_application(
            runtime,
            summary,
            operation.project_ref,
            operation_id,
        )
    if isinstance(operation, (ActivateNativeThread, DeleteThread, InterruptTurn)):
        return await _preflight_resource_under_application(
            runtime,
            summary,
            operation.thread_ref,
            operation_id,
        )
    if isinstance(operation, CreateProject):
        return None
    return _action_error(OperationErrorCode.UNSUPPORTED)


def _mutation_capability_error(
    application: ApplicationSummary,
    operation: ApplicationOperation,
) -> ActionError | None:
    capabilities = application.capabilities
    unsupported = False
    if isinstance(operation, CreateProject):
        unsupported = capabilities.projects.creation is SupportLevel.UNSUPPORTED
    elif isinstance(operation, DeleteProject):
        unsupported = capabilities.projects.deletion is SupportLevel.UNSUPPORTED
    elif isinstance(operation, CreateThread):
        unsupported = capabilities.threads.creation is SupportLevel.UNSUPPORTED or any(
            isinstance(item, AttachmentContent)
            and item.source.kind not in capabilities.attachment_sources
            for item in operation.initial_context
        )
    elif isinstance(operation, ActivateNativeThread):
        unsupported = capabilities.runtime.native_thread_activation is SupportLevel.UNSUPPORTED
    elif isinstance(operation, DeleteThread):
        deletion = capabilities.threads.deletion
        unsupported = (
            deletion is ThreadDeletionCapability.UNSUPPORTED
            or deletion.value != operation.mode.value
        )
    elif isinstance(operation, InterruptTurn):
        unsupported = capabilities.runtime.interruption is SupportLevel.UNSUPPORTED
    else:
        unsupported = True
    return _action_error(OperationErrorCode.UNSUPPORTED) if unsupported else None


def _preflight_request_application(
    runtime: _ActionRuntime,
    request_ref: RequestRef,
) -> ActionError | None:
    summary = _preflight_application_summary(runtime, request_ref.application_ref)
    if isinstance(summary, ActionError):
        return summary
    if summary.capabilities.runtime.interactive_requests is SupportLevel.UNSUPPORTED:
        return _action_error(OperationErrorCode.UNSUPPORTED)
    return None


def _ref_payload(value: ProjectRef | ThreadRef) -> FingerprintPayload:
    project_ref = value.project_ref if isinstance(value, ThreadRef) else value
    payload: FingerprintPayload = (
        ("application_id", project_ref.application_instance_id),
        ("project_id", project_ref.project_id),
    )
    if isinstance(value, ThreadRef):
        payload = (*payload, ("thread_id", value.thread_id))
    return payload


def _response_payload(
    request_ref: RequestRef,
    response: RequestResponse,
) -> FingerprintPayload:
    base: FingerprintPayload = (
        ("application_id", request_ref.application_ref.application_instance_id),
        ("request_id", request_ref.native_request_id),
    )
    if isinstance(response, ApprovalResponse):
        return (*base, ("response_kind", "approval"), ("choice_id", response.choice_id))
    answers = tuple(
        (question_id, tuple(values)) for question_id, values in sorted(response.answers.items())
    )
    encoded = json.dumps(answers, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return (
        *base,
        ("response_kind", "user_input"),
        ("answers_fingerprint", hashlib.sha256(encoded).hexdigest()),
    )


def _snapshot_request_response(response: RequestResponse) -> RequestResponse:
    if isinstance(response, ApprovalResponse):
        return ApprovalResponse(choice_id=response.choice_id)
    return UserInputResponse(
        answers=MappingProxyType(
            {question_id: tuple(answers) for question_id, answers in response.answers.items()}
        )
    )


def _snapshot_content(content: tuple[Content, ...]) -> tuple[Content, ...]:
    snapshots: list[Content] = []
    for item in content:
        if isinstance(item, TextContent):
            snapshots.append(item)
            continue
        if not isinstance(item, AttachmentContent):
            raise ContractViolation("initial_context contains unsupported content")
        snapshots.append(
            AttachmentContent(
                attachment_id=item.attachment_id,
                media_type=item.media_type,
                source=item.source,
                filename=item.filename,
                size_bytes=item.size_bytes,
                metadata=_snapshot_metadata(item.metadata),
            )
        )
    return tuple(snapshots)


def _snapshot_metadata(value: Mapping[str, object]) -> Mapping[str, object]:
    canonical = _canonical_metadata(value)
    if not isinstance(canonical, dict):
        raise ContractViolation("initial_context metadata must be an object")
    return MappingProxyType(
        {key: _freeze_canonical_metadata(item) for key, item in canonical.items()}
    )


def _freeze_canonical_metadata(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_canonical_metadata(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_canonical_metadata(item) for item in value)
    return value


def _content_fingerprint(content: tuple[Content, ...]) -> str:
    canonical = [_content_identity(item) for item in content]
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _content_identity(content: Content) -> object:
    if isinstance(content, TextContent):
        return {"kind": "text", "text": content.text, "format": content.format.value}
    if not isinstance(content, AttachmentContent):
        raise ContractViolation("initial_context contains unsupported content")
    source = content.source
    if isinstance(source, LocalPath):
        source_value = source.path
    elif isinstance(source, RemoteUrl):
        source_value = source.url
    elif isinstance(source, AttachmentHandle):
        source_value = source.handle_id
    else:
        raise ContractViolation("initial_context contains unsupported attachment source")
    return {
        "kind": "attachment",
        "attachment_id": content.attachment_id,
        "media_type": content.media_type,
        "source_kind": source.kind.value,
        "source_value": source_value,
        "filename": content.filename,
        "size_bytes": content.size_bytes,
        "metadata": _canonical_metadata(content.metadata),
    }


def _canonical_metadata(value: Mapping[str, object]) -> object:
    return {key: _canonical_metadata_value(item) for key, item in sorted(value.items())}


def _canonical_metadata_value(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractViolation("initial_context metadata floats must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ContractViolation("initial_context metadata keys must be strings")
        return {
            key: _canonical_metadata_value(item)
            for key, item in sorted(value.items())
            if isinstance(key, str)
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_metadata_value(item) for item in value]
    raise ContractViolation("initial_context metadata is not canonically fingerprintable")


def _read_operation_id(kind: str) -> str:
    return f"imagent:read:{kind}:{uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ActionResult",
    "ActionValue",
    "ApplicationActions",
    "ConversationActions",
    "ReadOutcome",
]

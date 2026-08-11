"""Stable Gateway package root and current orchestration implementation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .delivery.proactive_authorization import DeliveryAuthorizer
    from .runtime import Gateway as Gateway

import imagent.contracts as contracts_facade

from ..applications.contract import (
    AgentApplicationAdapter,
    ApplicationRef,
    ApplicationSummary,
    ThreadRef,
    validate_application_summary,
)
from ..applications.operations import (
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    DeleteProject,
    GetProject,
    GetThread,
    ProjectRead,
    ThreadRead,
    _LegacyApplicationOperation,
    validate_application_operation,
    validate_application_operation_result,
)
from ..interaction.channels.contract import ChannelAdapter, InboundAdmission
from ..interaction.controllers import ControllerLifecycle
from ..interaction.diagnostics import QueueDiagnosticFacts, QueueDiagnosticName
from ..interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
)
from ..interaction.operations import (
    ContractError,
    ContractViolation,
    OperationErrorCode,
    operation_error,
)
from .actions import ApplicationActions, ConversationActions
from .admission import (
    ClaimedInbound,
    InboundAdmissionService,
    inbound_idempotency_identity,
    start_channel_with_admission,
)
from .composition import GatewayExtensions, GatewayLimits, GatewayRepositories
from .concurrency import KeyedLockCapacityError as _KeyedLockCapacityError
from .controller_input import _ScopedControllerActionRuntime
from .delivery.coordination import DeliveryCoordinator
from .delivery.outcome_observation import (
    DeliveryOutcomeObserver as DeliveryOutcomeObserver,
)
from .delivery.outcome_observation import (
    DeliveryOutcomeObserverRuntime,
)
from .delivery.planning import DeliveryPlanningError
from .delivery.proactive import (
    DeliveryIntent,
    DeliveryTarget,
    ProactiveDeliveryResult,
)
from .delivery.proactive_runtime import ProactiveDeliveryService
from .diagnostics import (
    DiagnosticsSnapshot,
    GatewayDiagnosticFacts,
    _bounded_cleanup_error_summary,
    _bounded_lifecycle_error_summary,
    collect_application_diagnostics,
    collect_channel_diagnostics,
    new_diagnostics_snapshot,
    summarize_projection_health,
)
from .effect_execution import StoreBackedGatewayEffectExecutor
from .input import InboundContentTransformer as InboundContentTransformer
from .input import MissingBindingError as MissingBindingError
from .input import StaleBindingError as StaleBindingError
from .input.content_transformation import InboundContentTransformRuntime
from .input.dispatch import (
    InputDispatchRuntime,
    TurnAcceptanceOrderingGate,
    derive_client_message_id,
    preflight_authoritative_binding,
)
from .input.failure_presentation import InboundFailurePhase as InboundFailurePhase
from .input.failure_presentation import InboundFailurePresentationRuntime, handle_claimed_inbound
from .input.failure_presentation import InboundFailurePresenter as InboundFailurePresenter
from .lifecycle import (
    GatewayLifecycleFailure,
    GatewayNotRunning,
    GatewayStartupAdmission,
    _bounded_lifecycle_call,
    _detach_public_lifecycle_context,
    _public_lifecycle_error,
)
from .persistence import BindingConflict, InMemoryIdempotencyRepository
from .persistence.memory import (
    InMemoryDeliverySubmissionRepository,
    InMemoryProjectionRouteRepository,
    InMemoryRequestCorrelationRepository,
)
from .persistence.repository_contracts import (
    DeliverySubmissionCapacityError,
    DeliverySubmissionConflict,
    IdempotencyClaimStatus,
)
from .persistence.state_contracts import (
    ConversationBinding,
    DeliverySubmissionState,
    validate_binding,
)
from .persistence.store import GatewayStoreSession, _is_gateway_store_session
from .presentation import (
    OutboundPresentationContext,
    OutboundPresentationRuntime,
    _decide_claimed_outbound_presentation,
    _FailedClaimedOutbound,
    _PresentedClaimedOutbound,
    _projection_presentation_context,
    _SuppressedClaimedOutbound,
)
from .presentation import (
    OutboundPresentationPolicy as OutboundPresentationPolicy,
)
from .presentation import (
    ProjectionPresentationOrigin as ProjectionPresentationOrigin,
)
from .projection import request_correlation as _request_correlation
from .projection.observation import (
    ProjectionWorkerCapacityError,
    ProjectionWorkerHealth,
    RetryableDeliveryError,
    ThreadProjectionRuntime,
    _DestinationDecisionError,
    _ProjectionRecoveryRequired,
)
from .routing import projection_routes as _projection_routes
from .routing.bindings import (
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationThread,
    ConversationBound,
    _BindingRuntime,
)
from .routing.bindings import (
    ClearConversationApplication as ClearConversationApplication,
)
from .routing.bindings import (
    ClearConversationProject as ClearConversationProject,
)
from .routing.operations import _LegacyGatewayOperation

logger = logging.getLogger(__name__)

_GATEWAY_OPERATION_EXPORTS = frozenset(
    {
        "ApplicationsListed",
        "GatewayOperation",
        "GatewayOperationFailed",
        "GatewayOperationResult",
        "GatewayOperationType",
        "ListApplications",
        "SelectApplication",
        "validate_gateway_operation",
        "validate_gateway_operation_result",
    }
)
_ACTION_EXPORTS = frozenset(
    {
        "ActionResult",
        "ActionValue",
        "ApplicationActions",
        "ConversationActions",
        "ReadOutcome",
    }
)


class ImAgentGateway:
    """Channel/application orchestration independent from one interaction grammar."""

    def __init__(
        self,
        *,
        channels: list[ChannelAdapter],
        applications: list[AgentApplicationAdapter],
        repositories: GatewayRepositories,
        limits: GatewayLimits = GatewayLimits(),
        extensions: GatewayExtensions = GatewayExtensions(),
        delivery_authorizer: DeliveryAuthorizer | None = None,
        delivery_coordinator: DeliveryCoordinator | None = None,
        projection_policy: _projection_routes.ProjectionPolicy = (
            _projection_routes.ProjectionPolicy.REMEMBERED_LAST_RECIPIENT
        ),
    ) -> None:
        channel_ids = tuple(channel.channel_instance_id for channel in channels)
        if len(channel_ids) != len(set(channel_ids)):
            raise ValueError("Gateway Channel instance IDs must be unique")
        application_summaries = tuple(application.summary for application in applications)
        for summary in application_summaries:
            validate_application_summary(summary)
        application_ids = tuple(
            summary.ref.application_instance_id for summary in application_summaries
        )
        if len(application_ids) != len(set(application_ids)):
            raise ValueError("Gateway Application instance IDs must be unique")
        self._channels = dict(zip(channel_ids, channels, strict=True))
        self._applications = dict(zip(application_ids, applications, strict=True))
        self._lifecycle_owner_timeout_seconds = limits.lifecycle_owner_timeout_seconds
        coherent_session = _coherent_store_session(repositories)
        bindings = coherent_session if coherent_session is not None else repositories.bindings
        self._binding_runtime = _BindingRuntime(bindings)
        self._projection_policy = projection_policy
        idempotency = coherent_session if coherent_session is not None else repositories.idempotency
        if idempotency is None:
            idempotency = InMemoryIdempotencyRepository(
                max_records=limits.idempotency_max_records,
            )
        self._idempotency = idempotency
        request_correlations = (
            coherent_session if coherent_session is not None else repositories.request_correlations
        )
        if request_correlations is None:
            request_correlations = InMemoryRequestCorrelationRepository()
        self._delivery_coordinator = delivery_coordinator or DeliveryCoordinator()
        self._controller = extensions.controller
        self._controller_action_runtime: _ScopedControllerActionRuntime | None = None
        self._inbound_content_transform_runtime = (
            InboundContentTransformRuntime(
                extensions.inbound_content_transformer,
                timeout_seconds=limits.inbound_content_transform_timeout_seconds,
                max_items=limits.inbound_content_transform_max_items,
                max_concurrency=limits.inbound_content_transform_max_concurrency,
            )
            if extensions.inbound_content_transformer is not None
            else None
        )
        self._inbound_failure_presentation_runtime = (
            InboundFailurePresentationRuntime(
                extensions.inbound_failure_presenter,
                timeout_seconds=limits.inbound_failure_present_timeout_seconds,
                max_items=limits.inbound_failure_present_max_items,
                max_text_characters=limits.inbound_failure_present_max_text_characters,
                max_concurrency=limits.inbound_failure_present_max_concurrency,
            )
            if extensions.inbound_failure_presenter is not None
            else None
        )
        self._outbound_presentation_runtime = (
            OutboundPresentationRuntime(
                extensions.outbound_presentation,
                timeout_seconds=limits.outbound_presentation_timeout_seconds,
                max_items=limits.outbound_presentation_max_items,
                max_text_characters=limits.outbound_presentation_max_text_characters,
                max_concurrency=limits.outbound_presentation_max_concurrency,
            )
            if extensions.outbound_presentation is not None
            else None
        )
        self._delivery_outcome_observer_runtime = (
            DeliveryOutcomeObserverRuntime(
                extensions.delivery_outcome_observer,
                timeout_seconds=limits.delivery_outcome_observer_timeout_seconds,
                max_items=limits.delivery_outcome_observer_max_items,
                max_text_characters=limits.delivery_outcome_observer_max_text_characters,
                max_concurrency=limits.delivery_outcome_observer_max_concurrency,
            )
            if extensions.delivery_outcome_observer is not None
            else None
        )
        self._outbound_deliveries: dict[
            tuple[str, str],
            asyncio.Task[IdempotencyClaimStatus],
        ] = {}
        self._starting = False
        self._accepting_inbound = False
        self._startup_admission = GatewayStartupAdmission[ClaimedInbound](
            max_pending=limits.startup_buffer_max_pending
        )
        projection_repository = (
            coherent_session if coherent_session is not None else repositories.projections
        )
        if projection_repository is None:
            projection_repository = InMemoryProjectionRouteRepository()
        self._turn_acceptance_gate = TurnAcceptanceOrderingGate(
            max_pending=limits.turn_acceptance_event_max_pending,
        )
        self._projection_runtime = ThreadProjectionRuntime(
            applications=self._applications,
            bindings=bindings,
            projections=projection_repository,
            request_correlations=request_correlations,
            request_presenter=extensions.request_presenter,
            projection_policy=projection_policy,
            execute_application=self.execute_application,
            deliver_outbound=self._deliver_projected_outbound,
            deliver_request_outbound=partial(
                self._deliver_outbound,
                cancellable=True,
            ),
            baseline_history_limit=limits.baseline_history_limit,
            recovery_history_page_size=limits.recovery_history_page_size,
            recovery_max_pages=limits.recovery_max_pages,
            catchup_limit=limits.catchup_limit,
            projection_item_limit=limits.projection_item_limit,
            request_delivery_max_pending=limits.request_delivery_max_pending,
            acceptance_gate=self._turn_acceptance_gate,
            max_active_threads=limits.projection_max_active_threads,
            subscription_retry_initial_seconds=limits.subscription_retry_initial_seconds,
            subscription_retry_max_seconds=limits.subscription_retry_max_seconds,
            turn_correlation_retention_seconds=limits.turn_correlation_retention_seconds,
            request_correlation_retention_seconds=(limits.request_correlation_retention_seconds),
        )
        if coherent_session is not None:
            self._controller_action_runtime = _ScopedControllerActionRuntime(
                gateway_id=coherent_session.lease.gateway_id,
                applications=self._applications,
                execute_application=self._execute_scoped_application,
                get_binding=self._binding_runtime.current,
                effects=StoreBackedGatewayEffectExecutor(coherent_session),
                reconcile_projection_route=self._projection_runtime.reconcile_action_route,
                validate_projection_route=(
                    self._projection_runtime.validate_action_route_reconciliation
                ),
                begin_projection_route=self._projection_runtime.begin_action_route,
                complete_projection_route=self._projection_runtime.complete_action_route,
                abort_projection_route=self._projection_runtime.abort_action_route,
                fence_projection_route_commit=(self._projection_runtime.fence_action_route_commit),
                request_projection=self._projection_runtime.request_projection,
            )
        self._request_projection = self._projection_runtime.request_projection
        self._input_dispatch = InputDispatchRuntime(
            correlator=self._request_projection,
            acceptance_gate=self._turn_acceptance_gate,
            event_applier=self._projection_runtime,
        )
        from .routing.operations import _GatewayOperationExecutor

        self._gateway_operations = _GatewayOperationExecutor(
            delegates=self,
            max_active_conversation_keys=limits.conversation_serialization_max_active_keys,
            contract_error=_contract_error,
        )
        self._conversation_locks = self._gateway_operations.conversation_locks
        delivery_submissions = (
            coherent_session if coherent_session is not None else repositories.delivery_submissions
        )
        if delivery_submissions is None:
            delivery_submissions = InMemoryDeliverySubmissionRepository(
                max_records=limits.delivery_submission_max_records,
            )
        self._delivery_service = ProactiveDeliveryService(
            channels=self._channels,
            submissions=delivery_submissions,
            resolve_thread_routes=self._projection_runtime.active_routes,
            authorizer=delivery_authorizer,
            coordinator=self._delivery_coordinator,
            outcome_observer=self._delivery_outcome_observer_runtime,
        )
        self._inbound_admission = InboundAdmissionService(
            self._idempotency,
            self._handle_claimed_message_entry,
        )

    async def start(self) -> None:
        if isinstance(self._controller, ControllerLifecycle):
            self._controller.validate_startup()
        if self._controller is not None and self._controller_action_runtime is None:
            raise RuntimeError("Controller composition requires one coherent GatewayStore session")
        self._starting = True
        self._accepting_inbound = True
        self._startup_admission.reset()
        started_applications: list[AgentApplicationAdapter] = []
        started_channels: list[ChannelAdapter] = []
        startup_error: BaseException | None = None
        try:
            self._delivery_coordinator.start()
            if self._delivery_outcome_observer_runtime is not None:
                self._delivery_outcome_observer_runtime.start()
            await self._projection_runtime.cleanup_stale_correlations()
            restart_open_requests = await self._projection_runtime.open_request_refs()
            await self._projection_runtime.restore()
            for application in self._applications.values():
                try:
                    await application.start()
                except BaseException as start_error:
                    start_error = await self._cleanup_lifecycle_owner(
                        start_error,
                        (
                            "Application "
                            f"{application.summary.ref.application_instance_id!r} partial startup"
                        ),
                        application.stop,
                    )
                    assert start_error is not None
                    raise start_error
                started_applications.append(application)
                self._startup_admission.raise_if_overflowed()
            for channel in self._channels.values():
                try:
                    await start_channel_with_admission(
                        channel,
                        self._handle_message_entry,
                        partial(self._begin_inbound, channel.channel_instance_id),
                    )
                except BaseException as start_error:
                    start_error = await self._cleanup_lifecycle_owner(
                        start_error,
                        f"Channel {channel.channel_instance_id!r} partial startup",
                        channel.stop,
                    )
                    assert start_error is not None
                    raise start_error
                started_channels.append(channel)
                self._startup_admission.raise_if_overflowed()
            self._projection_runtime.mark_delivery_ready()
            await self._projection_runtime.reconcile_pending_requests(restart_open_requests)
            self._startup_admission.raise_if_overflowed()
            while self._startup_admission:
                entry = self._startup_admission.popleft()
                await self._handle_claimed_message(entry)
                self._startup_admission.raise_if_overflowed()
            self._starting = False
        except BaseException as error:
            self._accepting_inbound = False
            self._starting = False
            error = _public_lifecycle_error(error, "Gateway startup")
            while self._startup_admission:
                entry = self._startup_admission.popleft()
                try:
                    await self._idempotency.release(
                        entry.scope,
                        entry.key,
                        owner_token=entry.owner_token,
                    )
                except BaseException as release_error:
                    error.add_note(
                        "Failed to release a pre-side-effect inbound claim during "
                        "Gateway startup rollback: "
                        + _bounded_lifecycle_error_summary(
                            "inbound claim release",
                            release_error,
                        )
                    )
            self._startup_admission.clear()
            await self._cleanup_lifecycle_owner(
                error,
                "projection runtime",
                self._projection_runtime.stop,
            )
            if self._outbound_presentation_runtime is not None:
                await self._cleanup_lifecycle_owner(
                    error,
                    "outbound presentation runtime",
                    self._outbound_presentation_runtime.close,
                )
            await self._cleanup_lifecycle_owner(
                error,
                "delivery coordinator",
                self._delivery_coordinator.close,
            )
            for channel in reversed(started_channels):
                await self._cleanup_lifecycle_owner(
                    error,
                    f"Channel {channel.channel_instance_id!r}",
                    channel.stop,
                )
            if isinstance(self._controller, ControllerLifecycle):
                await self._cleanup_lifecycle_owner(
                    error,
                    "Controller",
                    self._controller.close,
                )
            if self._inbound_content_transform_runtime is not None:
                await self._cleanup_lifecycle_owner(
                    error,
                    "inbound content transform runtime",
                    self._inbound_content_transform_runtime.close,
                )
            if self._inbound_failure_presentation_runtime is not None:
                await self._cleanup_lifecycle_owner(
                    error,
                    "inbound failure presentation runtime",
                    self._inbound_failure_presentation_runtime.close,
                )
            if self._delivery_outcome_observer_runtime is not None:
                await self._cleanup_lifecycle_owner(
                    error,
                    "delivery outcome observer runtime",
                    self._delivery_outcome_observer_runtime.close,
                )
            for application in reversed(started_applications):
                await self._cleanup_lifecycle_owner(
                    error,
                    f"Application {application.summary.ref.application_instance_id!r}",
                    application.stop,
                )
            startup_error = _detach_public_lifecycle_context(error)
        if startup_error is not None:
            if isinstance(startup_error, GatewayLifecycleFailure):
                raise startup_error from startup_error.__cause__
            raise startup_error

    async def stop(self) -> None:
        self._accepting_inbound = False
        self._starting = False
        error: BaseException | None = None
        error = await self._cleanup_lifecycle_owner(
            error,
            "projection runtime",
            self._projection_runtime.stop,
        )
        if self._outbound_presentation_runtime is not None:
            error = await self._cleanup_lifecycle_owner(
                error,
                "outbound presentation runtime",
                self._outbound_presentation_runtime.close,
            )
        error = await self._cleanup_lifecycle_owner(
            error,
            "delivery coordinator",
            self._delivery_coordinator.close,
        )
        for channel in reversed(tuple(self._channels.values())):
            error = await self._cleanup_lifecycle_owner(
                error,
                f"Channel {channel.channel_instance_id!r}",
                channel.stop,
            )
        if isinstance(self._controller, ControllerLifecycle):
            error = await self._cleanup_lifecycle_owner(
                error,
                "Controller",
                self._controller.close,
            )
        if self._inbound_content_transform_runtime is not None:
            error = await self._cleanup_lifecycle_owner(
                error,
                "inbound content transform runtime",
                self._inbound_content_transform_runtime.close,
            )
        if self._inbound_failure_presentation_runtime is not None:
            error = await self._cleanup_lifecycle_owner(
                error,
                "inbound failure presentation runtime",
                self._inbound_failure_presentation_runtime.close,
            )
        if self._delivery_outcome_observer_runtime is not None:
            error = await self._cleanup_lifecycle_owner(
                error,
                "delivery outcome observer runtime",
                self._delivery_outcome_observer_runtime.close,
            )
        for application in reversed(tuple(self._applications.values())):
            error = await self._cleanup_lifecycle_owner(
                error,
                f"Application {application.summary.ref.application_instance_id!r}",
                application.stop,
            )
        if error is not None:
            raise error

    async def _cleanup_lifecycle_owner(
        self,
        primary: BaseException | None,
        owner: str,
        cleanup: Callable[[], Awaitable[None]],
    ) -> BaseException | None:
        return await _cleanup_lifecycle_owner(
            primary,
            owner,
            cleanup,
            timeout_seconds=self._lifecycle_owner_timeout_seconds,
        )

    def get_projection_health(
        self,
        thread_ref: ThreadRef,
    ) -> ProjectionWorkerHealth | None:
        """Return process-local infrastructure health for one projection worker."""
        return self._projection_runtime.get_health(thread_ref)

    def list_projection_health(self) -> tuple[ProjectionWorkerHealth, ...]:
        """Return process-local projection health without Agent Turn state."""
        return self._projection_runtime.list_health()

    def diagnostics_snapshot(self) -> DiagnosticsSnapshot:
        """Return redacted process-local facts; Native Applications remain authoritative."""

        startup = self._startup_admission
        return new_diagnostics_snapshot(
            applications=collect_application_diagnostics(self._applications.values()),
            channels=collect_channel_diagnostics(self._channels.values()),
            projections=summarize_projection_health(self._projection_runtime.list_health()),
            gateway=GatewayDiagnosticFacts(
                accepting_inbound=self._accepting_inbound,
                starting=self._starting,
                startup_queue=QueueDiagnosticFacts(
                    name=QueueDiagnosticName.GATEWAY_STARTUP,
                    capacity=startup.capacity,
                    depth=startup.depth,
                    overflow_count=startup.overflow_count,
                ),
                inbound_content_transformer=(
                    self._inbound_content_transform_runtime.diagnostic_facts()
                    if self._inbound_content_transform_runtime is not None
                    else None
                ),
                inbound_failure_presenter=(
                    self._inbound_failure_presentation_runtime.diagnostic_facts()
                    if self._inbound_failure_presentation_runtime is not None
                    else None
                ),
                outbound_presentation=(
                    self._outbound_presentation_runtime.diagnostic_facts()
                    if self._outbound_presentation_runtime is not None
                    else None
                ),
                delivery_outcome_observer=(
                    self._delivery_outcome_observer_runtime.diagnostic_facts()
                    if self._delivery_outcome_observer_runtime is not None
                    else None
                ),
            ),
        )

    async def execute_application(
        self,
        operation: _LegacyApplicationOperation,
    ) -> ApplicationOperationResult:
        """Route one typed application operation without mutating a binding."""
        if isinstance(operation, DeleteProject):
            return ApplicationOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=datetime.now(UTC),
                error=_contract_error(
                    NotImplementedError(
                        "project.delete requires principal-scoped ApplicationActions"
                    )
                ),
            )
        return await self._execute_scoped_application(operation)

    async def _execute_scoped_application(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult:
        """Execute one validated Application operation for a scoped SDK owner."""
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
        operation: _LegacyGatewayOperation,
    ) -> contracts_facade.GatewayOperationResult:
        """Execute one typed Gateway operation under Conversation serialization."""
        return await self._gateway_operations.execute(operation)

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None:
        return await self._binding_runtime.current(conversation_ref)

    def _conversation_actions(
        self,
        conversation_ref: ConversationRef,
        *,
        actor: str,
    ) -> ConversationActions:
        runtime = self._require_scoped_action_runtime()
        return runtime.actions(
            conversation_ref,
            actor=actor,
            foreground_route=(
                self._projection_policy is _projection_routes.ProjectionPolicy.FOREGROUND_ONLY
            ),
        )

    def _application_actions(
        self,
        application_ref: ApplicationRef,
        *,
        principal: str,
    ) -> ApplicationActions:
        return self._require_scoped_action_runtime().application(
            application_ref,
            principal=principal,
        )

    def _deactivate_scoped_actions(self) -> None:
        runtime = self._controller_action_runtime
        if runtime is not None:
            runtime.deactivate()

    def _require_scoped_action_runtime(self) -> _ScopedControllerActionRuntime:
        runtime = self._controller_action_runtime
        if runtime is None:
            raise RuntimeError("Scoped actions require one coherent GatewayStore session")
        return runtime

    async def deliver_proactively(
        self,
        intent: DeliveryIntent,
        *,
        credential: str,
    ) -> ProactiveDeliveryResult:
        """Deliver caller-provided content through an authorized Gateway target."""
        return await self._delivery_service.deliver(intent, credential=credential)

    async def authorize_proactive_target(
        self,
        target: DeliveryTarget,
        *,
        credential: str,
    ) -> None:
        """Fail closed before an ingress materializes caller-provided artifacts."""
        await self._delivery_service.authorize(target, credential=credential)

    async def _execute_gateway_locked(
        self,
        operation: _LegacyGatewayOperation,
    ) -> contracts_facade.GatewayOperationResult:
        return await self._gateway_operations.execute_locked(operation)

    def _list_applications(
        self,
        operation: contracts_facade.ListApplications,
        *,
        completed_at: datetime,
    ) -> tuple[ApplicationSummary, ...]:
        del operation, completed_at
        return tuple(application.summary for application in self._applications.values())

    async def _select_application(
        self,
        operation: contracts_facade.SelectApplication,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        self._require_application(operation.application_ref.application_instance_id)
        change = await self._binding_runtime.select_application(
            operation.conversation_ref,
            operation.application_ref,
            expected_generation=operation.expected_generation,
        )
        await self._projection_runtime.handle_binding_change(
            change.previous,
            change.binding,
        )
        return ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=completed_at,
            binding=change.binding,
        )

    async def _bind_conversation_to_project(
        self,
        operation: BindConversationToProject,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        from .routing.operations import _GatewayActionError

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
        change = await self._binding_runtime.bind_project(
            operation.conversation_ref,
            application.summary.ref,
            read.project.ref,
            expected_generation=operation.expected_generation,
        )
        await self._projection_runtime.handle_binding_change(
            change.previous,
            change.binding,
        )
        return ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=completed_at,
            binding=change.binding,
        )

    async def _bind_conversation_to_thread(
        self,
        operation: BindConversationToThread,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        from .routing.operations import _GatewayActionError

        application = self._require_application(
            operation.thread_ref.project_ref.application_instance_id
        )
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
        foreground_only = (
            self._projection_policy is _projection_routes.ProjectionPolicy.FOREGROUND_ONLY
        )
        prepared_binding = await self._binding_runtime.prepare_thread_binding(
            operation.conversation_ref,
            application.summary.ref,
            read.thread.ref,
            expected_generation=operation.expected_generation,
            converge_same_target=foreground_only,
        )
        if foreground_only:
            (
                prepared_route,
                route_was_created,
            ) = await self._projection_runtime.prepare_foreground_binding_route(
                read.thread.ref,
                operation.conversation_ref,
            )
            retain_prepared_barrier = False
            try:
                require_checkpoint = not route_was_created
                if prepared_binding.converge_without_write:
                    change = await self._binding_runtime.commit_thread_binding(prepared_binding)
                    retain_prepared_barrier = True
                    await self._projection_runtime.handle_binding_change(
                        None,
                        change.binding,
                        require_checkpoint=require_checkpoint,
                    )
                    return ConversationBound(
                        operation_id=operation.operation_id,
                        type=operation.type,
                        completed_at=completed_at,
                        binding=change.binding,
                    )
                try:
                    change = await self._binding_runtime.commit_thread_binding(prepared_binding)
                except BaseException as error:
                    retain_prepared_barrier = (
                        await self._binding_runtime.binding_write_may_have_committed(
                            prepared_binding,
                            error,
                        )
                    )
                    raise
                retain_prepared_barrier = True
                await self._projection_runtime.handle_binding_change(
                    change.previous,
                    change.binding,
                    require_checkpoint=require_checkpoint,
                )
                return ConversationBound(
                    operation_id=operation.operation_id,
                    type=operation.type,
                    completed_at=completed_at,
                    binding=change.binding,
                )
            finally:
                self._projection_runtime.complete_foreground_binding_route(
                    prepared_route.route_id,
                    complete_bootstrap=not retain_prepared_barrier,
                )
        change = await self._binding_runtime.commit_thread_binding(
            prepared_binding,
        )
        await self._projection_runtime.handle_binding_change(
            change.previous,
            change.binding,
        )
        return ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=completed_at,
            binding=change.binding,
        )

    async def _clear_conversation_thread(
        self,
        operation: ClearConversationThread,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        change = await self._binding_runtime.clear_thread(
            operation.conversation_ref,
            expected_generation=operation.expected_generation,
        )
        await self._projection_runtime.handle_binding_change(
            change.previous,
            change.binding,
        )
        return ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=completed_at,
            binding=change.binding,
        )

    async def _observe_thread(
        self,
        operation: _projection_routes.ObserveThread,
        *,
        completed_at: datetime,
    ) -> _projection_routes.ThreadObserved:
        from .routing.operations import _GatewayActionError

        application = self._require_application(
            operation.thread_ref.project_ref.application_instance_id
        )
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
        route = await self._projection_runtime.observe_thread(
            application,
            read.thread.ref,
            operation.conversation_ref,
            reply_to_message_id=operation.reply_to_message_id,
        )
        return _projection_routes.ThreadObserved(
            operation_id=operation.operation_id,
            completed_at=completed_at,
            route=route,
        )

    async def _route_request_response(
        self,
        operation: _request_correlation.RespondToRequest,
        *,
        completed_at: datetime,
    ) -> _request_correlation.RequestResponseRouted:
        from .routing.operations import _GatewayActionError

        try:
            return await self._request_projection.route_response(
                operation,
                completed_at=completed_at,
            )
        except _request_correlation._RequestResponseRejected as error:
            raise _GatewayActionError(error.error) from error

    async def _handle_claimed_message(self, claimed: ClaimedInbound) -> None:
        async def process(before_application_send: Callable[[], Awaitable[None]]) -> None:
            await self._process_message(
                claimed.message,
                idempotency_owner_token=claimed.owner_token,
                before_application_send=before_application_send,
            )

        await handle_claimed_inbound(
            claimed,
            process=process,
            idempotency=self._idempotency,
            presentation=self._inbound_failure_presentation_runtime,
            deliver=self._deliver_outbound,
        )

    async def _handle_message_entry(self, message: InboundMessage) -> None:
        if not self._accepting_inbound:
            raise GatewayNotRunning("gateway is not accepting Channel callbacks")
        admission = await self._begin_inbound(
            message.conversation_ref.channel_instance_id,
            message.conversation_ref,
            message.message_id,
        )
        if admission is None:
            return
        await admission.deliver(message)

    async def _begin_inbound(
        self,
        channel_instance_id: str,
        conversation_ref: ConversationRef,
        message_id: str,
    ) -> InboundAdmission | None:
        if not self._accepting_inbound:
            return None
        admission = await self._inbound_admission.begin(
            channel_instance_id,
            conversation_ref,
            message_id,
        )
        if admission is not None and not self._accepting_inbound:
            await admission.release()
            return None
        return admission

    async def _handle_claimed_message_entry(self, claimed: ClaimedInbound) -> None:
        if not self._accepting_inbound:
            await self._idempotency.release(
                claimed.scope,
                claimed.key,
                owner_token=claimed.owner_token,
            )
            return
        if self._starting:
            try:
                self._startup_admission.admit(claimed)
            except BaseException as error:
                try:
                    await self._idempotency.release(
                        claimed.scope,
                        claimed.key,
                        owner_token=claimed.owner_token,
                    )
                except BaseException as release_error:
                    error.add_note(
                        "Failed to release an inbound claim rejected by bounded "
                        "startup admission: "
                        + _bounded_cleanup_error_summary(
                            "inbound claim release",
                            release_error,
                        )
                    )
                raise
            return
        await self._handle_claimed_message(claimed)

    async def _process_message(
        self,
        message: InboundMessage,
        *,
        idempotency_owner_token: str,
        before_application_send: Callable[[], Awaitable[None]],
    ) -> None:
        async with self._gateway_operations.hold_conversation(message.conversation_ref):
            scope, key = inbound_idempotency_identity(
                message.conversation_ref,
                message.message_id,
            )
            await self._idempotency.refresh(
                scope,
                key,
                owner_token=idempotency_owner_token,
            )
            if self._controller is not None:
                action_runtime = self._controller_action_runtime
                if action_runtime is None:
                    raise RuntimeError(
                        "Controller composition requires one coherent GatewayStore session"
                    )
                outputs = await self._controller.handle(
                    message,
                    action_runtime.actions(
                        message.conversation_ref,
                        actor=message.sender,
                        foreground_route=(
                            self._projection_policy
                            is _projection_routes.ProjectionPolicy.FOREGROUND_ONLY
                        ),
                        inbound_message_id=message.message_id,
                        inbound_created_at=message.created_at,
                        enter_effect_fence=before_application_send,
                    ),
                )
                if outputs is not None:
                    if not isinstance(outputs, tuple):
                        raise TypeError("Controller output must be a tuple or None")
                    for output in outputs:
                        if not isinstance(output, OutboundMessage):
                            raise TypeError("Controller output must contain OutboundMessage values")
                        if output.conversation_ref != message.conversation_ref:
                            raise ValueError(
                                "Controller output belongs to a different Conversation"
                            )
                        await self._deliver_outbound(output)
                    return
            binding = await self._binding_runtime.current(message.conversation_ref)
            if binding is not None:
                validate_binding(binding)
                if binding.conversation_ref != message.conversation_ref:
                    raise ContractViolation("binding belongs to a different Conversation")
            if (
                binding is None
                or binding.application_ref is None
                or binding.project_ref is None
                or binding.thread_ref is None
            ):
                raise MissingBindingError()
            application = self._bound_application(binding)
            if application is None:
                raise StaleBindingError()
            thread_ref = binding.thread_ref
            await preflight_authoritative_binding(
                application,
                thread_ref,
                operation_id_prefix=derive_client_message_id(
                    message.conversation_ref,
                    message.message_id,
                ),
                created_at=message.created_at,
                execute_application=self._execute_scoped_application,
            )
            content = (
                await self._inbound_content_transform_runtime.transform(message)
                if self._inbound_content_transform_runtime is not None
                else message.content
            )
            await self._projection_runtime.prepare_input_route(
                application,
                thread_ref,
                message.conversation_ref,
                thread_was_created=False,
            )
            await self._input_dispatch.dispatch(
                application,
                thread_ref,
                message,
                content=content,
                before_application_send=before_application_send,
            )

    async def _deliver_outbound(
        self,
        message: OutboundMessage,
        presentation_context: OutboundPresentationContext | None = None,
        *,
        cancellable: bool = False,
    ) -> IdempotencyClaimStatus:
        scope = f"outbound:{message.conversation_ref.channel_instance_id}"
        key = (scope, message.delivery_id)
        existing = self._outbound_deliveries.get(key)
        if existing is not None:
            result = await existing if cancellable else await asyncio.shield(existing)
            return (
                IdempotencyClaimStatus.ALREADY_COMPLETED
                if result is IdempotencyClaimStatus.ACQUIRED
                else result
            )
        task = asyncio.create_task(
            self._deliver_outbound_once(
                message,
                scope=scope,
                presentation_context=presentation_context,
            ),
            name=f"imagent-outbound:{message.delivery_id}",
        )
        self._outbound_deliveries[key] = task
        task.add_done_callback(partial(self._finish_outbound_delivery, key))
        try:
            return await task if cancellable else await asyncio.shield(task)
        finally:
            if task.done():
                self._finish_outbound_delivery(key, task)

    async def _deliver_projected_outbound(
        self,
        message: OutboundMessage,
        checkpointable: bool,
    ) -> IdempotencyClaimStatus:
        return await self._deliver_outbound(
            message,
            _projection_presentation_context(checkpointable=checkpointable),
        )

    def _finish_outbound_delivery(
        self,
        key: tuple[str, str],
        task: asyncio.Task[IdempotencyClaimStatus],
    ) -> None:
        if self._outbound_deliveries.get(key) is task:
            self._outbound_deliveries.pop(key, None)

    async def _deliver_outbound_once(
        self,
        message: OutboundMessage,
        *,
        scope: str,
        presentation_context: OutboundPresentationContext | None = None,
    ) -> IdempotencyClaimStatus:
        owner_token = uuid4().hex
        claim = await self._idempotency.claim(
            scope,
            message.delivery_id,
            owner_token=owner_token,
        )
        if claim is not IdempotencyClaimStatus.ACQUIRED:
            return claim
        presentation = await _decide_claimed_outbound_presentation(
            message,
            presentation_context,
            runtime=self._outbound_presentation_runtime,
        )
        if isinstance(presentation, _FailedClaimedOutbound):
            await self._idempotency.release(
                scope,
                message.delivery_id,
                owner_token=owner_token,
            )
            if isinstance(presentation.error, asyncio.CancelledError):
                raise presentation.error
            raise _ProjectionRecoveryRequired(
                "outbound presentation failed before Channel side effect"
            ) from presentation.error
        if isinstance(presentation, _SuppressedClaimedOutbound):
            await self._idempotency.complete(
                scope,
                message.delivery_id,
                owner_token=owner_token,
            )
            return IdempotencyClaimStatus.ACQUIRED
        assert isinstance(presentation, _PresentedClaimedOutbound)
        message = presentation.message
        try:
            result = await self._delivery_service.deliver_internal(message)
        except (
            ContractViolation,
            DeliveryPlanningError,
            DeliverySubmissionCapacityError,
            DeliverySubmissionConflict,
        ):
            await self._idempotency.release(
                scope,
                message.delivery_id,
                owner_token=owner_token,
            )
            raise
        destination = result.destinations[0]
        if result.state is DeliverySubmissionState.IN_FLIGHT:
            return IdempotencyClaimStatus.IN_FLIGHT
        if result.state is not DeliverySubmissionState.ACCEPTED:
            if result.state is DeliverySubmissionState.RETRYABLE:
                await self._idempotency.release(
                    scope,
                    message.delivery_id,
                    owner_token=owner_token,
                )
                raise RetryableDeliveryError(
                    destination.error
                    or f"Channel delivery was deferred safely: {message.delivery_id}",
                    retry_after_seconds=(
                        destination.receipt.retry_after_seconds
                        if destination.receipt is not None
                        else None
                    ),
                )
            if result.state is DeliverySubmissionState.REJECTED:
                await self._idempotency.release(
                    scope,
                    message.delivery_id,
                    owner_token=owner_token,
                )
            raise _DestinationDecisionError(
                destination.error
                or f"Channel delivery did not complete successfully: {message.delivery_id}"
            )
        await self._idempotency.complete(
            scope,
            message.delivery_id,
            owner_token=owner_token,
        )
        return (
            IdempotencyClaimStatus.ALREADY_COMPLETED
            if destination.replayed
            else IdempotencyClaimStatus.ACQUIRED
        )

    def _bound_application(
        self,
        binding: ConversationBinding | None,
    ) -> AgentApplicationAdapter | None:
        if binding is None or binding.application_ref is None:
            return None
        return self._applications.get(binding.application_ref.application_instance_id)

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


async def _cleanup_lifecycle_owner(
    primary: BaseException | None,
    owner: str,
    cleanup: Callable[[], Awaitable[None]],
    *,
    timeout_seconds: float = 30.0,
) -> BaseException | None:
    """Continue teardown after one owner fails while preserving the first error."""

    cleanup_error = await _bounded_lifecycle_call(
        cleanup,
        timeout_seconds=timeout_seconds,
    )
    if cleanup_error is not None:
        summary = _bounded_lifecycle_error_summary(owner, cleanup_error)
        if primary is None:
            primary = _public_lifecycle_error(cleanup_error, owner)
            primary.add_note(f"Gateway cleanup failed: {summary}")
        else:
            primary = _public_lifecycle_error(primary, "Gateway lifecycle")
            primary.add_note(f"Gateway cleanup also failed: {summary}")
        logger.error("Gateway cleanup failed: %s", summary)
    return primary


def _coherent_store_session(
    repositories: GatewayRepositories,
) -> GatewayStoreSession | None:
    session = repositories.bindings
    if not _is_gateway_store_session(session):
        return None
    for repository in (
        repositories.idempotency,
        repositories.projections,
        repositories.request_correlations,
        repositories.delivery_submissions,
    ):
        if repository is not None and repository is not session:
            raise ValueError("a GatewayStore session cannot be mixed with another repository")
    return session


def _contract_error(error: Exception) -> ContractError:
    if isinstance(error, BindingConflict):
        return operation_error(error, code=OperationErrorCode.CONFLICT)
    if isinstance(error, (_KeyedLockCapacityError, ProjectionWorkerCapacityError)):
        return ContractError(
            code=OperationErrorCode.CAPACITY_EXHAUSTED.value,
            message=str(error),
            retryable=True,
            metadata={"native_exception": type(error).__name__},
        )
    return operation_error(error)


def __getattr__(name: str) -> object:
    if name == "Gateway":
        from .runtime import Gateway

        globals()[name] = Gateway
        return Gateway
    if name == "DeliveryAuthorizer":
        from .delivery.proactive_authorization import DeliveryAuthorizer

        globals()[name] = DeliveryAuthorizer
        return DeliveryAuthorizer
    if name in _GATEWAY_OPERATION_EXPORTS:
        from .routing import operations as operations_owner

        value = getattr(operations_owner, name)
        globals()[name] = value
        return value
    if name in _ACTION_EXPORTS:
        from . import actions as action_owner

        value = getattr(action_owner, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

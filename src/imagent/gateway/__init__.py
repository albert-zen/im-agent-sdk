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

from ..applications.capabilities import ProjectMode
from ..applications.contract import (
    AgentApplicationAdapter,
    AgentInput,
    ApplicationSummary,
    ThreadRef,
)
from ..contracts import (
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    CreateThread,
    GetProject,
    GetThread,
    ObserveThread,
    ProjectRead,
    RequestDuplicateError,
    RequestResolvedError,
    RequestResponded,
    RequestResponseRouted,
    RequestStaleError,
    RespondRequest,
    RespondToRequest,
    ThreadCreated,
    ThreadObserved,
    ThreadRead,
    validate_application_operation,
    validate_application_operation_result,
    validate_request_response,
)
from ..contracts.validators import derive_client_message_id
from ..interaction.channels.contract import ChannelAdapter, InboundAdmission
from ..interaction.controllers import ControllerActions, ControllerLifecycle
from ..interaction.controllers.contract import (
    CommandInvocationFacts,
    _derive_command_invocation_id,
)
from ..interaction.diagnostics import QueueDiagnosticFacts, QueueDiagnosticName
from ..interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
    TextFormat,
)
from ..interaction.operations import (
    ContractError,
    ContractViolation,
    OperationErrorCode,
    operation_error,
)
from ..keyed_locks import KeyedLockCapacityError, KeyedLockRegistry
from ..projection_runtime import ThreadProjectionRuntime
from ..projections import ProjectionWorkerHealth, RetryableDeliveryError
from .admission import (
    ClaimedInbound,
    InboundAdmissionService,
    inbound_idempotency_identity,
    start_channel_with_admission,
)
from .composition import GatewayExtensions, GatewayLimits, GatewayRepositories
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
    collect_application_diagnostics,
    collect_channel_diagnostics,
    new_diagnostics_snapshot,
    summarize_projection_health,
)
from .input import InboundContentTransformer as InboundContentTransformer
from .input.content_transformation import InboundContentTransformRuntime
from .input.failure_presentation import InboundFailurePhase as InboundFailurePhase
from .input.failure_presentation import InboundFailurePresentationRuntime, handle_claimed_inbound
from .input.failure_presentation import InboundFailurePresenter as InboundFailurePresenter
from .lifecycle import (
    GatewayNotRunning,
    GatewayStartupAdmission,
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
    RequestCorrelationConflict,
)
from .persistence.state_contracts import (
    ConversationBinding,
    DeliverySubmissionState,
    ProjectionPolicy,
    RequestRouteState,
)
from .presentation import (
    OutboundPresentationContext,
    OutboundPresentationRuntime,
)
from .presentation import (
    OutboundPresentationPolicy as OutboundPresentationPolicy,
)
from .presentation import (
    ProjectionPresentationOrigin as ProjectionPresentationOrigin,
)
from .routing.bindings import (
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationThread,
    ConversationBound,
)
from .routing.operations import (
    ApplicationsListed as ApplicationsListed,
)
from .routing.operations import (
    GatewayOperation,
    GatewayOperationFailed,
    GatewayOperationResult,
    ListApplications,
    SelectApplication,
    _GatewayActionError,
    _GatewayOperationExecutor,
)
from .routing.operations import (
    GatewayOperationType as GatewayOperationType,
)
from .routing.operations import (
    validate_gateway_operation as validate_gateway_operation,
)
from .routing.operations import (
    validate_gateway_operation_result as validate_gateway_operation_result,
)

logger = logging.getLogger(__name__)


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
        projection_policy: ProjectionPolicy = ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
    ) -> None:
        self._channels = {channel.channel_instance_id: channel for channel in channels}
        self._applications = {
            application.summary.ref.application_instance_id: application
            for application in applications
        }
        self._bindings = repositories.bindings
        self._projection_policy = projection_policy
        self._idempotency = repositories.idempotency or InMemoryIdempotencyRepository()
        self._request_correlations = (
            repositories.request_correlations or InMemoryRequestCorrelationRepository()
        )
        self._delivery_coordinator = delivery_coordinator or DeliveryCoordinator()
        self._controller = extensions.controller
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
        self._request_locks = KeyedLockRegistry()
        self._outbound_deliveries: dict[
            tuple[str, str],
            asyncio.Task[IdempotencyClaimStatus],
        ] = {}
        self._starting = False
        self._accepting_inbound = False
        self._startup_admission = GatewayStartupAdmission[ClaimedInbound](
            max_pending=limits.startup_buffer_max_pending
        )
        projection_repository = repositories.projections or InMemoryProjectionRouteRepository()
        self._projection_runtime = ThreadProjectionRuntime(
            applications=self._applications,
            bindings=repositories.bindings,
            projections=projection_repository,
            request_correlations=self._request_correlations,
            request_presenter=extensions.request_presenter,
            projection_policy=projection_policy,
            execute_application=self.execute_application,
            deliver_outbound=self._deliver_outbound,
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
            turn_acceptance_event_max_pending=limits.turn_acceptance_event_max_pending,
            subscription_retry_initial_seconds=limits.subscription_retry_initial_seconds,
            subscription_retry_max_seconds=limits.subscription_retry_max_seconds,
            turn_correlation_retention_seconds=limits.turn_correlation_retention_seconds,
            request_correlation_retention_seconds=(limits.request_correlation_retention_seconds),
        )
        self._gateway_operations = _GatewayOperationExecutor(
            delegates=self,
            max_active_conversation_keys=limits.conversation_serialization_max_active_keys,
            contract_error=_contract_error,
        )
        self._conversation_locks = self._gateway_operations.conversation_locks
        delivery_submissions = repositories.delivery_submissions
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
        self._delivery_coordinator.start()
        if self._delivery_outcome_observer_runtime is not None:
            self._delivery_outcome_observer_runtime.start()
        self._starting = True
        self._accepting_inbound = True
        self._startup_admission.reset()
        started_applications: list[AgentApplicationAdapter] = []
        started_channels: list[ChannelAdapter] = []
        try:
            await self._projection_runtime.cleanup_stale_correlations()
            restart_open_requests = await self._projection_runtime.open_request_refs()
            await self._projection_runtime.restore()
            for application in self._applications.values():
                await application.start()
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
                    try:
                        await channel.stop()
                    except BaseException as stop_error:
                        start_error.add_note(
                            f"Channel cleanup after startup failure also failed: {stop_error!r}"
                        )
                        logger.exception(
                            "Channel cleanup after startup failure failed",
                            exc_info=stop_error,
                        )
                    raise
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
                        f"Gateway startup rollback: {release_error!r}"
                    )
            self._startup_admission.clear()
            await self._projection_runtime.stop()
            if self._outbound_presentation_runtime is not None:
                await self._outbound_presentation_runtime.close()
            await self._delivery_coordinator.close()
            for channel in reversed(started_channels):
                await channel.stop()
            if isinstance(self._controller, ControllerLifecycle):
                await self._controller.close()
            if self._inbound_content_transform_runtime is not None:
                await self._inbound_content_transform_runtime.close()
            if self._inbound_failure_presentation_runtime is not None:
                await self._inbound_failure_presentation_runtime.close()
            if self._delivery_outcome_observer_runtime is not None:
                await self._delivery_outcome_observer_runtime.close()
            for application in reversed(started_applications):
                await application.stop()
            raise

    async def stop(self) -> None:
        self._accepting_inbound = False
        self._starting = False
        await self._projection_runtime.stop()
        if self._outbound_presentation_runtime is not None:
            await self._outbound_presentation_runtime.close()
        await self._delivery_coordinator.close()
        for channel in reversed(tuple(self._channels.values())):
            await channel.stop()
        if isinstance(self._controller, ControllerLifecycle):
            await self._controller.close()
        if self._inbound_content_transform_runtime is not None:
            await self._inbound_content_transform_runtime.close()
        if self._inbound_failure_presentation_runtime is not None:
            await self._inbound_failure_presentation_runtime.close()
        if self._delivery_outcome_observer_runtime is not None:
            await self._delivery_outcome_observer_runtime.close()
        for application in reversed(tuple(self._applications.values())):
            await application.stop()

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
        return await self._gateway_operations.execute(operation)

    async def get_binding(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None:
        return await self._bindings.get(conversation_ref)

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
        operation: GatewayOperation,
    ) -> GatewayOperationResult:
        return await self._gateway_operations.execute_locked(operation)

    def _list_applications(
        self,
        operation: ListApplications,
        *,
        completed_at: datetime,
    ) -> tuple[ApplicationSummary, ...]:
        del operation, completed_at
        return tuple(application.summary for application in self._applications.values())

    async def _select_application(
        self,
        operation: SelectApplication,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        self._require_application(operation.application_ref.application_instance_id)
        previous = await self._bindings.get(operation.conversation_ref)
        binding = await self._bindings.put(
            ConversationBinding(
                conversation_ref=operation.conversation_ref,
                application_ref=operation.application_ref,
            ),
            expected_revision=operation.expected_revision,
        )
        await self._projection_runtime.handle_binding_change(previous, binding)
        return ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=completed_at,
            binding=binding,
        )

    async def _bind_conversation_to_project(
        self,
        operation: BindConversationToProject,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
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
        previous = await self._bindings.get(operation.conversation_ref)
        binding = await self._bindings.put(
            ConversationBinding(
                conversation_ref=operation.conversation_ref,
                application_ref=application.summary.ref,
                project_ref=read.project.ref,
            ),
            expected_revision=operation.expected_revision,
        )
        await self._projection_runtime.handle_binding_change(previous, binding)
        return ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=completed_at,
            binding=binding,
        )

    async def _bind_conversation_to_thread(
        self,
        operation: BindConversationToThread,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
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
        previous = await self._bindings.get(operation.conversation_ref)
        if self._projection_policy is ProjectionPolicy.FOREGROUND_ONLY:
            same_target = (
                previous is not None
                and previous.application_ref == application.summary.ref
                and previous.project_ref == read.thread.ref.project_ref
                and previous.thread_ref == read.thread.ref
            )
            if (
                same_target
                and previous is not None
                and operation.expected_revision
                not in {None, previous.revision, previous.revision - 1}
            ):
                raise BindingConflict(
                    "same-target bind retry does not match the current "
                    "or immediately preceding revision"
                )
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
                if same_target:
                    assert previous is not None
                    retain_prepared_barrier = True
                    await self._projection_runtime.handle_binding_change(
                        None,
                        previous,
                        require_checkpoint=require_checkpoint,
                    )
                    return ConversationBound(
                        operation_id=operation.operation_id,
                        type=operation.type,
                        completed_at=completed_at,
                        binding=previous,
                    )
                desired_binding = ConversationBinding(
                    conversation_ref=operation.conversation_ref,
                    application_ref=application.summary.ref,
                    project_ref=read.thread.ref.project_ref,
                    thread_ref=read.thread.ref,
                )
                try:
                    binding = await self._bindings.put(
                        desired_binding,
                        expected_revision=operation.expected_revision,
                    )
                except BaseException as error:
                    retain_prepared_barrier = True
                    try:
                        current = await self._bindings.get(operation.conversation_ref)
                    except BaseException as verification_error:
                        error.add_note(
                            "Binding outcome verification also failed; the prepared "
                            f"route remains fenced: {verification_error!r}"
                        )
                    else:
                        retain_prepared_barrier = (
                            current is not None
                            and current.application_ref == desired_binding.application_ref
                            and current.project_ref == desired_binding.project_ref
                            and current.thread_ref == desired_binding.thread_ref
                        )
                    raise
                retain_prepared_barrier = True
                await self._projection_runtime.handle_binding_change(
                    previous,
                    binding,
                    require_checkpoint=require_checkpoint,
                )
                return ConversationBound(
                    operation_id=operation.operation_id,
                    type=operation.type,
                    completed_at=completed_at,
                    binding=binding,
                )
            finally:
                if not retain_prepared_barrier:
                    self._projection_runtime.complete_foreground_binding_route(
                        prepared_route.route_id
                    )
        binding = await self._bindings.put(
            ConversationBinding(
                conversation_ref=operation.conversation_ref,
                application_ref=application.summary.ref,
                project_ref=read.thread.ref.project_ref,
                thread_ref=read.thread.ref,
            ),
            expected_revision=operation.expected_revision,
        )
        await self._projection_runtime.handle_binding_change(previous, binding)
        return ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=completed_at,
            binding=binding,
        )

    async def _clear_conversation_thread(
        self,
        operation: ClearConversationThread,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
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
        await self._projection_runtime.handle_binding_change(current, binding)
        return ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=completed_at,
            binding=binding,
        )

    async def _observe_thread(
        self,
        operation: ObserveThread,
        *,
        completed_at: datetime,
    ) -> ThreadObserved:
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
        route = await self._projection_runtime.observe_thread(
            application,
            read.thread.ref,
            operation.conversation_ref,
            reply_to_message_id=operation.reply_to_message_id,
        )
        return ThreadObserved(
            operation_id=operation.operation_id,
            completed_at=completed_at,
            route=route,
        )

    async def _route_request_response(
        self,
        operation: RespondToRequest,
        *,
        completed_at: datetime,
    ) -> RequestResponseRouted:
        async with self._request_locks.hold(operation.request_ref):
            return await self._respond_to_request(
                operation,
                completed_at=completed_at,
            )

    async def _respond_to_request(
        self,
        operation: RespondToRequest,
        *,
        completed_at: datetime,
    ) -> RequestResponseRouted:
        correlations = await self._request_correlations.list_request_correlations(
            request_ref=operation.request_ref
        )
        if not correlations:
            raise RequestStaleError("request is unknown, expired, or no longer answerable")
        destination = next(
            (
                correlation
                for correlation in correlations
                if correlation.conversation_ref == operation.conversation_ref
            ),
            None,
        )
        if destination is None:
            raise _GatewayActionError(
                ContractError(
                    code=OperationErrorCode.UNAUTHORIZED_DESTINATION.value,
                    message="this Conversation did not receive the request",
                )
            )
        if destination.state is RequestRouteState.RESPONDED:
            raise RequestDuplicateError("request already has a submitted response")
        if destination.state is RequestRouteState.RESOLVED:
            raise RequestResolvedError("request is already resolved")
        if destination.state is RequestRouteState.STALE:
            raise RequestStaleError("request response handle is stale")
        now = datetime.now(UTC)
        if destination.expires_at is not None and destination.expires_at <= now:
            await self._transition_request_state(
                operation,
                state=RequestRouteState.STALE,
                expected_states=(RequestRouteState.OPEN,),
                updated_at=now,
            )
            raise RequestStaleError("request has expired")
        validate_request_response(operation.response, destination.response_shape)
        application = self._require_application(
            operation.request_ref.application_ref.application_instance_id
        )
        native = await self.execute_application(
            RespondRequest(
                operation_id=f"{operation.operation_id}:request.respond",
                application_ref=application.summary.ref,
                request_ref=operation.request_ref,
                response=operation.response,
                thread_ref=destination.thread_ref,
                created_at=operation.created_at,
            )
        )
        if isinstance(native, ApplicationOperationFailed):
            await self._converge_native_request_failure(operation, native)
            raise _GatewayActionError(native.error)
        if not isinstance(native, RequestResponded):
            raise RuntimeError("request.respond returned an incompatible result")
        try:
            await self._transition_request_state(
                operation,
                state=RequestRouteState.RESPONDED,
                expected_states=(RequestRouteState.OPEN,),
                updated_at=completed_at,
            )
        except RequestCorrelationConflict:
            current = await self._request_correlations.list_request_correlations(
                request_ref=operation.request_ref
            )
            if not current or any(
                correlation.state
                not in {
                    RequestRouteState.RESPONDED,
                    RequestRouteState.RESOLVED,
                }
                for correlation in current
            ):
                raise
        return RequestResponseRouted(
            operation_id=operation.operation_id,
            request_ref=operation.request_ref,
            completed_at=completed_at,
        )

    async def _converge_native_request_failure(
        self,
        operation: RespondToRequest,
        result: ApplicationOperationFailed,
    ) -> None:
        target = {
            OperationErrorCode.REQUEST_DUPLICATE.value: RequestRouteState.RESPONDED,
            OperationErrorCode.REQUEST_RESOLVED.value: RequestRouteState.RESOLVED,
            OperationErrorCode.REQUEST_STALE.value: RequestRouteState.STALE,
        }.get(result.error.code)
        if target is None:
            return
        try:
            await self._transition_request_state(
                operation,
                state=target,
                expected_states=(RequestRouteState.OPEN,),
                updated_at=result.completed_at,
            )
        except (KeyError, RequestCorrelationConflict):
            pass

    async def _transition_request_state(
        self,
        operation: RespondToRequest,
        *,
        state: RequestRouteState,
        expected_states: tuple[RequestRouteState, ...],
        updated_at: datetime,
    ):
        return await self._request_correlations.transition_request_correlations(
            operation.request_ref,
            expected_states=expected_states,
            state=state,
            updated_at=updated_at,
        )

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
                        f"startup admission: {release_error!r}"
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
            thread_was_created = False
            if self._controller is not None:
                outputs = await self._controller.handle(
                    message,
                    _LockedControllerActions(
                        self,
                        message=message,
                        enter_effect_fence=before_application_send,
                    ),
                )
                if outputs is not None:
                    for output in outputs:
                        if output.conversation_ref != message.conversation_ref:
                            raise ValueError(
                                "Controller output belongs to a different Conversation"
                            )
                        await self._deliver_outbound(output)
                    return
            content = (
                await self._inbound_content_transform_runtime.transform(message)
                if self._inbound_content_transform_runtime is not None
                else message.content
            )
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
                thread_was_created = True
            thread_ref = binding.thread_ref
            if thread_ref is None:
                raise RuntimeError("thread binding was not established")
            await self._projection_runtime.prepare_input_route(
                application,
                thread_ref,
                message.conversation_ref,
                thread_was_created=thread_was_created,
            )
            client_message_id = derive_client_message_id(
                message.conversation_ref,
                message.message_id,
            )
            await self._projection_runtime.send_input(
                application,
                thread_ref,
                AgentInput(
                    client_message_id=client_message_id,
                    content=content,
                    sender=message.sender,
                ),
                conversation_ref=message.conversation_ref,
                reply_to_message_id=message.message_id,
                before_application_send=before_application_send,
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
        if presentation_context is not None and self._outbound_presentation_runtime is not None:
            try:
                presented = await self._outbound_presentation_runtime.present(
                    message,
                    presentation_context,
                )
            except asyncio.CancelledError:
                await self._idempotency.release(
                    scope,
                    message.delivery_id,
                    owner_token=owner_token,
                )
                raise
            except BaseException as error:
                await self._idempotency.release(
                    scope,
                    message.delivery_id,
                    owner_token=owner_token,
                )
                raise RetryableDeliveryError(
                    "outbound presentation failed before Channel side effect"
                ) from error
            if presented is None:
                await self._idempotency.complete(
                    scope,
                    message.delivery_id,
                    owner_token=owner_token,
                )
                return IdempotencyClaimStatus.ACQUIRED
            message = presented
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
            raise RuntimeError(
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
    def __init__(
        self,
        gateway: ImAgentGateway,
        *,
        message: InboundMessage,
        enter_effect_fence: Callable[[], Awaitable[None]],
    ) -> None:
        self._gateway = gateway
        self._message = message
        self._enter_effect_fence = enter_effect_fence
        self._effect_fence_entered = False

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

    async def enter_effectful_command(
        self,
        invocation: CommandInvocationFacts,
    ) -> None:
        if self._effect_fence_entered:
            raise RuntimeError("effectful command fence may be entered only once")
        message = self._message
        if (
            invocation.conversation_ref != message.conversation_ref
            or invocation.message_id != message.message_id
            or invocation.actor != message.sender
            or invocation.created_at != message.created_at
            or invocation.invocation_id
            != _derive_command_invocation_id(
                invocation.conversation_ref,
                invocation.message_id,
                invocation.command_name,
                invocation.arguments,
            )
        ):
            raise ValueError("effectful command invocation does not match owned inbound identity")
        self._effect_fence_entered = True
        await self._enter_effect_fence()


def _contract_error(error: Exception) -> ContractError:
    if isinstance(error, BindingConflict):
        return operation_error(error, code=OperationErrorCode.CONFLICT)
    if isinstance(error, KeyedLockCapacityError):
        return ContractError(
            code=OperationErrorCode.CAPACITY_EXHAUSTED.value,
            message=str(error),
            retryable=True,
            metadata={"native_exception": type(error).__name__},
        )
    return operation_error(error)


def _operation_id(message: InboundMessage, operation_type: str) -> str:
    return f"imagent:operation:{message.message_id}:{operation_type}"


def __getattr__(name: str) -> object:
    if name == "DeliveryAuthorizer":
        from .delivery.proactive_authorization import DeliveryAuthorizer

        globals()[name] = DeliveryAuthorizer
        return DeliveryAuthorizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

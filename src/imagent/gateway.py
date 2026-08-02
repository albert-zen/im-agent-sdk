from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

from .adapters import (
    AgentApplicationAdapter,
    BindingRepository,
    ChannelAdapter,
    DeliveryAuthorizer,
    DeliveryOutcomeObserver,
    DeliverySubmissionConflict,
    DeliverySubmissionRepository,
    IdempotencyClaimStatus,
    IdempotencyRepository,
    InboundAdmission,
    OutboundPresentationPolicy,
    ProjectionRouteRepository,
    RequestCorrelationConflict,
    RequestCorrelationRepository,
)
from .bindings import BindingConflict
from .contracts import (
    AgentInput,
    ApplicationInputOutcomeUnknown,
    ApplicationOperation,
    ApplicationOperationFailed,
    ApplicationOperationResult,
    ApplicationsListed,
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationThread,
    ContractError,
    ContractViolation,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    CreateThread,
    DeliveryIntent,
    DeliverySubmissionState,
    DeliveryTarget,
    GatewayOperation,
    GatewayOperationFailed,
    GatewayOperationResult,
    GetProject,
    GetThread,
    InboundMessage,
    ListApplications,
    ObserveThread,
    OperationErrorCode,
    OutboundMessage,
    ProactiveDeliveryResult,
    ProjectionPolicy,
    ProjectMode,
    ProjectRead,
    RequestDuplicateError,
    RequestResolvedError,
    RequestResponded,
    RequestResponseRouted,
    RequestRouteState,
    RequestStaleError,
    RespondRequest,
    RespondToRequest,
    SelectApplication,
    TextContent,
    TextFormat,
    ThreadCreated,
    ThreadObserved,
    ThreadRead,
    ThreadRef,
    derive_client_message_id,
    operation_error,
    validate_application_operation,
    validate_application_operation_result,
    validate_gateway_operation,
    validate_gateway_operation_result,
    validate_request_response,
)
from .controllers import ControllerActions, InboundController, RequestPresenter
from .delivery_coordination import DeliveryCoordinator
from .delivery_planning import DeliveryPlanningError
from .diagnostics import (
    DiagnosticsSnapshot,
    GatewayDiagnosticFacts,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
    collect_application_diagnostics,
    collect_channel_diagnostics,
    new_diagnostics_snapshot,
    summarize_projection_health,
)
from .gateway_startup import (
    GatewayNotRunning,
    GatewayStartupAdmission,
)
from .inbound_admission import (
    ClaimedInbound,
    InboundAdmissionService,
    inbound_idempotency_identity,
    start_channel_with_admission,
)
from .keyed_locks import KeyedLockRegistry
from .outbound_presentation import present_outbound
from .proactive_delivery import (
    InMemoryDeliverySubmissionRepository,
    ProactiveDeliveryService,
)
from .projection_runtime import InputPostAcceptanceError, ThreadProjectionRuntime
from .projections import (
    InMemoryProjectionRouteRepository,
    ProjectionWorkerHealth,
    RetryableDeliveryError,
)
from .request_correlations import InMemoryRequestCorrelationRepository
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
        delivery_submissions: DeliverySubmissionRepository | None = None,
        delivery_authorizer: DeliveryAuthorizer | None = None,
        delivery_coordinator: DeliveryCoordinator | None = None,
        delivery_outcome_observer: DeliveryOutcomeObserver | None = None,
        projections: ProjectionRouteRepository | None = None,
        request_correlations: RequestCorrelationRepository | None = None,
        outbound_presentation: OutboundPresentationPolicy | None = None,
        projection_policy: ProjectionPolicy = ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
        controller: InboundController | None = None,
        request_presenter: RequestPresenter | None = None,
        baseline_history_limit: int = 3,
        recovery_history_page_size: int = 10,
        recovery_max_pages: int = 5,
        catchup_limit: int = 10,
        projection_item_limit: int = 20,
        request_delivery_max_pending: int = 256,
        startup_buffer_max_pending: int = 256,
        turn_acceptance_event_max_pending: int = 256,
        subscription_retry_initial_seconds: float = 0.05,
        subscription_retry_max_seconds: float = 2.0,
        turn_correlation_retention_seconds: float = 7 * 24 * 60 * 60,
        request_correlation_retention_seconds: float = 7 * 24 * 60 * 60,
    ) -> None:
        self._channels = {channel.channel_instance_id: channel for channel in channels}
        self._applications = {
            application.summary.ref.application_instance_id: application
            for application in applications
        }
        self._bindings = bindings
        self._idempotency = idempotency or InMemoryIdempotencyRepository()
        self._request_correlations = request_correlations or InMemoryRequestCorrelationRepository()
        self._delivery_coordinator = delivery_coordinator or DeliveryCoordinator()
        self._controller = controller
        self._outbound_presentation = outbound_presentation
        self._locks: dict[object, asyncio.Lock] = {}
        self._request_locks = KeyedLockRegistry()
        self._outbound_deliveries: dict[
            tuple[str, str],
            asyncio.Task[IdempotencyClaimStatus],
        ] = {}
        self._starting = False
        self._accepting_inbound = False
        self._startup_admission = GatewayStartupAdmission[ClaimedInbound | GatewayOperation](
            max_pending=startup_buffer_max_pending
        )
        projection_repository = projections or InMemoryProjectionRouteRepository()
        self._projection_runtime = ThreadProjectionRuntime(
            applications=self._applications,
            bindings=bindings,
            projections=projection_repository,
            request_correlations=self._request_correlations,
            request_presenter=request_presenter,
            projection_policy=projection_policy,
            execute_application=self.execute_application,
            deliver_outbound=self._deliver_outbound,
            deliver_request_outbound=partial(
                self._deliver_outbound,
                cancellable=True,
            ),
            baseline_history_limit=baseline_history_limit,
            recovery_history_page_size=recovery_history_page_size,
            recovery_max_pages=recovery_max_pages,
            catchup_limit=catchup_limit,
            projection_item_limit=projection_item_limit,
            request_delivery_max_pending=request_delivery_max_pending,
            turn_acceptance_event_max_pending=turn_acceptance_event_max_pending,
            subscription_retry_initial_seconds=subscription_retry_initial_seconds,
            subscription_retry_max_seconds=subscription_retry_max_seconds,
            turn_correlation_retention_seconds=turn_correlation_retention_seconds,
            request_correlation_retention_seconds=request_correlation_retention_seconds,
        )
        self._delivery_service = ProactiveDeliveryService(
            channels=self._channels,
            submissions=delivery_submissions or InMemoryDeliverySubmissionRepository(),
            resolve_thread_routes=self._projection_runtime.active_routes,
            authorizer=delivery_authorizer,
            coordinator=self._delivery_coordinator,
            outcome_observer=delivery_outcome_observer,
        )
        self._inbound_admission = InboundAdmissionService(
            self._idempotency,
            self._handle_claimed_message_entry,
        )

    async def start(self) -> None:
        self._delivery_coordinator.start()
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
                        self._handle_operation_entry,
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
                if isinstance(entry, ClaimedInbound):
                    await self._handle_claimed_message(entry)
                else:
                    await self._handle_operation(entry)
                self._startup_admission.raise_if_overflowed()
            self._starting = False
        except BaseException as error:
            self._accepting_inbound = False
            self._starting = False
            while self._startup_admission:
                entry = self._startup_admission.popleft()
                if not isinstance(entry, ClaimedInbound):
                    continue
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
            await self._delivery_coordinator.close()
            for channel in reversed(started_channels):
                await channel.stop()
            for application in reversed(started_applications):
                await application.stop()
            raise

    async def stop(self) -> None:
        self._accepting_inbound = False
        self._starting = False
        await self._projection_runtime.stop()
        await self._delivery_coordinator.close()
        for channel in reversed(tuple(self._channels.values())):
            await channel.stop()
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
        lock = self._locks.setdefault(operation.conversation_ref, asyncio.Lock())
        async with lock:
            return await self._execute_gateway_locked(operation)

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
            previous = await self._bindings.get(operation.conversation_ref)
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
        if isinstance(operation, ObserveThread):
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
        if isinstance(operation, RespondToRequest):
            async with self._request_locks.hold(operation.request_ref):
                return await self._respond_to_request(
                    operation,
                    completed_at=completed_at,
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
            await self._projection_runtime.handle_binding_change(current, binding)
            return ConversationBound(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=completed_at,
                binding=binding,
            )
        raise NotImplementedError(operation.type.value)

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
        try:
            await self._process_message(
                claimed.message,
                idempotency_owner_token=claimed.owner_token,
            )
        except InputPostAcceptanceError as exc:
            # The native Application already accepted the Turn. Redelivery is
            # unsafe when the Application has no native input-idempotency key.
            try:
                await self._idempotency.complete(
                    claimed.scope,
                    claimed.key,
                    owner_token=claimed.owner_token,
                )
            except BaseException as terminal_error:
                raise terminal_error from exc.cause
            raise exc.cause.with_traceback(exc.cause.__traceback__) from None
        except ApplicationInputOutcomeUnknown as exc:
            # Dispatch crossed the native side-effect boundary without a
            # definitive outcome. Keep the protected claim sticky.
            raise exc.cause.with_traceback(exc.cause.__traceback__) from None
        except BaseException:
            await self._idempotency.release(
                claimed.scope,
                claimed.key,
                owner_token=claimed.owner_token,
            )
            raise
        await self._idempotency.complete(
            claimed.scope,
            claimed.key,
            owner_token=claimed.owner_token,
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
    ) -> None:
        lock = self._locks.setdefault(message.conversation_ref, asyncio.Lock())
        async with lock:
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
                    content=message.content,
                    sender=message.sender,
                ),
                conversation_ref=message.conversation_ref,
                reply_to_message_id=message.message_id,
                before_application_send=partial(
                    self._idempotency.mark_side_effect_started,
                    scope,
                    key,
                    owner_token=idempotency_owner_token,
                ),
            )

    async def _handle_operation(self, operation: GatewayOperation) -> None:
        result = await self.execute_gateway(operation)
        if isinstance(result, GatewayOperationFailed):
            logger.warning(
                "Gateway operation %s failed: %s",
                operation.operation_id,
                result.error.message,
            )

    async def _handle_operation_entry(
        self,
        operation: GatewayOperation,
    ) -> None:
        if self._starting:
            self._startup_admission.admit(operation)
            return
        if not self._accepting_inbound:
            raise GatewayNotRunning("gateway is not accepting Channel callbacks")
        await self._handle_operation(operation)

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
            self._deliver_outbound_once(message, scope=scope),
            name=f"imagent-outbound:{message.delivery_id}",
        )
        self._outbound_deliveries[key] = task
        try:
            return await task if cancellable else await asyncio.shield(task)
        finally:
            if self._outbound_deliveries.get(key) is task:
                self._outbound_deliveries.pop(key, None)

    async def _deliver_outbound_once(
        self,
        message: OutboundMessage,
        *,
        scope: str,
    ) -> IdempotencyClaimStatus:
        owner_token = uuid4().hex
        claim = await self._idempotency.claim(
            scope,
            message.delivery_id,
            owner_token=owner_token,
        )
        if claim is not IdempotencyClaimStatus.ACQUIRED:
            return claim
        try:
            policy = self._outbound_presentation
            presented = await present_outbound(
                policy, message, self._idempotency, scope, owner_token
            )
            if presented is None:
                return IdempotencyClaimStatus.ACQUIRED
            result = await self._delivery_service.deliver_internal(presented)
        except (
            ContractViolation,
            DeliveryPlanningError,
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

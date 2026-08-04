from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .gateway.delivery.proactive_authorization import (
        DeliveryAuthorizer as DeliveryAuthorizer,
    )

from .applications.events import AgentEvent
from .contracts import (
    AcceptedTurn,
    AgentInput,
    ApplicationInputDispatch,
    ApplicationOperation,
    ApplicationOperationResult,
    ApplicationSummary,
    ConversationBinding,
    DeliveryReservation,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    InputContinuationPreference,
    InteractiveRequest,
    RequestRef,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    ThreadRef,
    TurnReplyCorrelation,
)
from .interaction.channels import (
    ChannelAdapter as ChannelAdapter,
)
from .interaction.channels import (
    ChannelStartupConfigurationValidator as ChannelStartupConfigurationValidator,
)
from .interaction.channels import (
    InboundAdmission as InboundAdmission,
)
from .interaction.channels import (
    InboundAdmissionHandler as InboundAdmissionHandler,
)
from .interaction.channels import (
    MessageHandler as MessageHandler,
)
from .interaction.messages import ConversationRef

ApplicationInputDispatchHandler = Callable[[ApplicationInputDispatch], Awaitable[None]]


class IdempotencyClaimStatus(StrEnum):
    ACQUIRED = "acquired"
    ALREADY_COMPLETED = "already_completed"
    IN_FLIGHT = "in_flight"


class ProjectionCheckpointConflict(RuntimeError):
    """The stored route checkpoint no longer matches the caller's expectation."""


class ProjectionRouteConflict(RuntimeError):
    """A stable route ID was reused for different immutable endpoints."""


class RequestCorrelationConflict(RuntimeError):
    """A request route correlation changed outside the expected state."""


class TurnReplyCorrelationConflict(RuntimeError):
    """A Thread/Turn correlation was reused for a different IM destination."""


class DeliverySubmissionConflict(RuntimeError):
    """A stable delivery ID was reused for a different immutable submission."""


class DeliverySubmissionCapacityError(RuntimeError):
    """A new process-local delivery identity exceeded its finite record bound."""


class AgentApplicationAdapter(Protocol):
    @property
    def summary(self) -> ApplicationSummary: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def execute(
        self,
        operation: ApplicationOperation,
    ) -> ApplicationOperationResult: ...

    async def send_input(
        self,
        thread_ref: ThreadRef,
        message: AgentInput,
        *,
        continuation: InputContinuationPreference = (
            InputContinuationPreference.PREFER_ACTIVE_TURN
        ),
        before_dispatch: ApplicationInputDispatchHandler | None = None,
    ) -> AcceptedTurn: ...

    async def list_pending_requests(self) -> tuple[InteractiveRequest, ...]: ...

    def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor: str | None = None,
    ) -> AsyncIterator[AgentEvent]: ...


class BindingRepository(Protocol):
    async def get(
        self,
        conversation: ConversationRef,
    ) -> ConversationBinding | None: ...

    async def put(
        self,
        binding: ConversationBinding,
        expected_revision: int | None = None,
    ) -> ConversationBinding: ...

    async def delete(
        self,
        conversation: ConversationRef,
        expected_revision: int | None = None,
    ) -> None: ...


class ProjectionRouteRepository(Protocol):
    async def list_projection_routes(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[ThreadProjectionRoute, ...]: ...

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute: ...

    async def replace_thread_projection_routes(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute: ...

    async def advance_projection_checkpoint(
        self,
        route_id: str,
        *,
        expected_agent_item_id: str | None,
        agent_item_id: str,
        checkpointed_at: datetime,
    ) -> ThreadProjectionRoute: ...

    async def delete_projection_routes(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef | None = None,
    ) -> int: ...

    async def get_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> TurnReplyCorrelation | None: ...

    async def list_turn_reply_correlations(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[TurnReplyCorrelation, ...]: ...

    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation: ...

    async def delete_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> bool: ...

    async def delete_turn_reply_correlations(
        self,
        *,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int: ...


class IdempotencyRepository(Protocol):
    async def claim(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> IdempotencyClaimStatus: ...

    async def mark_side_effect_started(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None: ...

    async def refresh(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None: ...

    async def complete(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None: ...

    async def release(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None: ...


class DeliverySubmissionRepository(Protocol):
    async def get_delivery_submission(
        self,
        submission_id: str,
    ) -> DeliverySubmissionRecord | None: ...

    async def reserve_delivery_submission(
        self,
        record: DeliverySubmissionRecord,
    ) -> DeliveryReservation: ...

    async def update_delivery_destination(
        self,
        submission_id: str,
        destination_delivery_id: str,
        *,
        expected_state: DeliverySubmissionState,
        destination: DestinationDeliveryRecord,
    ) -> DeliverySubmissionRecord: ...


class RequestCorrelationRepository(Protocol):
    async def list_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
    ) -> tuple[RequestRouteCorrelation, ...]: ...

    async def put_request_correlation(
        self,
        correlation: RequestRouteCorrelation,
    ) -> RequestRouteCorrelation: ...

    async def transition_request_correlations(
        self,
        request_ref: RequestRef,
        *,
        expected_states: tuple[RequestRouteState, ...],
        state: RequestRouteState,
        updated_at: datetime,
    ) -> tuple[RequestRouteCorrelation, ...]: ...

    async def delete_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int: ...


def __getattr__(name: str) -> object:
    if name == "DeliveryAuthorizer":
        from .gateway.delivery.proactive_authorization import (
            DeliveryAuthorizer,
        )

        globals()[name] = DeliveryAuthorizer
        return DeliveryAuthorizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

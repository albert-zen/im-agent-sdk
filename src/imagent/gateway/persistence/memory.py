from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime

from ...applications.contract import ThreadRef
from ...applications.requests import RequestRef
from ...interaction.messages import ConversationRef
from ..projection.request_correlation import (
    _matches,
    _merge_correlation,
    _reject_conflicting_endpoint,
    _require_delete_selector,
    _select_correlations,
    _transition_correlations,
)
from .repository_contracts import (
    BindingConflict,
    DeliverySubmissionCapacityError,
    DeliverySubmissionConflict,
    ProjectionCheckpointConflict,
    ProjectionRouteConflict,
    TurnReplyCorrelationConflict,
)
from .state_contracts import (
    ConversationBinding,
    DeliveryReservation,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
    validate_binding,
    validate_delivery_submission_record,
    validate_projection_route,
    validate_request_route_correlation,
    validate_turn_reply_correlation,
)
from .submission_identity import ensure_same_delivery_submission_reservation


class InMemoryBindingRepository:
    """Atomic process-local binding storage with optimistic generation checks."""

    def __init__(self) -> None:
        self._bindings: dict[ConversationRef, ConversationBinding] = {}
        self._binding_generation_floors: dict[ConversationRef, int] = {}
        self._lock = asyncio.Lock()

    async def get(self, conversation: ConversationRef) -> ConversationBinding | None:
        async with self._lock:
            return self._bindings.get(conversation)

    async def put(
        self,
        binding: ConversationBinding,
        expected_generation: int | None = None,
    ) -> ConversationBinding:
        validate_binding(binding)
        async with self._lock:
            current = self._bindings.get(binding.conversation_ref)
            current_generation = max(
                current.generation if current is not None else 0,
                self._binding_generation_floors.get(binding.conversation_ref, 0),
            )
            if expected_generation is not None and expected_generation != current_generation:
                raise BindingConflict(
                    "expected generation "
                    f"{expected_generation}, current generation is {current_generation}"
                )
            stored = ConversationBinding(
                conversation_ref=binding.conversation_ref,
                application_ref=binding.application_ref,
                project_ref=binding.project_ref,
                thread_ref=binding.thread_ref,
                generation=current_generation + 1,
                updated_at=datetime.now(UTC),
            )
            self._bindings[binding.conversation_ref] = stored
            self._binding_generation_floors[binding.conversation_ref] = stored.generation
            return stored

    async def delete(
        self,
        conversation: ConversationRef,
        expected_generation: int | None = None,
    ) -> None:
        async with self._lock:
            current = self._bindings.get(conversation)
            current_generation = max(
                current.generation if current is not None else 0,
                self._binding_generation_floors.get(conversation, 0),
            )
            if expected_generation is not None and expected_generation != current_generation:
                raise BindingConflict(
                    "expected generation "
                    f"{expected_generation}, current generation is {current_generation}"
                )
            self._bindings.pop(conversation, None)
            self._binding_generation_floors[conversation] = current_generation + 1


class InMemoryRequestCorrelationRepository:
    """Process-local destination-safe request response routing state."""

    def __init__(self) -> None:
        self._correlations: dict[str, RequestRouteCorrelation] = {}
        self._lock = asyncio.Lock()

    async def list_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
    ) -> tuple[RequestRouteCorrelation, ...]:
        async with self._lock:
            correlations = tuple(self._correlations.values())
        return _select_correlations(
            correlations,
            request_ref=request_ref,
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
        )

    async def put_request_correlation(
        self,
        correlation: RequestRouteCorrelation,
    ) -> RequestRouteCorrelation:
        validate_request_route_correlation(correlation)
        async with self._lock:
            existing = self._correlations.get(correlation.correlation_id)
            stored = _merge_correlation(
                existing,
                correlation,
                request_correlations=tuple(
                    candidate
                    for candidate in self._correlations.values()
                    if candidate.request_ref == correlation.request_ref
                ),
            )
            _reject_conflicting_endpoint(self._correlations.values(), stored)
            self._correlations[stored.correlation_id] = stored
            return stored

    async def transition_request_correlations(
        self,
        request_ref: RequestRef,
        *,
        expected_states: tuple[RequestRouteState, ...],
        state: RequestRouteState,
        updated_at: datetime,
    ) -> tuple[RequestRouteCorrelation, ...]:
        async with self._lock:
            selected = tuple(
                correlation
                for correlation in self._correlations.values()
                if correlation.request_ref == request_ref
            )
            transitioned = _transition_correlations(
                selected,
                expected_states=expected_states,
                state=state,
                updated_at=updated_at,
            )
            for correlation in transitioned:
                self._correlations[correlation.correlation_id] = correlation
            return transitioned

    async def delete_request_correlations(
        self,
        *,
        request_ref: RequestRef | None = None,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int:
        _require_delete_selector(request_ref, thread_ref, conversation_ref, older_than)
        async with self._lock:
            keys = tuple(
                correlation_id
                for correlation_id, correlation in self._correlations.items()
                if _matches(
                    correlation,
                    request_ref=request_ref,
                    thread_ref=thread_ref,
                    conversation_ref=conversation_ref,
                    older_than=older_than,
                )
            )
            for correlation_id in keys:
                self._correlations.pop(correlation_id)
            return len(keys)


def _same_turn_reply_correlation(
    left: TurnReplyCorrelation,
    right: TurnReplyCorrelation,
) -> bool:
    return (
        left.correlation_id == right.correlation_id
        and left.turn_ref == right.turn_ref
        and left.client_message_id == right.client_message_id
        and left.conversation_ref == right.conversation_ref
        and left.reply_to_message_id == right.reply_to_message_id
    )


def _merge_projection_route(
    existing: ThreadProjectionRoute | None,
    replacement: ThreadProjectionRoute,
) -> ThreadProjectionRoute:
    if existing is None:
        return replacement
    if (
        existing.route_id != replacement.route_id
        or existing.thread_ref != replacement.thread_ref
        or existing.conversation_ref != replacement.conversation_ref
    ):
        raise ProjectionRouteConflict(
            f"route ID belongs to different endpoints: {replacement.route_id}"
        )
    if replacement.checkpoint_agent_item_id is not None:
        if (
            replacement.checkpoint_agent_item_id != existing.checkpoint_agent_item_id
            or replacement.checkpointed_at != existing.checkpointed_at
        ):
            raise ProjectionCheckpointConflict(
                f"route refresh cannot change checkpoint: {replacement.route_id}"
            )
        return replacement
    if existing.checkpoint_agent_item_id is None:
        return replacement
    return replace(
        replacement,
        checkpoint_agent_item_id=existing.checkpoint_agent_item_id,
        checkpointed_at=existing.checkpointed_at,
    )


def _reject_conflicting_route_id(
    routes: Iterable[ThreadProjectionRoute],
    replacement: ThreadProjectionRoute,
) -> None:
    for existing in routes:
        if existing.route_id == replacement.route_id and (
            existing.thread_ref != replacement.thread_ref
            or existing.conversation_ref != replacement.conversation_ref
        ):
            raise ProjectionRouteConflict(
                f"route ID belongs to different endpoints: {replacement.route_id}"
            )


class InMemoryProjectionRouteRepository:
    """Process-local minimal Thread-to-Conversation delivery routing."""

    def __init__(self) -> None:
        self._routes: dict[
            tuple[ThreadRef, ConversationRef],
            ThreadProjectionRoute,
        ] = {}
        self._turn_correlations: dict[
            tuple[ThreadRef, str],
            TurnReplyCorrelation,
        ] = {}
        self._lock = asyncio.Lock()

    async def list_projection_routes(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[ThreadProjectionRoute, ...]:
        async with self._lock:
            routes = tuple(self._routes.values())
        if thread_ref is None:
            return routes
        return tuple(route for route in routes if route.thread_ref == thread_ref)

    async def put_projection_route(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            _reject_conflicting_route_id(self._routes.values(), route)
            key = (route.thread_ref, route.conversation_ref)
            stored = _merge_projection_route(self._routes.get(key), route)
            self._routes[key] = stored
        return stored

    async def replace_thread_projection_routes(
        self,
        route: ThreadProjectionRoute,
    ) -> ThreadProjectionRoute:
        validate_projection_route(route)
        async with self._lock:
            _reject_conflicting_route_id(self._routes.values(), route)
            key = (route.thread_ref, route.conversation_ref)
            stored = _merge_projection_route(self._routes.get(key), route)
            for key in tuple(self._routes):
                if key[0] == route.thread_ref:
                    self._routes.pop(key)
            self._routes[(route.thread_ref, route.conversation_ref)] = stored
        return stored

    async def advance_projection_checkpoint(
        self,
        route_id: str,
        *,
        expected_agent_item_id: str | None,
        agent_item_id: str,
        checkpointed_at: datetime,
    ) -> ThreadProjectionRoute:
        async with self._lock:
            for key, route in self._routes.items():
                if route.route_id == route_id:
                    if route.checkpoint_agent_item_id != expected_agent_item_id:
                        raise ProjectionCheckpointConflict(
                            f"projection checkpoint changed for route {route_id}"
                        )
                    advanced = replace(
                        route,
                        checkpoint_agent_item_id=agent_item_id,
                        checkpointed_at=checkpointed_at,
                    )
                    validate_projection_route(advanced)
                    self._routes[key] = advanced
                    return advanced
        raise KeyError(f"projection route does not exist: {route_id}")

    async def delete_projection_routes(
        self,
        thread_ref: ThreadRef,
        conversation_ref: ConversationRef | None = None,
    ) -> int:
        async with self._lock:
            keys = tuple(
                key
                for key in self._routes
                if key[0] == thread_ref and (conversation_ref is None or key[1] == conversation_ref)
            )
            for key in keys:
                self._routes.pop(key)
            return len(keys)

    async def get_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> TurnReplyCorrelation | None:
        async with self._lock:
            return self._turn_correlations.get((thread_ref, turn_id))

    async def list_turn_reply_correlations(
        self,
        thread_ref: ThreadRef | None = None,
    ) -> tuple[TurnReplyCorrelation, ...]:
        async with self._lock:
            correlations = tuple(self._turn_correlations.values())
        if thread_ref is None:
            return correlations
        return tuple(
            correlation
            for correlation in correlations
            if correlation.turn_ref.thread_ref == thread_ref
        )

    async def put_turn_reply_correlation(
        self,
        correlation: TurnReplyCorrelation,
    ) -> TurnReplyCorrelation:
        validate_turn_reply_correlation(correlation)
        async with self._lock:
            key = (correlation.turn_ref.thread_ref, correlation.turn_ref.turn_id)
            current = self._turn_correlations.get(key)
            if current is None:
                self._turn_correlations[key] = correlation
                return correlation
            if not _same_turn_reply_correlation(current, correlation):
                raise TurnReplyCorrelationConflict(
                    "Turn reply correlation already belongs to another IM input"
                )
            return current

    async def delete_turn_reply_correlation(
        self,
        thread_ref: ThreadRef,
        turn_id: str,
    ) -> bool:
        async with self._lock:
            return self._turn_correlations.pop((thread_ref, turn_id), None) is not None

    async def delete_turn_reply_correlations(
        self,
        *,
        thread_ref: ThreadRef | None = None,
        conversation_ref: ConversationRef | None = None,
        older_than: datetime | None = None,
    ) -> int:
        if thread_ref is None and conversation_ref is None and older_than is None:
            raise ValueError("correlation deletion requires at least one selector")
        async with self._lock:
            keys = tuple(
                key
                for key, correlation in self._turn_correlations.items()
                if (thread_ref is None or correlation.turn_ref.thread_ref == thread_ref)
                and (conversation_ref is None or correlation.conversation_ref == conversation_ref)
                and (older_than is None or correlation.created_at < older_than)
            )
            for key in keys:
                self._turn_correlations.pop(key)
            return len(keys)


class InMemoryDeliverySubmissionRepository:
    """Process-local route snapshots and outcomes without message content."""

    def __init__(self, *, max_records: int = 4096) -> None:
        if not isinstance(max_records, int) or isinstance(max_records, bool) or max_records < 1:
            raise ValueError("max_records must be a positive integer")
        self._max_records = max_records
        self._records: dict[str, DeliverySubmissionRecord] = {}
        self._lock = asyncio.Lock()

    @property
    def max_records(self) -> int:
        return self._max_records

    async def get_delivery_submission(
        self,
        submission_id: str,
    ) -> DeliverySubmissionRecord | None:
        async with self._lock:
            return self._records.get(submission_id)

    async def reserve_delivery_submission(
        self,
        record: DeliverySubmissionRecord,
    ) -> DeliveryReservation:
        validate_delivery_submission_record(record)
        async with self._lock:
            existing = self._records.get(record.submission_id)
            if existing is not None:
                ensure_same_delivery_submission_reservation(existing, record)
                return DeliveryReservation(acquired=False, record=existing)
            if len(self._records) >= self._max_records:
                raise DeliverySubmissionCapacityError(
                    "in-memory delivery submission record capacity is exhausted"
                )
            self._records[record.submission_id] = record
            return DeliveryReservation(acquired=True, record=record)

    async def update_delivery_destination(
        self,
        submission_id: str,
        destination_delivery_id: str,
        *,
        expected_state: DeliverySubmissionState,
        destination: DestinationDeliveryRecord,
    ) -> DeliverySubmissionRecord:
        async with self._lock:
            existing = self._records.get(submission_id)
            if existing is None:
                raise KeyError(f"delivery submission does not exist: {submission_id}")
            updated = _replace_destination(
                existing,
                destination_delivery_id,
                expected_state=expected_state,
                replacement=destination,
            )
            validate_delivery_submission_record(updated)
            self._records[submission_id] = updated
            return updated


def _replace_destination(
    record: DeliverySubmissionRecord,
    destination_delivery_id: str,
    *,
    expected_state: DeliverySubmissionState,
    replacement: DestinationDeliveryRecord,
) -> DeliverySubmissionRecord:
    if replacement.delivery_id != destination_delivery_id:
        raise DeliverySubmissionConflict("destination delivery identity changed")
    found = False
    destinations: list[DestinationDeliveryRecord] = []
    for current in record.destinations:
        if current.delivery_id != destination_delivery_id:
            destinations.append(current)
            continue
        found = True
        if current.state is not expected_state:
            if current == replacement:
                destinations.append(current)
                continue
            raise DeliverySubmissionConflict("delivery destination state changed")
        if current.snapshot != replacement.snapshot:
            raise DeliverySubmissionConflict("delivery destination snapshot changed")
        destinations.append(replacement)
    if not found:
        raise KeyError(f"delivery destination does not exist: {destination_delivery_id}")
    return replace(
        record,
        destinations=tuple(destinations),
        updated_at=max(record.updated_at, replacement.updated_at),
    )

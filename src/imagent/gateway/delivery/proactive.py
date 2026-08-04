from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ...adapters import (
    DeliveryAuthorizer,
    DeliverySubmissionConflict,
    DeliverySubmissionRepository,
)
from ...contracts import (
    ConversationDeliveryTarget,
    DeliveryIntent,
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryPrincipal,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliveryRouteSnapshot,
    DeliverySegmentStatus,
    DeliverySubmissionOrigin,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DeliveryTarget,
    DestinationDeliveryRecord,
    DestinationDeliveryResult,
    ProactiveDeliveryResult,
    ThreadProjectionRoute,
    ThreadRef,
    ThreadRouteDeliveryTarget,
    derive_delivery_payload_fingerprint,
    derive_delivery_submission_id,
    derive_delivery_target_fingerprint,
    derive_destination_delivery_id,
    validate_delivery_intent,
    validate_delivery_principal,
    validate_delivery_receipt_for_content,
)
from ...contracts import DeliveryTargetKind as DeliveryTargetKind
from ...interaction.channels import ChannelAdapter
from ...interaction.media import AttachmentContent, LocalPath
from ...interaction.messages import OutboundMessage
from ...interaction.operations import ContractViolation
from . import proactive_authorization as _proactive_authorization
from .coordination import DeliveryCoordinator
from .outcome_observation import (
    DeliveryOutcomeErrorCode,
    DeliveryOutcomeObserverRuntime,
)
from .planning import DeliveryPlanningError

ResolveThreadRoutes = Callable[
    [ThreadRef],
    Awaitable[tuple[ThreadProjectionRoute, ...]],
]


class DeliveryRouteError(RuntimeError):
    pass


class ProactiveDeliveryService:
    """Authorize and submit proactive content through the common Channel seam."""

    def __init__(
        self,
        *,
        channels: dict[str, ChannelAdapter],
        submissions: DeliverySubmissionRepository,
        resolve_thread_routes: ResolveThreadRoutes,
        authorizer: DeliveryAuthorizer | None,
        coordinator: DeliveryCoordinator,
        outcome_observer: DeliveryOutcomeObserverRuntime | None = None,
    ) -> None:
        self._channels = channels
        self._submissions = submissions
        self._resolve_thread_routes = resolve_thread_routes
        self._authorizer = authorizer
        self._coordinator = coordinator
        self._outcome_observer = outcome_observer

    async def deliver(
        self,
        intent: DeliveryIntent,
        *,
        credential: str,
    ) -> ProactiveDeliveryResult:
        principal = await self.authorize(intent.target, credential=credential)
        return await self._submit(intent, principal=principal, authorize=True)

    async def authorize(
        self,
        target: DeliveryTarget,
        *,
        credential: str,
    ) -> DeliveryPrincipal:
        authorizer = self._authorizer
        if authorizer is None:
            raise _proactive_authorization.DeliveryAuthorizationError(
                "proactive delivery is not configured"
            )
        principal = await authorizer.authenticate(credential)
        validate_delivery_principal(principal)
        authorize_delivery_target(principal, target)
        return principal

    async def deliver_internal(
        self,
        message: OutboundMessage,
    ) -> ProactiveDeliveryResult:
        intent = DeliveryIntent(
            delivery_id=message.delivery_id,
            target=ConversationDeliveryTarget(message.conversation_ref),
            content=message.content,
            created_at=message.created_at,
            reply_to=message.reply_to,
            metadata=message.metadata,
        )
        return await self._submit(
            intent,
            principal=DeliveryPrincipal(
                principal_id="imagent:gateway-internal",
                allowed_conversations=(message.conversation_ref,),
            ),
            authorize=False,
        )

    async def _submit(
        self,
        intent: DeliveryIntent,
        *,
        principal: DeliveryPrincipal,
        authorize: bool,
    ) -> ProactiveDeliveryResult:
        # Cardinality is the only check that must precede generic contract
        # validation and fingerprinting: both walk every content item.
        self._coordinator.validate_source_item_count(len(intent.content))
        validate_delivery_intent(intent)
        origin = (
            DeliverySubmissionOrigin.EXTERNAL
            if authorize
            else DeliverySubmissionOrigin.GATEWAY_INTERNAL
        )
        if authorize:
            _require_external_local_digests(intent)
        submission_id = derive_delivery_submission_id(
            origin,
            principal.principal_id,
            intent.delivery_id,
        )
        target_fingerprint = derive_delivery_target_fingerprint(intent.target)
        payload_fingerprint = derive_delivery_payload_fingerprint(intent)
        existing = await self._submissions.get_delivery_submission(submission_id)
        if existing is not None:
            _ensure_submission_identity(
                existing,
                principal_id=principal.principal_id,
                target_fingerprint=target_fingerprint,
                payload_fingerprint=payload_fingerprint,
            )
            if authorize:
                authorize_delivery_target(principal, intent.target)
            if any(
                destination.state is DeliverySubmissionState.RETRYABLE
                for destination in existing.destinations
            ):
                existing = await self._resume_retryable_destinations(
                    intent,
                    existing,
                )
            return _result_from_record(
                existing,
                replayed=True,
                expose_conversations=isinstance(
                    intent.target,
                    ConversationDeliveryTarget,
                ),
            )

        if authorize:
            authorize_delivery_target(principal, intent.target)
        snapshots = await self._resolve_snapshots(intent)
        execution_root_id = intent.delivery_id if not authorize else submission_id
        preflight_error = self._preflight(intent, snapshots)
        if preflight_error is not None:
            rejection = _preflight_rejection(
                intent,
                execution_root_id,
                snapshots,
                preflight_error,
            )
            now = datetime.now(UTC)
            rejected_record = DeliverySubmissionRecord(
                submission_id=submission_id,
                delivery_id=intent.delivery_id,
                origin=origin,
                principal_id=principal.principal_id,
                target_fingerprint=target_fingerprint,
                payload_fingerprint=payload_fingerprint,
                destinations=tuple(
                    DestinationDeliveryRecord(
                        delivery_id=destination.delivery_id,
                        snapshot=snapshot,
                        state=destination.state,
                        receipt=destination.receipt,
                        error=destination.error,
                        updated_at=now,
                    )
                    for destination, snapshot in zip(
                        rejection.destinations,
                        snapshots,
                        strict=True,
                    )
                ),
                created_at=now,
                updated_at=now,
            )
            reservation = await self._submissions.reserve_delivery_submission(rejected_record)
            if not reservation.acquired:
                _ensure_same_submission(reservation.record, rejected_record)
            return _result_from_record(
                reservation.record,
                replayed=not reservation.acquired,
                expose_conversations=isinstance(
                    intent.target,
                    ConversationDeliveryTarget,
                ),
            )

        now = datetime.now(UTC)
        destination_ids = _destination_ids(execution_root_id, snapshots)
        record = DeliverySubmissionRecord(
            submission_id=submission_id,
            delivery_id=intent.delivery_id,
            origin=origin,
            principal_id=principal.principal_id,
            target_fingerprint=target_fingerprint,
            payload_fingerprint=payload_fingerprint,
            destinations=tuple(
                DestinationDeliveryRecord(
                    delivery_id=destination_id,
                    snapshot=snapshot,
                    state=DeliverySubmissionState.IN_FLIGHT,
                    updated_at=now,
                )
                for destination_id, snapshot in zip(
                    destination_ids,
                    snapshots,
                    strict=True,
                )
            ),
            created_at=now,
            updated_at=now,
        )
        reservation = await self._submissions.reserve_delivery_submission(record)
        if not reservation.acquired:
            _ensure_same_submission(reservation.record, record)
            return _result_from_record(
                reservation.record,
                replayed=True,
                expose_conversations=isinstance(
                    intent.target,
                    ConversationDeliveryTarget,
                ),
            )

        if len(reservation.record.destinations) == 1:
            await self._send_destination(
                intent,
                submission_id,
                reservation.record.destinations[0],
            )
        else:
            await asyncio.gather(
                *(
                    self._send_destination(intent, submission_id, destination)
                    for destination in reservation.record.destinations
                )
            )
        completed = await self._submissions.get_delivery_submission(submission_id)
        if completed is None:
            raise RuntimeError("delivery submission disappeared during execution")
        return _result_from_record(
            completed,
            replayed=False,
            expose_conversations=isinstance(
                intent.target,
                ConversationDeliveryTarget,
            ),
        )

    async def _resume_retryable_destinations(
        self,
        intent: DeliveryIntent,
        record: DeliverySubmissionRecord,
    ) -> DeliverySubmissionRecord:
        claimed: list[DestinationDeliveryRecord] = []
        now = datetime.now(UTC)
        for destination in record.destinations:
            if destination.state is not DeliverySubmissionState.RETRYABLE:
                continue
            receipt = destination.receipt
            if (
                receipt is not None
                and receipt.retry_after_seconds is not None
                and now < destination.updated_at + timedelta(seconds=receipt.retry_after_seconds)
            ):
                continue
            in_flight = replace(
                destination,
                state=DeliverySubmissionState.IN_FLIGHT,
                receipt=None,
                error=None,
                updated_at=now,
            )
            try:
                await self._submissions.update_delivery_destination(
                    record.submission_id,
                    destination.delivery_id,
                    expected_state=DeliverySubmissionState.RETRYABLE,
                    destination=in_flight,
                )
            except DeliverySubmissionConflict:
                continue
            claimed.append(in_flight)
        if len(claimed) == 1:
            await self._send_destination(intent, record.submission_id, claimed[0])
        elif claimed:
            await asyncio.gather(
                *(
                    self._send_destination(intent, record.submission_id, destination)
                    for destination in claimed
                )
            )
        current = await self._submissions.get_delivery_submission(record.submission_id)
        if current is None:
            raise RuntimeError("delivery submission disappeared during retry")
        return current

    async def _resolve_snapshots(
        self,
        intent: DeliveryIntent,
    ) -> tuple[DeliveryRouteSnapshot, ...]:
        target = intent.target
        if isinstance(target, ConversationDeliveryTarget):
            return (DeliveryRouteSnapshot(conversation_ref=target.conversation_ref),)

        routes = await self._resolve_thread_routes(target.thread_ref)
        if target.route_id is not None:
            routes = tuple(route for route in routes if route.route_id == target.route_id)
        if not routes:
            qualifier = f" {target.route_id}" if target.route_id is not None else ""
            raise DeliveryRouteError(
                f"no active projection route{qualifier} exists for the requested Thread"
            )
        snapshots = tuple(
            DeliveryRouteSnapshot(
                conversation_ref=route.conversation_ref,
                thread_ref=route.thread_ref,
                route_id=route.route_id,
                route_updated_at=route.updated_at,
                reply_to_message_id=route.reply_to_message_id,
            )
            for route in sorted(
                routes,
                key=lambda route: (
                    route.conversation_ref.channel_instance_id,
                    route.conversation_ref.native_conversation_id,
                    route.route_id,
                ),
            )
        )
        if len({snapshot.conversation_ref for snapshot in snapshots}) != len(snapshots):
            raise DeliveryRouteError("active projection routes contain duplicate destinations")
        return snapshots

    def _preflight(
        self,
        intent: DeliveryIntent,
        snapshots: tuple[DeliveryRouteSnapshot, ...],
    ) -> str | None:
        for snapshot in snapshots:
            channel = self._channels.get(snapshot.conversation_ref.channel_instance_id)
            if channel is None:
                return (
                    "destination Channel is not registered: "
                    f"{snapshot.conversation_ref.channel_instance_id}"
                )
            try:
                self._coordinator.preflight(
                    channel,
                    OutboundMessage(
                        delivery_id=intent.delivery_id,
                        conversation_ref=snapshot.conversation_ref,
                        content=intent.content,
                        created_at=intent.created_at,
                        reply_to=intent.reply_to or snapshot.reply_to_message_id,
                        metadata=intent.metadata,
                    ),
                )
            except DeliveryPlanningError as error:
                return str(error)
        return None

    async def _send_destination(
        self,
        intent: DeliveryIntent,
        submission_id: str,
        destination: DestinationDeliveryRecord,
    ) -> None:
        snapshot = destination.snapshot
        channel = self._channels[snapshot.conversation_ref.channel_instance_id]
        message = OutboundMessage(
            delivery_id=destination.delivery_id,
            conversation_ref=snapshot.conversation_ref,
            content=intent.content,
            created_at=intent.created_at,
            reply_to=intent.reply_to or snapshot.reply_to_message_id,
            metadata=intent.metadata,
        )
        cancellation: asyncio.CancelledError | None = None
        outcome_error: DeliveryOutcomeErrorCode | None = None
        try:
            receipt = await self._coordinator.deliver(channel, message)
            validate_delivery_receipt_for_content(receipt, intent.content)
            state = _state_from_receipt(receipt)
            error = (
                receipt.detail
                if state
                in {
                    DeliverySubmissionState.REJECTED,
                    DeliverySubmissionState.UNKNOWN,
                    DeliverySubmissionState.PARTIAL,
                }
                else None
            )
        except DeliveryPlanningError as delivery_error:
            outcome_error = DeliveryOutcomeErrorCode.PLANNING_FAILED
            receipt = DeliveryReceipt(
                status=DeliveryReceiptStatus.REJECTED_BY_PLATFORM,
                detail=str(delivery_error),
            )
            state = DeliverySubmissionState.REJECTED
            error = receipt.detail
        except asyncio.CancelledError as delivery_error:
            # Cancellation means the native outcome cannot be trusted, but it
            # must not strand durable state at IN_FLIGHT. Persist UNKNOWN
            # before propagating so ingress may then remove staged artifacts.
            receipt = DeliveryReceipt(
                status=DeliveryReceiptStatus.UNKNOWN,
                detail="delivery cancelled before the native outcome was confirmed",
            )
            state = DeliverySubmissionState.UNKNOWN
            error = receipt.detail
            cancellation = delivery_error
            outcome_error = DeliveryOutcomeErrorCode.CANCELLED
        except BaseException as delivery_error:
            if isinstance(delivery_error, (KeyboardInterrupt, SystemExit)):
                raise
            receipt = DeliveryReceipt(
                status=DeliveryReceiptStatus.UNKNOWN,
                detail=str(delivery_error) or type(delivery_error).__name__,
            )
            state = DeliverySubmissionState.UNKNOWN
            error = receipt.detail
            outcome_error = DeliveryOutcomeErrorCode.EXECUTION_FAILED
        replacement = replace(
            destination,
            state=state,
            receipt=receipt,
            error=error,
            updated_at=datetime.now(UTC),
        )
        try:
            await self._submissions.update_delivery_destination(
                submission_id,
                destination.delivery_id,
                expected_state=DeliverySubmissionState.IN_FLIGHT,
                destination=replacement,
            )
        finally:
            if self._outcome_observer is not None:
                self._outcome_observer.notify(
                    message,
                    receipt=receipt if outcome_error is None else None,
                    error=outcome_error,
                )
        if cancellation is not None:
            raise cancellation


def authorize_delivery_target(
    principal: DeliveryPrincipal,
    target: DeliveryTarget,
) -> None:
    if isinstance(target, ConversationDeliveryTarget):
        if target.conversation_ref not in principal.allowed_conversations:
            raise _proactive_authorization.DeliveryAuthorizationError(
                "principal is not allowed to deliver to the explicit Conversation"
            )
        return
    if isinstance(target, ThreadRouteDeliveryTarget):
        if target.thread_ref not in principal.allowed_threads:
            raise _proactive_authorization.DeliveryAuthorizationError(
                "principal is not allowed to deliver through the requested Thread"
            )
        return
    raise _proactive_authorization.DeliveryAuthorizationError("delivery target is unsupported")


def _state_from_receipt(receipt: DeliveryReceipt) -> DeliverySubmissionState:
    item_states = {item.status for item in receipt.items}
    segment_states = {segment.status for segment in receipt.segments}
    has_acceptance = (
        DeliveryItemStatus.ACCEPTED in item_states
        or DeliverySegmentStatus.ACCEPTED_BY_PLATFORM in segment_states
    )
    if receipt.status is DeliveryReceiptStatus.UNKNOWN:
        return DeliverySubmissionState.UNKNOWN
    if receipt.status is DeliveryReceiptStatus.RETRYABLE_FAILURE:
        return DeliverySubmissionState.RETRYABLE
    if receipt.status is DeliveryReceiptStatus.REJECTED_BY_PLATFORM:
        return (
            DeliverySubmissionState.PARTIAL if has_acceptance else DeliverySubmissionState.REJECTED
        )
    if item_states & {
        DeliveryItemStatus.REJECTED,
        DeliveryItemStatus.RETRYABLE_FAILURE,
        DeliveryItemStatus.UNKNOWN,
        DeliveryItemStatus.SKIPPED,
    } or segment_states & {
        DeliverySegmentStatus.REJECTED_BY_PLATFORM,
        DeliverySegmentStatus.RETRYABLE_FAILURE,
        DeliverySegmentStatus.UNKNOWN,
        DeliverySegmentStatus.SKIPPED,
    }:
        return DeliverySubmissionState.PARTIAL
    return DeliverySubmissionState.ACCEPTED


def _destination_ids(
    root_submission_id: str,
    snapshots: tuple[DeliveryRouteSnapshot, ...],
) -> tuple[str, ...]:
    if len(snapshots) == 1:
        return (root_submission_id,)
    return tuple(
        derive_destination_delivery_id(root_submission_id, snapshot.conversation_ref)
        for snapshot in snapshots
    )


def _preflight_rejection(
    intent: DeliveryIntent,
    execution_root_id: str,
    snapshots: tuple[DeliveryRouteSnapshot, ...],
    error: str,
) -> ProactiveDeliveryResult:
    ids = _destination_ids(execution_root_id, snapshots)
    item_receipts = tuple(
        DeliveryItemReceipt(
            content_index=index,
            attachment_id=(item.attachment_id if isinstance(item, AttachmentContent) else None),
            status=DeliveryItemStatus.SKIPPED,
            detail=error,
        )
        for index, item in enumerate(intent.content)
    )
    destinations = tuple(
        DestinationDeliveryResult(
            delivery_id=delivery_id,
            state=DeliverySubmissionState.REJECTED,
            route_id=snapshot.route_id,
            conversation_ref=(
                snapshot.conversation_ref
                if isinstance(intent.target, ConversationDeliveryTarget)
                else None
            ),
            receipt=DeliveryReceipt(
                status=DeliveryReceiptStatus.REJECTED_BY_PLATFORM,
                detail=error,
                items=item_receipts,
            ),
            error=error,
        )
        for delivery_id, snapshot in zip(ids, snapshots, strict=True)
    )
    return ProactiveDeliveryResult(
        delivery_id=intent.delivery_id,
        state=DeliverySubmissionState.REJECTED,
        destinations=destinations,
        error=error,
    )


def _result_from_record(
    record: DeliverySubmissionRecord,
    *,
    replayed: bool,
    expose_conversations: bool,
) -> ProactiveDeliveryResult:
    destinations = tuple(
        DestinationDeliveryResult(
            delivery_id=destination.delivery_id,
            state=destination.state,
            route_id=destination.snapshot.route_id,
            conversation_ref=(
                destination.snapshot.conversation_ref if expose_conversations else None
            ),
            receipt=(
                destination.receipt
                if expose_conversations
                else _redact_receipt(destination.receipt)
            ),
            error=(destination.error if expose_conversations else None),
            replayed=replayed,
        )
        for destination in record.destinations
    )
    states = {destination.state for destination in destinations}
    if DeliverySubmissionState.IN_FLIGHT in states:
        state = DeliverySubmissionState.IN_FLIGHT
    elif len(states) == 1:
        state = next(iter(states))
    else:
        state = DeliverySubmissionState.PARTIAL
    return ProactiveDeliveryResult(
        delivery_id=record.delivery_id,
        state=state,
        destinations=destinations,
        error=(
            None
            if state is DeliverySubmissionState.ACCEPTED
            else "one or more destinations did not complete successfully"
        ),
    )


def _redact_receipt(
    receipt: DeliveryReceipt | None,
) -> DeliveryReceipt | None:
    if receipt is None:
        return None
    return replace(
        receipt,
        native_message_id=None,
        detail=None,
        items=tuple(
            replace(
                item,
                native_message_id=None,
                detail=None,
            )
            for item in receipt.items
        ),
        segments=tuple(
            replace(
                segment,
                native_message_id=None,
                detail=None,
            )
            for segment in receipt.segments
        ),
    )


def _require_external_local_digests(intent: DeliveryIntent) -> None:
    for item in intent.content:
        if not isinstance(item, AttachmentContent) or not isinstance(
            item.source,
            LocalPath,
        ):
            continue
        digest = item.metadata.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ContractViolation(
                "proactive LocalPath attachments require lowercase metadata.sha256"
            )


def _ensure_same_submission(
    existing: DeliverySubmissionRecord,
    replacement: DeliverySubmissionRecord,
) -> None:
    if (
        existing.submission_id != replacement.submission_id
        or existing.delivery_id != replacement.delivery_id
        or existing.origin is not replacement.origin
    ):
        raise DeliverySubmissionConflict("delivery submission identity changed")
    _ensure_submission_identity(
        existing,
        principal_id=replacement.principal_id,
        target_fingerprint=replacement.target_fingerprint,
        payload_fingerprint=replacement.payload_fingerprint,
    )


def _ensure_submission_identity(
    existing: DeliverySubmissionRecord,
    *,
    principal_id: str,
    target_fingerprint: str,
    payload_fingerprint: str,
) -> None:
    if (
        existing.principal_id != principal_id
        or existing.target_fingerprint != target_fingerprint
        or existing.payload_fingerprint != payload_fingerprint
    ):
        raise DeliverySubmissionConflict(
            f"delivery ID belongs to a different submission: {existing.delivery_id}"
        )

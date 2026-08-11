"""One public, production-shaped composition for the neutral consumer."""

from __future__ import annotations

from dataclasses import dataclass

from imagent import (
    Gateway,
    GatewayLimits,
    GatewayStore,
    MemoryGatewayStore,
    ProjectionPolicy,
)
from imagent.interaction.controllers import CommandRegistry

from .application import ReferenceApplication
from .interaction import ReferenceChannel, ReferenceStatusService, build_command_registry


@dataclass(slots=True)
class ReferenceConsumer:
    """The local adapters and the one SDK-owned Gateway runtime."""

    channel: ReferenceChannel
    application: ReferenceApplication
    registry: CommandRegistry
    gateway: Gateway


def build_reference_consumer(
    *,
    application: ReferenceApplication | None = None,
    channel: ReferenceChannel | None = None,
    store: GatewayStore | None = None,
) -> ReferenceConsumer:
    """Build one explicit graph without private SDK seams or global state."""

    if channel is None:
        channel = ReferenceChannel(max_outbound_records=128)
    if application is None:
        application = ReferenceApplication(
            max_projects=2,
            max_threads=4,
            max_turns_per_thread=8,
            max_events_per_thread=64,
        )
    if store is None:
        store = MemoryGatewayStore(
            max_effect_receipts=64,
            max_idempotency_records=128,
            max_delivery_submission_records=128,
        )
    registry = build_command_registry(ReferenceStatusService())
    gateway = Gateway(
        gateway_id="reference",
        channels=[channel],
        applications=[application],
        store=store,
        controller=registry,
        projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        limits=GatewayLimits(
            request_delivery_max_pending=16,
            startup_buffer_max_pending=16,
            turn_acceptance_event_max_pending=16,
            delivery_submission_max_records=128,
            conversation_serialization_max_active_keys=16,
            idempotency_max_records=128,
            projection_max_active_threads=4,
        ),
    )
    return ReferenceConsumer(
        channel=channel,
        application=application,
        registry=registry,
        gateway=gateway,
    )


__all__ = ["ReferenceConsumer", "build_reference_consumer"]

"""Three-layer composition for the neutral reference consumer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from imagent.applications.contract import ThreadRef
from imagent.contracts import (
    BindConversationToThread,
    ConversationBound,
    GatewayOperationFailed,
)
from imagent.gateway import ImAgentGateway
from imagent.gateway.composition import GatewayExtensions, GatewayRepositories
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryProjectionRouteRepository,
)
from imagent.gateway.routing import ObserveThread, ProjectionPolicy, ThreadObserved
from imagent.interaction.controllers import CommandRegistry
from imagent.interaction.messages import ConversationRef

from .application import ReferenceApplication
from .interaction import ReferenceChannel, build_command_registry


@dataclass(slots=True)
class ReferenceConsumer:
    """Explicit composition root: local state, adapters, and one Gateway."""

    channel: ReferenceChannel
    application: ReferenceApplication
    bindings: InMemoryBindingRepository
    projections: InMemoryProjectionRouteRepository
    registry: CommandRegistry
    gateway: ImAgentGateway

    async def start(self) -> None:
        await self.gateway.start()

    async def stop(self) -> None:
        await self.gateway.stop()

    async def bind(
        self,
        conversation_ref: ConversationRef,
        thread_ref: ThreadRef,
        *,
        operation_id: str,
        actor: str = "reference-user",
    ) -> ConversationBound:
        result = await self.gateway.execute_gateway(
            BindConversationToThread(
                operation_id=operation_id,
                conversation_ref=conversation_ref,
                actor=actor,
                thread_ref=thread_ref,
                created_at=datetime.now(UTC),
            )
        )
        if isinstance(result, GatewayOperationFailed):
            raise RuntimeError(result.error.message)
        if not isinstance(result, ConversationBound):
            raise RuntimeError("bind returned an incompatible Gateway result")
        return result

    async def observe(
        self,
        conversation_ref: ConversationRef,
        thread_ref: ThreadRef,
        *,
        operation_id: str,
        reply_to_message_id: str | None = None,
        actor: str = "reference-user",
    ) -> ThreadObserved:
        result = await self.gateway.execute_gateway(
            ObserveThread(
                operation_id=operation_id,
                conversation_ref=conversation_ref,
                actor=actor,
                thread_ref=thread_ref,
                reply_to_message_id=reply_to_message_id,
                created_at=datetime.now(UTC),
            )
        )
        if isinstance(result, GatewayOperationFailed):
            raise RuntimeError(result.error.message)
        if not isinstance(result, ThreadObserved):
            raise RuntimeError("observe returned an incompatible Gateway result")
        return result


def build_reference_consumer() -> ReferenceConsumer:
    """Return one fully explicit local composition with no process-global registry."""

    channel = ReferenceChannel()
    application = ReferenceApplication()
    bindings = InMemoryBindingRepository()
    projections = InMemoryProjectionRouteRepository()
    registry = build_command_registry()
    gateway = ImAgentGateway(
        channels=[channel],
        applications=[application],
        repositories=GatewayRepositories(
            bindings=bindings,
            projections=projections,
        ),
        extensions=GatewayExtensions(controller=registry),
        projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
    )
    return ReferenceConsumer(
        channel=channel,
        application=application,
        bindings=bindings,
        projections=projections,
        registry=registry,
        gateway=gateway,
    )


__all__ = ["ReferenceConsumer", "build_reference_consumer"]

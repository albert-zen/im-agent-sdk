"""Neutral Channel and Interaction composition for the reference consumer.

The example owns this small Channel adapter and its local command composition.
The SDK still owns inbound admission, command dispatch, and outbound delivery
once these public contracts are supplied to :class:`ImAgentGateway`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from imagent.diagnostics import (
    ChannelDiagnosticFacts,
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
)
from imagent.interaction.channels import (
    ChannelCapabilities,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySupportLevel,
    InboundAdmissionHandler,
    MessageHandler,
)
from imagent.interaction.controllers import (
    CommandDefinition,
    CommandExecutionSafety,
    CommandHandlerActions,
    CommandInvocation,
    CommandRegistry,
    CommandResult,
    register_common_commands,
)
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
    TextFormat,
)


class ReferenceChannel:
    """A deterministic, bounded local Channel used by the executable example."""

    kind = "reference"

    def __init__(
        self,
        channel_instance_id: str = "reference-channel",
        *,
        max_outbound_records: int = 256,
    ) -> None:
        if (
            not isinstance(max_outbound_records, int)
            or isinstance(max_outbound_records, bool)
            or max_outbound_records < 1
        ):
            raise ValueError("max_outbound_records must be a positive integer")
        self._channel_instance_id = channel_instance_id
        self._max_outbound_records = max_outbound_records
        self._capabilities = ChannelCapabilities(
            markdown=DeliverySupportLevel.NATIVE,
            reply_references=DeliverySupportLevel.NATIVE,
        )
        self._on_message: MessageHandler | None = None
        self._on_admission: InboundAdmissionHandler | None = None
        self._started = False
        self._start_count = 0
        self._next_message_id = 1
        self._sent: list[OutboundMessage] = []

    @property
    def channel_instance_id(self) -> str:
        return self._channel_instance_id

    @property
    def capabilities(self) -> ChannelCapabilities:
        return self._capabilities

    @property
    def started(self) -> bool:
        return self._started

    @property
    def sent(self) -> tuple[OutboundMessage, ...]:
        return tuple(self._sent)

    @property
    def max_outbound_records(self) -> int:
        return self._max_outbound_records

    async def start(
        self,
        on_message: MessageHandler,
        on_admission: InboundAdmissionHandler | None = None,
    ) -> None:
        self._on_message = on_message
        self._on_admission = on_admission
        self._started = True
        self._start_count += 1

    async def stop(self) -> None:
        self._started = False
        self._on_message = None
        self._on_admission = None

    async def emit(self, message: InboundMessage) -> None:
        if not self._started or self._on_message is None:
            raise RuntimeError("reference channel is not started")
        if self._on_admission is None:
            await self._on_message(message)
            return
        admission = await self._on_admission(
            message.conversation_ref,
            message.message_id,
        )
        if admission is not None:
            await admission.deliver(message)

    async def emit_text(
        self,
        conversation_ref: ConversationRef,
        text: str,
        *,
        message_id: str | None = None,
        sender: str = "reference-user",
    ) -> None:
        if message_id is None:
            message_id = f"reference-inbound-{self._next_message_id}"
            self._next_message_id += 1
        await self.emit(
            InboundMessage(
                message_id=message_id,
                conversation_ref=conversation_ref,
                sender=sender,
                content=(TextContent(text, TextFormat.MARKDOWN),),
                created_at=datetime.now(UTC),
            )
        )

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        if not self._started:
            raise RuntimeError("reference channel is not started")
        if len(self._sent) >= self._max_outbound_records:
            raise RuntimeError("reference Channel outbound-record capacity is exhausted")
        self._sent.append(message)
        return DeliveryReceipt(
            status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            native_message_id=f"reference-delivery-{len(self._sent)}",
        )

    def diagnostic_facts(self) -> ChannelDiagnosticFacts:
        return ChannelDiagnosticFacts(
            channel_instance_id=self._channel_instance_id,
            kind=self.kind,
            connection=ConnectionDiagnosticFacts(
                state=(
                    ConnectionDiagnosticState.READY
                    if self._started
                    else ConnectionDiagnosticState.DISCONNECTED
                ),
                connection_epoch=self._start_count,
                reconnect_count=max(0, self._start_count - 1),
                worker_running=self._started,
                worker_degraded=False,
            ),
        )


async def _about_command(
    invocation: CommandInvocation,
    actions: CommandHandlerActions,
) -> CommandResult:
    """One product-owned command, deliberately independent of SDK Core."""

    del invocation, actions
    return CommandResult.text(
        "Neutral reference consumer: Channel, Gateway, and Application composed "
        "through public SDK contracts."
    )


def build_command_registry() -> CommandRegistry:
    """Build and freeze the example's explicit local command registry."""

    registry = CommandRegistry()
    register_common_commands(registry, include=("help",))
    registry.register(
        CommandDefinition(
            name="about",
            handler=_about_command,
            summary="Describe this neutral reference consumer.",
            usage="/about",
            safety=CommandExecutionSafety.READ_ONLY,
        )
    )
    registry.freeze()
    return registry


__all__ = ["ReferenceChannel", "build_command_registry"]

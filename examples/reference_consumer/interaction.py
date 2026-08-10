"""Neutral Channel and Interaction composition for the reference consumer.

The example owns this small Channel adapter and its local command composition.
The SDK still owns inbound admission, command dispatch, and outbound delivery
once these public contracts are supplied to the public Gateway.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from imagent import ConversationActions
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
    CommandInvocation,
    CommandLimits,
    CommandRegistry,
    CommandResult,
    include_common_commands,
)
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
    TextFormat,
)
from imagent.interaction.operations import require_identifier


@dataclass(frozen=True, slots=True)
class ReferenceConversation:
    """One authenticated local Conversation using the Channel's native ingress."""

    channel: ReferenceChannel = field(repr=False)
    ref: ConversationRef
    authenticated_actor: str

    def text_message(self, *, message_id: str, text: str) -> InboundMessage:
        require_identifier(message_id, "message ID")
        return InboundMessage(
            message_id=message_id,
            conversation_ref=self.ref,
            sender=self.authenticated_actor,
            content=(TextContent(text, TextFormat.MARKDOWN),),
            created_at=datetime.now(UTC),
        )

    async def receive_text(self, *, message_id: str, text: str) -> None:
        await self.channel.receive(self.text_message(message_id=message_id, text=text))

    async def wait_for_text(
        self,
        text: str,
        *,
        after: int = 0,
        count: int = 1,
        timeout_seconds: float = 2.0,
    ) -> tuple[OutboundMessage, ...]:
        return await self.channel.wait_for_text(
            text,
            conversation_ref=self.ref,
            after=after,
            count=count,
            timeout_seconds=timeout_seconds,
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
        require_identifier(channel_instance_id, "Channel instance ID")
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
        self._sent: list[OutboundMessage] = []
        self._delivery_changed = asyncio.Condition()

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

    def conversation(
        self,
        native_conversation_id: str,
        *,
        authenticated_actor: str = "reference-user",
    ) -> ReferenceConversation:
        require_identifier(native_conversation_id, "native Conversation ID")
        require_identifier(authenticated_actor, "authenticated actor")
        return ReferenceConversation(
            channel=self,
            ref=ConversationRef(self.channel_instance_id, native_conversation_id),
            authenticated_actor=authenticated_actor,
        )

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

    async def receive(self, message: InboundMessage) -> None:
        if not self._started or self._on_message is None:
            raise RuntimeError("reference channel is not started")
        require_identifier(message.message_id, "message ID")
        require_identifier(message.conversation_ref.channel_instance_id, "Channel instance ID")
        require_identifier(
            message.conversation_ref.native_conversation_id,
            "native Conversation ID",
        )
        require_identifier(message.sender, "authenticated actor")
        if message.conversation_ref.channel_instance_id != self.channel_instance_id:
            raise ValueError("inbound message belongs to a different Channel instance")
        if self._on_admission is None:
            await self._on_message(message)
            return
        admission = await self._on_admission(
            message.conversation_ref,
            message.message_id,
        )
        if admission is not None:
            await admission.deliver(message)

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        if not self._started:
            raise RuntimeError("reference channel is not started")
        require_identifier(message.delivery_id, "delivery ID")
        require_identifier(message.conversation_ref.channel_instance_id, "Channel instance ID")
        require_identifier(
            message.conversation_ref.native_conversation_id,
            "native Conversation ID",
        )
        if message.conversation_ref.channel_instance_id != self.channel_instance_id:
            raise ValueError("outbound message belongs to a different Channel instance")
        async with self._delivery_changed:
            if len(self._sent) >= self._max_outbound_records:
                raise RuntimeError("reference Channel outbound-record capacity is exhausted")
            self._sent.append(message)
            delivery_number = len(self._sent)
            self._delivery_changed.notify_all()
        return DeliveryReceipt(
            status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            native_message_id=f"reference-delivery-{delivery_number}",
        )

    async def wait_for_text(
        self,
        text: str,
        *,
        conversation_ref: ConversationRef | None = None,
        after: int = 0,
        count: int = 1,
        timeout_seconds: float = 2.0,
    ) -> tuple[OutboundMessage, ...]:
        if (
            not isinstance(after, int)
            or isinstance(after, bool)
            or after < 0
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 1
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError("delivery wait bounds must be positive")

        def matches() -> tuple[OutboundMessage, ...]:
            return tuple(
                message
                for message in self._sent[after:]
                if _message_text(message) == text
                and (conversation_ref is None or message.conversation_ref == conversation_ref)
            )

        async with asyncio.timeout(timeout_seconds):
            async with self._delivery_changed:
                while len(found := matches()) < count:
                    await self._delivery_changed.wait()
                return found

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


@dataclass(frozen=True, slots=True)
class ReferenceStatusService:
    """One typed, consumer-owned service injected into a read-only handler."""

    def describe(self) -> str:
        return (
            "Neutral reference consumer: Channel, Gateway, and Application composed "
            "through public SDK contracts."
        )


def _about_definition(status: ReferenceStatusService) -> CommandDefinition:
    async def about(
        invocation: CommandInvocation,
        actions: ConversationActions,
    ) -> CommandResult:
        del invocation, actions
        return CommandResult.text(status.describe())

    return CommandDefinition(
        name="about",
        handler=about,
        summary="Describe this neutral reference consumer.",
        usage="/about",
        safety=CommandExecutionSafety.READ_ONLY,
    )


def _command_limits() -> CommandLimits:
    return CommandLimits(
        max_commands=4,
        max_aliases=4,
        max_aliases_per_command=2,
        max_command_name_length=32,
        max_help_text_length=256,
        max_input_line_length=256,
        max_arguments=4,
        max_argument_length=64,
        max_result_items=4,
        max_result_text_characters=1_024,
        max_concurrency=2,
        handler_timeout_seconds=2.0,
        cancellation_join_timeout_seconds=1.0,
        view_cache_capacity=4,
        view_cache_ttl_seconds=60.0,
    )


def _message_text(message: OutboundMessage) -> str:
    return " ".join(content.text for content in message.content if isinstance(content, TextContent))


def build_command_registry(status: ReferenceStatusService) -> CommandRegistry:
    """Build and freeze the example's explicit local command registry."""

    registry = CommandRegistry(limits=_command_limits())
    include_common_commands(registry, names=("help", "new"))
    registry.register(_about_definition(status))
    registry.freeze()
    return registry


__all__ = [
    "ReferenceChannel",
    "ReferenceConversation",
    "ReferenceStatusService",
    "build_command_registry",
]

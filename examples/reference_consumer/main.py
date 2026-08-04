"""Executable smoke path for the neutral reference consumer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from imagent.gateway.persistence import ProjectionPolicy
from imagent.interaction.messages import ConversationRef, OutboundMessage, TextContent

from .gateway import ReferenceConsumer, build_reference_consumer


@dataclass(frozen=True, slots=True)
class DemoReport:
    projection_policy: ProjectionPolicy
    command_outputs: tuple[str, ...]
    shared_thread_conversations: tuple[ConversationRef, ...]
    switched_old_thread_conversations: tuple[ConversationRef, ...]
    switched_new_thread_conversations: tuple[ConversationRef, ...]
    recovered_conversations: tuple[ConversationRef, ...]
    worker_max_active: tuple[int, ...]
    worker_subscription_calls: tuple[int, ...]
    diagnostics_schema_version: int
    diagnostics_application_ids: tuple[str, ...]
    diagnostics_channel_ids: tuple[str, ...]
    diagnostics_authoritative: bool
    gateway_stopped: bool
    registry_frozen: bool


def _message_text(message: OutboundMessage) -> str:
    return " ".join(content.text for content in message.content if isinstance(content, TextContent))


async def _wait_for_text(
    consumer: ReferenceConsumer,
    text: str,
    *,
    conversation_ref: ConversationRef | None = None,
    count: int = 1,
) -> None:
    async with asyncio.timeout(2):
        while True:
            matches = tuple(
                message
                for message in consumer.channel.sent
                if _message_text(message) == text
                and (conversation_ref is None or message.conversation_ref == conversation_ref)
            )
            if len(matches) >= count:
                return
            await asyncio.sleep(0)


def _conversation_ids(messages: tuple[OutboundMessage, ...]) -> tuple[ConversationRef, ...]:
    return tuple(
        sorted(
            {message.conversation_ref for message in messages},
            key=lambda ref: (ref.channel_instance_id, ref.native_conversation_id),
        )
    )


async def run_demo() -> DemoReport:
    """Exercise commands, binding, observation, switching, recovery, and stop."""

    consumer = build_reference_consumer()
    application = consumer.application
    conversation_a = ConversationRef("reference-channel", "conversation-a")
    conversation_b = ConversationRef("reference-channel", "conversation-b")
    first_thread = await application.create_thread(title="Shared reference Thread")
    second_thread = await application.create_thread(title="Switched reference Thread")

    await consumer.start()
    diagnostics = None
    command_outputs: list[str] = []
    shared_thread_messages: tuple[OutboundMessage, ...] = ()
    switched_old_thread_messages: tuple[OutboundMessage, ...] = ()
    switched_new_thread_messages: tuple[OutboundMessage, ...] = ()
    recovered_messages: tuple[OutboundMessage, ...] = ()
    try:
        before = len(consumer.channel.sent)
        await consumer.channel.emit_text(
            conversation_a,
            "/help",
            message_id="reference-command-help",
        )
        async with asyncio.timeout(2):
            while len(consumer.channel.sent) <= before:
                await asyncio.sleep(0)
        command_outputs.extend(_message_text(message) for message in consumer.channel.sent[before:])

        before = len(consumer.channel.sent)
        await consumer.channel.emit_text(
            conversation_a,
            "/about",
            message_id="reference-command-about",
        )
        async with asyncio.timeout(2):
            while len(consumer.channel.sent) <= before:
                await asyncio.sleep(0)
        command_outputs.extend(_message_text(message) for message in consumer.channel.sent[before:])

        await consumer.bind(
            conversation_a,
            first_thread.ref,
            operation_id="reference-bind-a-first",
        )
        await consumer.bind(
            conversation_b,
            first_thread.ref,
            operation_id="reference-bind-b-first",
        )
        await application.emit_native_turn(first_thread.ref, "shared-thread-turn")
        await _wait_for_text(
            consumer,
            "Neutral response: shared-thread-turn",
            count=2,
        )
        shared_thread_messages = tuple(
            message
            for message in consumer.channel.sent
            if _message_text(message) == "Neutral response: shared-thread-turn"
        )
        if _conversation_ids(shared_thread_messages) != (
            conversation_a,
            conversation_b,
        ):
            raise AssertionError("both Conversations must observe the first Thread")

        await consumer.bind(
            conversation_a,
            second_thread.ref,
            operation_id="reference-bind-a-second",
        )
        await application.emit_native_turn(first_thread.ref, "old-thread-after-switch")
        await _wait_for_text(
            consumer,
            "Neutral response: old-thread-after-switch",
            conversation_ref=conversation_b,
        )
        await asyncio.sleep(0)
        switched_old_thread_messages = tuple(
            message
            for message in consumer.channel.sent
            if _message_text(message) == "Neutral response: old-thread-after-switch"
        )
        if _conversation_ids(switched_old_thread_messages) != (conversation_b,):
            raise AssertionError("the switched Conversation must leave the old Thread")

        await application.emit_native_turn(second_thread.ref, "new-thread-after-switch")
        await _wait_for_text(
            consumer,
            "Neutral response: new-thread-after-switch",
            conversation_ref=conversation_a,
        )
        switched_new_thread_messages = tuple(
            message
            for message in consumer.channel.sent
            if _message_text(message) == "Neutral response: new-thread-after-switch"
        )

        diagnostics = consumer.gateway.diagnostics_snapshot()

        await consumer.stop()
        await application.emit_native_turn(first_thread.ref, "recovered-after-restart")
        await consumer.start()
        await _wait_for_text(
            consumer,
            "Neutral response: recovered-after-restart",
            conversation_ref=conversation_b,
        )
        await asyncio.sleep(0)
        recovered_messages = tuple(
            message
            for message in consumer.channel.sent
            if _message_text(message) == "Neutral response: recovered-after-restart"
        )
        if _conversation_ids(recovered_messages) != (conversation_b,):
            raise AssertionError("restart recovery must restore only the remaining route")
    finally:
        await consumer.stop()

    if diagnostics is None:
        raise AssertionError("the running Gateway must provide diagnostics")
    return DemoReport(
        projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        command_outputs=tuple(command_outputs),
        shared_thread_conversations=_conversation_ids(shared_thread_messages),
        switched_old_thread_conversations=_conversation_ids(switched_old_thread_messages),
        switched_new_thread_conversations=_conversation_ids(switched_new_thread_messages),
        recovered_conversations=_conversation_ids(recovered_messages),
        worker_max_active=(
            application.max_active_observation_workers(first_thread.ref),
            application.max_active_observation_workers(second_thread.ref),
        ),
        worker_subscription_calls=(
            application.subscription_calls(first_thread.ref),
            application.subscription_calls(second_thread.ref),
        ),
        diagnostics_schema_version=diagnostics.schema_version,
        diagnostics_application_ids=tuple(
            facts.application_instance_id for facts in diagnostics.applications
        ),
        diagnostics_channel_ids=tuple(facts.channel_instance_id for facts in diagnostics.channels),
        diagnostics_authoritative=diagnostics.authoritative,
        gateway_stopped=(not consumer.channel.started and not application.started),
        registry_frozen=consumer.registry.frozen,
    )


def main() -> None:
    report = asyncio.run(run_demo())
    print(
        "reference consumer OK: "
        f"commands={len(report.command_outputs)} "
        f"shared_conversations={len(report.shared_thread_conversations)} "
        f"workers={report.worker_max_active} "
        f"recovered={len(report.recovered_conversations)}"
    )


if __name__ == "__main__":
    main()


__all__ = ["DemoReport", "main", "run_demo"]

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from examples.reference_consumer.application import ReferenceApplication
from examples.reference_consumer.gateway import build_reference_consumer
from examples.reference_consumer.interaction import ReferenceChannel
from examples.reference_consumer.main import run_demo
from imagent.applications.contract import (
    AgentInput,
    ApplicationRef,
    ThreadHistory,
    ThreadRef,
)
from imagent.applications.operations import GetThreadHistory, ThreadHistoryRead
from imagent.gateway.persistence import ProjectionPolicy
from imagent.interaction.controllers import (
    CommandDefinition,
    CommandRegistry,
    CommandRegistryFrozenError,
    CommandResult,
)
from imagent.interaction.messages import ConversationRef, OutboundMessage, TextContent


class ReferenceConsumerExampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_executable_composition_covers_routing_recovery_and_shutdown(self) -> None:
        report = await run_demo()

        self.assertIs(report.projection_policy, ProjectionPolicy.FOREGROUND_ONLY)
        self.assertEqual(len(report.command_outputs), 2)
        self.assertIn("IM Agent commands", report.command_outputs[0])
        self.assertIn("Neutral reference consumer", report.command_outputs[1])

        conversation_a = ConversationRef("reference-channel", "conversation-a")
        conversation_b = ConversationRef("reference-channel", "conversation-b")
        self.assertEqual(
            report.shared_thread_conversations,
            (conversation_a, conversation_b),
        )
        self.assertEqual(report.switched_old_thread_conversations, (conversation_b,))
        self.assertEqual(report.switched_new_thread_conversations, (conversation_a,))
        self.assertEqual(report.recovered_conversations, (conversation_b,))

        self.assertEqual(report.worker_max_active, (1, 1))
        self.assertTrue(all(calls >= 1 for calls in report.worker_subscription_calls))

        self.assertEqual(report.diagnostics_schema_version, 8)
        self.assertEqual(report.diagnostics_application_ids, ("reference-agent",))
        self.assertEqual(report.diagnostics_channel_ids, ("reference-channel",))
        self.assertFalse(report.diagnostics_authoritative)
        self.assertTrue(report.registry_frozen)
        self.assertTrue(report.gateway_stopped)

    async def test_local_composition_is_typed_and_registry_is_frozen(self) -> None:
        consumer = build_reference_consumer()

        self.assertIsInstance(consumer.registry, CommandRegistry)
        self.assertTrue(consumer.registry.frozen)

        async def late_command(invocation, actions) -> CommandResult:
            del invocation, actions
            return CommandResult.text("late")

        with self.assertRaises(CommandRegistryFrozenError):
            consumer.registry.register(CommandDefinition(name="late", handler=late_command))

    async def test_channel_capacity_fails_before_append(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceChannel(max_outbound_records=0)
        with self.assertRaises(ValueError):
            ReferenceChannel(max_outbound_records=True)

        channel = ReferenceChannel(max_outbound_records=1)

        async def on_message(message) -> None:
            del message

        await channel.start(on_message)
        message = OutboundMessage(
            delivery_id="delivery-1",
            conversation_ref=ConversationRef("reference-channel", "conversation-a"),
            content=(TextContent("one"),),
            created_at=datetime.now(UTC),
        )
        await channel.send(message)
        with self.assertRaises(RuntimeError):
            await channel.send(message)
        self.assertEqual(channel.sent, (message,))

    async def test_application_limits_are_positive_and_capacity_is_pre_dispatch(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceApplication(max_threads=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_turns_per_thread=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_events_per_thread=0)
        with self.assertRaises(ValueError):
            ReferenceApplication(max_threads=True)

        application = ReferenceApplication(max_threads=1)
        await application.create_thread()
        with self.assertRaises(ValueError):
            await application.create_thread()
        with self.assertRaises(KeyError):
            await application.get_thread(
                ThreadRef(
                    application_instance_id="reference-agent",
                    native_thread_id="reference-thread-2",
                )
            )

        turn_limited = ReferenceApplication(
            max_turns_per_thread=1,
            max_events_per_thread=6,
        )
        turn_thread = await turn_limited.create_thread()
        await turn_limited.emit_native_turn(turn_thread.ref, "first")
        callback_calls: list[str] = []

        async def before_dispatch(dispatch) -> None:
            callback_calls.append(dispatch.client_message_id)

        with self.assertRaises(ValueError):
            await turn_limited.send_input(
                turn_thread.ref,
                AgentInput(
                    client_message_id="rejected-turn",
                    content=(TextContent("second"),),
                ),
                before_dispatch=before_dispatch,
            )
        self.assertEqual(callback_calls, [])
        self.assertEqual(len((await _history(turn_limited, turn_thread.ref)).turns), 1)

        event_limited = ReferenceApplication(
            max_turns_per_thread=2,
            max_events_per_thread=3,
        )
        event_thread = await event_limited.create_thread()
        await event_limited.emit_native_turn(event_thread.ref, "first")
        event_callback_calls: list[str] = []

        async def before_event_dispatch(dispatch) -> None:
            event_callback_calls.append(dispatch.client_message_id)

        with self.assertRaises(ValueError):
            await event_limited.send_input(
                event_thread.ref,
                AgentInput(
                    client_message_id="rejected-event",
                    content=(TextContent("rejected"),),
                ),
                before_dispatch=before_event_dispatch,
            )
        self.assertEqual(event_callback_calls, [])
        with self.assertRaises(ValueError):
            await event_limited.emit_native_turn(event_thread.ref, "rejected")
        self.assertEqual(len((await _history(event_limited, event_thread.ref)).turns), 1)


async def _history(
    application: ReferenceApplication,
    thread_ref: ThreadRef,
) -> ThreadHistory:
    result = await application.execute(
        GetThreadHistory(
            operation_id="reference-test-history",
            application_ref=ApplicationRef("reference-agent"),
            thread_ref=thread_ref,
            created_at=datetime.now(UTC),
        )
    )
    if not isinstance(result, ThreadHistoryRead):
        raise AssertionError(f"history read failed: {result!r}")
    return result.history

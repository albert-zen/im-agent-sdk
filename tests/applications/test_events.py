from __future__ import annotations

import asyncio
import unittest
from contextlib import suppress
from datetime import UTC, datetime
from subprocess import run
from sys import executable
from typing import get_type_hints

import imagent
from imagent import contracts
from imagent import events as legacy_events
from imagent.applications import capabilities, events
from imagent.applications.contract import AgentMessage, ThreadRef
from imagent.interaction.messages import MessageRole, TextContent
from imagent.interaction.operations import ContractViolation


def _capabilities(
    *,
    replay_from_cursor: capabilities.SupportLevel = capabilities.SupportLevel.UNSUPPORTED,
    event_sequence_scope: capabilities.EventSequenceScope = (capabilities.EventSequenceScope.NONE),
) -> capabilities.ApplicationCapabilities:
    return capabilities.ApplicationCapabilities(
        projects=capabilities.ProjectCapabilities(
            mode=capabilities.ProjectMode.FLAT,
            discovery=capabilities.SupportLevel.UNSUPPORTED,
            reading=capabilities.SupportLevel.UNSUPPORTED,
        ),
        threads=capabilities.ThreadCapabilities(
            listing=capabilities.SupportLevel.NATIVE,
            creation=capabilities.SupportLevel.NATIVE,
            reading=capabilities.SupportLevel.NATIVE,
        ),
        runtime=capabilities.RuntimeCapabilities(
            history=capabilities.SupportLevel.NATIVE,
            streaming=capabilities.SupportLevel.NATIVE,
            replay_from_cursor=replay_from_cursor,
            interruption=capabilities.SupportLevel.NATIVE,
            interactive_requests=capabilities.SupportLevel.NATIVE,
            event_sequence_scope=event_sequence_scope,
        ),
    )


class ApplicationEventTests(unittest.IsolatedAsyncioTestCase):
    def test_event_owner_import_keeps_concrete_adapters_lazy(self) -> None:
        result = run(
            [
                executable,
                "-c",
                "import sys; "
                "from imagent.applications import events; "
                "assert 'imagent.applications.adapters.codex' not in sys.modules; "
                "assert 'imagent.applications.adapters.zen' not in sys.modules; "
                "assert 'imagent.applications.adapters.t3' not in sys.modules",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_event_facades_preserve_exact_nominal_identities(self) -> None:
        self.assertIs(contracts.AgentEvent, events.AgentEvent)
        self.assertIs(contracts.AgentEventType, events.AgentEventType)
        self.assertIs(contracts.validate_agent_event, events.validate_agent_event)
        self.assertIs(imagent.events, legacy_events)
        self.assertEqual(
            legacy_events.__all__,
            [
                "AgentEvent",
                "AgentEventType",
                "CursorExpired",
                "EventBroadcaster",
                "EventBufferOverflow",
                "EventStreamGap",
                "EventStreamOverflow",
                "EventStreamReset",
                "FanoutSubscription",
                "validate_agent_event",
            ],
        )
        self.assertNotIn("__getattr__", legacy_events.__dict__)
        for name in legacy_events.__all__:
            with self.subTest(name=name):
                self.assertIs(getattr(legacy_events, name), getattr(events, name))
        self.assertFalse(hasattr(events, "AgentMessage"))

    def test_canonical_event_data_consumes_the_application_agent_message(self) -> None:
        message = AgentMessage(
            agent_item_id="item-1",
            thread_ref=ThreadRef("app-1", "thread-1"),
            role=MessageRole.ASSISTANT,
            content=(TextContent("answer"),),
            created_at=datetime.now(UTC),
        )
        event = events.AgentEvent(
            event_id="event-message-1",
            application_instance_id="app-1",
            type=events.AgentEventType.MESSAGE_COMPLETED,
            data={"message": message},
            created_at=message.created_at,
            thread_ref=message.thread_ref,
        )
        events.validate_agent_event(event, _capabilities())
        self.assertIs(event.data["message"], message)

    def test_event_annotations_keep_exact_resource_and_request_types(self) -> None:
        hints = get_type_hints(events.AgentEvent)
        self.assertEqual(hints["project_ref"], contracts.ProjectRef | None)
        self.assertEqual(hints["thread_ref"], contracts.ThreadRef | None)
        self.assertEqual(hints["request"], contracts.InteractiveRequest | None)
        self.assertEqual(hints["request_resolution"], contracts.RequestResolution | None)

    def test_capacity_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_pending must be positive"):
            events.EventBroadcaster[str, str](max_pending=0)

    def test_ordering_fields_require_declared_native_guarantees(self) -> None:
        created_at = datetime.now(UTC)
        honest = events.AgentEvent(
            event_id="event-1",
            application_instance_id="app-1",
            type=events.AgentEventType.TURN_COMPLETED,
            data={},
            created_at=created_at,
        )
        events.validate_agent_event(honest, _capabilities())

        with self.assertRaisesRegex(ContractViolation, "cursor"):
            events.validate_agent_event(
                events.AgentEvent(
                    event_id="event-2",
                    application_instance_id="app-1",
                    type=events.AgentEventType.TURN_COMPLETED,
                    data={},
                    created_at=created_at,
                    cursor="unsupported",
                ),
                _capabilities(),
            )

        replay_capabilities = _capabilities(
            replay_from_cursor=capabilities.SupportLevel.NATIVE,
            event_sequence_scope=capabilities.EventSequenceScope.THREAD,
        )
        with self.assertRaisesRegex(ContractViolation, "sequence_epoch"):
            events.validate_agent_event(
                events.AgentEvent(
                    event_id="event-3",
                    application_instance_id="app-1",
                    type=events.AgentEventType.TURN_COMPLETED,
                    data={},
                    created_at=created_at,
                    sequence=1,
                    cursor="cursor-1",
                ),
                replay_capabilities,
            )
        events.validate_agent_event(
            events.AgentEvent(
                event_id="event-4",
                application_instance_id="app-1",
                type=events.AgentEventType.TURN_COMPLETED,
                data={},
                created_at=created_at,
                sequence=1,
                sequence_epoch="epoch-1",
                cursor="cursor-1",
            ),
            replay_capabilities,
        )

    async def test_slow_and_cancelled_subscribers_do_not_block_or_steal(self) -> None:
        broadcaster = events.EventBroadcaster[str, str]()
        fast = broadcaster.subscribe("thread")
        slow = broadcaster.subscribe("thread")
        cancelled = broadcaster.subscribe("thread")
        pending = asyncio.create_task(anext(cancelled))
        await asyncio.sleep(0)
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending

        broadcaster.publish("thread", "first")
        broadcaster.publish("thread", "second")

        self.assertEqual(await anext(fast), "first")
        self.assertEqual(await anext(fast), "second")
        self.assertEqual(await anext(slow), "first")
        self.assertEqual(await anext(slow), "second")
        self.assertEqual(broadcaster.subscriber_count("thread"), 2)
        await fast.aclose()
        await slow.aclose()
        self.assertEqual(broadcaster.subscriber_count("thread"), 0)

    async def test_slow_subscriber_overflow_is_explicit_and_isolated(self) -> None:
        broadcaster = events.EventBroadcaster[str, str](max_pending=2)
        fast = broadcaster.subscribe("thread")
        slow = broadcaster.subscribe("thread")
        unrelated = broadcaster.subscribe("other-thread")

        broadcaster.publish("thread", "first")
        self.assertEqual(await anext(fast), "first")
        broadcaster.publish("thread", "second")
        self.assertEqual(await anext(fast), "second")
        broadcaster.publish("thread", "third")
        broadcaster.publish("other-thread", "unrelated")

        self.assertEqual(slow.pending_count, 0)
        self.assertEqual(broadcaster.subscriber_count("thread"), 1)
        with self.assertRaises(events.EventStreamOverflow) as raised:
            await anext(slow)
        self.assertEqual(raised.exception.max_pending, 2)
        self.assertEqual(
            raised.exception.gap_code,
            "application_event_fanout_overflow",
        )
        self.assertEqual(await anext(fast), "third")
        self.assertEqual(await anext(unrelated), "unrelated")
        await fast.aclose()
        await unrelated.aclose()

    async def test_key_scoped_failure_preserves_other_subscriptions(self) -> None:
        broadcaster = events.EventBroadcaster[str, str]()
        failed = broadcaster.subscribe("failed-thread")
        unrelated = broadcaster.subscribe("other-thread")
        broadcaster.publish("failed-thread", "before-gap")
        broadcaster.fail(
            "failed-thread",
            lambda: events.EventStreamReset("application_event_poll_failed"),
            discard_pending=False,
        )
        broadcaster.publish("other-thread", "still-running")

        self.assertEqual(await anext(failed), "before-gap")
        with self.assertRaisesRegex(events.EventStreamReset, "application_event_poll_failed"):
            await anext(failed)
        self.assertEqual(await anext(unrelated), "still-running")
        await unrelated.aclose()

    async def test_explicit_stream_reset_terminates_current_subscribers(self) -> None:
        broadcaster = events.EventBroadcaster[str, str](max_pending=2)
        first = broadcaster.subscribe("first")
        second = broadcaster.subscribe("second")
        broadcaster.publish("first", "discarded")

        broadcaster.fail_all(events.EventStreamReset)

        self.assertEqual(broadcaster.subscriber_count("first"), 0)
        self.assertEqual(broadcaster.subscriber_count("second"), 0)
        with self.assertRaises(events.EventStreamReset):
            await anext(first)
        with self.assertRaises(events.EventStreamReset):
            await anext(second)

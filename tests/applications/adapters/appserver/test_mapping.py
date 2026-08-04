from __future__ import annotations

import unittest
from datetime import UTC, datetime

from imagent.applications.adapters.appserver.mapping import (
    is_agent_item,
    is_unsupported_method_error,
    item_text,
    native_list,
    native_object,
    native_turn_id,
    normalized_item_type,
    optional_string,
    parse_datetime,
    parse_optional_datetime,
    thread_status,
    turn_error,
    turn_id,
    turn_items,
    turn_list,
    turn_status,
    turn_updated_at,
)
from imagent.applications.appserver_client import AppServerError
from imagent.contracts import ThreadStatus, TurnStatus


class AppServerMappingTests(unittest.TestCase):
    def test_native_container_shapes_are_validated_and_filtered(self) -> None:
        thread = {"id": "thread-1"}
        self.assertIs(native_object({"thread": thread}, "thread"), thread)
        with self.assertRaisesRegex(RuntimeError, "did not contain thread"):
            native_object({"thread": "invalid"}, "thread")

        self.assertEqual(native_turn_id({"turn": {"turnId": " turn-1 "}}), "turn-1")
        self.assertEqual(native_turn_id({"turnId": "turn-2"}), "turn-2")
        self.assertIsNone(native_turn_id({}))
        self.assertEqual(native_list({"data": [thread, "invalid"]}, "threads", "data"), (thread,))
        with self.assertRaisesRegex(RuntimeError, "did not contain a thread list"):
            native_list({"data": "invalid"}, "data")

    def test_status_aliases_and_unknown_shapes_are_bounded(self) -> None:
        self.assertEqual(thread_status({"type": "in-progress"}), ThreadStatus.RUNNING)
        self.assertEqual(thread_status("waiting-for-input"), ThreadStatus.WAITING_FOR_INPUT)
        self.assertEqual(thread_status(object()), ThreadStatus.UNKNOWN)
        self.assertEqual(turn_status({"status": "working"}), TurnStatus.RUNNING)
        self.assertEqual(turn_status("cancelled"), TurnStatus.INTERRUPTED)
        self.assertEqual(turn_status(None), TurnStatus.UNKNOWN)

    def test_turn_shapes_and_item_content_are_normalized(self) -> None:
        turn = {"turnId": "turn-1", "items": [{"type": "agent_message"}, "invalid"]}
        self.assertEqual(turn_list({"thread": {"turns": [turn, "invalid"]}}), (turn,))
        self.assertEqual(turn_list({"data": [turn]}), (turn,))
        self.assertEqual(turn_list({}), ())
        self.assertEqual(turn_items(turn), ({"type": "agent_message"},))
        self.assertEqual(turn_items({"items": "invalid"}), ())
        self.assertEqual(turn_id(turn), "turn-1")
        with self.assertRaisesRegex(RuntimeError, "did not contain an id"):
            turn_id({})

        item = {"kind": "assistant-message", "content": [{"text": "one"}, {"text": "two"}]}
        self.assertEqual(normalized_item_type(item), "assistantmessage")
        self.assertTrue(is_agent_item(item))
        self.assertFalse(is_agent_item({"type": "commandExecution"}))
        self.assertEqual(item_text(item), "one\ntwo")
        self.assertEqual(item_text({"text": " answer "}), "answer")
        self.assertEqual(item_text({"content": " answer "}), "answer")
        self.assertEqual(item_text({"content": object()}), "")

    def test_errors_and_datetimes_use_stable_fallbacks(self) -> None:
        self.assertEqual(optional_string(" value "), "value")
        self.assertIsNone(optional_string(None))
        self.assertEqual(turn_error({"error": {"message": "failed"}}), "failed")
        self.assertEqual(turn_error({"error": "failed"}), "failed")

        expected = datetime(2026, 8, 2, 1, 2, 3, tzinfo=UTC)
        self.assertEqual(parse_optional_datetime("2026-08-02T01:02:03Z"), expected)
        self.assertEqual(turn_updated_at({"completedAt": "2026-08-02T01:02:03Z"}), expected)
        self.assertIsNone(parse_optional_datetime("not-a-date"))
        before = datetime.now(UTC)
        fallback = parse_datetime(None)
        after = datetime.now(UTC)
        self.assertLessEqual(before, fallback)
        self.assertLessEqual(fallback, after)

        self.assertTrue(is_unsupported_method_error(AppServerError("failed", code=-32601)))
        self.assertTrue(is_unsupported_method_error(RuntimeError("unknown method")))
        self.assertFalse(is_unsupported_method_error(RuntimeError("connection closed")))


if __name__ == "__main__":
    unittest.main()

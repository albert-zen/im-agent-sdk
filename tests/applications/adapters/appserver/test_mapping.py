from __future__ import annotations

import unittest
from datetime import UTC, datetime

from imagent.applications.adapters.appserver.client import AppServerError
from imagent.applications.adapters.appserver.mapping import (
    APP_SERVER_MAPPING_ERROR_MESSAGE,
    MAX_NATIVE_COLLECTION_ITEMS,
    MAX_NATIVE_CONTENT_PARTS,
    MAX_NATIVE_ID_CHARACTERS,
    MAX_NATIVE_MAPPING_KEY_CHARACTERS,
    MAX_NATIVE_MAPPING_KEYS,
    MAX_NATIVE_TEXT_CHARACTERS,
    MAX_NATIVE_TOTAL_VALUES,
    AppServerMappingError,
    derive_appserver_event_id,
    is_agent_item,
    is_unsupported_method_error,
    item_id,
    item_text,
    native_list,
    native_mapping,
    native_object,
    native_turn_id,
    normalize_appserver_message,
    normalized_item_type,
    optional_string,
    parse_datetime,
    parse_optional_datetime,
    thread_id,
    thread_status,
    turn_error,
    turn_id,
    turn_items,
    turn_list,
    turn_status,
    turn_updated_at,
)
from imagent.applications.contract import ThreadStatus, TurnStatus


class AppServerMappingTests(unittest.TestCase):
    def assert_mapping_error(self, callback) -> None:
        with self.assertRaisesRegex(
            AppServerMappingError,
            f"^{APP_SERVER_MAPPING_ERROR_MESSAGE}$",
        ):
            callback()

    def test_event_identity_includes_workspace_project_scope(self) -> None:
        first = derive_appserver_event_id(
            "codex-main",
            project_id="workspace-1",
            event_type="turn.completed",
            thread_id="thread-1",
            turn_id="turn-1",
        )
        second = derive_appserver_event_id(
            "codex-main",
            project_id="workspace-2",
            event_type="turn.completed",
            thread_id="thread-1",
            turn_id="turn-1",
        )
        self.assertNotEqual(first, second)

    def test_native_container_shapes_are_validated_copied_and_filtered(self) -> None:
        thread = {"id": "thread-1"}
        copied_thread = native_object({"thread": thread}, "thread")
        self.assertEqual(copied_thread, thread)
        self.assertIsNot(copied_thread, thread)
        self.assert_mapping_error(lambda: native_object({"thread": "invalid"}, "thread"))

        self.assertEqual(native_turn_id({"turn": {"turnId": " turn-1 "}}), " turn-1 ")
        self.assertEqual(native_turn_id({"turnId": "turn-2"}), "turn-2")
        self.assertIsNone(native_turn_id({}))
        self.assertEqual(native_list({"data": [thread, "invalid"]}, "threads", "data"), (thread,))
        self.assert_mapping_error(lambda: native_list({"data": "invalid"}, "data"))

    def test_native_fact_limits_accept_the_exact_boundary_and_reject_plus_one(self) -> None:
        exact_text = "x" * MAX_NATIVE_TEXT_CHARACTERS
        self.assertEqual(item_text({"text": exact_text}), exact_text)
        self.assert_mapping_error(
            lambda: item_text({"text": "x" * (MAX_NATIVE_TEXT_CHARACTERS + 1)})
        )

        exact_collection = [
            {"id": f"thread-{index}"} for index in range(MAX_NATIVE_COLLECTION_ITEMS)
        ]
        self.assertEqual(
            len(native_list({"data": exact_collection}, "data")),
            MAX_NATIVE_COLLECTION_ITEMS,
        )
        self.assert_mapping_error(
            lambda: native_list(
                {
                    "data": [
                        {"id": f"thread-{index}"}
                        for index in range(MAX_NATIVE_COLLECTION_ITEMS + 1)
                    ]
                },
                "data",
            )
        )

        exact_mapping = {f"key-{index}": index for index in range(MAX_NATIVE_MAPPING_KEYS)}
        self.assertEqual(native_mapping(exact_mapping), exact_mapping)
        self.assert_mapping_error(
            lambda: native_mapping(
                {f"key-{index}": index for index in range(MAX_NATIVE_MAPPING_KEYS + 1)}
            )
        )
        self.assertEqual(
            native_mapping({"k" * MAX_NATIVE_MAPPING_KEY_CHARACTERS: "value"}),
            {"k" * MAX_NATIVE_MAPPING_KEY_CHARACTERS: "value"},
        )
        self.assert_mapping_error(
            lambda: native_mapping({"k" * (MAX_NATIVE_MAPPING_KEY_CHARACTERS + 1): "value"})
        )

        full_row_count = (MAX_NATIVE_TOTAL_VALUES - 2) // (MAX_NATIVE_MAPPING_KEYS + 1)
        last_row_width = (
            MAX_NATIVE_TOTAL_VALUES - 2 - (full_row_count * (MAX_NATIVE_MAPPING_KEYS + 1)) - 1
        )

        def recursive_payload(last_width: int) -> dict[str, object]:
            rows: list[dict[str, int]] = [
                {f"row-{row}-key-{column}": column for column in range(MAX_NATIVE_MAPPING_KEYS)}
                for row in range(full_row_count)
            ]
            rows.append({f"last-key-{column}": column for column in range(last_width)})
            return {"data": rows}

        exact_recursive_payload = recursive_payload(last_row_width)
        self.assertEqual(native_mapping(exact_recursive_payload), exact_recursive_payload)
        self.assert_mapping_error(lambda: native_mapping(recursive_payload(last_row_width + 1)))

        exact_parts = [{"text": "x"} for _ in range(MAX_NATIVE_CONTENT_PARTS)]
        self.assertEqual(
            item_text({"content": exact_parts}),
            "\n".join("x" for _ in range(MAX_NATIVE_CONTENT_PARTS)),
        )
        self.assert_mapping_error(
            lambda: item_text(
                {"content": [{"text": "x"} for _ in range(MAX_NATIVE_CONTENT_PARTS + 1)]}
            )
        )
        oversized_content = [{"text": "x"} for _ in range(MAX_NATIVE_CONTENT_PARTS + 1)]
        self.assert_mapping_error(
            lambda: normalize_appserver_message(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "id": "item-1",
                            "type": "agentMessage",
                            "content": oversized_content,
                        },
                    },
                }
            )
        )
        self.assert_mapping_error(
            lambda: native_mapping({"data": [{"items": [{"content": oversized_content}]}]})
        )
        self.assertEqual(item_text({"content": [{"text": exact_text}]}), exact_text)
        self.assert_mapping_error(
            lambda: item_text({"content": [{"text": "x" * (MAX_NATIVE_TEXT_CHARACTERS + 1)}]})
        )

    def test_method_required_identities_are_exact_and_fail_closed(self) -> None:
        completed = {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"id": "item-1", "type": "agentMessage"},
            },
        }
        event = normalize_appserver_message(completed)
        self.assertEqual(
            (event.thread_id, event.turn_id, event.item_id), ("thread-1", "turn-1", "item-1")
        )

        for key, target in (
            ("threadId", completed["params"]),
            ("turnId", completed["params"]),
            ("id", completed["params"]["item"]),
        ):
            assert isinstance(target, dict)
            for invalid in (None, "", ["not-a-scalar"], "x" * (MAX_NATIVE_ID_CHARACTERS + 1)):
                with self.subTest(identity=key, invalid=type(invalid).__name__):
                    message = {
                        "method": completed["method"],
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {"id": "item-1", "type": "agentMessage"},
                        },
                    }
                    params = message["params"]
                    assert isinstance(params, dict)
                    if key == "id":
                        item = params["item"]
                        assert isinstance(item, dict)
                        if invalid is None:
                            item.pop("id")
                        else:
                            item["id"] = invalid
                    elif invalid is None:
                        params.pop(key)
                    else:
                        params[key] = invalid
                    self.assert_mapping_error(lambda: normalize_appserver_message(message))

        for method, params in (
            ("item/agentMessage/delta", {"threadId": "thread-1", "delta": "partial"}),
            ("turn/completed", {"threadId": "thread-1"}),
            ("turn/completed", {"turnId": "turn-1"}),
            ("serverRequest/resolved", {}),
            ("serverRequest/resolved", {"requestId": ""}),
            ("serverRequest/resolved", {"requestId": ["request"]}),
            ("serverRequest/resolved", {"requestId": "x" * (MAX_NATIVE_ID_CHARACTERS + 1)}),
        ):
            with self.subTest(method=method, params=params):
                self.assert_mapping_error(
                    lambda method=method, params=params: normalize_appserver_message(
                        {"method": method, "params": params}
                    )
                )

        request = {
            "id": "request-1",
            "method": "item/commandExecution/requestApproval",
            "_connection_epoch": 1,
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
            },
        }
        self.assertEqual(normalize_appserver_message(request).transport_request_id, "request-1")
        for field, value in (
            ("id", ""),
            ("id", ["request"]),
            ("id", "x" * (MAX_NATIVE_ID_CHARACTERS + 1)),
            ("threadId", ""),
            ("turnId", ""),
            ("_connection_epoch", None),
        ):
            with self.subTest(request_field=field, value=value):
                candidate = {
                    "id": "request-1",
                    "method": request["method"],
                    "_connection_epoch": request["_connection_epoch"],
                    "params": dict(request["params"]),
                }
                if field == "id":
                    candidate["id"] = value
                elif field == "_connection_epoch":
                    if value is None:
                        candidate.pop(field)
                    else:
                        candidate[field] = value
                elif value is None:
                    candidate["params"].pop(field)
                else:
                    candidate["params"][field] = value
                self.assert_mapping_error(lambda: normalize_appserver_message(candidate))

    def test_native_identity_aliases_must_agree_exactly(self) -> None:
        self.assertEqual(
            thread_id({"id": "thread-1", "threadId": "thread-1"}),
            "thread-1",
        )
        self.assertEqual(
            turn_id({"id": "turn-1", "turnId": "turn-1"}),
            "turn-1",
        )
        self.assertEqual(
            item_id({"id": "item-1", "itemId": "item-1"}),
            "item-1",
        )
        self.assertEqual(
            native_turn_id(
                {
                    "turnId": "turn-1",
                    "turn": {"id": "turn-1", "turnId": "turn-1"},
                }
            ),
            "turn-1",
        )
        for callback in (
            lambda: thread_id({"id": "thread-one", "threadId": "thread-two"}),
            lambda: turn_id({"id": "turn-one", "turnId": "turn-two"}),
            lambda: item_id({"id": "item-one", "itemId": "item-two"}),
            lambda: native_turn_id({"turnId": "turn-one", "turn": {"id": "turn-two"}}),
            lambda: normalize_appserver_message(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-outer",
                        "turn": {"id": "turn-nested"},
                        "item": {"id": "item-1", "type": "agentMessage"},
                    },
                }
            ),
            lambda: normalize_appserver_message(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "item-outer",
                        "item": {"id": "item-nested", "type": "agentMessage"},
                    },
                }
            ),
            lambda: normalize_appserver_message(
                {
                    "method": "thread/status/changed",
                    "params": {
                        "threadId": "thread-outer",
                        "thread": {"id": "thread-nested"},
                    },
                }
            ),
            lambda: normalize_appserver_message(
                {
                    "method": "vendor/extension",
                    "params": {"eventId": "event-one", "event_id": "event-two"},
                }
            ),
            lambda: normalize_appserver_message(
                {
                    "id": "request-root",
                    "method": "item/fileChange/requestApproval",
                    "_connection_epoch": 1,
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "requestId": "request-conflicts-with-root",
                    },
                }
            ),
            lambda: normalize_appserver_message(
                {
                    "method": "vendor/extension",
                    "_connection_epoch": 1,
                    "params": {"_connection_epoch": 1},
                }
            ),
            lambda: normalize_appserver_message(
                {
                    "method": "vendor/extension",
                    "_connection_epoch": 1,
                    "params": {"_transport_request_id": "forged"},
                }
            ),
            lambda: normalize_appserver_message(
                {
                    "method": "vendor/extension",
                    "_connection_epoch": 1,
                    "params": {"_request_id": "forged"},
                }
            ),
        ):
            self.assert_mapping_error(callback)

    def test_unknown_native_methods_remain_unknown_without_unrelated_identity_rules(self) -> None:
        raw_payload = {"nested": {"value": "kept internal"}}
        event = normalize_appserver_message({"method": "vendor/extension", "params": raw_payload})
        self.assertEqual(
            (event.direction, event.category, event.kind), ("notification", "unknown", "unknown")
        )
        self.assertIsNone(event.thread_id)
        self.assertEqual(event.payload, raw_payload)
        self.assertIsNot(event.payload, raw_payload)
        raw_payload["nested"]["value"] = "mutated after mapping"
        nested = event.payload["nested"]
        assert isinstance(nested, dict)
        self.assertEqual(nested["value"], "kept internal")

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
        self.assert_mapping_error(lambda: turn_id({}))

        item = {"kind": "assistant-message", "content": [{"text": "one"}, {"text": "two"}]}
        self.assertEqual(normalized_item_type(item), "assistantmessage")
        self.assertTrue(is_agent_item(item))
        self.assertFalse(is_agent_item({"type": "commandExecution"}))
        self.assertEqual(item_text(item), "one\ntwo")
        self.assertEqual(item_text({"text": " answer "}), "answer")
        self.assertEqual(item_text({"content": " answer "}), "answer")
        self.assert_mapping_error(lambda: item_text({"content": object()}))

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

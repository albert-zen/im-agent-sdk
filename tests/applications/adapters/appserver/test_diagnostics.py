from __future__ import annotations

import importlib.util
import unittest
from collections.abc import Iterator, Mapping
from typing import cast
from unittest.mock import patch

from imagent.applications.adapters.appserver.client import codex_app_server_client
from imagent.applications.adapters.appserver.diagnostics import (
    DEBUG_SCHEMA,
    MAX_DEBUG_COLLECTION_COUNT,
    MAX_DEBUG_COUNTER,
    MAX_DEBUG_SAMPLE_ITEMS,
    MAX_DEBUG_SCALAR_CHARACTERS,
    MAX_PREVIEW_CHARS,
    AppServerDiagnosticState,
    emit_event,
    mark_appserver_health,
    summarize_text,
    summarize_transport_message,
)
from imagent.interaction.diagnostics import (
    ConnectionDiagnosticState,
    DiagnosticFailureCode,
    QueueDiagnosticName,
)

_SENSITIVE_SENTINELS = (
    "native-response-id-sentinel",
    "native-request-id-sentinel",
    "native-thread-id-sentinel",
    "native-turn-id-sentinel",
    "native-item-id-sentinel",
    "prompt-content-sentinel",
    "delta-content-sentinel",
    "command-content-sentinel",
    "permission-value-sentinel",
    "token-credential-sentinel",
    "https://user:token-credential-sentinel@example.invalid/path",
    "/workspace/inbound-media/private-path-sentinel",
    "C:\\workspace\\private-path-sentinel",
    "/secret",
    "cwd=/tmp/x",
    "cwd=C:\\x",
    "cwd=\\\\server\\share\\private-path-sentinel",
    "unix://user:token-credential-sentinel@example.invalid/socket",
    "custom+agent://user:token-credential-sentinel@example.invalid/path",
)


def _assert_no_sensitive_text(test: unittest.TestCase, value: object) -> None:
    rendered = repr(value)
    for sentinel in _SENSITIVE_SENTINELS:
        with test.subTest(sentinel=sentinel):
            test.assertNotIn(sentinel, rendered)


class _CountingMapping(Mapping[str, object]):
    def __init__(self, count: int) -> None:
        self._values = {f"safe-key-{index}": "safe" for index in range(count)}
        self.key_pulls = 0

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        for key in self._values:
            self.key_pulls += 1
            yield key

    def __len__(self) -> int:
        return len(self._values)


class _CountingList(list[object]):
    def __init__(self, count: int) -> None:
        super().__init__(["safe"] * count)
        self.value_pulls = 0

    def __iter__(self) -> Iterator[object]:
        for value in super().__iter__():
            self.value_pulls += 1
            yield value


class AppServerDiagnosticsTests(unittest.TestCase):
    def test_client_uses_the_adapter_diagnostic_owner(self) -> None:
        client = codex_app_server_client(endpoint="stdio://")

        self.assertIs(type(client._diagnostics), AppServerDiagnosticState)

    def test_diagnostic_state_snapshot_preserves_existing_fact_shape(self) -> None:
        state = AppServerDiagnosticState(
            notification_capacity=4,
            server_request_capacity=2,
        )
        state.record_connect_failure()
        state.record_overflow(QueueDiagnosticName.NOTIFICATION)

        facts = state.snapshot(
            transport_open=False,
            initialized=False,
            reconnecting=False,
            connection_epoch=3,
            closing=False,
            worker_running=False,
            notification_depth=4,
            server_request_depth=1,
        )

        self.assertEqual(facts.state, ConnectionDiagnosticState.DISCONNECTED)
        self.assertEqual(facts.connection_epoch, 3)
        self.assertEqual(facts.reconnect_count, 2)
        self.assertTrue(facts.worker_degraded)
        self.assertEqual(facts.last_failure_code, DiagnosticFailureCode.NOTIFICATION_OVERFLOW)
        queue_facts = [
            (queue.name, queue.capacity, queue.depth, queue.overflow_count)
            for queue in facts.queues
        ]
        self.assertEqual(
            queue_facts,
            [
                (QueueDiagnosticName.NOTIFICATION, 4, 4, 1),
                (QueueDiagnosticName.SERVER_REQUEST, 2, 1, 0),
            ],
        )
        self.assertEqual(
            facts,
            state.snapshot(
                transport_open=False,
                initialized=False,
                reconnecting=False,
                connection_epoch=3,
                closing=False,
                worker_running=False,
                notification_depth=4,
                server_request_depth=1,
            ),
        )

    def test_transport_summary_is_fixed_content_free_and_bounded(self) -> None:
        summary = summarize_transport_message(
            {
                "method": "turn/plan/updated",
                "params": {
                    "threadId": "native-thread-id-sentinel",
                    "turnId": "native-turn-id-sentinel",
                    "message": "prompt-content-sentinel",
                    "delta": "delta-content-sentinel",
                    "permissions": {"permission-key-sentinel": "permission-value-sentinel"},
                    "questions": [
                        {
                            "id": "native-request-id-sentinel",
                            "question": "prompt-content-sentinel",
                        }
                    ],
                    "item": {
                        "id": "native-item-id-sentinel",
                        "command": "command-content-sentinel",
                        "cwd": "/workspace/inbound-media/private-path-sentinel",
                        "changes": [{"path": "C:\\workspace\\private-path-sentinel"}],
                    },
                },
            },
            max_preview_chars=12,
        )

        self.assertEqual(
            set(summary),
            {
                "schema",
                "record_type",
                "transport_shape",
                "method_category",
                "method_kind",
                "direction",
                "error_present",
                "body",
                "preview_limit",
                "preview_emitted",
            },
        )
        self.assertEqual(summary["schema"], DEBUG_SCHEMA)
        self.assertEqual(summary["record_type"], "transport")
        self.assertEqual(summary["transport_shape"], "notification")
        self.assertEqual(summary["method_category"], "turn")
        self.assertEqual(summary["method_kind"], "plan_updated")
        self.assertEqual(summary["direction"], "notification")
        self.assertEqual(summary["preview_limit"], 12)
        self.assertFalse(summary["preview_emitted"])
        body = cast(dict[str, object], summary["body"])
        self.assertEqual(
            set(body),
            {
                "value_type",
                "scalar_length",
                "scalar_length_capped",
                "collection_count",
                "collection_count_capped",
                "samples",
            },
        )
        self.assertEqual(body["value_type"], "mapping")
        self.assertLessEqual(cast(int, body["collection_count"]), MAX_DEBUG_COLLECTION_COUNT)
        self.assertLessEqual(len(cast(list[object], body["samples"])), MAX_DEBUG_SAMPLE_ITEMS)
        _assert_no_sensitive_text(self, summary)

    def test_response_and_unknown_payload_summaries_never_expose_ids_or_keys(self) -> None:
        response = summarize_transport_message(
            {
                "id": "native-response-id-sentinel",
                "error": {
                    "code": 500,
                    "message": "prompt-content-sentinel token-credential-sentinel",
                },
            }
        )
        self.assertEqual(response["transport_shape"], "response")
        self.assertTrue(response["error_present"])
        _assert_no_sensitive_text(self, response)

        payload = {
            f"native-request-id-sentinel-{index}": "permission-value-sentinel"
            for index in range(MAX_DEBUG_COLLECTION_COUNT)
        }
        unknown = summarize_transport_message({"method": "vendor/unknown", "params": payload})
        body = cast(dict[str, object], unknown["body"])
        self.assertEqual(unknown["method_category"], "unknown")
        self.assertEqual(unknown["method_kind"], "unknown")
        self.assertEqual(body["collection_count"], MAX_DEBUG_COLLECTION_COUNT)
        self.assertFalse(body["collection_count_capped"])
        _assert_no_sensitive_text(self, unknown)

        plus_one = summarize_transport_message(
            {
                "method": "vendor/unknown",
                "params": {
                    f"native-request-id-sentinel-{index}": "permission-value-sentinel"
                    for index in range(MAX_DEBUG_COLLECTION_COUNT + 1)
                },
            }
        )
        self.assertEqual(plus_one["method_category"], "invalid")
        _assert_no_sensitive_text(self, plus_one)

    def test_text_summary_has_no_preview_and_validates_its_bounded_policy(self) -> None:
        summary = summarize_text("hello world", max_preview_chars=7)

        self.assertEqual(
            summary,
            {
                "schema": DEBUG_SCHEMA,
                "record_type": "text",
                "text_length": 11,
                "text_length_capped": False,
                "text_sha256": "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
                "fingerprint_redacted": False,
                "path_or_endpoint_redacted": False,
                "preview_limit": 7,
                "preview_emitted": False,
            },
        )
        for invalid in (0, -1, MAX_PREVIEW_CHARS + 1, True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    summarize_text("safe", max_preview_chars=invalid)

    def test_text_path_and_endpoint_summaries_fail_closed(self) -> None:
        for value in (
            "/workspace/inbound-media/private-path-sentinel",
            "/secret",
            "cwd=/tmp/x",
            "C:\\workspace\\private-path-sentinel",
            "cwd=C:\\x",
            "cwd=\\\\server\\share\\private-path-sentinel",
            "https://user:token-credential-sentinel@example.invalid/path",
            "unix://user:token-credential-sentinel@example.invalid/socket",
            "custom+agent://user:token-credential-sentinel@example.invalid/path",
        ):
            with self.subTest(value=value):
                summary = summarize_text(value)
                self.assertTrue(summary["path_or_endpoint_redacted"])
                self.assertIsNone(summary["text_sha256"])
                self.assertTrue(summary["fingerprint_redacted"])
                self.assertFalse(summary["preview_emitted"])
                self.assertNotIn(value, repr(summary))
                _assert_no_sensitive_text(self, summary)

    def test_scalar_and_key_caps_fail_closed_before_fingerprinting(self) -> None:
        oversized = "x" * (MAX_DEBUG_SCALAR_CHARACTERS + 1)
        with patch("imagent.applications.adapters.appserver.diagnostics._sha256_text") as sha256:
            text = summarize_text(oversized)
        self.assertEqual(text["text_length"], MAX_DEBUG_SCALAR_CHARACTERS)
        self.assertTrue(text["text_length_capped"])
        self.assertTrue(text["fingerprint_redacted"])
        self.assertFalse(text["path_or_endpoint_redacted"])
        self.assertIsNone(text["text_sha256"])
        sha256.assert_not_called()

        path_key = "cwd=/tmp/x"
        with (
            patch("imagent.applications.adapters.appserver.diagnostics._sha256_text") as sha256,
            patch("imagent.applications.adapters.appserver.diagnostics.logger.debug") as debug,
        ):
            emit_event(
                component="appserver.client",
                event="appserver.connect.started",
                data={path_key: "safe", oversized: "safe"},
            )
        record = cast(dict[str, object], debug.call_args.args[1])
        samples = cast(list[dict[str, object]], cast(dict[str, object], record["data"])["samples"])
        self.assertEqual(len(samples), 2)
        self.assertIsNone(samples[0]["key_sha256"])
        self.assertTrue(samples[0]["key_redacted"])
        self.assertIsNone(samples[1]["key_sha256"])
        self.assertTrue(samples[1]["key_redacted"])
        self.assertEqual(samples[1]["key_length"], MAX_DEBUG_SCALAR_CHARACTERS)
        self.assertTrue(samples[1]["key_length_capped"])
        sha256.assert_not_called()
        _assert_no_sensitive_text(self, record)

    def test_structural_samples_never_consume_a_fifth_native_item(self) -> None:
        mapping = _CountingMapping(MAX_DEBUG_SAMPLE_ITEMS + 1)
        sequence = _CountingList(MAX_DEBUG_SAMPLE_ITEMS + 1)
        with patch("imagent.applications.adapters.appserver.diagnostics.logger.debug") as debug:
            emit_event(
                component="appserver.client",
                event="appserver.connect.started",
                data=mapping,
            )
            mapping_record = cast(dict[str, object], debug.call_args.args[1])
            emit_event(
                component="appserver.client",
                event="appserver.connect.started",
                data=sequence,
            )
            sequence_record = cast(dict[str, object], debug.call_args.args[1])

        self.assertEqual(mapping.key_pulls, MAX_DEBUG_SAMPLE_ITEMS)
        self.assertEqual(sequence.value_pulls, MAX_DEBUG_SAMPLE_ITEMS)
        for record in (mapping_record, sequence_record):
            data = cast(dict[str, object], record["data"])
            self.assertEqual(len(cast(list[object], data["samples"])), MAX_DEBUG_SAMPLE_ITEMS)

    def test_health_counter_cap_is_fixed(self) -> None:
        with patch("imagent.applications.adapters.appserver.diagnostics.logger.debug") as debug:
            mark_appserver_health(
                connection_epoch=MAX_DEBUG_COUNTER + 1,
                retry_attempt=MAX_DEBUG_COUNTER + 1,
            )
        record = cast(dict[str, object], debug.call_args.args[1])
        self.assertEqual(record["connection_epoch"], MAX_DEBUG_COUNTER)
        self.assertTrue(record["connection_epoch_capped"])
        self.assertEqual(record["retry_attempt"], MAX_DEBUG_COUNTER)
        self.assertTrue(record["retry_attempt_capped"])

    def test_event_and_health_logs_normalize_unknown_kwargs(self) -> None:
        with patch("imagent.applications.adapters.appserver.diagnostics.logger.debug") as debug:
            emit_event(
                component="component-sentinel",
                event="event-sentinel",
                level="level-sentinel",
                message="prompt-content-sentinel",
                data={
                    f"token-credential-sentinel-{index}": "permission-value-sentinel"
                    for index in range(MAX_DEBUG_COLLECTION_COUNT + 1)
                },
                connection_mode="https://user:token-credential-sentinel@example.invalid/path",
                connection_epoch=MAX_DEBUG_COLLECTION_COUNT + 1,
                endpoint="https://user:token-credential-sentinel@example.invalid/path",
            )
        event_record = cast(dict[str, object], debug.call_args.args[1])
        self.assertEqual(event_record["schema"], DEBUG_SCHEMA)
        self.assertEqual(event_record["record_type"], "event")
        self.assertEqual(event_record["component"], "other")
        self.assertEqual(event_record["event"], "appserver.other")
        self.assertEqual(event_record["level"], "DEBUG")
        self.assertEqual(event_record["ignored_field_count"], 1)
        event_data = cast(dict[str, object], event_record["data"])
        self.assertEqual(event_data["collection_count"], MAX_DEBUG_COLLECTION_COUNT)
        self.assertTrue(event_data["collection_count_capped"])
        self.assertLessEqual(len(cast(list[object], event_data["samples"])), MAX_DEBUG_SAMPLE_ITEMS)
        _assert_no_sensitive_text(self, event_record)

        with patch("imagent.applications.adapters.appserver.diagnostics.logger.debug") as debug:
            mark_appserver_health(
                connected=True,
                ready=True,
                status="connected",
                mode="external",
                transport="tcp-websocket",
                ownership="external",
                connection_epoch=3,
                reconnect_enabled=True,
                local_image_paths=False,
                retry_attempt=2,
                error_type="token-credential-sentinel",
                endpoint="https://user:token-credential-sentinel@example.invalid/path",
                rehydration={"cwd": "/workspace/inbound-media/private-path-sentinel"},
            )
        health_record = cast(dict[str, object], debug.call_args.args[1])
        self.assertEqual(health_record["schema"], DEBUG_SCHEMA)
        self.assertEqual(health_record["record_type"], "health")
        self.assertEqual(health_record["health"], "unknown")
        self.assertTrue(health_record["failure_present"])
        self.assertEqual(health_record["ignored_field_count"], 2)
        _assert_no_sensitive_text(self, health_record)

    def test_client_protocol_logging_uses_the_same_content_free_path(self) -> None:
        client = codex_app_server_client(endpoint="stdio://")
        with patch("imagent.applications.adapters.appserver.diagnostics.logger.debug") as debug:
            client._trace_protocol_message(
                stage="received",
                payload={
                    "id": "native-response-id-sentinel",
                    "result": {"token-credential-sentinel": "prompt-content-sentinel"},
                },
            )
        record = cast(dict[str, object], debug.call_args.args[1])
        self.assertEqual(record["component"], "appserver.protocol")
        self.assertEqual(record["event"], "appserver.protocol.received")
        _assert_no_sensitive_text(self, record)

    def test_historical_diagnostic_modules_are_not_importable(self) -> None:
        for module_name in (
            "imagent.applications.appserver_client.diagnostic_facts",
            "imagent.applications.appserver_client.diagnostics",
            "imagent.applications.appserver_client.runtime_diagnostics",
        ):
            with self.subTest(module_name=module_name):
                try:
                    spec = importlib.util.find_spec(module_name)
                except ModuleNotFoundError:
                    spec = None
                self.assertIsNone(spec)


if __name__ == "__main__":
    unittest.main()

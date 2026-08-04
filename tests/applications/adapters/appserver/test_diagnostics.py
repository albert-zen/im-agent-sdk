from __future__ import annotations

import importlib.util
import unittest

from imagent.applications import codex_app_server_client
from imagent.applications.adapters.appserver.diagnostics import (
    AppServerDiagnosticState,
    summarize_text,
    summarize_transport_message,
)
from imagent.interaction.diagnostics import (
    ConnectionDiagnosticState,
    DiagnosticFailureCode,
    QueueDiagnosticName,
)


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

    def test_legacy_summary_and_text_helpers_keep_existing_shapes(self) -> None:
        self.assertEqual(
            summarize_text("  hello   world ", max_preview_chars=20),
            {
                "text_preview": "hello world",
                "text_length": 16,
                "text_sha256": "79702ae289f6be61eab589f2aea9f6dcc9ff10e63c570761ba77a313aa485bd5",
            },
        )
        summary = summarize_transport_message(
            {
                "method": "turn/started",
                "params": {"threadId": "thread-1", "turnId": "turn-1"},
            }
        )

        self.assertEqual(summary["transport_shape"], "notification")
        self.assertEqual(summary["method"], "turn/started")
        self.assertEqual(summary["thread_id"], "thread-1")
        self.assertEqual(summary["turn_id"], "turn-1")

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

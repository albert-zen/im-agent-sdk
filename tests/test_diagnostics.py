from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from datetime import UTC, datetime
from typing import cast

from imagent.applications.t3 import T3ApplicationAdapter
from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import ProjectMode, ThreadRef
from imagent.diagnostics import (
    ApplicationDiagnosticFacts,
    ConnectionDiagnosticFacts,
    ConnectionDiagnosticState,
    DiagnosticsSnapshot,
    QueueDiagnosticFacts,
    QueueDiagnosticName,
    summarize_projection_health,
)
from imagent.gateway import ImAgentGateway
from imagent.projections import ProjectionWorkerHealth, ProjectionWorkerState
from imagent.testing.fakes import FakeAgentApplicationAdapter


class _UnusedT3Client:
    async def shell_snapshot(self):
        return {}

    async def thread_detail(self, thread_id: str):
        del thread_id
        return {}

    async def dispatch(self, command):
        del command
        return {}


class DiagnosticsSurfaceTests(unittest.TestCase):
    def test_queue_fact_vocabulary_and_bounds_are_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "fixed vocabulary"):
            QueueDiagnosticFacts(
                cast(QueueDiagnosticName, "consumer-controlled-name"),
                capacity=1,
                depth=0,
            )
        with self.assertRaisesRegex(ValueError, "within capacity"):
            QueueDiagnosticFacts(
                QueueDiagnosticName.NOTIFICATION,
                capacity=1,
                depth=2,
            )
        queue = QueueDiagnosticFacts(
            QueueDiagnosticName.NOTIFICATION,
            capacity=1,
            depth=0,
        )
        with self.assertRaisesRegex(ValueError, "must be unique"):
            ConnectionDiagnosticFacts(
                state=ConnectionDiagnosticState.READY,
                connection_epoch=1,
                reconnect_count=0,
                worker_running=True,
                worker_degraded=False,
                queues=(queue, queue),
            )

    def test_projection_summary_is_identity_free_and_bounds_gap_codes(self) -> None:
        secret_thread = "native-thread-secret"
        health = (
            ProjectionWorkerHealth(
                thread_ref=ThreadRef("codex-a", secret_thread),
                state=ProjectionWorkerState.RETRYING,
                restart_count=2,
                delivery_failure_count=1,
                event_overflow_count=3,
                last_subscription_error="secret transport failure",
                last_event_gap="application_event_fanout_overflow",
                interactive_request_recovery_degraded=True,
                updated_at=datetime.now(UTC),
            ),
            ProjectionWorkerHealth(
                thread_ref=ThreadRef("codex-a", "another-native-thread"),
                state=ProjectionWorkerState.RUNNING,
                last_gap="secret-route:checkpoint_out_of_window",
                last_event_gap="consumer-controlled-unbounded-value",
            ),
        )

        facts = summarize_projection_health(health)

        self.assertEqual(facts.worker_count, 2)
        self.assertEqual(facts.running_count, 1)
        self.assertEqual(facts.retrying_count, 1)
        self.assertEqual(facts.degraded_count, 2)
        self.assertEqual(facts.restart_count, 2)
        self.assertEqual(facts.event_overflow_count, 3)
        self.assertEqual(facts.request_recovery_degraded_count, 1)
        self.assertEqual(facts.recovery_gap_count, 2)
        self.assertEqual(
            facts.recovery_gap_codes,
            (
                "application_event_fanout_overflow",
                "checkpoint_out_of_window",
                "other",
            ),
        )
        serialized = json.dumps(asdict(facts), default=str)
        self.assertNotIn(secret_thread, serialized)
        self.assertNotIn("secret transport failure", serialized)
        self.assertNotIn("consumer-controlled-unbounded-value", serialized)
        self.assertNotIn("secret-route", serialized)

    def test_gateway_snapshot_is_stable_read_only_and_non_authoritative(self) -> None:
        application = FakeAgentApplicationAdapter(
            application_instance_id="fake-agent",
            project_mode=ProjectMode.FLAT,
        )
        gateway = ImAgentGateway(
            channels=[],
            applications=[application],
            bindings=InMemoryBindingRepository(),
            startup_buffer_max_pending=7,
        )

        first = gateway.diagnostics_snapshot()
        second = gateway.diagnostics_snapshot()

        self.assertIsInstance(first, DiagnosticsSnapshot)
        self.assertFalse(first.authoritative)
        self.assertEqual(first.schema_version, 1)
        self.assertEqual(first.applications, (ApplicationDiagnosticFacts("fake-agent", "fake"),))
        self.assertEqual(
            first.gateway.startup_queue,
            QueueDiagnosticFacts(QueueDiagnosticName.GATEWAY_STARTUP, capacity=7, depth=0),
        )
        self.assertEqual(first.applications, second.applications)
        self.assertEqual(first.projections, second.projections)
        self.assertEqual(first.gateway, second.gateway)
        self.assertLessEqual(first.generated_at, second.generated_at)

    def test_t3_reports_no_synthetic_long_lived_connection(self) -> None:
        adapter = T3ApplicationAdapter(
            application_instance_id="t3-a",
            client=_UnusedT3Client(),
        )

        facts = adapter.diagnostic_facts()

        self.assertEqual(facts.application_instance_id, "t3-a")
        self.assertEqual(facts.kind, "t3")
        self.assertIsNone(facts.connection)


if __name__ == "__main__":
    unittest.main()

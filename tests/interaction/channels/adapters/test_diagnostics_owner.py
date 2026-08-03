from __future__ import annotations

import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError

from imagent.interaction.channels.adapters.diagnostics import (
    NativeChannelDiagnosticState,
    NativeQueueDiagnosticSnapshot,
)


class NativeChannelDiagnosticsOwnershipTests(unittest.TestCase):
    def test_state_transitions_and_queue_facts_remain_bounded_and_immutable(self) -> None:
        state = NativeChannelDiagnosticState()
        initial = state.snapshot()
        self.assertEqual(initial.state, "disconnected")
        self.assertEqual(initial.connection_epoch, 0)
        self.assertEqual(initial.reconnect_count, 0)
        self.assertFalse(initial.worker_running)

        state.update(status="connecting")
        state.update(status="connected", connected=True)
        state.update(status="connected", connected=True)
        state.update(status="reconnecting")
        state.update(status="reconnecting")
        queue = NativeQueueDiagnosticSnapshot(
            name="channel_inbound",
            capacity=64,
            depth=3,
            overflow_count=2,
        )
        snapshot = state.snapshot(queues=(queue,))
        self.assertEqual(snapshot.state, "reconnecting")
        self.assertEqual(snapshot.connection_epoch, 1)
        self.assertEqual(snapshot.reconnect_count, 1)
        self.assertTrue(snapshot.worker_running)
        self.assertTrue(snapshot.worker_degraded)
        self.assertEqual(snapshot.last_failure_code, "transport_failed")
        self.assertEqual(snapshot.queues, (queue,))
        with self.assertRaises(FrozenInstanceError):
            setattr(queue, "depth", 4)

        state.update(status="stopped")
        stopped = state.snapshot()
        self.assertEqual(stopped.state, "disconnected")
        self.assertFalse(stopped.worker_running)

    def test_historical_native_diagnostics_path_is_absent(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from importlib.util import find_spec; "
                    "from imagent.interaction.channels.adapters.diagnostics import "
                    "NativeChannelDiagnosticState; "
                    "assert find_spec('imagent.channels.native') is None"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

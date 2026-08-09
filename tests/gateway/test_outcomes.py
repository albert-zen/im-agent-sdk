from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from imagent.gateway.outcomes import (
    Failed,
    OutcomeStatus,
    OutcomeUnknown,
    Partial,
    Succeeded,
)


class GatewayOutcomeAlgebraTests(unittest.TestCase):
    def test_closed_discriminants_and_value_shapes_are_exact(self) -> None:
        self.assertEqual(
            {status.value for status in OutcomeStatus},
            {"succeeded", "failed", "partial", "outcome_unknown"},
        )
        self.assertEqual(Succeeded("value").status, OutcomeStatus.SUCCEEDED)
        self.assertEqual(Failed("error").status, OutcomeStatus.FAILED)
        self.assertEqual(Partial("value", "error").status, OutcomeStatus.PARTIAL)
        self.assertEqual(
            OutcomeUnknown("error").status,
            OutcomeStatus.OUTCOME_UNKNOWN,
        )

    def test_outcomes_are_frozen(self) -> None:
        outcome = Succeeded("value")
        with self.assertRaises(FrozenInstanceError):
            outcome.value = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()

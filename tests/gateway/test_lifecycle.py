from __future__ import annotations

import importlib
import unittest

from imagent.gateway.lifecycle import (
    GatewayNotRunning,
    GatewayStartupAdmission,
    GatewayStartupOverflow,
)


class GatewayLifecycleHelperTests(unittest.TestCase):
    def test_helpers_have_one_lifecycle_owner_and_old_module_is_absent(self) -> None:
        self.assertEqual(GatewayStartupAdmission.__module__, "imagent.gateway.lifecycle")
        self.assertEqual(GatewayStartupOverflow.__module__, "imagent.gateway.lifecycle")
        self.assertEqual(GatewayNotRunning.__module__, "imagent.gateway.lifecycle")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("imagent.gateway_startup")

    def test_admission_preserves_fifo_and_sticky_overflow(self) -> None:
        admission = GatewayStartupAdmission[str](max_pending=2)
        admission.admit("first")
        admission.admit("second")

        with self.assertRaises(GatewayStartupOverflow) as overflow:
            admission.admit("third")
        with self.assertRaises(GatewayStartupOverflow) as repeated:
            admission.admit("later")

        self.assertIs(repeated.exception, overflow.exception)
        self.assertEqual(admission.popleft(), "first")
        self.assertEqual(admission.popleft(), "second")
        self.assertEqual(admission.overflow_count, 1)


if __name__ == "__main__":
    unittest.main()

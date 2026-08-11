from __future__ import annotations

import importlib.util
import inspect
import unittest
from importlib import import_module
from pathlib import Path
from typing import get_type_hints

import imagent.gateway as gateway_package
from imagent import Gateway as RootGateway
from imagent.gateway import (
    Gateway,
    GatewayExtensions,
    GatewayLimits,
)
from imagent.gateway.composition import (
    GatewayExtensions as CompositionGatewayExtensions,
)
from imagent.gateway.composition import GatewayLimits as CompositionGatewayLimits
from imagent.gateway.orchestration import _GatewayRuntime
from imagent.interaction.controllers import InboundController, RequestPresenter


class GatewayPackageRootTests(unittest.TestCase):
    def test_canonical_gateway_is_one_real_public_class_without_private_inputs(self) -> None:
        from imagent.gateway.runtime import Gateway as RuntimeGateway

        self.assertIs(Gateway, RuntimeGateway)
        self.assertIs(RootGateway, RuntimeGateway)
        self.assertEqual(Gateway.__module__, "imagent.gateway.runtime")
        self.assertFalse(issubclass(Gateway, _GatewayRuntime))
        parameters = inspect.signature(Gateway).parameters
        self.assertNotIn("repositories", parameters)
        self.assertNotIn("effect_executor", parameters)
        self.assertNotIn("session", parameters)
        self.assertTrue(callable(Gateway.diagnostics))
        self.assertTrue(callable(Gateway.wait_closed))
        self.assertTrue(callable(Gateway.run))
        self.assertFalse(hasattr(Gateway, "diagnostics_snapshot"))

    def test_public_gateway_surface_resolves_to_target_package(self) -> None:
        module_path = Path(gateway_package.__file__).resolve()
        self.assertEqual(module_path.name, "__init__.py")
        self.assertEqual(module_path.parent.name, "gateway")
        self.assertEqual(_GatewayRuntime.__module__, "imagent.gateway.orchestration")

    def test_package_facade_is_finite_and_omits_internal_runtime_seams(self) -> None:
        self.assertEqual(
            gateway_package.__all__,
            [
                "ActionResult",
                "ActionValue",
                "ApplicationActions",
                "ConversationActions",
                "Gateway",
                "GatewayExtensions",
                "GatewayLimits",
                "MissingBindingError",
                "ReadOutcome",
                "StaleBindingError",
            ],
        )
        for name in (
            "ClaimedInbound",
            "InboundAdmissionService",
            "InboundContentTransformer",
            "InboundFailurePhase",
            "InboundFailurePresenter",
            "_GatewayRuntime",
            "_GatewayRuntimeDependencies",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(gateway_package, name))

    def test_composition_exports_preserve_exact_object_identity(self) -> None:
        self.assertIs(GatewayExtensions, CompositionGatewayExtensions)
        self.assertIs(GatewayLimits, CompositionGatewayLimits)
        self.assertEqual(GatewayExtensions.__module__, "imagent.gateway.composition")
        self.assertEqual(GatewayLimits.__module__, "imagent.gateway.composition")

    def test_composition_runtime_type_hints_resolve_canonical_controller_contracts(self) -> None:
        hints = get_type_hints(GatewayExtensions)
        self.assertEqual(
            hints["controller"],
            InboundController | None,
        )
        self.assertEqual(
            hints["request_presenter"],
            RequestPresenter | None,
        )

    def test_removed_historical_composition_module_cannot_be_imported(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("imagent.gateway_composition")

    def test_historical_gateway_modules_are_absent(self) -> None:
        self.assertIsNone(importlib.util.find_spec("imagent.inbound_admission"))
        self.assertIsNone(importlib.util.find_spec("imagent.inbound_content"))
        with self.assertRaises(ModuleNotFoundError):
            import_module("imagent.inbound_failures")


if __name__ == "__main__":
    unittest.main()

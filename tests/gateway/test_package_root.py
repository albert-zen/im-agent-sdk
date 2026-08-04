from __future__ import annotations

import importlib.util
import unittest
from importlib import import_module
from pathlib import Path
from subprocess import run
from sys import executable

import imagent.gateway as gateway_package
from imagent.gateway import (
    ClaimedInbound,
    GatewayExtensions,
    GatewayLimits,
    GatewayRepositories,
    ImAgentGateway,
    InboundAdmissionService,
    InboundFailurePhase,
    InboundFailurePresenter,
)
from imagent.gateway.admission import ClaimedInbound as AdmissionClaimedInbound
from imagent.gateway.admission import (
    InboundAdmissionService as AdmissionInboundAdmissionService,
)
from imagent.gateway.input import InboundContentTransformer as InputInboundContentTransformer
from imagent.gateway.input import (
    InboundFailurePhase as InputInboundFailurePhase,
)
from imagent.gateway.input import (
    InboundFailurePresenter as InputInboundFailurePresenter,
)
from imagent.gateway_composition import (
    GatewayExtensions as CompositionGatewayExtensions,
)
from imagent.gateway_composition import GatewayLimits as CompositionGatewayLimits
from imagent.gateway_composition import (
    GatewayRepositories as CompositionGatewayRepositories,
)


class GatewayPackageRootTests(unittest.TestCase):
    def test_public_gateway_surface_resolves_to_target_package(self) -> None:
        module_path = Path(gateway_package.__file__).resolve()
        self.assertEqual(module_path.name, "__init__.py")
        self.assertEqual(module_path.parent.name, "gateway")
        self.assertEqual(ImAgentGateway.__module__, "imagent.gateway")

    def test_composition_exports_preserve_exact_object_identity(self) -> None:
        self.assertIs(GatewayExtensions, CompositionGatewayExtensions)
        self.assertIs(GatewayLimits, CompositionGatewayLimits)
        self.assertIs(GatewayRepositories, CompositionGatewayRepositories)

    def test_admission_exports_preserve_exact_object_identity(self) -> None:
        self.assertIs(ClaimedInbound, AdmissionClaimedInbound)
        self.assertIs(InboundAdmissionService, AdmissionInboundAdmissionService)
        self.assertEqual(ClaimedInbound.__module__, "imagent.gateway.admission")
        self.assertEqual(InboundAdmissionService.__module__, "imagent.gateway.admission")

    def test_historical_admission_module_is_not_importable_in_a_clean_process(self) -> None:
        result = run(
            [
                executable,
                "-c",
                "from imagent.gateway import ClaimedInbound; "
                "from imagent.gateway.admission import ClaimedInbound as target; "
                "assert ClaimedInbound is target; "
                "import imagent.inbound_admission",
            ],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ModuleNotFoundError", result.stderr)
        self.assertIn("imagent.inbound_admission", result.stderr)

    def test_inbound_content_transformer_facades_preserve_exact_object_identity(self) -> None:
        self.assertIs(gateway_package.InboundContentTransformer, InputInboundContentTransformer)

    def test_historical_inbound_content_module_is_absent(self) -> None:
        self.assertIsNone(importlib.util.find_spec("imagent.inbound_content"))

    def test_inbound_failure_exports_preserve_exact_object_identity(self) -> None:
        self.assertIs(InboundFailurePhase, InputInboundFailurePhase)
        self.assertIs(InboundFailurePresenter, InputInboundFailurePresenter)

    def test_historical_inbound_failure_module_is_absent(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            import_module("imagent.inbound_failures")


if __name__ == "__main__":
    unittest.main()

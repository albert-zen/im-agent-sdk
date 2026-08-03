from __future__ import annotations

import unittest
from pathlib import Path

import imagent.gateway as gateway_package
from imagent.gateway import (
    GatewayExtensions,
    GatewayLimits,
    GatewayRepositories,
    ImAgentGateway,
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


if __name__ == "__main__":
    unittest.main()

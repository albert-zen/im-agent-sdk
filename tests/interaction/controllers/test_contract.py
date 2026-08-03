from __future__ import annotations

import unittest

from imagent import controllers as controllers_facade
from imagent.controllers import base as legacy_base
from imagent.interaction.controllers import ControllerActions, InboundController


class ControllerContractOwnershipTests(unittest.TestCase):
    def test_controller_facade_reexports_exact_owner_objects(self) -> None:
        self.assertIs(controllers_facade.ControllerActions, ControllerActions)
        self.assertIs(controllers_facade.InboundController, InboundController)

    def test_legacy_base_does_not_retain_a_contract_implementation(self) -> None:
        self.assertFalse(hasattr(legacy_base, "ControllerActions"))
        self.assertFalse(hasattr(legacy_base, "InboundController"))
        self.assertEqual(
            set(controllers_facade.__all__),
            {
                "ControllerActions",
                "InboundController",
                "MarkdownRequestPresenter",
                "RequestPresentation",
                "RequestPresenter",
                "SlashController",
            },
        )


if __name__ == "__main__":
    unittest.main()

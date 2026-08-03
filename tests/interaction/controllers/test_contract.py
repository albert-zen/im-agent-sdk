from __future__ import annotations

import unittest
from importlib.util import find_spec

from imagent import controllers as controllers_facade
from imagent.interaction.controllers import ControllerActions, InboundController


class ControllerContractOwnershipTests(unittest.TestCase):
    def test_controller_facade_reexports_exact_owner_objects(self) -> None:
        self.assertIs(controllers_facade.ControllerActions, ControllerActions)
        self.assertIs(controllers_facade.InboundController, InboundController)

    def test_legacy_base_is_removed_after_its_leaves_move(self) -> None:
        self.assertIsNone(find_spec("imagent.controllers.base"))
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

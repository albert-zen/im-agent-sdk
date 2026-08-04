from __future__ import annotations

import unittest
from subprocess import run
from sys import executable

import imagent.projections as historical_projections
from imagent.contracts import ThreadRef
from imagent.gateway.projection import derive_projection_delivery_id
from imagent.interaction.messages import ConversationRef


class ProjectionCheckpointOwnershipTests(unittest.TestCase):
    def test_gateway_projection_facade_preserves_exact_delivery_id_owner(self) -> None:
        from imagent.gateway.projection.checkpoints import (
            derive_projection_delivery_id as owner_derivation,
        )

        self.assertIs(derive_projection_delivery_id, owner_derivation)

    def test_derivation_preserves_stable_destination_item_identity(self) -> None:
        conversation = ConversationRef("channel-a", "conversation-a")
        thread = ThreadRef("application-a", "thread-a")

        delivery_id = derive_projection_delivery_id(conversation, thread, "item-a")

        self.assertEqual(
            delivery_id,
            derive_projection_delivery_id(conversation, thread, "item-a"),
        )
        self.assertNotEqual(
            delivery_id,
            derive_projection_delivery_id(conversation, thread, "item-b"),
        )
        self.assertNotEqual(
            delivery_id,
            derive_projection_delivery_id(conversation, thread, "item-a", segment_index=1),
        )
        self.assertNotEqual(
            delivery_id,
            derive_projection_delivery_id(
                ConversationRef("channel-a", "conversation-b"),
                thread,
                "item-a",
            ),
        )

    def test_historical_projection_symbol_is_absent(self) -> None:
        self.assertFalse(hasattr(historical_projections, "derive_projection_delivery_id"))
        result = run(
            [
                executable,
                "-c",
                "from imagent.projections import derive_projection_delivery_id",
            ],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ImportError", result.stderr)
        self.assertIn("derive_projection_delivery_id", result.stderr)

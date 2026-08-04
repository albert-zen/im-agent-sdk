from __future__ import annotations

import importlib.util
import unittest

import imagent.gateway.delivery as delivery_facade
from imagent import contracts
from imagent.gateway.delivery import DeliverySubmissionOrigin
from imagent.gateway.delivery import submissions as submissions_owner
from imagent.gateway.persistence import state_contracts as state_owner


class DeliverySubmissionOwnershipTests(unittest.TestCase):
    def test_facades_use_exact_owner_objects_and_old_module_is_absent(self) -> None:
        helper_names = (
            "derive_delivery_payload_fingerprint",
            "derive_delivery_submission_id",
            "derive_delivery_target_fingerprint",
            "derive_destination_delivery_id",
        )
        for name in helper_names:
            with self.subTest(name=name):
                owner = getattr(submissions_owner, name)
                self.assertIs(getattr(delivery_facade, name), owner)
                self.assertEqual(owner.__module__, submissions_owner.__name__)
                self.assertNotIn(name, contracts.__all__)
                self.assertFalse(hasattr(contracts, name))
                self.assertFalse(hasattr(state_owner, name))
        self.assertTrue(hasattr(submissions_owner, "_content_identity"))
        self.assertFalse(hasattr(state_owner, "_content_identity"))
        self.assertTrue(hasattr(submissions_owner, "_canonical_metadata"))
        self.assertFalse(hasattr(state_owner, "_canonical_metadata"))
        self.assertIs(submissions_owner.DeliverySubmissionOrigin, DeliverySubmissionOrigin)
        self.assertIs(delivery_facade.DeliverySubmissionOrigin, DeliverySubmissionOrigin)
        self.assertFalse(hasattr(contracts, "DeliverySubmissionOrigin"))
        self.assertNotIn("DeliverySubmissionOrigin", contracts.__all__)
        self.assertIsNone(importlib.util.find_spec("imagent.delivery_submissions"))


if __name__ == "__main__":
    unittest.main()

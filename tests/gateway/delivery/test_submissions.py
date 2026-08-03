from __future__ import annotations

import importlib.util
import unittest

import imagent.gateway.delivery as delivery_facade
from imagent import contracts
from imagent.gateway.delivery import submissions as submissions_owner


class DeliverySubmissionOwnershipTests(unittest.TestCase):
    def test_facades_use_exact_owner_objects_and_old_module_is_absent(self) -> None:
        names = (
            "DeliverySubmissionOrigin",
            "derive_delivery_payload_fingerprint",
            "derive_delivery_submission_id",
            "derive_delivery_target_fingerprint",
            "derive_destination_delivery_id",
        )
        for name in names:
            with self.subTest(name=name):
                owner = getattr(submissions_owner, name)
                self.assertIs(getattr(delivery_facade, name), owner)
                self.assertIs(getattr(contracts, name), owner)
        self.assertIsNone(importlib.util.find_spec("imagent.delivery_submissions"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from imagent import contracts
from imagent.interaction import channels
from imagent.interaction.messages import TextContent
from imagent.interaction.operations import ContractViolation


class ChannelReceiptContractTests(unittest.TestCase):
    def test_contracts_facade_reexports_exact_receipt_owners(self) -> None:
        names = (
            "DeliveryItemReceipt",
            "DeliveryItemStatus",
            "DeliveryReceipt",
            "DeliveryReceiptStatus",
            "DeliverySegmentReceipt",
            "DeliverySegmentStatus",
            "validate_delivery_receipt",
            "validate_delivery_receipt_for_content",
        )

        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(contracts, name), getattr(channels, name))

    def test_retryable_receipt_rejects_native_acceptance_identity(self) -> None:
        receipt = channels.DeliveryReceipt(
            status=channels.DeliveryReceiptStatus.RETRYABLE_FAILURE,
            native_message_id="native-1",
        )

        with self.assertRaisesRegex(ContractViolation, "acceptance identity"):
            channels.validate_delivery_receipt(receipt)

    def test_receipt_indexes_are_checked_against_submitted_content(self) -> None:
        receipt = channels.DeliveryReceipt(
            status=channels.DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            items=(
                channels.DeliveryItemReceipt(
                    content_index=1,
                    status=channels.DeliveryItemStatus.ACCEPTED,
                ),
            ),
        )

        with self.assertRaisesRegex(ContractViolation, "outside submitted content"):
            channels.validate_delivery_receipt_for_content(
                receipt,
                (TextContent("only item"),),
            )


if __name__ == "__main__":
    unittest.main()

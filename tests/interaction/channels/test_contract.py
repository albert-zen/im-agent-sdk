from __future__ import annotations

import unittest

from imagent import contracts
from imagent.interaction import channels
from imagent.interaction.messages import TextContent
from imagent.interaction.operations import ContractViolation


class ChannelReceiptContractTests(unittest.TestCase):
    def test_contracts_facade_reexports_exact_channel_contract_owners(self) -> None:
        names = (
            "ChannelCapabilities",
            "DeliveryItemReceipt",
            "DeliveryItemStatus",
            "DeliveryProfile",
            "DeliveryReceipt",
            "DeliveryReceiptStatus",
            "DeliverySegmentReceipt",
            "DeliverySegmentStatus",
            "DeliverySupportLevel",
            "ReplyReferenceScope",
            "validate_delivery_receipt",
            "validate_delivery_receipt_for_content",
        )

        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(contracts, name), getattr(channels, name))

    def test_delivery_support_is_distinct_from_application_support(self) -> None:
        self.assertIsNot(channels.DeliverySupportLevel, contracts.SupportLevel)
        self.assertEqual(
            tuple(level.value for level in channels.DeliverySupportLevel),
            ("native", "fallback", "unsupported"),
        )
        capabilities = channels.ChannelCapabilities(
            markdown=channels.DeliverySupportLevel.FALLBACK,
            reply_reference_scope=channels.ReplyReferenceScope.EVERY_SEGMENT,
        )

        self.assertIs(
            capabilities.delivery.markdown,
            channels.DeliverySupportLevel.FALLBACK,
        )
        self.assertIs(
            capabilities.delivery.reply_reference_scope,
            channels.ReplyReferenceScope.EVERY_SEGMENT,
        )

        with self.assertRaisesRegex(ContractViolation, "DeliverySupportLevel"):
            channels.ChannelCapabilities(markdown=contracts.SupportLevel.NATIVE)  # type: ignore[arg-type]

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

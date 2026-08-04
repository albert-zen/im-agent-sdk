from __future__ import annotations

import inspect
import subprocess
import sys
import unittest

from imagent import adapters, contracts
from imagent.channels import (
    NativeTransportChannelAdapter,
    channel_from_config,
)
from imagent.interaction import channels
from imagent.interaction.channels import contract as contract_owner
from imagent.interaction.channels.adapters import runtime as runtime_owner
from imagent.interaction.messages import TextContent
from imagent.interaction.operations import ContractViolation


class ChannelReceiptContractTests(unittest.TestCase):
    RETIRED_ADAPTER_NAMES = (
        "ChannelAdapter",
        "ChannelStartupConfigurationValidator",
        "MessageHandler",
        "InboundAdmission",
        "InboundAdmissionHandler",
    )
    RETIRED_CONTRACT_NAMES = (
        "ChannelCapabilities",
        "DeliveryProfile",
        "DeliverySupportLevel",
        "ReplyReferenceScope",
        "DeliveryReceipt",
        "DeliveryItemReceipt",
        "DeliverySegmentReceipt",
        "DeliveryItemStatus",
        "DeliveryReceiptStatus",
        "DeliverySegmentStatus",
        "validate_delivery_receipt",
        "validate_delivery_receipt_for_content",
    )

    def test_channel_lifecycle_has_no_gateway_operation_callback(self) -> None:
        self.assertFalse(hasattr(adapters, "OperationHandler"))
        for owner in (channels.ChannelAdapter, NativeTransportChannelAdapter):
            with self.subTest(owner=owner.__name__):
                parameters = inspect.signature(owner.start).parameters
                self.assertEqual(
                    tuple(parameters),
                    ("self", "on_message", "on_admission"),
                )

    def test_interaction_facade_reexports_exact_contract_owner_objects(self) -> None:
        self.assertEqual(
            set(channels.__all__),
            set(self.RETIRED_ADAPTER_NAMES + self.RETIRED_CONTRACT_NAMES),
        )
        for name in channels.__all__:
            with self.subTest(name=name):
                self.assertIs(getattr(channels, name), getattr(contract_owner, name))

    def test_historical_facades_omit_only_retired_channel_names(self) -> None:
        for name in self.RETIRED_ADAPTER_NAMES:
            with self.subTest(name=name):
                self.assertFalse(hasattr(adapters, name))
                self.assertNotIn(name, getattr(adapters, "__all__", ()))
        for name in self.RETIRED_CONTRACT_NAMES:
            with self.subTest(name=name):
                self.assertFalse(hasattr(contracts, name))
                self.assertNotIn(name, contracts.__all__)

        self.assertTrue(hasattr(adapters, "AgentApplicationAdapter"))
        self.assertTrue(hasattr(contracts, "SupportLevel"))
        self.assertFalse(hasattr(contracts, "DeliverySubmissionOrigin"))

    def test_historical_facade_imports_fail_in_clean_process(self) -> None:
        snippets = tuple(
            f"import {module} as facade; from {module} import {name}"
            for module, names in (
                ("imagent.adapters", self.RETIRED_ADAPTER_NAMES),
                ("imagent.contracts", self.RETIRED_CONTRACT_NAMES),
            )
            for name in names
        )
        for snippet in snippets:
            with self.subTest(snippet=snippet):
                completed = subprocess.run(
                    [sys.executable, "-c", snippet],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("ImportError", completed.stderr)

    def test_native_adapter_facade_reexports_exact_runtime_objects(self) -> None:
        self.assertIs(
            NativeTransportChannelAdapter,
            runtime_owner.NativeTransportChannelAdapter,
        )
        self.assertIs(channel_from_config, runtime_owner.channel_from_config)

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

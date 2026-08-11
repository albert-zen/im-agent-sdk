from __future__ import annotations

import importlib.util
import unittest

from imagent.applications.requests import (
    RequestDuplicateError,
    RequestResolvedError,
    RequestStaleError,
)
from imagent.interaction.operations import (
    ContractViolation,
    OperationErrorCode,
    operation_error,
    require_identifier,
)


class CommonOperationVocabularyTests(unittest.TestCase):
    def test_historical_cross_layer_contract_facade_is_absent(self) -> None:
        self.assertIsNone(importlib.util.find_spec("imagent.contracts"))

    def test_operation_errors_use_stable_codes(self) -> None:
        self.assertEqual(
            OperationErrorCode.CAPACITY_EXHAUSTED.value,
            "capacity_exhausted",
        )
        self.assertEqual(OperationErrorCode.MISSING_BINDING.value, "missing_binding")
        self.assertEqual(OperationErrorCode.STALE_BINDING.value, "stale_binding")
        cases = (
            (ValueError("bad input"), OperationErrorCode.INVALID_OPERATION),
            (NotImplementedError("missing"), OperationErrorCode.UNSUPPORTED),
            (KeyError("gone"), OperationErrorCode.NOT_FOUND),
            (RuntimeError("boom"), OperationErrorCode.ADAPTER_FAILURE),
        )
        for error, expected_code in cases:
            with self.subTest(error=type(error).__name__):
                projected = operation_error(error)
                self.assertEqual(projected.code, expected_code.value)
                self.assertFalse(projected.retryable)
                self.assertEqual(projected.metadata["native_exception"], type(error).__name__)

    def test_request_errors_keep_application_owned_stable_codes(self) -> None:
        cases = (
            (RequestDuplicateError("duplicate"), OperationErrorCode.REQUEST_DUPLICATE),
            (RequestResolvedError("resolved"), OperationErrorCode.REQUEST_RESOLVED),
            (RequestStaleError("stale"), OperationErrorCode.REQUEST_STALE),
        )
        for error, expected_code in cases:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(operation_error(error).code, expected_code.value)

    def test_identifier_validation_is_bounded(self) -> None:
        require_identifier("x" * 512, "operation_id")

        for value in ("", "x" * 513):
            with self.subTest(length=len(value)):
                with self.assertRaisesRegex(ContractViolation, "at most 512"):
                    require_identifier(value, "operation_id")


if __name__ == "__main__":
    unittest.main()

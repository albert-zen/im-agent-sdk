from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from importlib import import_module
from typing import get_type_hints

import imagent.applications as applications
from imagent.applications import contract, requests
from imagent.gateway.persistence import RequestRouteCorrelation
from imagent.interaction.operations import (
    ContractViolation,
    OperationErrorCode,
    operation_error,
)


class ApplicationRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application = contract.ApplicationRef("codex-local")
        self.request_ref = requests.RequestRef(
            self.application,
            "epoch-4:request-7",
        )
        self.thread = contract.ThreadRef(
            contract.ProjectRef("codex-local", "workspace"), "thread-1"
        )

    def _approval(
        self,
        *,
        choices: tuple[requests.RequestChoice, ...] = (
            requests.RequestChoice("accept", "Approve"),
            requests.RequestChoice("decline", "Deny"),
        ),
        prompt: str = "Run the command?",
    ) -> requests.ApprovalRequest:
        return requests.ApprovalRequest(
            request_ref=self.request_ref,
            turn_ref=contract.TurnRef(self.thread, "turn-1"),
            prompt=prompt,
            choices=choices,
        )

    def test_owner_has_exact_finite_exports_and_facades_are_negative(self) -> None:
        for name in (
            "ApprovalRequest",
            "ApprovalResponse",
            "ApprovalResponseShape",
            "InteractiveRequest",
            "InteractiveRequestKind",
            "MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH",
            "MAX_INTERACTIVE_REQUEST_CHOICES",
            "MAX_INTERACTIVE_REQUEST_QUESTIONS",
            "RequestChoice",
            "RequestDuplicateError",
            "RequestRef",
            "RequestResolution",
            "RequestResolutionStatus",
            "RequestResponse",
            "RequestResponseShape",
            "RequestResolvedError",
            "RequestStaleError",
            "UserInputQuestion",
            "UserInputQuestionShape",
            "UserInputRequest",
            "UserInputResponse",
            "UserInputResponseShape",
            "derive_request_response_shape",
            "validate_interactive_request",
            "validate_request_ref",
            "validate_request_resolution",
            "validate_request_response",
            "validate_request_response_admission",
            "validate_request_response_shape",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(requests, name))
                self.assertIn(name, requests.__all__)
                self.assertNotIn(name, applications.__all__)
        self.assertIsNone(importlib.util.find_spec("imagent.contracts"))

    def test_historical_contract_modules_no_longer_define_request_contract(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            import_module("imagent.contracts.errors")
        with self.assertRaises(ModuleNotFoundError):
            import_module("imagent.contracts.operations")
        with self.assertRaises(ModuleNotFoundError):
            import_module("imagent.contracts.model")
        with self.assertRaises(ModuleNotFoundError):
            import_module("imagent.contracts.request_validation")
        self.assertTrue(hasattr(RequestRouteCorrelation, "__dataclass_fields__"))

    def test_request_annotations_and_gateway_route_annotations_resolve(self) -> None:
        request_hints = get_type_hints(requests.ApprovalRequest)
        self.assertIs(request_hints["request_ref"], requests.RequestRef)
        self.assertIs(request_hints["turn_ref"], contract.TurnRef)
        self.assertIs(
            get_type_hints(requests.RequestRef)["application_ref"],
            contract.ApplicationRef,
        )

        route_hints = get_type_hints(RequestRouteCorrelation)
        self.assertIs(route_hints["request_ref"], requests.RequestRef)
        self.assertIs(route_hints["turn_ref"], contract.TurnRef)
        self.assertEqual(route_hints["response_shape"], requests.RequestResponseShape)

    def test_request_and_historical_resource_import_orders_are_clean(self) -> None:
        import_orders = (
            "import imagent.applications.requests",
            "import imagent.gateway.persistence.state_contracts",
            "import imagent.applications.operations",
            (
                "from imagent.applications.requests import RequestRef\n"
                "try:\n"
                "    from imagent.contracts import RequestRef\n"
                "except ImportError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError('retired RequestRef import')"
            ),
        )
        for import_order in import_orders:
            with self.subTest(import_order=import_order):
                completed = subprocess.run(
                    [sys.executable, "-c", import_order],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_request_ref_preserves_application_and_epoch_scope(self) -> None:
        requests.validate_request_ref(self.request_ref)
        self.assertEqual(self.request_ref.native_request_id, "epoch-4:request-7")

        with self.assertRaisesRegex(ContractViolation, "application_instance_id"):
            requests.validate_request_ref(
                requests.RequestRef(
                    contract.ApplicationRef(""),
                    self.request_ref.native_request_id,
                )
            )
        with self.assertRaisesRegex(ContractViolation, "native_request_id"):
            requests.validate_request_ref(requests.RequestRef(self.application, ""))

    def test_request_validation_preserves_scope_text_and_cardinality(self) -> None:
        requests.validate_interactive_request(self._approval())
        with self.assertRaisesRegex(ContractViolation, "different application"):
            requests.validate_interactive_request(
                requests.ApprovalRequest(
                    request_ref=requests.RequestRef(
                        contract.ApplicationRef("other-application"),
                        self.request_ref.native_request_id,
                    ),
                    turn_ref=contract.TurnRef(self.thread, "turn-1"),
                    prompt="Run the command?",
                    choices=(requests.RequestChoice("accept", "Approve"),),
                )
            )
        with self.assertRaisesRegex(ContractViolation, "prompt cannot be empty"):
            requests.validate_interactive_request(self._approval(prompt="  "))
        with self.assertRaisesRegex(ContractViolation, "label cannot be empty"):
            requests.validate_interactive_request(
                self._approval(choices=(requests.RequestChoice("accept", "  "),))
            )

        boundary_choices = tuple(
            requests.RequestChoice(f"choice-{index}", f"Choice {index}")
            for index in range(requests.MAX_INTERACTIVE_REQUEST_CHOICES)
        )
        requests.validate_interactive_request(self._approval(choices=boundary_choices))
        with self.assertRaisesRegex(ContractViolation, "choices exceed"):
            requests.validate_interactive_request(
                self._approval(
                    choices=boundary_choices + (requests.RequestChoice("overflow", "Overflow"),)
                )
            )

        boundary_questions = tuple(
            requests.UserInputQuestion(
                question_id=f"question-{index}",
                prompt=f"Question {index}",
                allows_other=True,
            )
            for index in range(requests.MAX_INTERACTIVE_REQUEST_QUESTIONS)
        )
        requests.validate_interactive_request(
            requests.UserInputRequest(
                request_ref=self.request_ref,
                turn_ref=contract.TurnRef(self.thread, "turn-1"),
                questions=boundary_questions,
            )
        )
        with self.assertRaisesRegex(ContractViolation, "questions exceed"):
            requests.validate_interactive_request(
                requests.UserInputRequest(
                    request_ref=self.request_ref,
                    turn_ref=contract.TurnRef(self.thread, "turn-1"),
                    questions=boundary_questions
                    + (
                        requests.UserInputQuestion(
                            question_id="overflow",
                            prompt="Overflow",
                            allows_other=True,
                        ),
                    ),
                )
            )

        for min_answers, max_answers in (
            (False, 1),
            (0, True),
            (0.0, 1),
            (0, 1.0),
        ):
            with self.subTest(min_answers=min_answers, max_answers=max_answers):
                with self.assertRaisesRegex(ContractViolation, "answers must be"):
                    requests.validate_interactive_request(
                        requests.UserInputRequest(
                            request_ref=self.request_ref,
                            turn_ref=contract.TurnRef(self.thread, "turn-1"),
                            questions=(
                                requests.UserInputQuestion(
                                    question_id="typed-cardinality",
                                    prompt="Typed cardinality",
                                    allows_other=True,
                                    min_answers=min_answers,  # type: ignore[arg-type]
                                    max_answers=max_answers,  # type: ignore[arg-type]
                                ),
                            ),
                        )
                    )

    def test_response_shape_derivation_and_validation_preserve_errors(self) -> None:
        request = self._approval()
        shape = requests.derive_request_response_shape(request)
        self.assertEqual(shape, requests.ApprovalResponseShape(("accept", "decline")))
        requests.validate_request_response(
            requests.ApprovalResponse("accept"),
            shape,
        )
        with self.assertRaisesRegex(ContractViolation, "not offered"):
            requests.validate_request_response(requests.ApprovalResponse("unknown"), shape)

        question = requests.UserInputQuestion(
            question_id="environment",
            prompt="Choose an environment",
            choices=(
                requests.RequestChoice("staging", "Staging"),
                requests.RequestChoice("production", "Production"),
            ),
            min_answers=1,
            max_answers=1,
        )
        user_request = requests.UserInputRequest(
            request_ref=self.request_ref,
            turn_ref=contract.TurnRef(self.thread, "turn-1"),
            questions=(question,),
        )
        user_shape = requests.derive_request_response_shape(user_request)
        self.assertEqual(
            user_shape,
            requests.UserInputResponseShape(
                (
                    requests.UserInputQuestionShape(
                        question_id="environment",
                        choice_ids=("staging", "production"),
                        allows_other=False,
                        min_answers=1,
                        max_answers=1,
                    ),
                )
            ),
        )
        requests.validate_request_response(
            requests.UserInputResponse({"environment": ("staging",)}),
            user_shape,
        )
        with self.assertRaisesRegex(ContractViolation, "too many"):
            requests.validate_request_response(
                requests.UserInputResponse({"environment": ("staging", "production")}),
                user_shape,
            )

    def test_user_input_response_admission_is_bounded_before_routing(self) -> None:
        boundary_questions = requests.UserInputResponse(
            {
                f"question-{index}": ("answer",)
                for index in range(requests.MAX_INTERACTIVE_REQUEST_QUESTIONS)
            }
        )
        boundary_answers = requests.UserInputResponse(
            {
                "question": tuple(
                    f"answer-{index}" for index in range(requests.MAX_INTERACTIVE_REQUEST_CHOICES)
                )
            }
        )
        boundary_length = requests.UserInputResponse(
            {"question": ("x" * requests.MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH,)}
        )
        for response in (boundary_questions, boundary_answers, boundary_length):
            requests.validate_request_response_admission(response)

        invalid = (
            requests.UserInputResponse(
                {
                    f"question-{index}": ("answer",)
                    for index in range(requests.MAX_INTERACTIVE_REQUEST_QUESTIONS + 1)
                }
            ),
            requests.UserInputResponse(
                {
                    "question": tuple(
                        f"answer-{index}"
                        for index in range(requests.MAX_INTERACTIVE_REQUEST_CHOICES + 1)
                    )
                }
            ),
            requests.UserInputResponse(
                {"question": ("x" * (requests.MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH + 1),)}
            ),
        )
        for response in invalid:
            with self.subTest(response=response):
                with self.assertRaises(ContractViolation):
                    requests.validate_request_response_admission(response)

        with self.assertRaisesRegex(ContractViolation, "response limit"):
            requests.validate_request_response_shape(
                requests.UserInputResponseShape(
                    (
                        requests.UserInputQuestionShape(
                            question_id="question",
                            choice_ids=(),
                            allows_other=True,
                            min_answers=1,
                            max_answers=requests.MAX_INTERACTIVE_REQUEST_CHOICES + 1,
                        ),
                    )
                )
            )

        for min_answers, max_answers in (
            (False, 1),
            (0, True),
            (0.0, 1),
            (0, 1.0),
        ):
            with self.subTest(min_answers=min_answers, max_answers=max_answers):
                with self.assertRaisesRegex(ContractViolation, "answers must be"):
                    requests.validate_request_response_shape(
                        requests.UserInputResponseShape(
                            (
                                requests.UserInputQuestionShape(
                                    question_id="typed-cardinality",
                                    choice_ids=(),
                                    allows_other=True,
                                    min_answers=min_answers,  # type: ignore[arg-type]
                                    max_answers=max_answers,  # type: ignore[arg-type]
                                ),
                            )
                        )
                    )

    def test_request_resolution_and_native_error_codes_remain_stable(self) -> None:
        requests.validate_request_resolution(
            requests.RequestResolution(
                request_ref=self.request_ref,
                turn_ref=contract.TurnRef(self.thread, "turn-1"),
                status=requests.RequestResolutionStatus.RESOLVED,
                resolved_at=datetime.now(UTC),
            )
        )
        with self.assertRaisesRegex(ContractViolation, "timezone"):
            requests.validate_request_resolution(
                requests.RequestResolution(
                    request_ref=self.request_ref,
                    turn_ref=contract.TurnRef(self.thread, "turn-1"),
                    status=requests.RequestResolutionStatus.STALE,
                    resolved_at=datetime(2026, 1, 1),
                )
            )
        with self.assertRaisesRegex(ContractViolation, "different application"):
            requests.validate_request_resolution(
                requests.RequestResolution(
                    request_ref=self.request_ref,
                    turn_ref=contract.TurnRef(
                        contract.ThreadRef(
                            contract.ProjectRef("other-app", "project-1"),
                            "thread-1",
                        ),
                        "turn-1",
                    ),
                    status=requests.RequestResolutionStatus.RESOLVED,
                    resolved_at=datetime.now(UTC),
                )
            )

        for error_type, error_code in (
            (
                requests.RequestDuplicateError,
                OperationErrorCode.REQUEST_DUPLICATE,
            ),
            (
                requests.RequestResolvedError,
                OperationErrorCode.REQUEST_RESOLVED,
            ),
            (requests.RequestStaleError, OperationErrorCode.REQUEST_STALE),
        ):
            with self.subTest(error=error_type.__name__):
                self.assertEqual(
                    operation_error(error_type("request error")).code,
                    error_code.value,
                )

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from typing import cast

from imagent.applications import contract as owner
from imagent.applications.contract import (
    ProjectRef,
    QueuedInputDispatch,
    QueuedInputReceipt,
    QueuedInputState,
    ThreadRef,
    TurnRef,
    validate_queued_input_dispatch,
    validate_queued_input_receipt,
)
from imagent.interaction.operations import ContractViolation


class QueuedInputContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thread = ThreadRef(ProjectRef("app", "workspace"), "thread")

    def test_queued_admission_has_real_item_and_no_placeholder_turn(self) -> None:
        receipt = QueuedInputReceipt(
            self.thread,
            "client",
            "queued-item",
            QueuedInputState.QUEUED,
            input_fingerprint="a" * 64,
        )
        validate_queued_input_receipt(receipt)
        self.assertIsNone(receipt.turn_ref)
        with self.assertRaises(FrozenInstanceError):
            receipt.queued_item_id = "other"  # type: ignore[misc]
        self.assertNotIn("queued", {state.value for state in owner.InputDisposition})
        self.assertEqual(owner.InputContinuationPreference.QUEUE.value, "queue")

    def test_only_admitted_requires_exact_same_thread_turn(self) -> None:
        for state in QueuedInputState:
            for turn in (None, TurnRef(self.thread, "turn")):
                receipt = QueuedInputReceipt(
                    self.thread, "client", "item", state, turn, input_fingerprint="a" * 64
                )
                if (state is QueuedInputState.ADMITTED) == (turn is not None):
                    validate_queued_input_receipt(receipt)
                else:
                    with self.assertRaises(ContractViolation):
                        validate_queued_input_receipt(receipt)
        foreign = ThreadRef(self.thread.project_ref, "other-thread")
        with self.assertRaises(ContractViolation):
            validate_queued_input_receipt(
                QueuedInputReceipt(
                    self.thread,
                    "client",
                    "item",
                    QueuedInputState.ADMITTED,
                    TurnRef(foreign, "turn"),
                    input_fingerprint="a" * 64,
                )
            )

    def test_receipt_rejects_missing_identity_and_untyped_state(self) -> None:
        for client, queued, state in (
            ("", "item", QueuedInputState.QUEUED),
            ("client", "", QueuedInputState.QUEUED),
            ("client", "item", cast(QueuedInputState, "queued")),
        ):
            with self.assertRaises(ContractViolation):
                validate_queued_input_receipt(
                    QueuedInputReceipt(
                        self.thread, client, queued, state, input_fingerprint="a" * 64
                    )
                )

    def test_prepared_dispatch_requires_bounded_sha256(self) -> None:
        validate_queued_input_dispatch(QueuedInputDispatch(self.thread, "client", "a" * 64))
        for fingerprint in ("", "A" * 64, "a" * 63, "g" * 64):
            with self.assertRaises(ContractViolation):
                validate_queued_input_dispatch(
                    QueuedInputDispatch(self.thread, "client", fingerprint)
                )

    def test_default_protocol_and_historical_handler_remain_narrow(self) -> None:
        from typing import get_args, get_overloads, get_type_hints

        callback_args = get_args(owner.ApplicationInputDispatchHandler)
        self.assertEqual(callback_args[0], [owner.ApplicationInputDispatch])
        plan_callback_args = get_args(owner.ApplicationInputPlanHandler)
        self.assertEqual(plan_callback_args[0], [owner.ApplicationInputPlan])
        ordinary, dynamic = get_overloads(owner.AgentApplicationAdapter.send_input)
        self.assertIs(get_type_hints(ordinary)["return"], owner.AcceptedTurn)
        self.assertEqual(get_type_hints(dynamic)["return"], owner.ApplicationInputAcceptance)

    def test_public_facade_keeps_exact_model_and_union_owners(self) -> None:
        import imagent.applications as facade

        for name in (
            "QueuedInputDispatch",
            "QueuedInputReceipt",
            "QueuedInputState",
            "ApplicationInputPlan",
            "ApplicationInputAcceptance",
            "ApplicationInputPlanHandler",
            "validate_queued_input_dispatch",
            "validate_queued_input_receipt",
        ):
            self.assertIs(getattr(facade, name), getattr(owner, name))


class QueuedInputSchemaTests(unittest.TestCase):
    def test_language_neutral_receipts_keep_the_same_closed_turn_states(self) -> None:
        from jsonschema import ValidationError

        from tests.interaction.test_contracts import _definition_validator

        validator = _definition_validator("messages.schema.json", "ApplicationInputAcceptance")
        thread = {
            "projectRef": {"applicationInstanceId": "app", "projectId": "project"},
            "threadId": "thread",
        }
        receipt = {
            "threadRef": thread,
            "clientMessageId": "client",
            "queuedItemId": "item",
            "state": "queued",
            "inputFingerprint": "a" * 64,
        }
        validator.validate(receipt)
        for changed in (
            {"turnRef": {"threadRef": thread, "turnId": "turn"}},
            {"state": "admitted"},
            {"inputFingerprint": "A" * 64},
        ):
            with self.assertRaises(ValidationError):
                validator.validate({**receipt, **changed})
        validator.validate(
            {**receipt, "state": "admitted", "turnRef": {"threadRef": thread, "turnId": "turn"}}
        )


async def _check_public_protocol_input_typing(
    app: owner.AgentApplicationAdapter,
    thread: ThreadRef,
    message: owner.AgentInput,
    preference: owner.InputContinuationPreference,
) -> None:
    """Checked by pyright: unchanged consumers plus explicit dynamic plans."""
    from typing import assert_type

    async def old_fence(dispatch: owner.ApplicationInputDispatch) -> None:
        assert_type(dispatch, owner.ApplicationInputDispatch)

    old_handler: owner.ApplicationInputDispatchHandler = old_fence
    default = await app.send_input(thread, message, before_dispatch=old_handler)
    assert_type(default, owner.AcceptedTurn)
    ordinary = await app.send_input(
        thread,
        message,
        continuation=owner.InputContinuationPreference.PREFER_ACTIVE_TURN,
        before_dispatch=old_handler,
    )
    assert_type(ordinary, owner.AcceptedTurn)
    new_turn = await app.send_input(
        thread,
        message,
        continuation=owner.InputContinuationPreference.START_NEW_TURN,
        before_dispatch=old_handler,
    )
    assert_type(new_turn, owner.AcceptedTurn)

    async def plan_fence(plan: owner.ApplicationInputPlan) -> None:
        if isinstance(plan, QueuedInputDispatch):
            assert_type(plan, QueuedInputDispatch)
        else:
            assert_type(plan, owner.ApplicationInputDispatch)

    plan_handler: owner.ApplicationInputPlanHandler = plan_fence
    dynamic = await app.send_input(
        thread, message, continuation=preference, before_dispatch=plan_handler
    )
    assert_type(dynamic, owner.ApplicationInputAcceptance)
    queue = await app.send_input(
        thread,
        message,
        continuation=owner.InputContinuationPreference.QUEUE,
        before_dispatch=plan_handler,
    )
    assert_type(queue, owner.ApplicationInputAcceptance)
    if isinstance(queue, QueuedInputReceipt):
        assert_type(queue, QueuedInputReceipt)

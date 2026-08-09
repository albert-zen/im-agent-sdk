from __future__ import annotations

import unittest
from datetime import UTC, datetime

from imagent.applications.contract import ApplicationRef
from imagent.gateway.outcomes import Failed, OutcomeUnknown, Partial, Succeeded
from imagent.gateway.persistence.effects import (
    ActionError,
    ActionErrorCode,
    ActionIdentity,
    BindingClearScope,
    EffectCategory,
    EffectPhase,
    EffectReceipt,
    EffectValue,
    StableReference,
    StoreMutationPlan,
    StoreMutationRequest,
    decode_action_outcome,
    derive_action_fingerprint,
    encode_action_outcome,
    validate_action_error,
    validate_effect_receipt,
    validate_effect_value,
    validate_store_mutation_request,
)
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation, OperationErrorCode


class GatewayEffectContractTests(unittest.TestCase):
    def test_action_fingerprint_is_stable_namespaced_and_domain_separated(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        identity = ActionIdentity(
            "gateway",
            "principal",
            "conversation.select",
            "action",
            conversation,
        )
        first = derive_action_fingerprint(identity, (("project_id", "project"),))
        repeated = derive_action_fingerprint(identity, (("project_id", "project"),))
        changed = derive_action_fingerprint(identity, (("project_id", "other"),))
        other_principal = derive_action_fingerprint(
            ActionIdentity(
                "gateway",
                "other-principal",
                "conversation.select",
                "action",
                conversation,
            ),
            (("project_id", "project"),),
        )
        self.assertEqual(first, repeated)
        self.assertEqual(first.action_key, changed.action_key)
        self.assertNotEqual(first.payload_fingerprint, changed.payload_fingerprint)
        self.assertNotEqual(first.action_key, other_principal.action_key)
        self.assertNotEqual(first.phase_id("native"), first.phase_id("binding"))

    def test_fingerprint_payload_is_closed_and_bounded(self) -> None:
        identity = ActionIdentity("gateway", "principal", "kind", "action")
        with self.assertRaises(ContractViolation):
            derive_action_fingerprint(identity, (("number", 2**64),))
        with self.assertRaises(ContractViolation):
            derive_action_fingerprint(identity, (("invalid", (object(),)),))  # type: ignore[arg-type]

    def test_minimal_outcome_codec_round_trips_every_variant(self) -> None:
        value = EffectValue(reference=StableReference.from_value(ApplicationRef("app")))
        error = ActionError(ActionErrorCode.NATIVE_REJECTED, OperationErrorCode.NOT_FOUND)
        outcomes = (
            Succeeded(value),
            Failed(error),
            Partial(value, error),
            OutcomeUnknown(ActionError(ActionErrorCode.NATIVE_OUTCOME_UNKNOWN)),
        )
        for outcome in outcomes:
            with self.subTest(status=outcome.status):
                encoded = encode_action_outcome(outcome)
                self.assertEqual(decode_action_outcome(encoded), outcome)
                assert encoded is not None
                self.assertNotIn("principal", encoded)
                self.assertNotIn("credential", encoded)

    def test_error_projection_is_closed_and_codec_rejects_unknown_fields(self) -> None:
        invalid_error = ActionError(
            ActionErrorCode.STORE_FAILURE,
            "/private/credential/native-error",  # type: ignore[arg-type]
        )
        with self.assertRaises(ContractViolation):
            validate_action_error(invalid_error)
        with self.assertRaises(ContractViolation):
            encode_action_outcome(Failed(invalid_error))
        malformed = (
            '{"status":"failed","error":{"code":"store_failure",'
            '"operation_error_code":null},"native_metadata":"secret"}',
            '{"status":"failed","error":{"code":"store_failure",'
            '"operation_error_code":null,"content":"secret"}}',
            '{"status":"succeeded","value":{"reference":null,'
            '"conversation":null,"binding_generation":null,"route_id":null,'
            '"path":"/private"}}',
        )
        for encoded in malformed:
            with self.subTest(encoded=encoded):
                with self.assertRaises(ContractViolation):
                    decode_action_outcome(encoded)

    def test_generations_are_strict_non_boolean_non_negative_integers(self) -> None:
        conversation = ConversationRef("channel", "conversation")
        fingerprint = derive_action_fingerprint(
            ActionIdentity("gateway", "principal", "conversation.clear", "action", conversation),
            (),
        )
        now = datetime.now(UTC)
        for invalid in (True, False, -1, 1.0, "1"):
            with self.subTest(field="value", invalid=invalid):
                with self.assertRaises(ContractViolation):
                    validate_effect_value(EffectValue(binding_generation=invalid))  # type: ignore[arg-type]
            with self.subTest(field="plan", invalid=invalid):
                with self.assertRaises(ContractViolation):
                    validate_store_mutation_request(
                        StoreMutationRequest(
                            fingerprint,
                            StoreMutationPlan(
                                conversation_ref=conversation,
                                binding_clear=BindingClearScope.APPLICATION,
                                expected_generation=invalid,  # type: ignore[arg-type]
                            ),
                        )
                    )
            with self.subTest(field="receipt", invalid=invalid):
                with self.assertRaises(ContractViolation):
                    validate_effect_receipt(
                        EffectReceipt(
                            gateway_id="gateway",
                            action_kind=fingerprint.action_kind,
                            action_key=fingerprint.action_key,
                            payload_fingerprint=fingerprint.payload_fingerprint,
                            category=EffectCategory.WORKFLOW,
                            phase=EffectPhase.RESERVED,
                            native_phase_id=fingerprint.phase_id("native"),
                            binding_generation=invalid,  # type: ignore[arg-type]
                            outcome=None,
                            created_at=now,
                            updated_at=now,
                        )
                    )


if __name__ == "__main__":
    unittest.main()

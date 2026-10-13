from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from typing import cast

from jsonschema import ValidationError

import imagent.gateway as facade
from imagent.gateway import queued_input as owner
from imagent.interaction.operations import ContractViolation
from tests.interaction.test_contracts import _definition_validator


class QueuedGatewayContractTests(unittest.TestCase):
    def test_public_status_is_bounded_immutable_and_has_no_native_identity(self) -> None:
        for name in owner.__all__:
            self.assertIs(getattr(facade, name), getattr(owner, name))
        for state in owner.QueuedInputStatusKind:
            status = owner.QueuedInputStatus("original-im-id", state)
            self.assertFalse(hasattr(status, "turn_ref"))
            self.assertFalse(hasattr(status, "queued_item_id"))
            with self.assertRaises(FrozenInstanceError):
                status.message_id = "changed"  # type: ignore[misc]
        for identity in ("", " ", "x" * 513):
            with self.assertRaises(ContractViolation):
                owner.QueuedInputStatus(identity, owner.QueuedInputStatusKind.UNKNOWN)
        with self.assertRaises(ContractViolation):
            owner.QueuedInputStatus("message", cast(owner.QueuedInputStatusKind, "pending"))

    def test_schema_status_is_closed_and_matches_each_public_state(self) -> None:
        validator = _definition_validator("projections.schema.json", "QueuedInputStatus")
        for state in owner.QueuedInputStatusKind:
            validator.validate({"messageId": "original", "state": state.value})
        for change in (
            {"messageId": "x" * 513},
            {"state": "failed"},
            {"nativeId": "secret"},
            {"turnId": "guess"},
            {"failureMessage": "private failure"},
        ):
            with self.assertRaises(ValidationError):
                validator.validate({"messageId": "original", "state": "pending", **change})

    def test_schema_origin_and_correlation_persist_only_fixed_receipt_facts(self) -> None:
        validator = _definition_validator("projections.schema.json", "QueuedInputCorrelation")
        origin = {
            "conversationRef": {
                "channelInstanceId": "channel",
                "nativeConversationId": "conversation",
            },
            "messageId": "original",
            "actorFingerprint": "a" * 64,
            "threadRef": {
                "projectRef": {"applicationInstanceId": "app", "projectId": "project"},
                "threadId": "thread",
            },
            "bindingGeneration": 1,
            "clientMessageId": "client",
            "inputFingerprint": "b" * 64,
            "continuation": "queue",
        }
        row = {
            "origin": origin,
            "receipt": None,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        validator.validate(row)
        for change in (
            {"input": "body"},
            {"actor": "raw actor"},
            {"imagePath": "/private"},
            {"continuation": "prefer_active_turn"},
            {"bindingGeneration": -1},
        ):
            with self.assertRaises(ValidationError):
                validator.validate({**row, "origin": {**origin, **change}})
        for change in (
            {"failureMessage": "native text"},
            {"retryAt": "2026-01-01"},
            {"state": "delivered"},
        ):
            with self.assertRaises(ValidationError):
                validator.validate({**row, **change})

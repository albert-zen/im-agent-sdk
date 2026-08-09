from __future__ import annotations

import json
import sqlite3
import unittest
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import get_type_hints

import imagent.gateway.persistence as persistence_package
from imagent.applications.contract import ApplicationRef, ProjectRef, ThreadRef, TurnRef
from imagent.applications.requests import (
    RequestRef,
    UserInputQuestionShape,
    UserInputResponseShape,
)
from imagent.gateway.delivery import DeliverySubmissionOrigin
from imagent.gateway.persistence import row_mapping
from imagent.gateway.persistence.state_contracts import (
    ConversationBinding,
    DeliveryRouteSnapshot,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
)
from imagent.interaction.channels import (
    DeliveryItemReceipt,
    DeliveryItemStatus,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    DeliverySegmentReceipt,
    DeliverySegmentStatus,
)
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation

_NOW = datetime(2026, 8, 4, 12, 34, 56, 789000, tzinfo=timezone(timedelta(hours=8)))


def _row(values: Mapping[str, object]) -> sqlite3.Row:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    projection = ", ".join(f'? AS "{name}"' for name in values)
    row = connection.execute(f"SELECT {projection}", tuple(values.values())).fetchone()
    assert row is not None
    connection.close()
    return row


def _row_from_values(names: tuple[str, ...], values: tuple[object, ...]) -> sqlite3.Row:
    return _row(dict(zip(names, values, strict=True)))


def _mapping_from_values(names: tuple[str, ...], values: tuple[object, ...]) -> dict[str, object]:
    return {str(name): value for name, value in zip(names, values, strict=True)}


class RowMappingTests(unittest.TestCase):
    def test_type_hints_resolve_to_the_state_contract_objects(self) -> None:
        self.assertIs(
            get_type_hints(row_mapping.request_correlation_from_row)["return"],
            RequestRouteCorrelation,
        )
        self.assertIs(
            get_type_hints(row_mapping.delivery_submission_from_rows)["return"],
            DeliverySubmissionRecord,
        )
        self.assertIs(
            get_type_hints(row_mapping.projection_route_from_row)["return"],
            ThreadProjectionRoute,
        )

    def test_binding_route_and_turn_rows_round_trip_exact_identity_and_timezone(self) -> None:
        binding = ConversationBinding(
            conversation_ref=ConversationRef("qq-main", "conversation-1"),
            application_ref=ApplicationRef("application-1"),
            project_ref=ProjectRef("application-1", "project-1"),
            thread_ref=ThreadRef(ProjectRef("application-1", "project-1"), "thread-1"),
            revision=7,
            updated_at=_NOW,
        )
        binding_names = (
            "channel_instance_id",
            "native_conversation_id",
            "application_instance_id",
            "project_id",
            "thread_id",
            "revision",
            "updated_at",
        )
        binding_row = _row_from_values(binding_names, row_mapping.binding_to_row(binding))
        self.assertEqual(row_mapping.binding_from_row(binding_row), binding)

        route = ThreadProjectionRoute(
            route_id="route-1",
            thread_ref=ThreadRef(ProjectRef("application-1", "workspace"), "thread-1"),
            conversation_ref=ConversationRef("qq-main", "conversation-1"),
            reply_to_message_id=None,
            checkpoint_agent_item_id="item-9",
            checkpointed_at=_NOW,
            updated_at=_NOW,
        )
        route_names = (
            "route_id",
            "application_instance_id",
            "project_id",
            "thread_id",
            "channel_instance_id",
            "native_conversation_id",
            "reply_to_message_id",
            "checkpoint_agent_item_id",
            "checkpointed_at",
            "updated_at",
        )
        route_row = _row_from_values(route_names, row_mapping.projection_route_to_row(route))
        decoded_route = row_mapping.projection_route_from_row(route_row)
        self.assertEqual(decoded_route, route)
        self.assertEqual(
            decoded_route.thread_ref.project_ref,
            ProjectRef("application-1", "workspace"),
        )
        self.assertEqual(decoded_route.checkpointed_at, _NOW)

        correlation = TurnReplyCorrelation(
            correlation_id="correlation-1",
            turn_ref=TurnRef(ThreadRef(ProjectRef("application-1", "p"), "thread-1"), "turn-1"),
            client_message_id="client-1",
            conversation_ref=ConversationRef("qq-main", "conversation-1"),
            reply_to_message_id="message-1",
            created_at=_NOW,
        )
        correlation_names = (
            "correlation_id",
            "application_instance_id",
            "project_id",
            "thread_id",
            "turn_id",
            "client_message_id",
            "channel_instance_id",
            "native_conversation_id",
            "reply_to_message_id",
            "created_at",
        )
        correlation_row = _row_from_values(
            correlation_names,
            row_mapping.turn_reply_correlation_to_row(correlation),
        )
        self.assertEqual(row_mapping.turn_reply_correlation_from_row(correlation_row), correlation)

    def test_request_shape_round_trip_preserves_enum_and_bounded_validation(self) -> None:
        shape = UserInputResponseShape(
            questions=(
                UserInputQuestionShape(
                    question_id="question-1",
                    choice_ids=("yes", "no"),
                    allows_other=False,
                    min_answers=1,
                    max_answers=1,
                ),
            )
        )
        correlation = RequestRouteCorrelation(
            correlation_id="request-correlation-1",
            request_ref=RequestRef(ApplicationRef("application-1"), "epoch-1:request-1"),
            turn_ref=TurnRef(ThreadRef(ProjectRef("application-1", "p"), "thread-1"), "turn-1"),
            conversation_ref=ConversationRef("qq-main", "conversation-1"),
            delivery_id="delivery-1",
            response_shape=shape,
            state=RequestRouteState.OPEN,
            created_at=_NOW,
            updated_at=_NOW + timedelta(minutes=1),
            expires_at=_NOW + timedelta(hours=1),
        )
        names = (
            "correlation_id",
            "application_instance_id",
            "native_request_id",
            "project_id",
            "thread_id",
            "turn_id",
            "channel_instance_id",
            "native_conversation_id",
            "delivery_id",
            "response_shape_json",
            "state",
            "created_at",
            "updated_at",
            "expires_at",
        )
        row = _row_from_values(names, row_mapping.request_correlation_to_row(correlation))
        decoded = row_mapping.request_correlation_from_row(row)
        self.assertEqual(decoded, correlation)
        self.assertIs(type(decoded.response_shape), type(shape))
        self.assertIs(decoded.state, RequestRouteState.OPEN)
        self.assertEqual(decoded.expires_at, correlation.expires_at)

        malformed = _mapping_from_values(names, row_mapping.request_correlation_to_row(correlation))
        malformed["response_shape_json"] = '{"kind":"unknown"}'
        malformed_row = _row(malformed)
        with self.assertRaisesRegex(ValueError, "response shape kind is invalid"):
            row_mapping.request_correlation_from_row(malformed_row)

        oversized = {
            "kind": "approval",
            "choice_ids": [f"choice-{index}" for index in range(65)],
        }
        oversized_row_values = _mapping_from_values(
            names,
            row_mapping.request_correlation_to_row(correlation),
        )
        oversized_row_values["response_shape_json"] = json.dumps(oversized)
        with self.assertRaises(ContractViolation):
            row_mapping.request_correlation_from_row(_row(oversized_row_values))

    def test_delivery_rows_round_trip_null_scope_receipt_and_origin_identity(self) -> None:
        receipt = DeliveryReceipt(
            status=DeliveryReceiptStatus.ACCEPTED_BY_PLATFORM,
            native_message_id="native-message-1",
            detail="accepted",
            items=(
                DeliveryItemReceipt(
                    content_index=0,
                    status=DeliveryItemStatus.ACCEPTED,
                    attachment_id="attachment-1",
                ),
            ),
            segments=(
                DeliverySegmentReceipt(
                    segment_index=0,
                    delivery_id="segment-1",
                    source_content_indexes=(0,),
                    status=DeliverySegmentStatus.ACCEPTED_BY_PLATFORM,
                    native_message_id="native-message-1",
                ),
            ),
        )
        destination = DestinationDeliveryRecord(
            delivery_id="destination-1",
            snapshot=DeliveryRouteSnapshot(
                conversation_ref=ConversationRef("qq-main", "conversation-1"),
            ),
            state=DeliverySubmissionState.ACCEPTED,
            updated_at=_NOW,
            receipt=receipt,
        )
        record = DeliverySubmissionRecord(
            submission_id="submission-1",
            delivery_id="delivery-1",
            origin=DeliverySubmissionOrigin.EXTERNAL,
            principal_id="principal-1",
            target_fingerprint="target-1",
            payload_fingerprint="payload-1",
            destinations=(destination,),
            created_at=_NOW,
            updated_at=_NOW,
        )
        root_names = (
            "submission_id",
            "delivery_id",
            "origin",
            "principal_id",
            "target_fingerprint",
            "payload_fingerprint",
            "created_at",
            "updated_at",
        )
        destination_names = (
            "root_submission_id",
            "destination_delivery_id",
            "channel_instance_id",
            "native_conversation_id",
            "application_instance_id",
            "project_id",
            "thread_id",
            "route_id",
            "route_updated_at",
            "reply_to_message_id",
            "state",
            "receipt_json",
            "error",
            "updated_at",
        )
        root = _row_from_values(root_names, row_mapping.delivery_submission_to_row(record))
        destination_row = _row_from_values(
            destination_names,
            row_mapping.delivery_destination_to_row(record.submission_id, destination),
        )
        decoded = row_mapping.delivery_submission_from_rows(root, (destination_row,))
        self.assertEqual(decoded, record)
        self.assertIs(decoded.origin, DeliverySubmissionOrigin.EXTERNAL)
        self.assertIs(decoded.destinations[0].state, DeliverySubmissionState.ACCEPTED)
        self.assertIsNone(decoded.destinations[0].snapshot.thread_ref)
        self.assertIsNone(decoded.destinations[0].snapshot.route_id)
        self.assertEqual(decoded.destinations[0].receipt, receipt)

    def test_malformed_rows_fail_deterministically_and_mapper_has_no_mutation_policy(self) -> None:
        with self.assertRaisesRegex(ValueError, "must include a timezone") as first:
            row_mapping.decode_datetime("2026-08-04T12:00:00", "created_at")
        with self.assertRaisesRegex(ValueError, "must include a timezone") as second:
            row_mapping.decode_datetime("2026-08-04T12:00:00", "created_at")
        self.assertEqual(str(first.exception), str(second.exception))

        with self.assertRaisesRegex(ValueError, "receipt_json must be valid JSON"):
            row_mapping.decode_receipt("{not-json}")
        with self.assertRaises(ValueError):
            row_mapping.decode_receipt(
                json.dumps(
                    {
                        "status": "not-a-status",
                        "native_message_id": None,
                        "detail": None,
                        "retry_after_seconds": None,
                        "items": [],
                        "segments": [],
                    }
                )
            )

        self.assertFalse(hasattr(row_mapping, "merge_projection_route"))
        self.assertNotIn("row_mapping", persistence_package.__all__)


if __name__ == "__main__":
    unittest.main()

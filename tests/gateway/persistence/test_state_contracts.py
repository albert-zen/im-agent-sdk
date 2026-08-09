from __future__ import annotations

import importlib.util
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import get_type_hints

import imagent.contracts as contracts_facade
import imagent.gateway.persistence as persistence_facade
from imagent.applications.capabilities import (
    ApplicationCapabilities,
    ProjectCapabilities,
    ProjectMode,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
)
from imagent.applications.contract import ApplicationRef, ProjectRef, ThreadRef, TurnRef
from imagent.applications.requests import (
    ApprovalResponseShape,
    RequestRef,
)
from imagent.contracts import ConversationBound
from imagent.gateway.persistence import state_contracts as owner
from imagent.interaction.channels import DeliveryReceipt, DeliveryReceiptStatus
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation


def _capabilities(mode: ProjectMode) -> ApplicationCapabilities:
    support = SupportLevel.NATIVE if mode is ProjectMode.MANAGED else SupportLevel.UNSUPPORTED
    return ApplicationCapabilities(
        projects=ProjectCapabilities(mode=mode, discovery=support, reading=support),
        threads=ThreadCapabilities(
            listing=SupportLevel.NATIVE,
            creation=SupportLevel.NATIVE,
            reading=SupportLevel.NATIVE,
        ),
        runtime=RuntimeCapabilities(
            history=SupportLevel.NATIVE,
            streaming=SupportLevel.NATIVE,
            replay_from_cursor=SupportLevel.UNSUPPORTED,
            interruption=SupportLevel.NATIVE,
            interactive_requests=SupportLevel.NATIVE,
        ),
    )


class StateContractOwnershipTests(unittest.TestCase):
    def test_gateway_operation_result_hints_resolve_without_an_import_cycle(self) -> None:
        self.assertIs(
            get_type_hints(ConversationBound)["binding"],
            owner.ConversationBinding,
        )

    def test_persistence_facade_reexports_exact_owner_objects(self) -> None:
        names = (
            "ConversationBinding",
            "ThreadProjectionRoute",
            "TurnReplyCorrelation",
            "RequestRouteState",
            "RequestRouteCorrelation",
            "DeliverySubmissionState",
            "DeliveryRouteSnapshot",
            "DestinationDeliveryRecord",
            "DeliverySubmissionRecord",
            "DeliveryReservation",
            "validate_binding",
            "validate_projection_route",
            "validate_turn_reply_correlation",
            "validate_request_route_correlation",
            "validate_delivery_route_snapshot",
            "validate_delivery_submission_record",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(persistence_facade, name), getattr(owner, name))
        self.assertIs(contracts_facade.ConversationBinding, owner.ConversationBinding)
        self.assertIn("ConversationBinding", contracts_facade.__all__)
        for name in set(names) - {"ConversationBinding"}:
            self.assertFalse(hasattr(contracts_facade, name))
            self.assertNotIn(name, contracts_facade.__all__)
        self.assertFalse(hasattr(persistence_facade, "DeliverySubmissionOrigin"))
        self.assertNotIn("DeliverySubmissionOrigin", persistence_facade.__all__)
        self.assertFalse(hasattr(persistence_facade, "ProjectionPolicy"))
        self.assertFalse(hasattr(owner, "ProjectionPolicy"))

    def test_retired_contract_modules_are_not_importable(self) -> None:
        for name in (
            "imagent.contracts.model",
            "imagent.contracts.delivery",
            "imagent.contracts.request_validation",
        ):
            with self.subTest(name=name):
                self.assertIsNone(importlib.util.find_spec(name))

    def test_binding_validation_preserves_project_capability_rules(self) -> None:
        conversation = ConversationRef("channel-1", "conversation-1")
        application = ApplicationRef("app-1")
        project = ProjectRef("app-1", "project-1")
        thread = ThreadRef(project, "thread-1")
        owner.validate_binding(
            owner.ConversationBinding(conversation, application, project, thread),
            _capabilities(ProjectMode.MANAGED),
        )
        owner.validate_binding(
            owner.ConversationBinding(conversation, application, project),
            _capabilities(ProjectMode.FIXED),
        )

    def test_projection_route_and_turn_correlation_require_explicit_identity(self) -> None:
        conversation = ConversationRef("channel-1", "conversation-1")
        thread = ThreadRef(ProjectRef("app-1", "workspace"), "thread-1")
        route = owner.ThreadProjectionRoute("route-1", thread, conversation)
        owner.validate_projection_route(route)
        owner.validate_turn_reply_correlation(
            owner.TurnReplyCorrelation(
                "correlation-1",
                TurnRef(thread, "turn-1"),
                "client-1",
                conversation,
                "reply-1",
                datetime.now(UTC),
            )
        )
        with self.assertRaisesRegex(ContractViolation, "present together"):
            owner.validate_projection_route(
                owner.ThreadProjectionRoute(
                    "route-1",
                    thread,
                    conversation,
                    checkpoint_agent_item_id="item-1",
                )
            )

    def test_request_correlation_preserves_scope_and_declared_state(self) -> None:
        now = datetime.now(UTC)
        request_ref = RequestRef(ApplicationRef("app-1"), "request-1")
        correlation = owner.RequestRouteCorrelation(
            correlation_id="correlation-1",
            request_ref=request_ref,
            turn_ref=TurnRef(ThreadRef(ProjectRef("app-1", "workspace"), "thread-1"), "turn-1"),
            conversation_ref=ConversationRef("channel-1", "conversation-1"),
            delivery_id="delivery-1",
            response_shape=ApprovalResponseShape(("accept",)),
            state=owner.RequestRouteState.OPEN,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        owner.validate_request_route_correlation(correlation)
        with self.assertRaisesRegex(ContractViolation, "different application"):
            owner.validate_request_route_correlation(
                replace(
                    correlation,
                    turn_ref=TurnRef(
                        ThreadRef(ProjectRef("other", "workspace"), "thread-1"),
                        "turn-1",
                    ),
                )
            )

    def test_delivery_snapshot_rejects_route_fields_without_a_thread(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "explicit Conversation"):
            owner.validate_delivery_route_snapshot(
                owner.DeliveryRouteSnapshot(
                    conversation_ref=ConversationRef("channel-1", "conversation-1"),
                    route_id="route-1",
                )
            )

    def test_delivery_record_validation_is_bounded_and_fail_closed(self) -> None:
        now = datetime.now(UTC)
        destination = owner.DestinationDeliveryRecord(
            delivery_id="destination-1",
            snapshot=owner.DeliveryRouteSnapshot(
                conversation_ref=ConversationRef("channel-1", "conversation-1")
            ),
            state=owner.DeliverySubmissionState.IN_FLIGHT,
            updated_at=now,
            receipt=DeliveryReceipt(status=DeliveryReceiptStatus.UNKNOWN),
        )
        record = owner.DeliverySubmissionRecord(
            submission_id="submission-1",
            delivery_id="delivery-1",
            origin=owner.DeliverySubmissionOrigin.EXTERNAL,
            principal_id="principal-1",
            target_fingerprint="target-1",
            payload_fingerprint="payload-1",
            destinations=(destination,),
            created_at=now,
            updated_at=now,
        )
        owner.validate_delivery_submission_record(record)
        with self.assertRaisesRegex(ContractViolation, "unique"):
            owner.validate_delivery_submission_record(
                replace(record, destinations=(destination, destination))
            )


if __name__ == "__main__":
    unittest.main()

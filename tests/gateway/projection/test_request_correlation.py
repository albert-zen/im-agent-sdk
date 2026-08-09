from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from dataclasses import fields, replace
from datetime import UTC, datetime

import imagent.contracts as contracts_facade
import imagent.gateway.projection as projection_facade
import imagent.gateway.projection.request_correlation as owner
from imagent.applications.capabilities import ProjectMode
from imagent.applications.contract import (
    AcceptedTurn,
    ApplicationInputDispatch,
    ApplicationRef,
    InputDisposition,
    ProjectRef,
    ThreadRef,
    TurnRef,
    TurnReplyCorrelationPolicy,
)
from imagent.applications.requests import (
    ApprovalResponse,
    ApprovalResponseShape,
    RequestRef,
    derive_request_response_shape,
)
from imagent.gateway.persistence.memory import (
    InMemoryProjectionRouteRepository,
    InMemoryRequestCorrelationRepository,
)
from imagent.gateway.persistence.repository_contracts import (
    IdempotencyClaimStatus,
    RequestCorrelationConflict,
    TurnReplyCorrelationConflict,
)
from imagent.gateway.persistence.state_contracts import (
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
)
from imagent.gateway.projection import (
    InteractiveRequestProjection,
    RequestResponseRouted,
    RespondToRequest,
)
from imagent.gateway.projection.request_correlation import (
    _merge_correlation,
    _select_correlations,
    _transition_correlations,
    derive_request_correlation_id,
    derive_request_delivery_id,
)
from imagent.gateway.routing.operations import (
    validate_gateway_operation,
    validate_gateway_operation_result,
)
from imagent.interaction.controllers import MarkdownRequestPresenter
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation
from imagent.testing import FakeAgentApplicationAdapter

_NOW = datetime(2026, 8, 4, 12, 34, 56, tzinfo=UTC)


def _correlation(
    *,
    conversation_id: str = "conversation-1",
    state: RequestRouteState = RequestRouteState.OPEN,
) -> RequestRouteCorrelation:
    request_ref = RequestRef(ApplicationRef("application-1"), "epoch-1:request-1")
    conversation_ref = ConversationRef("qq-main", conversation_id)
    return RequestRouteCorrelation(
        correlation_id=derive_request_correlation_id(request_ref, conversation_ref),
        request_ref=request_ref,
        turn_ref=TurnRef(ThreadRef(ProjectRef("application-1", "workspace"), "thread-1"), "turn-1"),
        conversation_ref=conversation_ref,
        delivery_id=derive_request_delivery_id(request_ref, conversation_ref),
        response_shape=ApprovalResponseShape(("approve",)),
        state=state,
        created_at=_NOW,
        updated_at=_NOW,
    )


class RequestCorrelationPolicyTests(unittest.TestCase):
    def test_policy_has_one_projection_owner_and_old_module_is_absent(self) -> None:
        self.assertIsNone(importlib.util.find_spec("imagent.request_correlations"))
        self.assertIsNone(importlib.util.find_spec("imagent.request_projection_runtime"))
        self.assertIsNone(importlib.util.find_spec("imagent.contracts.validators"))
        self.assertEqual(
            derive_request_correlation_id.__module__,
            "imagent.gateway.projection.request_correlation",
        )

    def test_public_values_have_one_exact_owner_and_historical_attributes_are_absent(
        self,
    ) -> None:
        self.assertEqual(
            owner.__all__,
            [
                "InteractiveRequestProjection",
                "RequestResponseRouted",
                "RespondToRequest",
            ],
        )
        for name in owner.__all__:
            with self.subTest(name=name):
                value = getattr(owner, name)
                self.assertIs(getattr(projection_facade, name), value)
                self.assertEqual(value.__module__, owner.__name__)
        for name in ("RespondToRequest", "RequestResponseRouted"):
            with self.subTest(name=name, historical=contracts_facade.__name__):
                self.assertFalse(hasattr(contracts_facade, name))
                self.assertNotIn(name, getattr(contracts_facade, "__all__", ()))
        self.assertIsNone(importlib.util.find_spec("imagent.contracts.operations"))

    def test_historical_runtime_import_fails_in_a_clean_process(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-c", "import imagent.request_projection_runtime"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ModuleNotFoundError", completed.stderr)

    def test_response_contract_validation_is_owned_with_the_operation_family(
        self,
    ) -> None:
        request_ref = RequestRef(ApplicationRef("application-1"), "epoch:request-1")
        operation = RespondToRequest(
            operation_id="respond-1",
            conversation_ref=ConversationRef("qq-main", "conversation-1"),
            actor="user-1",
            request_ref=request_ref,
            response=ApprovalResponse("approve"),
            created_at=_NOW,
        )
        validate_gateway_operation(operation)
        result = RequestResponseRouted(
            operation_id=operation.operation_id,
            request_ref=request_ref,
            completed_at=_NOW,
        )
        validate_gateway_operation_result(operation, result)
        with self.assertRaises(ContractViolation):
            validate_gateway_operation(replace(operation, response=ApprovalResponse("")))
        with self.assertRaises(ContractViolation):
            validate_gateway_operation_result(
                operation,
                replace(
                    result,
                    request_ref=RequestRef(
                        ApplicationRef("application-1"),
                        "epoch:request-2",
                    ),
                ),
            )

    def test_stable_ids_include_exact_request_and_conversation_scope(self) -> None:
        request_ref = RequestRef(ApplicationRef("application-1"), "epoch-1:request-1")
        first = ConversationRef("qq-main", "conversation-1")
        second = ConversationRef("qq-main", "conversation-2")
        self.assertEqual(
            derive_request_correlation_id(request_ref, first),
            derive_request_correlation_id(request_ref, first),
        )
        self.assertNotEqual(
            derive_request_delivery_id(request_ref, first),
            derive_request_delivery_id(request_ref, second),
        )

    def test_policy_selects_destinations_and_preserves_terminal_late_delivery(self) -> None:
        first = _correlation()
        second = _correlation(conversation_id="conversation-2")
        selected = _select_correlations(
            (first, second),
            request_ref=first.request_ref,
            thread_ref=None,
            conversation_ref=second.conversation_ref,
        )
        self.assertEqual(selected, (second,))
        terminal = _correlation(state=RequestRouteState.RESOLVED)
        late = _merge_correlation(
            None,
            first,
            request_correlations=(terminal,),
        )
        self.assertIs(late.state, RequestRouteState.RESOLVED)

    def test_transitions_are_forward_only_and_same_state_is_idempotent(self) -> None:
        open_correlation = _correlation()
        responded = _transition_correlations(
            (open_correlation,),
            expected_states=(RequestRouteState.OPEN,),
            state=RequestRouteState.RESPONDED,
            updated_at=_NOW,
        )
        self.assertIs(responded[0].state, RequestRouteState.RESPONDED)
        self.assertEqual(
            _transition_correlations(
                responded,
                expected_states=(RequestRouteState.RESPONDED,),
                state=RequestRouteState.RESPONDED,
                updated_at=_NOW,
            ),
            responded,
        )
        with self.assertRaises(RequestCorrelationConflict):
            _transition_correlations(
                responded,
                expected_states=(RequestRouteState.RESPONDED,),
                state=RequestRouteState.OPEN,
                updated_at=_NOW,
            )


class InteractiveRequestProjectionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.application = FakeAgentApplicationAdapter(
            application_instance_id="application-1",
            project_mode=ProjectMode.FLAT,
        )
        self.thread_ref = (
            await self.application.create_thread(self.application.default_project_ref)
        ).ref
        self.conversation_ref = ConversationRef("qq-main", "conversation-1")
        self.route = ThreadProjectionRoute(
            route_id="route-1",
            thread_ref=self.thread_ref,
            conversation_ref=self.conversation_ref,
            updated_at=_NOW,
        )
        self.projections = InMemoryProjectionRouteRepository()
        await self.projections.put_projection_route(self.route)
        self.correlations = InMemoryRequestCorrelationRepository()
        self.delivery_outcome = IdempotencyClaimStatus.ACQUIRED
        self.delivered_messages = []

        async def active_routes(
            thread_ref: ThreadRef | None,
        ) -> tuple[ThreadProjectionRoute, ...]:
            return await self.projections.list_projection_routes(thread_ref)

        async def deliver_request_outbound(message):
            self.delivered_messages.append(message)
            return self.delivery_outcome

        self.runtime = InteractiveRequestProjection(
            applications={self.application.summary.ref.application_instance_id: self.application},
            projections=self.projections,
            correlations=self.correlations,
            request_presenter=MarkdownRequestPresenter(),
            execute_application=self.application.execute,
            active_routes=active_routes,
            deliver_request_outbound=deliver_request_outbound,
        )

    async def test_started_input_authorizes_create_only_correlation(self) -> None:
        dispatch = ApplicationInputDispatch(
            thread_ref=self.thread_ref,
            client_message_id="client-1",
            disposition=InputDisposition.STARTED,
            correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
        )
        await self.runtime.authorize_input_dispatch(
            dispatch,
            thread_ref=self.thread_ref,
            client_message_id="client-1",
        )
        accepted = AcceptedTurn(
            turn_ref=TurnRef(self.thread_ref, "turn-1"), client_message_id="client-1"
        )
        await self.runtime.correlate_accepted_turn(
            accepted,
            dispatch,
            thread_ref=self.thread_ref,
            client_message_id="client-1",
            conversation_ref=self.conversation_ref,
            reply_to_message_id="message-1",
        )
        stored = await self.projections.get_turn_reply_correlation(
            self.thread_ref,
            "turn-1",
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(
            stored,
            TurnReplyCorrelation(
                correlation_id=owner.derive_turn_reply_correlation_id(
                    TurnRef(self.thread_ref, "turn-1")
                ),
                turn_ref=TurnRef(self.thread_ref, "turn-1"),
                client_message_id="client-1",
                conversation_ref=self.conversation_ref,
                reply_to_message_id="message-1",
                created_at=stored.created_at,
            ),
        )
        with self.assertRaises(ValueError):
            await self.runtime.authorize_input_dispatch(
                replace(
                    dispatch,
                    correlation_policy=TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
                    expected_turn_ref=TurnRef(self.thread_ref, "turn-1"),
                ),
                thread_ref=self.thread_ref,
                client_message_id="client-1",
            )
        with self.assertRaises(TurnReplyCorrelationConflict):
            await self.runtime.correlate_accepted_turn(
                accepted,
                dispatch,
                thread_ref=self.thread_ref,
                client_message_id="client-1",
                conversation_ref=ConversationRef("qq-main", "conversation-2"),
                reply_to_message_id="message-2",
            )

    async def test_steered_input_preserves_exact_turn_and_never_retargets(self) -> None:
        started = ApplicationInputDispatch(
            thread_ref=self.thread_ref,
            client_message_id="client-start",
            disposition=InputDisposition.STARTED,
            correlation_policy=TurnReplyCorrelationPolicy.CREATE_NEW,
        )
        await self.runtime.correlate_accepted_turn(
            AcceptedTurn(
                turn_ref=TurnRef(self.thread_ref, "turn-active"), client_message_id="client-start"
            ),
            started,
            thread_ref=self.thread_ref,
            client_message_id="client-start",
            conversation_ref=self.conversation_ref,
            reply_to_message_id="message-original",
        )
        original = await self.projections.get_turn_reply_correlation(
            self.thread_ref,
            "turn-active",
        )
        dispatch = ApplicationInputDispatch(
            thread_ref=self.thread_ref,
            client_message_id="client-steer",
            disposition=InputDisposition.STEERED,
            correlation_policy=TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
            expected_turn_ref=TurnRef(self.thread_ref, "turn-active"),
        )
        await self.runtime.authorize_input_dispatch(
            dispatch,
            thread_ref=self.thread_ref,
            client_message_id="client-steer",
        )
        await self.runtime.correlate_accepted_turn(
            AcceptedTurn(
                turn_ref=TurnRef(self.thread_ref, "turn-active"),
                client_message_id="client-steer",
                disposition=InputDisposition.STEERED,
                correlation_policy=TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
            ),
            dispatch,
            thread_ref=self.thread_ref,
            client_message_id="client-steer",
            conversation_ref=ConversationRef("qq-main", "conversation-2"),
            reply_to_message_id="message-new-input",
        )
        self.assertEqual(
            await self.projections.get_turn_reply_correlation(
                self.thread_ref,
                "turn-active",
            ),
            original,
        )
        with self.assertRaises(RuntimeError):
            await self.runtime.correlate_accepted_turn(
                AcceptedTurn(
                    turn_ref=TurnRef(self.thread_ref, "turn-retargeted"),
                    client_message_id="client-steer",
                    disposition=InputDisposition.STEERED,
                    correlation_policy=TurnReplyCorrelationPolicy.PRESERVE_EXISTING,
                ),
                dispatch,
                thread_ref=self.thread_ref,
                client_message_id="client-steer",
                conversation_ref=self.conversation_ref,
                reply_to_message_id="message-new-input",
            )
        with self.assertRaises(ValueError):
            await self.runtime.authorize_input_dispatch(
                replace(
                    dispatch,
                    expected_turn_ref=TurnRef(self.thread_ref, "turn-missing"),
                ),
                thread_ref=self.thread_ref,
                client_message_id="client-steer",
            )

    async def test_delivery_persists_minimal_authority_only_after_acceptance(
        self,
    ) -> None:
        request = await self.application.open_approval_request(
            self.thread_ref,
            turn_id="turn-request",
        )
        await self.runtime.deliver_request_once(self.route, request)
        stored = await self.correlations.list_request_correlations(request_ref=request.request_ref)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].delivery_id, self.delivered_messages[0].delivery_id)
        self.assertEqual(stored[0].conversation_ref, self.conversation_ref)
        self.assertEqual(stored[0].response_shape, derive_request_response_shape(request))
        self.assertNotIn("prompt", {item.name for item in fields(RequestRouteCorrelation)})
        self.assertNotIn("response", {item.name for item in fields(RequestRouteCorrelation)})

        blocked = await self.application.open_approval_request(
            self.thread_ref,
            turn_id="turn-blocked",
        )
        self.delivery_outcome = IdempotencyClaimStatus.IN_FLIGHT
        with self.assertRaises(RuntimeError):
            await self.runtime.deliver_request_once(self.route, blocked)
        self.assertEqual(
            await self.correlations.list_request_correlations(request_ref=blocked.request_ref),
            (),
        )

    async def test_response_authority_submits_once_and_transitions_bridge_state(
        self,
    ) -> None:
        request = await self.application.open_approval_request(
            self.thread_ref,
            turn_id="turn-response",
        )
        await self.runtime.deliver_request_once(self.route, request)
        operation = RespondToRequest(
            operation_id="respond-1",
            conversation_ref=self.conversation_ref,
            actor="user-1",
            request_ref=request.request_ref,
            response=ApprovalResponse("accept"),
            created_at=_NOW,
        )
        completed_at = datetime.now(UTC)
        result = await self.runtime.route_response(
            operation,
            completed_at=completed_at,
        )
        self.assertEqual(
            result,
            RequestResponseRouted(
                operation_id="respond-1",
                request_ref=request.request_ref,
                completed_at=completed_at,
            ),
        )
        self.assertEqual(
            self.application.request_responses[request.request_ref],
            ApprovalResponse("accept"),
        )
        self.assertTrue(
            all(
                correlation.state is RequestRouteState.RESPONDED
                for correlation in await self.correlations.list_request_correlations(
                    request_ref=request.request_ref
                )
            )
        )
        self.assertEqual(self.runtime._request_locks.active_key_count, 0)

    async def test_pending_snapshot_gap_reconciliation_is_thread_scoped(self) -> None:
        second_thread_ref = (
            await self.application.create_thread(self.application.default_project_ref)
        ).ref
        first_request = await self.application.open_approval_request(
            self.thread_ref,
            turn_id="turn-first",
        )
        await self.application.open_approval_request(
            second_thread_ref,
            turn_id="turn-second",
        )
        delivered = []

        async def deliver_request(routes, request) -> None:
            del routes
            delivered.append(request)

        degraded = await self.runtime.reconcile_application_after_event_gap(
            self.application,
            self.thread_ref,
            deliver_request=deliver_request,
        )

        self.assertFalse(degraded)
        self.assertEqual(
            [request.request_ref for request in delivered],
            [first_request.request_ref],
        )


if __name__ == "__main__":
    unittest.main()

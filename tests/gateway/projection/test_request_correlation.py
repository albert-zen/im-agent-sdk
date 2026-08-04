from __future__ import annotations

import importlib.util
import unittest
from datetime import UTC, datetime

from imagent.applications.contract import ApplicationRef, ThreadRef
from imagent.applications.requests import ApprovalResponseShape, RequestRef
from imagent.gateway.persistence.repository_contracts import RequestCorrelationConflict
from imagent.gateway.persistence.state_contracts import (
    RequestRouteCorrelation,
    RequestRouteState,
)
from imagent.gateway.projection.request_correlation import (
    _merge_correlation,
    _select_correlations,
    _transition_correlations,
    derive_request_correlation_id,
    derive_request_delivery_id,
)
from imagent.interaction.messages import ConversationRef

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
        thread_ref=ThreadRef("application-1", "thread-1"),
        turn_id="turn-1",
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
        self.assertEqual(
            derive_request_correlation_id.__module__,
            "imagent.gateway.projection.request_correlation",
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


if __name__ == "__main__":
    unittest.main()

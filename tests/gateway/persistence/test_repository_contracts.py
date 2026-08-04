from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from imagent.adapters import RequestCorrelationConflict, RequestCorrelationRepository
from imagent.contracts import (
    ApplicationRef,
    ApprovalResponseShape,
    ConversationRef,
    RequestRef,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadRef,
)
from imagent.gateway.persistence.memory import InMemoryRequestCorrelationRepository
from imagent.request_correlations import derive_request_correlation_id
from imagent.storage import SQLiteGatewayState

_ALLOWED_TARGETS = {
    RequestRouteState.OPEN: frozenset(RequestRouteState),
    RequestRouteState.RESPONDED: frozenset(
        {
            RequestRouteState.RESPONDED,
            RequestRouteState.STALE,
            RequestRouteState.RESOLVED,
        }
    ),
    RequestRouteState.STALE: frozenset({RequestRouteState.STALE, RequestRouteState.RESOLVED}),
    RequestRouteState.RESOLVED: frozenset({RequestRouteState.RESOLVED}),
}


class RequestCorrelationTransitionContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_and_sqlite_enforce_the_complete_monotonic_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sqlite = SQLiteGatewayState(Path(directory) / "request-transitions.sqlite3")
            repositories = (InMemoryRequestCorrelationRepository(), sqlite)
            try:
                for repository in repositories:
                    await self._assert_transition_graph(repository)
            finally:
                await sqlite.close()

    async def _assert_transition_graph(
        self,
        repository: RequestCorrelationRepository,
    ) -> None:
        now = datetime.now(UTC)
        for index, (source, target) in enumerate(
            (source, target) for source in RequestRouteState for target in RequestRouteState
        ):
            with self.subTest(
                repository=type(repository).__name__,
                source=source.value,
                target=target.value,
            ):
                request_id = f"request-{source.value}-{target.value}"
                correlations = tuple(
                    _correlation(
                        request_id=request_id,
                        conversation_id=f"conversation-{suffix}-{request_id}",
                        now=now + timedelta(seconds=index * 3),
                    )
                    for suffix in ("a", "b")
                )
                for correlation in correlations:
                    await repository.put_request_correlation(correlation)
                request_ref = correlations[0].request_ref
                if source is not RequestRouteState.OPEN:
                    await repository.transition_request_correlations(
                        request_ref,
                        expected_states=(RequestRouteState.OPEN,),
                        state=source,
                        updated_at=correlations[0].updated_at + timedelta(seconds=1),
                    )
                before = await repository.list_request_correlations(request_ref=request_ref)
                if target in _ALLOWED_TARGETS[source]:
                    transitioned = await repository.transition_request_correlations(
                        request_ref,
                        expected_states=(source,),
                        state=target,
                        updated_at=correlations[0].updated_at + timedelta(seconds=2),
                    )
                    self.assertEqual(len(transitioned), 2)
                    self.assertTrue(all(item.state is target for item in transitioned))
                    continue
                with self.assertRaises(RequestCorrelationConflict):
                    await repository.transition_request_correlations(
                        request_ref,
                        expected_states=(source,),
                        state=target,
                        updated_at=correlations[0].updated_at + timedelta(seconds=2),
                    )
                self.assertEqual(
                    await repository.list_request_correlations(request_ref=request_ref),
                    before,
                )


def _correlation(
    *,
    request_id: str,
    conversation_id: str,
    now: datetime,
) -> RequestRouteCorrelation:
    application = ApplicationRef("app-main")
    request_ref = RequestRef(application, request_id)
    conversation = ConversationRef("channel-main", conversation_id)
    return RequestRouteCorrelation(
        correlation_id=derive_request_correlation_id(request_ref, conversation),
        request_ref=request_ref,
        thread_ref=ThreadRef("app-main", "thread-main"),
        turn_id="turn-main",
        conversation_ref=conversation,
        delivery_id=f"delivery-{request_id}",
        response_shape=ApprovalResponseShape(("approve", "decline")),
        state=RequestRouteState.OPEN,
        created_at=now,
        updated_at=now,
    )

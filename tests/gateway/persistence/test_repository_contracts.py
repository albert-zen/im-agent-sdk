from __future__ import annotations

import importlib.util
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_type_hints

import imagent.gateway.persistence as persistence_facade
from imagent.applications.contract import ApplicationRef, ProjectRef, ThreadRef, TurnRef
from imagent.applications.requests import ApprovalResponseShape, RequestRef
from imagent.gateway.persistence import (
    ConversationBinding,
    DeliveryReservation,
    DeliverySubmissionRecord,
    DeliverySubmissionState,
    DestinationDeliveryRecord,
    RequestRouteCorrelation,
    RequestRouteState,
    ThreadProjectionRoute,
    TurnReplyCorrelation,
)
from imagent.gateway.persistence import repository_contracts as owner
from imagent.gateway.persistence.memory import InMemoryRequestCorrelationRepository
from imagent.gateway.persistence.sqlite import SQLiteGatewayState
from imagent.gateway.projection.request_correlation import derive_request_correlation_id
from imagent.interaction.messages import ConversationRef

_MOVED_NAMES = (
    "BindingRepository",
    "ProjectionRouteRepository",
    "IdempotencyRepository",
    "RequestCorrelationRepository",
    "DeliverySubmissionRepository",
    "IdempotencyClaimStatus",
    "IdempotencyCapacityError",
    "ProjectionCheckpointConflict",
    "ProjectionRouteConflict",
    "RequestCorrelationConflict",
    "TurnReplyCorrelationConflict",
    "DeliverySubmissionConflict",
    "DeliverySubmissionCapacityError",
)

_ADAPTER_FACADE_NAMES = tuple(name for name in _MOVED_NAMES if name != "IdempotencyCapacityError")

_METHOD_SIGNATURES = {
    (
        "BindingRepository",
        "get",
    ): "(self, conversation: 'ConversationRef') -> 'ConversationBinding | None'",
    (
        "BindingRepository",
        "put",
    ): (
        "(self, binding: 'ConversationBinding', "
        "expected_generation: 'int | None' = None) -> 'ConversationBinding'"
    ),
    (
        "BindingRepository",
        "delete",
    ): (
        "(self, conversation: 'ConversationRef', "
        "expected_generation: 'int | None' = None) -> 'None'"
    ),
    (
        "ProjectionRouteRepository",
        "list_projection_routes",
    ): "(self, thread_ref: 'ThreadRef | None' = None) -> 'tuple[ThreadProjectionRoute, ...]'",
    (
        "ProjectionRouteRepository",
        "put_projection_route",
    ): "(self, route: 'ThreadProjectionRoute') -> 'ThreadProjectionRoute'",
    (
        "ProjectionRouteRepository",
        "replace_thread_projection_routes",
    ): "(self, route: 'ThreadProjectionRoute') -> 'ThreadProjectionRoute'",
    (
        "ProjectionRouteRepository",
        "advance_projection_checkpoint",
    ): (
        "(self, route_id: 'str', *, "
        "expected_agent_item_id: 'str | None', agent_item_id: 'str', "
        "checkpointed_at: 'datetime') -> 'ThreadProjectionRoute'"
    ),
    (
        "ProjectionRouteRepository",
        "delete_projection_routes",
    ): (
        "(self, thread_ref: 'ThreadRef', "
        "conversation_ref: 'ConversationRef | None' = None) -> 'int'"
    ),
    (
        "ProjectionRouteRepository",
        "get_turn_reply_correlation",
    ): "(self, thread_ref: 'ThreadRef', turn_id: 'str') -> 'TurnReplyCorrelation | None'",
    (
        "ProjectionRouteRepository",
        "list_turn_reply_correlations",
    ): "(self, thread_ref: 'ThreadRef | None' = None) -> 'tuple[TurnReplyCorrelation, ...]'",
    (
        "ProjectionRouteRepository",
        "put_turn_reply_correlation",
    ): "(self, correlation: 'TurnReplyCorrelation') -> 'TurnReplyCorrelation'",
    (
        "ProjectionRouteRepository",
        "delete_turn_reply_correlation",
    ): "(self, thread_ref: 'ThreadRef', turn_id: 'str') -> 'bool'",
    (
        "ProjectionRouteRepository",
        "delete_turn_reply_correlations",
    ): (
        "(self, *, thread_ref: 'ThreadRef | None' = None, "
        "conversation_ref: 'ConversationRef | None' = None, "
        "older_than: 'datetime | None' = None) -> 'int'"
    ),
    (
        "IdempotencyRepository",
        "claim",
    ): (
        "(self, scope: 'str', key: 'str', *, "
        "owner_token: 'str | None' = None) -> 'IdempotencyClaimStatus'"
    ),
    (
        "IdempotencyRepository",
        "mark_side_effect_started",
    ): "(self, scope: 'str', key: 'str', *, owner_token: 'str | None' = None) -> 'None'",
    (
        "IdempotencyRepository",
        "refresh",
    ): "(self, scope: 'str', key: 'str', *, owner_token: 'str | None' = None) -> 'None'",
    (
        "IdempotencyRepository",
        "complete",
    ): "(self, scope: 'str', key: 'str', *, owner_token: 'str | None' = None) -> 'None'",
    (
        "IdempotencyRepository",
        "release",
    ): "(self, scope: 'str', key: 'str', *, owner_token: 'str | None' = None) -> 'None'",
    (
        "DeliverySubmissionRepository",
        "get_delivery_submission",
    ): "(self, submission_id: 'str') -> 'DeliverySubmissionRecord | None'",
    (
        "DeliverySubmissionRepository",
        "reserve_delivery_submission",
    ): "(self, record: 'DeliverySubmissionRecord') -> 'DeliveryReservation'",
    (
        "DeliverySubmissionRepository",
        "update_delivery_destination",
    ): (
        "(self, submission_id: 'str', destination_delivery_id: 'str', *, "
        "expected_state: 'DeliverySubmissionState', "
        "destination: 'DestinationDeliveryRecord') -> 'DeliverySubmissionRecord'"
    ),
    (
        "RequestCorrelationRepository",
        "list_request_correlations",
    ): (
        "(self, *, request_ref: 'RequestRef | None' = None, "
        "thread_ref: 'ThreadRef | None' = None, "
        "conversation_ref: 'ConversationRef | None' = None) -> "
        "'tuple[RequestRouteCorrelation, ...]'"
    ),
    (
        "RequestCorrelationRepository",
        "put_request_correlation",
    ): "(self, correlation: 'RequestRouteCorrelation') -> 'RequestRouteCorrelation'",
    (
        "RequestCorrelationRepository",
        "transition_request_correlations",
    ): (
        "(self, request_ref: 'RequestRef', *, "
        "expected_states: 'tuple[RequestRouteState, ...]', "
        "state: 'RequestRouteState', updated_at: 'datetime') -> "
        "'tuple[RequestRouteCorrelation, ...]'"
    ),
    (
        "RequestCorrelationRepository",
        "delete_request_correlations",
    ): (
        "(self, *, request_ref: 'RequestRef | None' = None, "
        "thread_ref: 'ThreadRef | None' = None, "
        "conversation_ref: 'ConversationRef | None' = None, "
        "older_than: 'datetime | None' = None) -> 'int'"
    ),
}

_EXPECTED_METHOD_HINTS = {
    ("BindingRepository", "get"): {
        "conversation": ConversationRef,
        "return": ConversationBinding | None,
    },
    ("BindingRepository", "put"): {
        "binding": ConversationBinding,
        "expected_generation": int | None,
        "return": ConversationBinding,
    },
    ("BindingRepository", "delete"): {
        "conversation": ConversationRef,
        "expected_generation": int | None,
        "return": type(None),
    },
    ("ProjectionRouteRepository", "list_projection_routes"): {
        "thread_ref": ThreadRef | None,
        "return": tuple[ThreadProjectionRoute, ...],
    },
    ("ProjectionRouteRepository", "put_projection_route"): {
        "route": ThreadProjectionRoute,
        "return": ThreadProjectionRoute,
    },
    ("ProjectionRouteRepository", "replace_thread_projection_routes"): {
        "route": ThreadProjectionRoute,
        "return": ThreadProjectionRoute,
    },
    ("ProjectionRouteRepository", "advance_projection_checkpoint"): {
        "route_id": str,
        "expected_agent_item_id": str | None,
        "agent_item_id": str,
        "checkpointed_at": datetime,
        "return": ThreadProjectionRoute,
    },
    ("ProjectionRouteRepository", "delete_projection_routes"): {
        "thread_ref": ThreadRef,
        "conversation_ref": ConversationRef | None,
        "return": int,
    },
    ("ProjectionRouteRepository", "get_turn_reply_correlation"): {
        "thread_ref": ThreadRef,
        "turn_id": str,
        "return": TurnReplyCorrelation | None,
    },
    ("ProjectionRouteRepository", "list_turn_reply_correlations"): {
        "thread_ref": ThreadRef | None,
        "return": tuple[TurnReplyCorrelation, ...],
    },
    ("ProjectionRouteRepository", "put_turn_reply_correlation"): {
        "correlation": TurnReplyCorrelation,
        "return": TurnReplyCorrelation,
    },
    ("ProjectionRouteRepository", "delete_turn_reply_correlation"): {
        "thread_ref": ThreadRef,
        "turn_id": str,
        "return": bool,
    },
    ("ProjectionRouteRepository", "delete_turn_reply_correlations"): {
        "thread_ref": ThreadRef | None,
        "conversation_ref": ConversationRef | None,
        "older_than": datetime | None,
        "return": int,
    },
    ("IdempotencyRepository", "claim"): {
        "scope": str,
        "key": str,
        "owner_token": str | None,
        "return": owner.IdempotencyClaimStatus,
    },
    ("IdempotencyRepository", "mark_side_effect_started"): {
        "scope": str,
        "key": str,
        "owner_token": str | None,
        "return": type(None),
    },
    ("IdempotencyRepository", "refresh"): {
        "scope": str,
        "key": str,
        "owner_token": str | None,
        "return": type(None),
    },
    ("IdempotencyRepository", "complete"): {
        "scope": str,
        "key": str,
        "owner_token": str | None,
        "return": type(None),
    },
    ("IdempotencyRepository", "release"): {
        "scope": str,
        "key": str,
        "owner_token": str | None,
        "return": type(None),
    },
    ("DeliverySubmissionRepository", "get_delivery_submission"): {
        "submission_id": str,
        "return": DeliverySubmissionRecord | None,
    },
    ("DeliverySubmissionRepository", "reserve_delivery_submission"): {
        "record": DeliverySubmissionRecord,
        "return": DeliveryReservation,
    },
    ("DeliverySubmissionRepository", "update_delivery_destination"): {
        "submission_id": str,
        "destination_delivery_id": str,
        "expected_state": DeliverySubmissionState,
        "destination": DestinationDeliveryRecord,
        "return": DeliverySubmissionRecord,
    },
    ("RequestCorrelationRepository", "list_request_correlations"): {
        "request_ref": RequestRef | None,
        "thread_ref": ThreadRef | None,
        "conversation_ref": ConversationRef | None,
        "return": tuple[RequestRouteCorrelation, ...],
    },
    ("RequestCorrelationRepository", "put_request_correlation"): {
        "correlation": RequestRouteCorrelation,
        "return": RequestRouteCorrelation,
    },
    ("RequestCorrelationRepository", "transition_request_correlations"): {
        "request_ref": RequestRef,
        "expected_states": tuple[RequestRouteState, ...],
        "state": RequestRouteState,
        "updated_at": datetime,
        "return": tuple[RequestRouteCorrelation, ...],
    },
    ("RequestCorrelationRepository", "delete_request_correlations"): {
        "request_ref": RequestRef | None,
        "thread_ref": ThreadRef | None,
        "conversation_ref": ConversationRef | None,
        "older_than": datetime | None,
        "return": int,
    },
}

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


class RepositoryContractOwnershipTests(unittest.TestCase):
    def test_repository_contract_facades_preserve_exact_owner_identity(self) -> None:
        self.assertEqual(set(owner.__all__), {"BindingConflict", *_MOVED_NAMES})
        self.assertIs(persistence_facade.BindingConflict, owner.BindingConflict)

        for name in _MOVED_NAMES:
            with self.subTest(name=name):
                value = getattr(owner, name)
                self.assertEqual(value.__module__, owner.__name__)
                self.assertIs(getattr(persistence_facade, name), value)
        self.assertIsNone(importlib.util.find_spec("imagent.adapters"))

    def test_adapters_has_no_repository_contract_class_definitions(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        self.assertFalse((repository_root / "src" / "imagent" / "adapters.py").exists())

    def test_repository_protocol_signatures_are_unchanged(self) -> None:
        for (protocol_name, method_name), expected in _METHOD_SIGNATURES.items():
            with self.subTest(protocol=protocol_name, method=method_name):
                method = getattr(getattr(owner, protocol_name), method_name)
                self.assertEqual(str(inspect.signature(method)), expected)

    def test_repository_protocol_type_hints_resolve_to_owner_contracts(self) -> None:
        for (protocol_name, method_name), expected in _EXPECTED_METHOD_HINTS.items():
            with self.subTest(protocol=protocol_name, method=method_name):
                method = getattr(getattr(owner, protocol_name), method_name)
                self.assertEqual(get_type_hints(method), expected)

    def test_repository_contract_imports_and_hints_are_clean_in_new_process(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        environment = os.environ.copy()
        source_root = str(repository_root / "src")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_root
            if not existing_pythonpath
            else os.pathsep.join((source_root, existing_pythonpath))
        )
        code = """
import typing

import importlib.util
import imagent.gateway.persistence as persistence
from imagent.applications.requests import RequestRef
from imagent.gateway.persistence import repository_contracts as owner

names = (
    "BindingRepository",
    "ProjectionRouteRepository",
    "IdempotencyRepository",
    "RequestCorrelationRepository",
    "DeliverySubmissionRepository",
    "IdempotencyClaimStatus",
    "ProjectionCheckpointConflict",
    "ProjectionRouteConflict",
    "RequestCorrelationConflict",
    "TurnReplyCorrelationConflict",
    "DeliverySubmissionConflict",
    "DeliverySubmissionCapacityError",
)
assert importlib.util.find_spec("imagent.adapters") is None
assert persistence.IdempotencyCapacityError is owner.IdempotencyCapacityError
assert typing.get_type_hints(
    owner.RequestCorrelationRepository.transition_request_correlations,
)["request_ref"] is RequestRef
assert owner.IdempotencyClaimStatus.ACQUIRED.value == "acquired"
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            cwd=repository_root,
            env=environment,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


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
        repository: owner.RequestCorrelationRepository,
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
                with self.assertRaises(owner.RequestCorrelationConflict):
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
        turn_ref=TurnRef(
            ThreadRef(ProjectRef("app-main", "workspace"), "thread-main"), "turn-main"
        ),
        conversation_ref=conversation,
        delivery_id=f"delivery-{request_id}",
        response_shape=ApprovalResponseShape(("approve", "decline")),
        state=RequestRouteState.OPEN,
        created_at=now,
        updated_at=now,
    )

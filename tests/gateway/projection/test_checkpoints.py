from __future__ import annotations

import asyncio
import importlib.util
import unittest
from subprocess import run
from sys import executable
from typing import cast

from imagent.applications.contract import ThreadRef
from imagent.gateway.persistence import ThreadProjectionRoute
from imagent.gateway.persistence.memory import InMemoryProjectionRouteRepository
from imagent.gateway.persistence.repository_contracts import (
    IdempotencyClaimStatus,
    ProjectionCheckpointConflict,
)
from imagent.gateway.projection import derive_projection_delivery_id
from imagent.gateway.projection.checkpoints import _ProjectionCheckpointAuthority
from imagent.gateway.routing.projection_routes import derive_projection_route_id
from imagent.interaction.messages import ConversationRef


class ProjectionCheckpointOwnershipTests(unittest.TestCase):
    def test_gateway_projection_facade_preserves_exact_delivery_id_owner(self) -> None:
        from imagent.gateway.projection.checkpoints import (
            derive_projection_delivery_id as owner_derivation,
        )

        self.assertIs(derive_projection_delivery_id, owner_derivation)

    def test_derivation_preserves_stable_destination_item_identity(self) -> None:
        conversation = ConversationRef("channel-a", "conversation-a")
        thread = ThreadRef("application-a", "thread-a")

        delivery_id = derive_projection_delivery_id(conversation, thread, "item-a")

        self.assertEqual(
            delivery_id,
            derive_projection_delivery_id(conversation, thread, "item-a"),
        )
        self.assertNotEqual(
            delivery_id,
            derive_projection_delivery_id(conversation, thread, "item-b"),
        )
        self.assertNotEqual(
            delivery_id,
            derive_projection_delivery_id(conversation, thread, "item-a", segment_index=1),
        )
        self.assertNotEqual(
            delivery_id,
            derive_projection_delivery_id(
                ConversationRef("channel-a", "conversation-b"),
                thread,
                "item-a",
            ),
        )

    def test_historical_projection_modules_are_absent(self) -> None:
        for module_name in (
            "imagent.projection_runtime",
            "imagent.projections",
            "imagent.projection_routes",
        ):
            self.assertIsNone(importlib.util.find_spec(module_name))
            result = run(
                [executable, "-c", f"import {module_name}"],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ModuleNotFoundError", result.stderr)
            self.assertIn(module_name, result.stderr)


class ProjectionCheckpointConvergenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_completed_outcome_advances_expected_checkpoint(self) -> None:
        repository = InMemoryProjectionRouteRepository()
        route = await repository.put_projection_route(_route("first-conversation"))
        authority = _ProjectionCheckpointAuthority(projections=repository)

        advanced = await authority.apply_delivery_outcome(
            route,
            agent_item_id="item-1",
            checkpointable=True,
            delivery_outcome=IdempotencyClaimStatus.ACQUIRED,
            authoritative=False,
            delivery_id=_delivery_id(route, "item-1"),
        )

        self.assertEqual(advanced.checkpoint_agent_item_id, "item-1")
        self.assertIsNotNone(advanced.checkpointed_at)

    async def test_authoritative_completed_evidence_converges_without_item_ordering(self) -> None:
        repository = InMemoryProjectionRouteRepository()
        route = await repository.put_projection_route(_route("ordered-conversation"))
        authority = _ProjectionCheckpointAuthority(projections=repository)
        checkpointed = await authority.apply_delivery_outcome(
            route,
            agent_item_id="z-opaque-item",
            checkpointable=True,
            delivery_outcome=IdempotencyClaimStatus.ACQUIRED,
            authoritative=False,
            delivery_id=_delivery_id(route, "z-opaque-item"),
        )

        live_duplicate = await authority.apply_delivery_outcome(
            checkpointed,
            agent_item_id="a-opaque-item",
            checkpointable=True,
            delivery_outcome=IdempotencyClaimStatus.ALREADY_COMPLETED,
            authoritative=False,
            delivery_id=_delivery_id(route, "a-opaque-item"),
        )
        converged = await authority.apply_delivery_outcome(
            checkpointed,
            agent_item_id="a-opaque-item",
            checkpointable=True,
            delivery_outcome=IdempotencyClaimStatus.ALREADY_COMPLETED,
            authoritative=True,
            delivery_id=_delivery_id(route, "a-opaque-item"),
        )

        self.assertEqual(live_duplicate.checkpoint_agent_item_id, "z-opaque-item")
        self.assertEqual(converged.checkpoint_agent_item_id, "a-opaque-item")

    async def test_live_only_and_in_flight_outcomes_never_advance(self) -> None:
        repository = InMemoryProjectionRouteRepository()
        route = await repository.put_projection_route(_route("live-conversation"))
        authority = _ProjectionCheckpointAuthority(projections=repository)

        live_only = await authority.apply_delivery_outcome(
            route,
            agent_item_id="live-item",
            checkpointable=False,
            delivery_outcome=IdempotencyClaimStatus.ACQUIRED,
            authoritative=False,
            delivery_id="imagent:delivery:live:sha256:live-item",
        )
        with self.assertRaisesRegex(RuntimeError, "delivery remains in flight"):
            await authority.apply_delivery_outcome(
                route,
                agent_item_id="in-flight-item",
                checkpointable=True,
                delivery_outcome=IdempotencyClaimStatus.IN_FLIGHT,
                authoritative=True,
                delivery_id=_delivery_id(route, "in-flight-item"),
            )

        self.assertIsNone(live_only.checkpoint_agent_item_id)
        stored = (await repository.list_projection_routes(route.thread_ref))[0]
        self.assertIsNone(stored.checkpoint_agent_item_id)

    async def test_non_completed_outcomes_fail_without_advancing(self) -> None:
        repository = InMemoryProjectionRouteRepository()
        route = await repository.put_projection_route(_route("failed-conversation"))
        authority = _ProjectionCheckpointAuthority(projections=repository)

        for outcome in ("retryable", "failed", "outcome_unknown"):
            with self.subTest(outcome=outcome):
                with self.assertRaisesRegex(RuntimeError, "did not durably complete"):
                    await authority.apply_delivery_outcome(
                        route,
                        agent_item_id=f"{outcome}-item",
                        checkpointable=True,
                        delivery_outcome=cast(IdempotencyClaimStatus, outcome),
                        authoritative=True,
                        delivery_id=_delivery_id(route, f"{outcome}-item"),
                    )

        stored = (await repository.list_projection_routes(route.thread_ref))[0]
        self.assertIsNone(stored.checkpoint_agent_item_id)

    async def test_same_item_is_idempotent_without_a_second_compare_and_swap(self) -> None:
        repository = InMemoryProjectionRouteRepository()
        route = await repository.put_projection_route(_route("repeat-conversation"))
        authority = _ProjectionCheckpointAuthority(projections=repository)
        first = await authority.apply_delivery_outcome(
            route,
            agent_item_id="same-item",
            checkpointable=True,
            delivery_outcome=IdempotencyClaimStatus.ACQUIRED,
            authoritative=False,
            delivery_id=_delivery_id(route, "same-item"),
        )

        repeated = await authority.apply_delivery_outcome(
            first,
            agent_item_id="same-item",
            checkpointable=True,
            delivery_outcome=IdempotencyClaimStatus.ACQUIRED,
            authoritative=False,
            delivery_id=_delivery_id(route, "same-item"),
        )

        self.assertEqual(repeated, first)
        self.assertEqual(repeated.checkpointed_at, first.checkpointed_at)

    async def test_competing_expected_current_compare_and_swap_stays_explicit(self) -> None:
        repository = InMemoryProjectionRouteRepository()
        route = await repository.put_projection_route(_route("competing-conversation"))
        authority = _ProjectionCheckpointAuthority(projections=repository)

        outcomes = await asyncio.gather(
            authority.apply_delivery_outcome(
                route,
                agent_item_id="first-item",
                checkpointable=True,
                delivery_outcome=IdempotencyClaimStatus.ACQUIRED,
                authoritative=False,
                delivery_id=_delivery_id(route, "first-item"),
            ),
            authority.apply_delivery_outcome(
                route,
                agent_item_id="second-item",
                checkpointable=True,
                delivery_outcome=IdempotencyClaimStatus.ACQUIRED,
                authoritative=False,
                delivery_id=_delivery_id(route, "second-item"),
            ),
            return_exceptions=True,
        )

        self.assertEqual(
            sum(isinstance(outcome, ThreadProjectionRoute) for outcome in outcomes),
            1,
        )
        self.assertEqual(
            sum(isinstance(outcome, ProjectionCheckpointConflict) for outcome in outcomes),
            1,
        )

    async def test_destinations_advance_independently(self) -> None:
        repository = InMemoryProjectionRouteRepository()
        first = await repository.put_projection_route(_route("first-destination"))
        second = await repository.put_projection_route(_route("second-destination"))
        authority = _ProjectionCheckpointAuthority(projections=repository)

        first_result, second_result = await asyncio.gather(
            authority.apply_delivery_outcome(
                first,
                agent_item_id="first-item",
                checkpointable=True,
                delivery_outcome=IdempotencyClaimStatus.ACQUIRED,
                authoritative=False,
                delivery_id=_delivery_id(first, "first-item"),
            ),
            authority.apply_delivery_outcome(
                second,
                agent_item_id="second-item",
                checkpointable=True,
                delivery_outcome=IdempotencyClaimStatus.ACQUIRED,
                authoritative=False,
                delivery_id=_delivery_id(second, "second-item"),
            ),
        )

        self.assertEqual(first_result.checkpoint_agent_item_id, "first-item")
        self.assertEqual(second_result.checkpoint_agent_item_id, "second-item")
        self.assertNotEqual(first_result.route_id, second_result.route_id)


def _route(conversation_id: str) -> ThreadProjectionRoute:
    thread = ThreadRef("application", "thread")
    conversation = ConversationRef("channel", conversation_id)
    return ThreadProjectionRoute(
        route_id=derive_projection_route_id(thread, conversation),
        thread_ref=thread,
        conversation_ref=conversation,
    )


def _delivery_id(route: ThreadProjectionRoute, agent_item_id: str) -> str:
    return derive_projection_delivery_id(
        route.conversation_ref,
        route.thread_ref,
        agent_item_id,
    )

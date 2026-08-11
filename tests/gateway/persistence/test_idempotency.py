from __future__ import annotations

import asyncio
import unittest

import imagent.gateway.persistence.sqlite as sqlite_owner
from imagent.gateway import persistence
from imagent.gateway.persistence import (
    IdempotencyCapacityError,
    InMemoryIdempotencyRepository,
)
from imagent.gateway.persistence.idempotency import (
    InMemoryIdempotencyRepository as LeafInMemoryIdempotencyRepository,
)
from imagent.gateway.persistence.repository_contracts import IdempotencyClaimStatus


class InMemoryIdempotencyRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def test_persistence_facade_is_exact_and_sqlite_owner_export_is_absent(self) -> None:
        self.assertIs(
            InMemoryIdempotencyRepository,
            LeafInMemoryIdempotencyRepository,
        )
        self.assertIs(persistence.InMemoryIdempotencyRepository, InMemoryIdempotencyRepository)
        self.assertIs(persistence.IdempotencyCapacityError, IdempotencyCapacityError)
        self.assertFalse(hasattr(sqlite_owner, "InMemoryIdempotencyRepository"))

    def test_constructor_requires_a_positive_finite_record_bound(self) -> None:
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    InMemoryIdempotencyRepository(max_records=invalid)  # type: ignore[arg-type]

        self.assertEqual(InMemoryIdempotencyRepository(max_records=3).max_records, 3)

    async def test_claim_transitions_are_owner_fenced(self) -> None:
        repository = InMemoryIdempotencyRepository()
        scope = "inbound:channel-a"
        key = "message-a"

        self.assertEqual(
            await repository.claim(scope, key, owner_token="owner-a"),
            IdempotencyClaimStatus.ACQUIRED,
        )
        self.assertEqual(
            await repository.claim(scope, key, owner_token="owner-b"),
            IdempotencyClaimStatus.IN_FLIGHT,
        )
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            await repository.refresh(scope, key, owner_token="owner-b")

        await repository.refresh(scope, key, owner_token="owner-a")
        await repository.mark_side_effect_started(scope, key, owner_token="owner-a")
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            await repository.refresh(scope, key, owner_token="owner-a")
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            await repository.complete(scope, key, owner_token="owner-b")

        await repository.complete(scope, key, owner_token="owner-a")
        self.assertEqual(
            await repository.claim(scope, key, owner_token="owner-c"),
            IdempotencyClaimStatus.ALREADY_COMPLETED,
        )

    async def test_release_is_owner_checked_and_restart_starts_empty(self) -> None:
        repository = InMemoryIdempotencyRepository()
        scope = "outbound:channel-a"
        key = "delivery-a"

        self.assertEqual(
            await repository.claim(scope, key, owner_token="owner-a"),
            IdempotencyClaimStatus.ACQUIRED,
        )
        await repository.release(scope, key, owner_token="owner-b")
        self.assertEqual(
            await repository.claim(scope, key, owner_token="owner-c"),
            IdempotencyClaimStatus.IN_FLIGHT,
        )
        await repository.release(scope, key, owner_token="owner-a")
        self.assertEqual(
            await repository.claim(scope, key, owner_token="owner-c"),
            IdempotencyClaimStatus.ACQUIRED,
        )

        restarted = InMemoryIdempotencyRepository()
        self.assertEqual(
            await restarted.claim(scope, key, owner_token="after-restart"),
            IdempotencyClaimStatus.ACQUIRED,
        )

    async def test_capacity_preserves_existing_replay_and_fenced_transitions(self) -> None:
        repository = InMemoryIdempotencyRepository(max_records=2)
        scope = "outbound:channel-a"

        self.assertEqual(
            await repository.claim(scope, "completed", owner_token="owner-completed"),
            IdempotencyClaimStatus.ACQUIRED,
        )
        await repository.complete(scope, "completed", owner_token="owner-completed")
        self.assertEqual(
            await repository.claim(scope, "active", owner_token="owner-active"),
            IdempotencyClaimStatus.ACQUIRED,
        )

        with self.assertRaises(IdempotencyCapacityError):
            await repository.claim(scope, "new", owner_token="owner-new")

        self.assertEqual(
            await repository.claim(scope, "completed", owner_token="replay"),
            IdempotencyClaimStatus.ALREADY_COMPLETED,
        )
        self.assertEqual(
            await repository.claim(scope, "active", owner_token="joiner"),
            IdempotencyClaimStatus.IN_FLIGHT,
        )
        await repository.refresh(scope, "active", owner_token="owner-active")
        await repository.mark_side_effect_started(scope, "active", owner_token="owner-active")
        self.assertEqual(
            await repository.claim(scope, "active", owner_token="joiner"),
            IdempotencyClaimStatus.IN_FLIGHT,
        )
        await repository.complete(scope, "active", owner_token="owner-active")
        self.assertEqual(
            await repository.claim(scope, "active", owner_token="replay"),
            IdempotencyClaimStatus.ALREADY_COMPLETED,
        )

        with self.assertRaises(IdempotencyCapacityError):
            await repository.claim(scope, "new", owner_token="owner-new")

    async def test_capacity_race_has_one_winner_and_owner_release_frees_only_its_claim(
        self,
    ) -> None:
        repository = InMemoryIdempotencyRepository(max_records=1)
        scope = "inbound:channel-a"

        async def claim(key: str):
            try:
                return await repository.claim(scope, key, owner_token=key)
            except IdempotencyCapacityError as error:
                return error

        first, second = await asyncio.gather(claim("first"), claim("second"))
        outcomes = (first, second)
        acquired = [
            key
            for key, outcome in zip(("first", "second"), outcomes, strict=True)
            if outcome is IdempotencyClaimStatus.ACQUIRED
        ]
        self.assertEqual(len(acquired), 1)
        self.assertEqual(
            sum(isinstance(outcome, IdempotencyCapacityError) for outcome in outcomes),
            1,
        )

        winner = acquired[0]
        with self.assertRaises(IdempotencyCapacityError):
            await repository.claim(scope, "third", owner_token="third")
        await repository.release(scope, winner, owner_token=winner)
        self.assertEqual(
            await repository.claim(scope, "third", owner_token="third"),
            IdempotencyClaimStatus.ACQUIRED,
        )


if __name__ == "__main__":
    unittest.main()

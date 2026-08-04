from __future__ import annotations

import unittest

import imagent.gateway.persistence.sqlite as sqlite_owner
from imagent.adapters import IdempotencyClaimStatus
from imagent.gateway import persistence
from imagent.gateway.persistence import InMemoryIdempotencyRepository
from imagent.gateway.persistence.idempotency import (
    InMemoryIdempotencyRepository as LeafInMemoryIdempotencyRepository,
)


class InMemoryIdempotencyRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def test_persistence_facade_is_exact_and_sqlite_owner_export_is_absent(self) -> None:
        self.assertIs(
            InMemoryIdempotencyRepository,
            LeafInMemoryIdempotencyRepository,
        )
        self.assertIs(persistence.InMemoryIdempotencyRepository, InMemoryIdempotencyRepository)
        self.assertFalse(hasattr(sqlite_owner, "InMemoryIdempotencyRepository"))

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


if __name__ == "__main__":
    unittest.main()

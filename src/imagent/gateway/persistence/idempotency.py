from __future__ import annotations

import asyncio

from .repository_contracts import IdempotencyClaimStatus


class InMemoryIdempotencyRepository:
    """Process-local stable claim state for tests and ephemeral deployments."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], tuple[str, str | None]] = {}
        self._lock = asyncio.Lock()

    async def claim(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> IdempotencyClaimStatus:
        async with self._lock:
            record = (scope, key)
            current = self._records.get(record)
            status = current[0] if current is not None else None
            if status == "completed":
                return IdempotencyClaimStatus.ALREADY_COMPLETED
            if status in {"in_flight", "side_effect_started"}:
                return IdempotencyClaimStatus.IN_FLIGHT
            self._records[record] = ("in_flight", owner_token)
            return IdempotencyClaimStatus.ACQUIRED

    async def complete(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            record = (scope, key)
            current = self._records.get(record)
            if current is None or current[1] != owner_token:
                raise RuntimeError("idempotency claim is not owned by caller")
            self._records[record] = ("completed", owner_token)

    async def mark_side_effect_started(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            record = (scope, key)
            if self._records.get(record) != ("in_flight", owner_token):
                raise RuntimeError("idempotency claim is not owned by caller")
            self._records[record] = ("side_effect_started", owner_token)

    async def refresh(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            if self._records.get((scope, key)) != ("in_flight", owner_token):
                raise RuntimeError("idempotency claim is not owned by caller")

    async def release(
        self,
        scope: str,
        key: str,
        *,
        owner_token: str | None = None,
    ) -> None:
        async with self._lock:
            record = (scope, key)
            current = self._records.get(record)
            if current in {
                ("in_flight", owner_token),
                ("side_effect_started", owner_token),
            }:
                self._records.pop(record, None)

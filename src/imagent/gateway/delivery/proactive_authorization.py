from __future__ import annotations

import asyncio
import secrets

from ...adapters import DeliveryAuthorizer as DeliveryAuthorizer
from ...contracts import DeliveryPrincipal as DeliveryPrincipal
from ...contracts import validate_delivery_principal as validate_delivery_principal


class DeliveryAuthorizationError(PermissionError):
    pass


class ScopedDeliveryAuthorizer:
    """Reference process-local capability registry; consumers may replace it."""

    def __init__(self) -> None:
        self._principals: dict[str, DeliveryPrincipal] = {}
        self._lock = asyncio.Lock()

    async def issue(
        self,
        principal: DeliveryPrincipal,
        *,
        credential: str | None = None,
    ) -> str:
        validate_delivery_principal(principal)
        token = credential or secrets.token_urlsafe(32)
        if not token or len(token) > 4_096:
            raise ValueError("delivery credential must contain at most 4096 characters")
        async with self._lock:
            if token in self._principals:
                raise ValueError("delivery credential already exists")
            self._principals[token] = principal
        return token

    async def revoke(self, credential: str) -> bool:
        async with self._lock:
            return self._principals.pop(credential, None) is not None

    async def authenticate(self, credential: str) -> DeliveryPrincipal:
        if not credential:
            raise DeliveryAuthorizationError("delivery credential is required")
        async with self._lock:
            principal = self._principals.get(credential)
        if principal is None:
            raise DeliveryAuthorizationError("delivery credential is invalid")
        return principal

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Protocol

from ...applications.contract import ThreadRef, validate_thread_ref
from ...interaction.messages import ConversationRef
from ...interaction.operations import ContractViolation, require_identifier


class DeliveryAuthorizer(Protocol):
    async def authenticate(self, credential: str) -> DeliveryPrincipal: ...


@dataclass(frozen=True, slots=True)
class DeliveryPrincipal:
    principal_id: str
    allowed_threads: tuple[ThreadRef, ...] = ()
    allowed_conversations: tuple[ConversationRef, ...] = ()


def validate_delivery_principal(principal: DeliveryPrincipal) -> None:
    require_identifier(principal.principal_id, "principal_id")
    if len(set(principal.allowed_threads)) != len(principal.allowed_threads):
        raise ContractViolation("allowed_threads must be unique")
    if len(set(principal.allowed_conversations)) != len(principal.allowed_conversations):
        raise ContractViolation("allowed_conversations must be unique")
    for thread_ref in principal.allowed_threads:
        validate_thread_ref(thread_ref)
    for conversation_ref in principal.allowed_conversations:
        _validate_conversation_ref(conversation_ref)


class DeliveryAuthorizationError(PermissionError):
    pass


class ScopedDeliveryAuthorizer:
    """Reference process-local capability registry; consumers may replace it."""

    def __init__(self, *, max_principals: int = 4096) -> None:
        if (
            not isinstance(max_principals, int)
            or isinstance(max_principals, bool)
            or max_principals < 1
        ):
            raise ValueError("max_principals must be a positive integer")
        self._max_principals = max_principals
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
            if len(self._principals) >= self._max_principals:
                raise ValueError("delivery credential registry capacity is exhausted")
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


def _validate_conversation_ref(conversation_ref: ConversationRef) -> None:
    require_identifier(conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(conversation_ref.native_conversation_id, "native_conversation_id")

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ...interaction.messages import ConversationRef

if TYPE_CHECKING:
    from ...contracts.model import ThreadRef


class DeliveryAuthorizer(Protocol):
    async def authenticate(self, credential: str) -> DeliveryPrincipal: ...


@dataclass(frozen=True, slots=True)
class DeliveryPrincipal:
    principal_id: str
    allowed_threads: tuple[ThreadRef, ...] = ()
    allowed_conversations: tuple[ConversationRef, ...] = ()


def validate_delivery_principal(principal: DeliveryPrincipal) -> None:
    from ...contracts._validation import validate_thread_ref
    from ...interaction.operations import ContractViolation, require_identifier

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


def _validate_conversation_ref(conversation_ref: ConversationRef) -> None:
    from ...interaction.operations import require_identifier

    require_identifier(conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(conversation_ref.native_conversation_id, "native_conversation_id")


# Bind the historical Thread reference only after this Gateway owner is fully
# defined, so runtime annotation inspection remains supported without an
# import-time cycle through the contracts facade.
from ...contracts.model import ThreadRef  # noqa: E402

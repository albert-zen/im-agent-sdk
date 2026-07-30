from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from .contracts import ConversationBinding, ConversationRef, validate_binding


class BindingConflict(RuntimeError):
    pass


class InMemoryBindingRepository:
    """Atomic process-local binding storage with optimistic revision checks."""

    def __init__(self) -> None:
        self._bindings: dict[ConversationRef, ConversationBinding] = {}
        self._lock = asyncio.Lock()

    async def get(self, conversation: ConversationRef) -> ConversationBinding | None:
        async with self._lock:
            return self._bindings.get(conversation)

    async def put(
        self,
        binding: ConversationBinding,
        expected_revision: int | None = None,
    ) -> ConversationBinding:
        validate_binding(binding)
        async with self._lock:
            current = self._bindings.get(binding.conversation_ref)
            current_revision = current.revision if current is not None else 0
            if expected_revision is not None and expected_revision != current_revision:
                raise BindingConflict(
                    f"expected revision {expected_revision}, current revision is {current_revision}"
                )
            stored = ConversationBinding(
                conversation_ref=binding.conversation_ref,
                application_ref=binding.application_ref,
                project_ref=binding.project_ref,
                thread_ref=binding.thread_ref,
                revision=current_revision + 1,
                updated_at=datetime.now(UTC),
            )
            self._bindings[binding.conversation_ref] = stored
            return stored

    async def delete(
        self,
        conversation: ConversationRef,
        expected_revision: int | None = None,
    ) -> None:
        async with self._lock:
            current = self._bindings.get(conversation)
            current_revision = current.revision if current is not None else 0
            if expected_revision is not None and expected_revision != current_revision:
                raise BindingConflict(
                    f"expected revision {expected_revision}, current revision is {current_revision}"
                )
            self._bindings.pop(conversation, None)

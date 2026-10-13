"""Bounded Conversation preparation before recovery or native owner startup."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..interaction.messages import ConversationRef
from ..interaction.operations import require_identifier
from .actions import ActionResult, ConversationActions
from .persistence.state_contracts import ConversationBinding


@dataclass(frozen=True, slots=True, init=False)
class StartupConversationActions:
    """One callback-owned scope with only bridge binding read/clear authority."""

    conversation_ref: ConversationRef
    actor: str
    _actions: ConversationActions = field(repr=False)
    _owner: asyncio.Task[object] = field(repr=False)
    _active: bool = field(repr=False)

    def __new__(cls) -> StartupConversationActions:
        raise TypeError("StartupConversationActions is created by Gateway startup")

    @classmethod
    def _create(cls, actions: ConversationActions) -> StartupConversationActions:
        owner = asyncio.current_task()
        if owner is None:
            raise RuntimeError("Startup preparation requires an owning Task")
        value = object.__new__(cls)
        object.__setattr__(value, "conversation_ref", actions.conversation_ref)
        object.__setattr__(value, "actor", actions.actor)
        object.__setattr__(value, "_actions", actions)
        object.__setattr__(value, "_owner", owner)
        object.__setattr__(value, "_active", True)
        return value

    def _require_active(self) -> None:
        if not self._active or asyncio.current_task() is not self._owner:
            raise RuntimeError("Startup preparation actions are outside their owning invocation")

    def _deactivate(self) -> None:
        object.__setattr__(self, "_active", False)

    async def get_binding(self) -> ConversationBinding | None:
        self._require_active()
        result = await self._actions.get_binding()
        self._require_active()
        return result

    async def clear_thread(
        self, *, action_id: str, expected_generation: int | None = None
    ) -> ActionResult:
        self._require_active()
        result = await self._actions.clear_thread(
            action_id=action_id, expected_generation=expected_generation
        )
        self._require_active()
        return result


@dataclass(frozen=True, slots=True, kw_only=True)
class StartupPreparation:
    """Explicit consumer-owned preparation for one Conversation before recovery."""

    conversation_ref: ConversationRef
    actor: str
    prepare: Callable[[StartupConversationActions], Awaitable[None]] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.conversation_ref) is not ConversationRef:
            raise TypeError("startup preparation requires ConversationRef")
        require_identifier(self.conversation_ref.channel_instance_id, "channel_instance_id")
        require_identifier(self.conversation_ref.native_conversation_id, "native_conversation_id")
        require_identifier(self.actor, "actor")
        if not callable(self.prepare) or not (
            inspect.iscoroutinefunction(self.prepare)
            or inspect.iscoroutinefunction(getattr(self.prepare, "__call__", None))
        ):
            raise TypeError("startup preparation must be an async callable")

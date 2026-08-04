from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..applications.contract import ApplicationRef, ProjectRef, ThreadRef
from ..applications.requests import (
    RequestRef as _RequestRef,
)
from ..applications.requests import (
    RequestResponseShape as _RequestResponseShape,
)
from ..interaction.messages import ConversationRef


class ProjectionPolicy(StrEnum):
    FOREGROUND_ONLY = "foreground_only"
    REMEMBERED_LAST_RECIPIENT = "remembered_last_recipient"
    ALL_OBSERVERS = "all_observers"


class RequestRouteState(StrEnum):
    OPEN = "open"
    RESPONDED = "responded"
    RESOLVED = "resolved"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class RequestRouteCorrelation:
    correlation_id: str
    request_ref: _RequestRef
    thread_ref: ThreadRef
    turn_id: str
    conversation_ref: ConversationRef
    delivery_id: str
    response_shape: _RequestResponseShape
    state: RequestRouteState
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConversationBinding:
    conversation_ref: ConversationRef
    application_ref: ApplicationRef | None = None
    project_ref: ProjectRef | None = None
    thread_ref: ThreadRef | None = None
    revision: int = 0
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ThreadProjectionRoute:
    route_id: str
    thread_ref: ThreadRef
    conversation_ref: ConversationRef
    reply_to_message_id: str | None = None
    checkpoint_agent_item_id: str | None = None
    checkpointed_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TurnReplyCorrelation:
    correlation_id: str
    thread_ref: ThreadRef
    turn_id: str
    client_message_id: str
    conversation_ref: ConversationRef
    reply_to_message_id: str
    created_at: datetime

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..diagnostics import (
    _DIAGNOSTIC_ID_MAX_CHARS,
    ConnectionDiagnosticFacts,
    QueueDiagnosticName,
)

__all__ = ["ChannelDiagnosticFacts", "ChannelDiagnosticsProvider"]


@dataclass(frozen=True, slots=True)
class ChannelDiagnosticFacts:
    """Optional Channel-adapter facts without native Conversation identities."""

    channel_instance_id: str
    kind: str
    connection: ConnectionDiagnosticFacts | None = None

    def __post_init__(self) -> None:
        if (
            type(self.channel_instance_id) is not str
            or not self.channel_instance_id
            or len(self.channel_instance_id) > _DIAGNOSTIC_ID_MAX_CHARS
            or type(self.kind) is not str
            or not self.kind
            or len(self.kind) > _DIAGNOSTIC_ID_MAX_CHARS
        ):
            raise ValueError("Channel diagnostic identity exceeds the fixed bound")
        if self.connection is not None and type(self.connection) is not ConnectionDiagnosticFacts:
            raise TypeError("Channel diagnostics must use the exact connection fact shape")
        if self.connection is not None and any(
            queue.name is not QueueDiagnosticName.CHANNEL_INBOUND
            for queue in self.connection.queues
        ):
            raise ValueError("diagnostic queue is not Channel-scoped")


class ChannelDiagnosticsProvider(Protocol):
    """Optional structural Channel seam; not a required lifecycle capability."""

    def diagnostic_facts(self) -> ChannelDiagnosticFacts: ...

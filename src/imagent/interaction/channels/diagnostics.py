from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..diagnostics import ConnectionDiagnosticFacts, QueueDiagnosticName

__all__ = ["ChannelDiagnosticFacts", "ChannelDiagnosticsProvider"]


@dataclass(frozen=True, slots=True)
class ChannelDiagnosticFacts:
    """Optional Channel-adapter facts without native Conversation identities."""

    channel_instance_id: str
    kind: str
    connection: ConnectionDiagnosticFacts | None = None

    def __post_init__(self) -> None:
        if self.connection is not None and any(
            queue.name is not QueueDiagnosticName.CHANNEL_INBOUND
            for queue in self.connection.queues
        ):
            raise ValueError("diagnostic queue is not Channel-scoped")


class ChannelDiagnosticsProvider(Protocol):
    """Optional structural Channel seam; not a required lifecycle capability."""

    def diagnostic_facts(self) -> ChannelDiagnosticFacts: ...

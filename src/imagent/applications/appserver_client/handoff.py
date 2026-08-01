from __future__ import annotations

from dataclasses import dataclass

APP_SERVER_DISPATCH_POSITION_KEY = "imagent_dispatch_position"


@dataclass(frozen=True, slots=True)
class AppServerDispatchPosition:
    """Epoch-scoped wire order for one admitted non-response callback."""

    connection_epoch: int
    sequence: int

    def __post_init__(self) -> None:
        if self.connection_epoch < 1 or self.sequence < 0:
            raise ValueError("App Server dispatch positions require a live epoch and sequence")

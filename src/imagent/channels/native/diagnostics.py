from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def emit_event(**event: Any) -> None:
    """Keep transferred diagnostics observable without defining telemetry API."""

    logger.debug("native channel event: %s", event)


def mark_channel_health(channel_id: str, **state: Any) -> None:
    """Log native health changes until Issue #13 defines telemetry export."""

    logger.debug("native channel health %s: %s", channel_id, state)

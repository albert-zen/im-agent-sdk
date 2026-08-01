from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def emit_event(**event: Any) -> None:
    """Keep transferred transport diagnostics visible without defining OTel."""

    logger.debug("app-server event: %s", event)


def mark_appserver_health(**state: Any) -> None:
    """Keep logs separate from the stable read-only diagnostic fact surface."""

    logger.debug("app-server health: %s", state)

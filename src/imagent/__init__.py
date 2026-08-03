"""Reference contracts and test kit for IM Agent SDK."""

# ruff: noqa: I001 -- load the Gateway-owned planner before its legacy coordinator consumer.

__version__ = "0.1.0a1"

from . import (
    adapters,
    contracts,
    diagnostics,
    events,
    projections,
    recovery,
)
from .gateway.delivery import planning as delivery_planning
from . import delivery_coordination

__all__ = [
    "adapters",
    "contracts",
    "delivery_coordination",
    "delivery_planning",
    "diagnostics",
    "events",
    "projections",
    "recovery",
]

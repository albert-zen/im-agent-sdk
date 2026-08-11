"""Finite public facade for the canonical v1 Gateway composition."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .actions import (
        ActionResult,
        ActionValue,
        ApplicationActions,
        ConversationActions,
        ReadOutcome,
    )
    from .composition import GatewayExtensions, GatewayLimits
    from .input import MissingBindingError, StaleBindingError
    from .runtime import Gateway

__all__ = [
    "ActionResult",
    "ActionValue",
    "ApplicationActions",
    "ConversationActions",
    "Gateway",
    "GatewayExtensions",
    "GatewayLimits",
    "MissingBindingError",
    "ReadOutcome",
    "StaleBindingError",
]

_ACTION_EXPORTS = frozenset(
    {"ActionResult", "ActionValue", "ApplicationActions", "ConversationActions", "ReadOutcome"}
)
_COMPOSITION_EXPORTS = frozenset({"GatewayExtensions", "GatewayLimits"})
_INPUT_EXPORTS = frozenset({"MissingBindingError", "StaleBindingError"})


def __getattr__(name: str) -> object:
    if name == "Gateway":
        module = import_module(".runtime", __name__)
    elif name in _ACTION_EXPORTS:
        module = import_module(".actions", __name__)
    elif name in _COMPOSITION_EXPORTS:
        module = import_module(".composition", __name__)
    elif name in _INPUT_EXPORTS:
        module = import_module(".input", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value

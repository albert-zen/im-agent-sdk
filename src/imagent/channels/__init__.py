from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..interaction.channels.adapters.runtime import (
        NativeTransportChannelAdapter,
        channel_from_config,
    )

__all__ = [
    "NativeTransportChannelAdapter",
    "channel_from_config",
]

_PUBLIC_EXPORTS = frozenset(__all__)


def __getattr__(name: str) -> object:
    if name not in _PUBLIC_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module("imagent.interaction.channels.adapters.runtime")
    value = getattr(module, name)
    globals()[name] = value
    return value

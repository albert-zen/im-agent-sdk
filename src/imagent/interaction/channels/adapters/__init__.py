"""Concrete native Channel adapters and their approved public facade."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qq import QQChannelAdapter
    from .qq_quote import (
        QQ_QUOTE_ATTACHMENT_LIMIT,
        QQ_QUOTE_CONTENT_LIMIT,
        QQ_QUOTE_FILENAME_LIMIT,
        QQ_QUOTE_MESSAGE_TYPE,
        QQ_QUOTE_REFERENCE_LIMIT,
        QQ_QUOTE_TRANSCRIPT_LIMIT,
    )

__all__ = [
    "QQChannelAdapter",
    "QQ_QUOTE_ATTACHMENT_LIMIT",
    "QQ_QUOTE_CONTENT_LIMIT",
    "QQ_QUOTE_FILENAME_LIMIT",
    "QQ_QUOTE_MESSAGE_TYPE",
    "QQ_QUOTE_REFERENCE_LIMIT",
    "QQ_QUOTE_TRANSCRIPT_LIMIT",
]

_QQ_PUBLIC_EXPORTS = frozenset(__all__)
_QQ_QUOTE_PUBLIC_EXPORTS = _QQ_PUBLIC_EXPORTS - {"QQChannelAdapter"}


def __getattr__(name: str) -> object:
    if name not in _QQ_PUBLIC_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name = (
        "imagent.interaction.channels.adapters.qq_quote"
        if name in _QQ_QUOTE_PUBLIC_EXPORTS
        else "imagent.interaction.channels.adapters.qq"
    )
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value

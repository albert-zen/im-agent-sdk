from __future__ import annotations

import shlex
from dataclasses import dataclass

from .contracts import ChannelMessage, TextContent


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    arguments: tuple[str, ...]
    raw: str


def parse_slash_command(message: ChannelMessage) -> SlashCommand | None:
    """Parse the channel-independent command language at the message seam."""

    text = "\n".join(part.text for part in message.content if isinstance(part, TextContent)).strip()
    if not text.startswith("/"):
        return None
    try:
        tokens = shlex.split(text)
    except ValueError as error:
        raise ValueError(f"Invalid slash command: {error}") from error
    if not tokens:
        return None
    return SlashCommand(
        name=tokens[0][1:].casefold(),
        arguments=tuple(tokens[1:]),
        raw=text,
    )

from __future__ import annotations

import inspect

from ..contracts import AttachmentContent, Content, InboundMessage, TextContent
from .base import InboundContentAdapter


async def adapt_inbound_content(
    adapter: InboundContentAdapter | None,
    message: InboundMessage,
) -> tuple[Content, ...]:
    if adapter is None:
        return message.content
    adapted = adapter(message)
    content = await adapted if inspect.isawaitable(adapted) else adapted
    if not isinstance(content, tuple):
        raise TypeError("Inbound content adapter must return a tuple")
    if not content:
        raise ValueError("Inbound content adapter returned empty content")
    if not all(isinstance(part, (TextContent, AttachmentContent)) for part in content):
        raise TypeError("Inbound content adapter returned an unsupported content item")
    return content

from __future__ import annotations

from .contracts import (
    AgentMessage,
    TextContent,
    ThreadHistory,
    TurnCatchup,
    TurnStatus,
)

_HISTORY_TEXT_LIMIT = 1_200
_CATCHUP_TEXT_LIMIT = 800


def render_turn_catchup(catchup: TurnCatchup) -> str:
    if catchup.turn_id is None:
        return "## Recent Activity\n\n_No Turn is available._"
    lines = [
        "## Recent Activity",
        "",
        f"_Latest Turn · {_human_state(catchup.status)}_",
    ]
    if not catchup.messages:
        lines.extend(["", "_No Agent progress is available for this Turn._"])
    for index, message in enumerate(catchup.messages, start=1):
        text = _message_text(message)
        if not text:
            continue
        lines.extend(
            [
                "",
                f"### {index}",
                "",
                _compact_text(text, _CATCHUP_TEXT_LIMIT),
            ]
        )
    if catchup.status is TurnStatus.RUNNING:
        lines.extend(["", "_Thread is still working._"])
    return "\n".join(lines)


def render_thread_history(history: ThreadHistory) -> str:
    lines = [f"## Thread History · Page {history.page}"]
    if not history.turns:
        lines.extend(["", "_No turns on this page._"])
        return "\n".join(lines)
    count = len(history.turns)
    lines.extend(["", f"_{count} turn{'s' if count != 1 else ''}_"])
    for index, turn in enumerate(history.turns, start=1):
        if index > 1:
            lines.extend(["", "---"])
        lines.extend(
            [
                "",
                f"### {index}. {_human_state(turn.status)} · `{_compact_turn_id(turn.turn_id)}`",
            ]
        )
        user_text = _message_text(turn.user_message)
        if user_text:
            lines.extend(
                [
                    "",
                    "**You**",
                    _blockquote(_compact_text(user_text, _HISTORY_TEXT_LIMIT)),
                ]
            )
        agent_text = _message_text(turn.agent_message)
        if agent_text:
            lines.extend(
                [
                    "",
                    "**Agent**",
                    "",
                    _compact_text(agent_text, _HISTORY_TEXT_LIMIT),
                ]
            )
        if not user_text and not agent_text:
            lines.extend(["", "_No user or Agent message._"])
        if turn.had_compaction:
            lines.extend(["", "_Native context compaction occurred in this turn._"])
        if turn.error:
            lines.extend(
                [
                    "",
                    "**Error**",
                    "",
                    _compact_text(turn.error, _HISTORY_TEXT_LIMIT),
                ]
            )
    if history.has_older:
        lines.extend(
            [
                "",
                "---",
                "",
                f"Older turns: `/history {count} --page {history.page + 1}`",
            ]
        )
    return "\n".join(lines)


def _message_text(message: AgentMessage | None) -> str:
    if message is None:
        return ""
    return "\n".join(part.text for part in message.content if isinstance(part, TextContent)).strip()


def _human_state(status: TurnStatus) -> str:
    return {
        TurnStatus.IDLE: "Idle",
        TurnStatus.RUNNING: "Working",
        TurnStatus.COMPLETED: "Completed",
        TurnStatus.FAILED: "Failed",
        TurnStatus.INTERRUPTED: "Interrupted",
        TurnStatus.UNKNOWN: "Unknown",
    }[status]


def _compact_turn_id(turn_id: str) -> str:
    return (turn_id[:10] or "turn").replace("`", "")


def _compact_text(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    truncated = text[: max(0, limit - 2)].rstrip()
    open_fence = _open_markdown_fence(truncated)
    if open_fence:
        truncated = f"{truncated}\n{open_fence}"
    return f"{truncated}\n…"


def _open_markdown_fence(value: str) -> str | None:
    open_fence: str | None = None
    for line in value.splitlines():
        candidate = line.lstrip()
        if len(line) - len(candidate) > 3 or not candidate:
            continue
        character = candidate[0]
        if character not in {"`", "~"}:
            continue
        length = len(candidate) - len(candidate.lstrip(character))
        if length < 3:
            continue
        remainder = candidate[length:]
        if open_fence is None:
            open_fence = character * length
        elif character == open_fence[0] and length >= len(open_fence) and not remainder.strip():
            open_fence = None
    return open_fence


def _blockquote(value: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in value.splitlines())

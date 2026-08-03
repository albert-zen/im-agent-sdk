from __future__ import annotations

import html
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ...contracts import (
    ApprovalRequest,
    InteractiveRequest,
    UserInputQuestion,
    UserInputRequest,
)
from ..messages import ConversationRef, OutboundMessage, TextContent, TextFormat


@dataclass(frozen=True, slots=True)
class RequestPresentation:
    message: OutboundMessage
    response_supported: bool


class RequestPresenter(Protocol):
    def present_request(
        self,
        request: InteractiveRequest,
        *,
        conversation_ref: ConversationRef,
        delivery_id: str,
        reply_to_message_id: str | None,
    ) -> RequestPresentation:
        """Render one typed request for one already-selected destination."""
        ...


class MarkdownRequestPresenter:
    """Plain portable request presentation without approval policy."""

    def present_request(
        self,
        request: InteractiveRequest,
        *,
        conversation_ref: ConversationRef,
        delivery_id: str,
        reply_to_message_id: str | None,
    ) -> RequestPresentation:
        if isinstance(request, ApprovalRequest):
            body = self._approval(request)
            response_supported = True
        else:
            body, response_supported = self._user_input(request)
        return RequestPresentation(
            message=OutboundMessage(
                delivery_id=delivery_id,
                conversation_ref=conversation_ref,
                content=(TextContent(body, TextFormat.MARKDOWN),),
                created_at=datetime.now(UTC),
                reply_to=reply_to_message_id,
            ),
            response_supported=response_supported,
        )

    def _approval(self, request: ApprovalRequest) -> str:
        lines = [
            "## Approval requested",
            "",
            _indented_code_block(request.prompt),
            "",
            "### Choices",
            "",
        ]
        for choice in request.choices:
            label = _escape_markdown_text(choice.label)
            suffix = f" — {_escape_markdown_text(choice.description)}" if choice.description else ""
            lines.append(f"- **{label}** (`{_inline_code(choice.choice_id)}`){suffix}")
        command = " ".join(
            (
                "/respond",
                _command_arg(request.request_ref.application_ref.application_instance_id),
                _command_arg(request.request_ref.native_request_id),
                "<choice-id>",
            )
        )
        lines.extend(["", f"Respond with `{_inline_code(command)}`."])
        return "\n".join(lines)

    def _user_input(self, request: UserInputRequest) -> tuple[str, bool]:
        if any(question.secret for question in request.questions):
            return (
                "\n".join(
                    (
                        "## Secure input required",
                        "",
                        "This IM destination cannot collect the requested secret safely.",
                        "Respond in the Agent application or another explicitly secure client.",
                    )
                ),
                False,
            )
        lines = ["## Input requested"]
        if request.prompt:
            lines.extend(["", _escape_markdown_text(request.prompt)])
        for index, question in enumerate(request.questions, start=1):
            title = question.header or question.prompt
            lines.extend(
                [
                    "",
                    f"### {index}. {_escape_markdown_text(title)}",
                ]
            )
            if question.header:
                lines.extend(["", _escape_markdown_text(question.prompt)])
            lines.extend(["", _cardinality(question)])
            for choice in question.choices:
                label = _escape_markdown_text(choice.label)
                suffix = (
                    f" — {_escape_markdown_text(choice.description)}" if choice.description else ""
                )
                lines.append(f"- **{label}** (`{_inline_code(choice.choice_id)}`){suffix}")
            if question.allows_other:
                lines.append("- A free-text answer is also accepted.")
        command = " ".join(
            (
                "/answer",
                _command_arg(request.request_ref.application_ref.application_instance_id),
                _command_arg(request.request_ref.native_request_id),
                "<question-id>=<answer>",
            )
        )
        lines.extend(
            [
                "",
                f"Respond with `{_inline_code(command)}`.",
                "Repeat the same question ID only when that question allows multiple answers.",
            ]
        )
        return "\n".join(lines), True


def _cardinality(question: UserInputQuestion) -> str:
    if question.min_answers == question.max_answers == 1:
        return "_Choose exactly one answer._"
    if question.min_answers == question.max_answers:
        return f"_Provide exactly {question.min_answers} answers._"
    return f"_Provide {question.min_answers} to {question.max_answers} answers._"


def _command_arg(value: str) -> str:
    return shlex.quote(value)


def _inline_code(value: str) -> str:
    return value.replace("`", "ˋ")


def _indented_code_block(value: str) -> str:
    return "\n".join(f"    {line}" if line else "    " for line in value.splitlines())


def _escape_markdown_text(value: str) -> str:
    single_line = " ".join(value.splitlines())
    escaped_html = html.escape(single_line, quote=False)
    return re.sub(r"([\\`*_[\]{}()#+.!|>~-])", r"\\\1", escaped_html)

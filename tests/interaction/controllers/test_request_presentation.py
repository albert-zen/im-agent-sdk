from __future__ import annotations

import shlex
import unittest
from datetime import UTC, datetime
from importlib.util import find_spec

from imagent import controllers as controllers_facade
from imagent.contracts import (
    ApplicationRef,
    ConversationRef,
    InboundMessage,
    RequestChoice,
    RequestRef,
    TextContent,
    ThreadRef,
    UserInputQuestion,
    UserInputRequest,
)
from imagent.controllers.slash import parse_slash_command
from imagent.interaction.controllers import (
    MarkdownRequestPresenter,
    RequestPresentation,
    RequestPresenter,
)


class RequestPresentationOwnershipTests(unittest.TestCase):
    def test_controller_facade_reexports_exact_owner_objects(self) -> None:
        self.assertIs(
            controllers_facade.MarkdownRequestPresenter,
            MarkdownRequestPresenter,
        )
        self.assertIs(controllers_facade.RequestPresentation, RequestPresentation)
        self.assertIs(controllers_facade.RequestPresenter, RequestPresenter)

    def test_historical_implementation_modules_are_removed(self) -> None:
        self.assertIsNone(find_spec("imagent.controllers.base"))
        self.assertIsNone(find_spec("imagent.controllers.requests"))


class MarkdownRequestPresenterTests(unittest.TestCase):
    def test_secret_question_is_explicitly_not_answerable_over_plain_text(
        self,
    ) -> None:
        presentation = MarkdownRequestPresenter().present_request(
            UserInputRequest(
                request_ref=RequestRef(
                    ApplicationRef("codex-local"),
                    "epoch-1:request-7",
                ),
                thread_ref=ThreadRef("codex-local", "thread-1"),
                turn_id="turn-1",
                questions=(
                    UserInputQuestion(
                        question_id="token",
                        prompt="Enter the API token",
                        allows_other=True,
                        secret=True,
                    ),
                ),
            ),
            conversation_ref=ConversationRef(
                "fake-channel",
                "conversation-1",
            ),
            delivery_id="request-delivery-1",
            reply_to_message_id=None,
        )
        self.assertFalse(presentation.response_supported)
        text = presentation.message.content[0]
        self.assertIsInstance(text, TextContent)
        assert isinstance(text, TextContent)
        self.assertIn("cannot collect", text.text)
        self.assertNotIn("/answer", text.text)

    def test_rendered_request_identity_round_trips_through_slash_parser(
        self,
    ) -> None:
        application_id = "Codex 本地 'alpha'"
        native_request_id = 'request "七" with spaces'
        presentation = MarkdownRequestPresenter().present_request(
            UserInputRequest(
                request_ref=RequestRef(
                    ApplicationRef(application_id),
                    native_request_id,
                ),
                thread_ref=ThreadRef(application_id, "thread-1"),
                turn_id="turn-1",
                questions=(
                    UserInputQuestion(
                        question_id="deployment target",
                        prompt="Choose a target",
                        choices=(RequestChoice("东京 staging", "Tokyo"),),
                    ),
                ),
            ),
            conversation_ref=ConversationRef(
                "fake-channel",
                "conversation-1",
            ),
            delivery_id="request-delivery-1",
            reply_to_message_id=None,
        )
        text = presentation.message.content[0]
        assert isinstance(text, TextContent)
        rendered = next(
            line.removeprefix("Respond with `").removesuffix("`.")
            for line in text.text.splitlines()
            if line.startswith("Respond with `")
        )
        prefix = shlex.split(rendered)
        self.assertEqual(prefix[:3], ["/answer", application_id, native_request_id])

        answer_argument = 'deployment target=东京 "blue"'
        command = " ".join(
            (
                rendered.rsplit(" ", 1)[0],
                shlex.quote(answer_argument),
            )
        )
        parsed = parse_slash_command(_message(command))
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(
            parsed.arguments,
            (application_id, native_request_id, answer_argument),
        )


def _message(text: str) -> InboundMessage:
    return InboundMessage(
        message_id="message-1",
        conversation_ref=ConversationRef("fake-channel", "conversation-1"),
        sender="user-1",
        content=(TextContent(text),),
        created_at=datetime.now(UTC),
    )


if __name__ == "__main__":
    unittest.main()

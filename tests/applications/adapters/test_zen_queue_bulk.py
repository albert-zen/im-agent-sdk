from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any

from imagent.applications.adapters.zen_queue import (
    _queued_input_fingerprint,
    _queued_receipts_from_items,
)
from imagent.applications.contract import ProjectRef, QueuedInputState, ThreadRef, TurnRef
from imagent.interaction.operations import ContractViolation


def _foreign_native_queued_items() -> list[dict[str, Any]]:
    """Canonical native snapshot shapes, not SDK or native-ingress fixtures."""
    image = {
        "type": "image",
        "attachment": {
            "type": "attachment",
            "sha256": "a" * 64,
            "mediaType": "image/png",
            "byteLength": 68,
            "width": 1,
            "height": 1,
        },
    }
    inputs = {
        "batch": [{"type": "text", "text": "native batch text"}],
        "skill": [
            {
                "type": "text",
                "text": "Host-loaded instruction",
                "skillSource": {
                    "kind": "instruction",
                    "id": "native-skill",
                    "path": "/native/SKILL.md",
                    "sha256": "b" * 64,
                },
            }
        ],
        "audio": [
            {
                "type": "audio",
                "attachment": {
                    "type": "attachment",
                    "sha256": "c" * 64,
                    "mediaType": "audio/wav",
                    "byteLength": 44,
                },
            }
        ],
        # Native canonical-only images retain their own envelope limits.
        "images": [deepcopy(image) for _ in range(17)],
    }
    return [
        {
            "id": f"foreign-queued-{name}",
            "threadId": "thread",
            "type": "user_message_queued",
            "clientId": f"foreign-{name}",
            "input": parts,
            **({"deliveryMode": "batch-next"} if name == "batch" else {}),
        }
        for name, parts in inputs.items()
    ]


class ZenQueueBulkMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thread = ThreadRef(ProjectRef("zen", "workspace"), "thread")

    def queued(self, client_id: str) -> dict[str, object]:
        return {
            "id": f"queued-{client_id}",
            "threadId": self.thread.thread_id,
            "type": "user_message_queued",
            "clientId": client_id,
            "input": [{"type": "text", "text": f"original {client_id}"}],
        }

    def test_mixed_receipts_preserve_requested_order_and_duplicate_positions(self) -> None:
        items = [self.queued(client_id) for client_id in ("pending", "admitted", "cancelled")]
        items.extend(
            [
                {
                    "id": "human-admitted",
                    "threadId": "thread",
                    "type": "user_message",
                    "clientId": "admitted",
                    "turnId": "original-turn",
                },
                {
                    "id": "cancellation",
                    "threadId": "thread",
                    "type": "user_message_queue_cancelled",
                    "clientId": "cancelled",
                    "queuedItemId": "queued-cancelled",
                },
            ]
        )
        receipts = _queued_receipts_from_items(
            items, self.thread, ("missing", "cancelled", "pending", "admitted", "pending")
        )
        self.assertEqual(len(receipts), 5)
        self.assertIsNone(receipts[0])
        cancelled, pending, admitted = receipts[1:4]
        assert cancelled is not None and pending is not None and admitted is not None
        self.assertIs(cancelled.state, QueuedInputState.CANCELLED)
        self.assertIs(pending.state, QueuedInputState.QUEUED)
        self.assertIs(admitted.state, QueuedInputState.ADMITTED)
        self.assertEqual(admitted.turn_ref, TurnRef(self.thread, "original-turn"))
        self.assertEqual(receipts[2], receipts[4])

    def test_edit_and_execution_input_never_replace_original_fingerprint(self):
        queued = self.queued("input")
        changed = {
            "id": "edited",
            "threadId": "thread",
            "type": "user_message_queue_edited",
            "clientId": "input",
            "queuedItemId": "queued-input",
            "input": [{"type": "text", "text": "edited"}],
        }
        human = {**changed, "id": "human", "type": "user_message", "turnId": "turn"}
        receipt = _queued_receipts_from_items([queued, changed, human], self.thread, ("input",))[0]
        assert receipt is not None
        self.assertEqual(receipt.input_fingerprint, _queued_input_fingerprint(queued["input"]))
        self.assertIs(receipt.state, QueuedInputState.ADMITTED)

    def test_unrequested_item_scope_and_duplicate_identity_are_still_validated(self):
        for items in (
            [{**self.queued("other"), "threadId": "foreign"}],
            [self.queued("other"), self.queued("other")],
        ):
            with self.assertRaises(ContractViolation):
                _queued_receipts_from_items(items, self.thread, ("missing",))
            with self.assertRaises(ContractViolation):
                _queued_receipts_from_items(items, self.thread, ())

    def test_each_requested_identity_has_independent_absence_and_lineage(self):
        invalid = {
            "id": "human",
            "threadId": "thread",
            "type": "user_message",
            "clientId": "bad",
            "turnId": "turn",
        }
        self.assertIsNone(_queued_receipts_from_items([invalid], self.thread, ("missing",))[0])
        with self.assertRaises(ContractViolation):
            _queued_receipts_from_items([invalid], self.thread, ("missing", "bad"))

    def test_foreign_native_modes_and_payloads_do_not_change_owned_receipts(self):
        own = self.queued("owned")
        expected = _queued_receipts_from_items([own], self.thread, ("owned", "missing"))
        self.assertEqual(
            _queued_receipts_from_items(
                [*_foreign_native_queued_items(), own], self.thread, ("owned", "missing")
            ),
            expected,
        )

    def test_native_only_mode_and_payload_fail_when_exact_identity_is_requested(self):
        for foreign in _foreign_native_queued_items():
            with self.subTest(client=foreign["clientId"]), self.assertRaises(ContractViolation):
                _queued_receipts_from_items([foreign], self.thread, (foreign["clientId"],))

    def test_mixed_history_same_client_collision_cannot_replace_owned_queue(self):
        own = self.queued("owned")
        for foreign in _foreign_native_queued_items():
            with self.subTest(client=foreign["clientId"]), self.assertRaises(ContractViolation):
                _queued_receipts_from_items(
                    [own, {**foreign, "clientId": "owned"}], self.thread, ("owned",)
                )

    def test_mixed_history_edits_and_terminal_content_keep_original_fingerprint(self):
        own = self.queued("owned")
        expected = _queued_input_fingerprint(own["input"])
        for foreign in _foreign_native_queued_items():
            edited = {
                "id": "own-edited",
                "threadId": "thread",
                "type": "user_message_queue_edited",
                "clientId": "owned",
                "queuedItemId": "queued-owned",
                "input": foreign["input"],
            }
            for terminal in (
                None,
                {**edited, "id": "own-cancelled", "type": "user_message_queue_cancelled"},
                {**edited, "id": "own-human", "type": "user_message", "turnId": "own-turn"},
            ):
                with self.subTest(client=foreign["clientId"], terminal=terminal):
                    items = [foreign, own, edited]
                    if terminal is not None:
                        items.append(terminal)
                    receipt = _queued_receipts_from_items(items, self.thread, ("owned",))[0]
                    assert receipt is not None
                    self.assertEqual(receipt.input_fingerprint, expected)

    def test_foreign_history_never_promotes_host_or_agent_source_to_human(self):
        for kind in (
            "host_message",
            "host_message_queued",
            "thread_message",
            "thread_message_queued",
        ):
            foreign = _foreign_native_queued_items()
            source = {**self.queued("owned"), "type": kind}
            with self.subTest(kind=kind):
                self.assertIsNone(
                    _queued_receipts_from_items([*foreign, source], self.thread, ("owned",))[0]
                )
                own = self.queued("owned")
                source["id"] = "source-item"
                receipt = _queued_receipts_from_items(
                    [*foreign, own, source], self.thread, ("owned",)
                )[0]
                assert receipt is not None
                self.assertIs(receipt.state, QueuedInputState.QUEUED)


if __name__ == "__main__":
    unittest.main()

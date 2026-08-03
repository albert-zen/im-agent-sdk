from __future__ import annotations

import unittest

from imagent.interaction.channels.ingress import ChannelAccessPolicy, parse_id_set


class ChannelAccessPolicyTests(unittest.TestCase):
    def test_configuration_parses_id_dimensions_and_match_mode(self) -> None:
        policy = ChannelAccessPolicy.from_config(
            {
                "allowed_user_ids": " user-1, user-2\nuser-1 ",
                "allowed_conversation_ids": ["chat-1", " chat-2 "],
                "access_match": " ALL ",
            }
        )

        self.assertEqual(policy.allowed_user_ids, frozenset({"user-1", "user-2"}))
        self.assertEqual(
            policy.allowed_conversation_ids,
            frozenset({"chat-1", "chat-2"}),
        )
        self.assertEqual(policy.access_match, "all")
        self.assertEqual(parse_id_set(None), frozenset())

    def test_empty_and_unrestricted_dimensions_allow_platform_scope(self) -> None:
        for policy in (
            ChannelAccessPolicy.allow_all(),
            ChannelAccessPolicy(allowed_user_ids=frozenset({"*"})),
            ChannelAccessPolicy(allowed_conversation_ids=frozenset({"*"})),
        ):
            with self.subTest(policy=policy):
                self.assertEqual(policy.mode, "platform")
                self.assertTrue(policy.allows(user_id="user-1", conversation_id="chat-1"))

    def test_deny_all_rejects_every_identity(self) -> None:
        policy = ChannelAccessPolicy(allowed_user_ids=frozenset({"none"}))

        self.assertTrue(policy.denies_all)
        self.assertEqual(policy.mode, "deny_all")
        self.assertFalse(policy.allows(user_id="user-1", conversation_id="chat-1"))

    def test_any_and_all_apply_only_configured_dimensions(self) -> None:
        any_match = ChannelAccessPolicy(
            allowed_user_ids=frozenset({"user-1"}),
            allowed_conversation_ids=frozenset({"chat-1"}),
        )
        all_match = ChannelAccessPolicy(
            allowed_user_ids=frozenset({"user-1"}),
            allowed_conversation_ids=frozenset({"chat-1"}),
            access_match="all",
        )

        self.assertTrue(any_match.allows(user_id="user-2", conversation_id="chat-1"))
        self.assertFalse(any_match.allows(user_id="user-2", conversation_id="chat-2"))
        self.assertTrue(all_match.allows(user_id="user-1", conversation_id="chat-1"))
        self.assertFalse(all_match.allows(user_id="user-2", conversation_id="chat-1"))

    def test_invalid_mode_and_mixed_deny_all_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "access_match"):
            ChannelAccessPolicy.from_config({"access_match": "sometimes"})
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            ChannelAccessPolicy(
                allowed_user_ids=frozenset({"none", "user-1"}),
            )


if __name__ == "__main__":
    unittest.main()

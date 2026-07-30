from __future__ import annotations

import unittest

from imagent.applications import imcodex_app_server_client


class ImcodexAppServerClientTests(unittest.TestCase):
    def test_proven_codex_client_can_be_composed_for_stdio_or_remote(self) -> None:
        local = imcodex_app_server_client(codex_bin="codex")
        remote = imcodex_app_server_client(
            endpoint="ws://127.0.0.1:4500/ws",
            auth_token="test-token",
        )

        self.assertEqual(type(local).__name__, "AppServerClient")
        self.assertEqual(type(remote).__name__, "AppServerClient")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest

from imagent import __version__
from imagent.applications import codex_app_server_client
from imagent.applications.appserver_client.client import (
    AppServerClient,
    StdioAppServerTransport,
)
from imagent.applications.appserver_client.retry import RetryBackoff
from imagent.applications.appserver_client.supervisor import AppServerSupervisor
from imagent.applications.appserver_client.target import (
    AppServerTargetConfigError,
    parse_app_server_target,
    resolve_app_server_target,
)


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None


class _FakeStdout:
    def __init__(self, *lines: bytes) -> None:
        self.lines = list(lines)

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class _FakeProcess:
    def __init__(self, *lines: bytes) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(*lines)


class AppServerClientTests(unittest.IsolatedAsyncioTestCase):
    def test_stdio_composition_does_not_load_websocket_transport(self) -> None:
        client = codex_app_server_client(endpoint="stdio://")

        self.assertEqual(type(client).__name__, "AppServerClient")
        self.assertEqual(client._client_info["version"], __version__)

    def test_sdk_owned_client_can_be_composed_for_stdio_or_remote(self) -> None:
        local = codex_app_server_client(codex_bin="codex", endpoint="stdio://")
        remote = codex_app_server_client(
            endpoint="ws://127.0.0.1:4500/ws",
            auth_token="test-token",
        )

        self.assertEqual(type(local).__name__, "AppServerClient")
        self.assertEqual(type(remote).__name__, "AppServerClient")

    def test_targets_are_explicit_about_transport_and_ownership(self) -> None:
        cases = {
            "stdio://": ("bridge-child", "stdio-jsonl"),
            "unix:///tmp/codex.sock": ("external", "unix-websocket"),
            "ws://127.0.0.1:8765": ("external", "tcp-websocket"),
            "wss://codex.example.test/rpc": ("external", "tcp-websocket"),
        }
        for endpoint, expected in cases.items():
            with self.subTest(endpoint=endpoint):
                target = parse_app_server_target(endpoint)
                self.assertEqual((target.ownership, target.transport), expected)
                self.assertEqual(target.preserves_server_state, target.ownership == "external")

        self.assertEqual(resolve_app_server_target(os_name="posix").endpoint, "unix://")
        self.assertEqual(
            resolve_app_server_target(os_name="nt").endpoint,
            "ws://127.0.0.1:8765",
        )
        for invalid in (
            "http://127.0.0.1:8765",
            "wss://user:secret@example.test/rpc",
            "wss://example.test/rpc?token=secret",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(AppServerTargetConfigError):
                    parse_app_server_target(invalid)

    async def test_stdio_transport_uses_jsonl_frames(self) -> None:
        process = _FakeProcess(b'{"id":1,"result":{"threads":[]}}\n')
        transport = StdioAppServerTransport(process)

        await transport.send_json({"id": 1, "method": "thread/list"})
        response = await transport.receive_json()

        self.assertEqual(
            process.stdin.writes,
            [(json.dumps({"id": 1, "method": "thread/list"}) + "\n").encode()],
        )
        self.assertEqual(response, {"id": 1, "result": {"threads": []}})

    async def test_local_paths_require_transport_or_verified_shared_filesystem(self) -> None:
        remote = AppServerClient(
            supervisor=AppServerSupervisor(
                app_server_url="wss://codex.example.test/rpc",
            ),
            client_info={"name": "test", "title": "Test", "version": "0"},
            shared_filesystem_verifier=lambda: False,
        )
        verified = AppServerClient(
            supervisor=AppServerSupervisor(
                app_server_url="ws://127.0.0.1:8765",
            ),
            client_info={"name": "test", "title": "Test", "version": "0"},
            shared_filesystem_verifier=lambda: True,
        )

        await remote._refresh_verified_shared_filesystem()
        await verified._refresh_verified_shared_filesystem()

        self.assertFalse(remote.supports_local_image_paths())
        self.assertTrue(verified.supports_local_image_paths())
        self.assertTrue(codex_app_server_client(endpoint="stdio://").supports_local_image_paths())

    def test_turn_input_and_retry_policy_are_bounded(self) -> None:
        self.assertEqual(
            AppServerClient._resolve_turn_input(text="hello", input_items=None),
            [{"type": "text", "text": "hello"}],
        )
        with self.assertRaises(ValueError):
            AppServerClient._resolve_turn_input(
                text="hello",
                input_items=[{"type": "text", "text": "world"}],
            )

        retry = RetryBackoff(initial_delay_s=0.5, max_delay_s=30.0, jitter_fraction=0.25)
        self.assertEqual(
            retry.delay_after_failure(
                10_000,
                random_float=lambda: 0.0,
                downward_jitter=True,
            ),
            30.0,
        )
        self.assertEqual(
            retry.delay_after_failure(
                10_000,
                random_float=lambda: 1.0,
                downward_jitter=True,
            ),
            22.5,
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import unittest

import imagent.applications as applications_facade
import imagent.applications.adapters.appserver.client as client_facade
from imagent import __version__
from imagent.applications import codex_app_server_client
from imagent.applications.adapters.appserver.client.client import AppServerClient
from imagent.applications.adapters.appserver.client.handoff import (
    APP_SERVER_DISPATCH_POSITION_KEY,
    AppServerDispatchPosition,
    AppServerResponse,
)
from imagent.applications.adapters.appserver.client.retry import RetryBackoff
from imagent.applications.adapters.appserver.client.supervisor import AppServerSupervisor
from imagent.applications.adapters.appserver.client.target import (
    AppServerTargetConfigError,
    parse_app_server_target,
    resolve_app_server_target,
)
from imagent.applications.adapters.appserver.transport import AppServerError


class AppServerClientFacadeTests(unittest.TestCase):
    def test_target_facade_has_exact_public_names_and_owner_identity(self) -> None:
        names = [
            "AppServerClient",
            "AppServerDispatchPosition",
            "AppServerError",
            "AppServerResponse",
            "AppServerSupervisor",
            "APP_SERVER_DISPATCH_POSITION_KEY",
            "codex_app_server_client",
        ]
        self.assertEqual(client_facade.__all__, names)
        owners = {
            "AppServerClient": AppServerClient,
            "AppServerDispatchPosition": AppServerDispatchPosition,
            "AppServerError": AppServerError,
            "AppServerResponse": AppServerResponse,
            "AppServerSupervisor": AppServerSupervisor,
            "APP_SERVER_DISPATCH_POSITION_KEY": APP_SERVER_DISPATCH_POSITION_KEY,
            "codex_app_server_client": codex_app_server_client,
        }
        for name, owner in owners.items():
            with self.subTest(name=name):
                self.assertIs(getattr(client_facade, name), owner)
        self.assertIs(
            applications_facade.codex_app_server_client,
            client_facade.codex_app_server_client,
        )
        self.assertIsNone(importlib.util.find_spec("imagent.applications.appserver_client"))


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

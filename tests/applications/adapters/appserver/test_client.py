from __future__ import annotations

import asyncio
import importlib.util
import os
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import imagent.applications as applications_facade
import imagent.applications.adapters.appserver.client as client_facade
from imagent import __version__
from imagent.applications.adapters.appserver._errors import AppServerError
from imagent.applications.adapters.appserver.client import codex_app_server_client
from imagent.applications.adapters.appserver.client._client import AppServerClient
from imagent.applications.adapters.appserver.client._handoff import (
    APP_SERVER_DISPATCH_POSITION_KEY,
    AppServerDispatchPosition,
    AppServerResponse,
)
from imagent.applications.adapters.appserver.client._supervisor import AppServerSupervisor
from imagent.applications.adapters.appserver.client.retry import RetryBackoff
from imagent.applications.adapters.appserver.client.target import (
    AppServerTargetConfigError,
    parse_app_server_target,
    resolve_app_server_target,
)


class _BlockingWebSocket:
    def __init__(self) -> None:
        self.closed = False

    async def send(self, _data: str) -> None:
        return None

    async def recv(self) -> str:
        await asyncio.Future()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


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
        self.assertNotIn("codex_app_server_client", applications_facade.__all__)
        self.assertFalse(hasattr(applications_facade, "codex_app_server_client"))
        self.assertIsNone(importlib.util.find_spec("imagent.applications.appserver_client"))


class AppServerClientTests(unittest.IsolatedAsyncioTestCase):
    def test_inbound_frame_limit_flows_through_public_factory(self) -> None:
        default_client = codex_app_server_client(endpoint="stdio://")
        configured_client = codex_app_server_client(
            endpoint="stdio://",
            max_inbound_frame_bytes=123,
        )

        self.assertEqual(default_client._max_inbound_frame_bytes, 64 * 1024 * 1024)
        self.assertEqual(configured_client._max_inbound_frame_bytes, 123)
        for invalid in (0, -1, True, False, 1.0, "1", None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "must be a positive integer"):
                    codex_app_server_client(
                        endpoint="stdio://",
                        max_inbound_frame_bytes=cast(int, invalid),
                    )

    async def test_default_tcp_websocket_receives_configured_protocol_limit(self) -> None:
        connection = object()
        connect = AsyncMock(return_value=connection)
        supervisor = AppServerSupervisor(app_server_url="ws://127.0.0.1:8765")

        with patch(
            "imagent.applications.adapters.appserver.client._supervisor._websocket_module",
            return_value=SimpleNamespace(connect=connect),
        ):
            actual = await supervisor.connect_external(max_inbound_frame_bytes=123)

        self.assertIs(actual, connection)
        connect.assert_awaited_once_with(
            "ws://127.0.0.1:8765",
            max_size=123,
            open_timeout=3.0,
        )

    @unittest.skipIf(os.name == "nt", "Unix-domain WebSockets are unsupported on Windows")
    async def test_default_unix_websocket_receives_configured_protocol_limit(self) -> None:
        connection = object()
        unix_connect = AsyncMock(return_value=connection)
        supervisor = AppServerSupervisor(app_server_url="unix:///tmp/im-agent-sdk-test.sock")

        with patch(
            "imagent.applications.adapters.appserver.client._supervisor._websocket_module",
            return_value=SimpleNamespace(unix_connect=unix_connect),
        ):
            actual = await supervisor.connect_external(max_inbound_frame_bytes=456)

        self.assertIs(actual, connection)
        unix_connect.assert_awaited_once_with(
            "/tmp/im-agent-sdk-test.sock",
            uri="ws://localhost/",
            compression=None,
            max_size=456,
            open_timeout=3.0,
        )

    async def test_supplied_websocket_is_rechecked_by_transport_wrapper(self) -> None:
        websocket = _BlockingWebSocket()
        factory_calls: list[tuple[str, dict[str, Any]]] = []

        async def websocket_factory(url: str, **kwargs: Any) -> _BlockingWebSocket:
            factory_calls.append((url, kwargs))
            return websocket

        client = AppServerClient(
            supervisor=AppServerSupervisor(
                app_server_url="ws://127.0.0.1:8765",
                websocket_factory=websocket_factory,
            ),
            client_info={"name": "test", "title": "Test", "version": "0"},
            max_inbound_frame_bytes=789,
        )
        try:
            await client.connect()

            self.assertEqual(factory_calls, [("ws://127.0.0.1:8765", {})])
            transport = cast(Any, client._transport)
            self.assertIsNotNone(transport)
            self.assertEqual(transport._max_inbound_frame_bytes, 789)
        finally:
            await client.close()
        self.assertTrue(websocket.closed)

    async def test_supervisor_rejects_invalid_frame_limit_before_connecting(self) -> None:
        supervisor = AppServerSupervisor(app_server_url="stdio://")

        for invalid in (0, True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "must be a positive integer"):
                    await supervisor.connect_external(max_inbound_frame_bytes=cast(int, invalid))

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

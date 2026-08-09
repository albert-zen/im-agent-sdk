from __future__ import annotations

import ast
import base64
import hashlib
import importlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from imagent.interaction.client_tools import send

ROOT = Path(__file__).resolve().parents[3]


class _Response:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class SendClientToolTests(unittest.TestCase):
    def _thread_args(self, *extra: str) -> list[str]:
        return [
            "--endpoint",
            "http://127.0.0.1:8080/deliver",
            "--credential-stdin",
            "--delivery-id",
            "delivery-cli",
            "--application",
            "application",
            "--project",
            "project-1",
            "--thread",
            "thread-1",
            *extra,
        ]

    def _invoke(
        self,
        arguments: list[str],
        *,
        response: _Response | None = None,
        post_error: Exception | None = None,
        stdin: str = "scoped-token\n",
    ) -> tuple[int, str, str, MagicMock]:
        client_factory = MagicMock()
        client = client_factory.return_value.__enter__.return_value
        if post_error is not None:
            client.post.side_effect = post_error
        else:
            client.post.return_value = response or _Response(
                200,
                {"deliveryId": "delivery-cli", "state": "accepted"},
            )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(send.httpx, "Client", client_factory),
            patch.object(sys, "stdin", io.StringIO(stdin)),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = send.main(arguments)
        return exit_code, stdout.getvalue(), stderr.getvalue(), client_factory

    def test_canonical_owner_entrypoint_and_historical_package_absence(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            metadata["project"]["scripts"]["imagent-send"],
            "imagent.interaction.client_tools.send:main",
        )
        self.assertEqual(send.main.__module__, "imagent.interaction.client_tools.send")
        self.assertFalse((ROOT / "src" / "imagent" / "cli").exists())
        self.assertIsNone(importlib.util.find_spec("imagent.cli"))
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("imagent.cli")

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        code = """
import importlib.util
import sys

from imagent.interaction.client_tools.send import main

assert main.__module__ == "imagent.interaction.client_tools.send"
assert importlib.util.find_spec("imagent.cli") is None
assert not any(
    name == "imagent.gateway" or name.startswith("imagent.gateway.")
    for name in sys.modules
)
assert not any(
    name == "imagent.applications" or name.startswith("imagent.applications.")
    for name in sys.modules
)
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_source_has_no_gateway_or_application_implementation_import(self) -> None:
        source = Path(send.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)
        self.assertFalse(
            any(
                name == "imagent.gateway"
                or name.startswith("imagent.gateway.")
                or name == "imagent.applications"
                or name.startswith("imagent.applications.")
                for name in imported_modules
            )
        )
        self.assertNotIn("ProactiveDeliveryJsonHandler", source)

    def test_posts_text_and_inline_artifact_to_loopback_with_exact_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential_path = root / "credential"
            credential_path.write_text("scoped-token\n", encoding="utf-8")
            artifact_path = root / "result.txt"
            artifact_path.write_bytes(b"result")
            arguments = [
                "--endpoint",
                "http://127.0.0.1:8080/deliver",
                "--credential-file",
                str(credential_path),
                "--delivery-id",
                "delivery-cli",
                "--application",
                "application",
                "--thread",
                "thread-1",
                "--project",
                "project-1",
                "--route",
                "route-1",
                "--text",
                "done",
                "--artifact",
                str(artifact_path),
                "--timeout",
                "17.5",
            ]
            exit_code, stdout, stderr, client_factory = self._invoke(arguments)

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout)["state"], "accepted")
        self.assertEqual(stderr, "")
        client_factory.assert_called_once_with(trust_env=False)
        client = client_factory.return_value.__enter__.return_value
        client.post.assert_called_once()
        endpoint = client.post.call_args.args[0]
        request = client.post.call_args.kwargs
        self.assertEqual(endpoint, "http://127.0.0.1:8080/deliver")
        self.assertEqual(request["headers"]["Authorization"], "Bearer scoped-token")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertEqual(request["timeout"], 17.5)
        self.assertEqual(
            request["json"]["target"],
            {
                "kind": "threadRoutes",
                "threadRef": {
                    "projectRef": {
                        "applicationInstanceId": "application",
                        "projectId": "project-1",
                    },
                    "threadId": "thread-1",
                },
                "routeId": "route-1",
            },
        )
        self.assertEqual(
            request["json"]["content"][0],
            {"type": "text", "text": "done", "format": "plain"},
        )
        digest = hashlib.sha256(b"result").hexdigest()
        self.assertEqual(
            request["json"]["content"][1],
            {
                "type": "inlineArtifact",
                "attachmentId": f"artifact-1-{digest[:16]}",
                "filename": "result.txt",
                "mediaType": "text/plain",
                "sizeBytes": 6,
                "contentBase64": base64.b64encode(b"result").decode("ascii"),
            },
        )

    def test_ipv6_conversation_markdown_and_other_typed_state_exit_three(self) -> None:
        arguments = [
            "--endpoint",
            "https://[::1]:8443/deliver",
            "--credential-stdin",
            "--delivery-id",
            "delivery-cli",
            "--channel",
            "channel",
            "--conversation",
            "conversation-1",
            "--text",
            "**done**",
            "--markdown",
        ]
        exit_code, stdout, stderr, client_factory = self._invoke(
            arguments,
            response=_Response(200, {"deliveryId": "delivery-cli", "state": "partial"}),
        )
        self.assertEqual(exit_code, 3)
        self.assertEqual(json.loads(stdout)["state"], "partial")
        self.assertEqual(stderr, "")
        request = client_factory.return_value.__enter__.return_value.post.call_args.kwargs
        self.assertEqual(request["timeout"], 60.0)
        self.assertEqual(
            request["json"]["target"],
            {
                "kind": "conversation",
                "channelInstanceId": "channel",
                "nativeConversationId": "conversation-1",
            },
        )
        self.assertEqual(
            request["json"]["content"],
            [{"type": "text", "text": "**done**", "format": "markdown"}],
        )

    def test_rejects_non_numeric_loopback_scheme_remote_and_url_credentials(self) -> None:
        invalid_endpoints = (
            "ftp://127.0.0.1/deliver",
            "http://localhost/deliver",
            "https://example.com/deliver",
            "http://user:password@127.0.0.1/deliver",
        )
        for endpoint in invalid_endpoints:
            with self.subTest(endpoint=endpoint):
                arguments = self._thread_args("--text", "done")
                arguments[1] = endpoint
                exit_code, stdout, stderr, client_factory = self._invoke(arguments)
                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("imagent-send:", stderr)
                client_factory.assert_not_called()

    def test_credential_source_validation_and_secrecy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            credential_path = Path(directory) / "credential"
            credential_path.write_text("file-secret\n", encoding="utf-8")
            cases = (
                [
                    argument
                    for argument in self._thread_args("--text", "done")
                    if argument != "--credential-stdin"
                ],
                [
                    *self._thread_args("--text", "done"),
                    "--credential-file",
                    str(credential_path),
                ],
                self._thread_args("--text", "done"),
                [
                    argument
                    for argument in self._thread_args("--text", "done")
                    if argument != "--credential-stdin"
                ]
                + ["--credential-file", str(Path(directory) / "missing")],
            )
            stdin_values = ("ignored-secret\n", "stdin-secret\n", "\n", "stdin-secret\n")
            for arguments, stdin in zip(cases, stdin_values, strict=True):
                with self.subTest(arguments=arguments, stdin=stdin):
                    exit_code, stdout, stderr, client_factory = self._invoke(
                        arguments,
                        stdin=stdin,
                    )
                    self.assertEqual(exit_code, 2)
                    self.assertEqual(stdout, "")
                    self.assertNotIn("ignored-secret", stderr)
                    self.assertNotIn("stdin-secret", stderr)
                    self.assertNotIn("file-secret", stderr)
                    client_factory.assert_not_called()

    def test_target_validation_is_exclusive_and_complete(self) -> None:
        common = [
            "--endpoint",
            "http://127.0.0.1:8080/deliver",
            "--credential-stdin",
            "--delivery-id",
            "delivery-cli",
            "--text",
            "done",
        ]
        invalid_targets = (
            (),
            ("--application", "application"),
            ("--thread", "thread-1"),
            ("--project", "project-1"),
            ("--application", "application", "--thread", "thread-1"),
            ("--channel", "channel"),
            ("--conversation", "conversation-1"),
            (
                "--application",
                "application",
                "--thread",
                "thread-1",
                "--channel",
                "channel",
                "--conversation",
                "conversation-1",
            ),
            (
                "--channel",
                "channel",
                "--conversation",
                "conversation-1",
                "--route",
                "route-1",
            ),
        )
        for target in invalid_targets:
            with self.subTest(target=target):
                exit_code, stdout, _stderr, client_factory = self._invoke([*common, *target])
                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout, "")
                client_factory.assert_not_called()

    def test_content_and_file_validation_fail_before_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid_cases = (
                self._thread_args("--markdown"),
                self._thread_args("--artifact", str(Path(directory) / "missing.bin")),
                self._thread_args("--artifact", directory),
            )
            for arguments in invalid_cases:
                with self.subTest(arguments=arguments):
                    exit_code, stdout, _stderr, client_factory = self._invoke(arguments)
                    self.assertEqual(exit_code, 2)
                    self.assertEqual(stdout, "")
                    client_factory.assert_not_called()

        client_factory = MagicMock()
        with (
            patch.object(send.httpx, "Client", client_factory),
            patch.object(sys, "stdin", io.StringIO("scoped-token\n")),
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as raised:
                send.main(self._thread_args())
        self.assertEqual(raised.exception.code, 2)
        client_factory.assert_not_called()

    def test_transport_non_json_and_http_error_exit_codes(self) -> None:
        arguments = self._thread_args("--text", "done")
        exit_code, stdout, stderr, _client_factory = self._invoke(
            arguments,
            post_error=httpx.ConnectError("connect failed"),
            stdin="credential-must-stay-secret\n",
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("connect failed", stderr)
        self.assertNotIn("credential-must-stay-secret", stderr)

        exit_code, stdout, stderr, _client_factory = self._invoke(
            arguments,
            response=_Response(502, ValueError("not JSON")),
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("non-JSON HTTP 502", stderr)

        exit_code, stdout, stderr, _client_factory = self._invoke(
            arguments,
            response=_Response(403, {"state": "rejected", "code": "forbidden"}),
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(stdout)["state"], "rejected")
        self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()

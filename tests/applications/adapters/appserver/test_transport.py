from __future__ import annotations

import json
import unittest

from imagent.applications.adapters.appserver.client import AppServerError as FacadeAppServerError
from imagent.applications.adapters.appserver.transport import (
    AppServerError,
    StdioAppServerTransport,
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


class AppServerTransportTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_stdio_transport_translates_closed_stream(self) -> None:
        transport = StdioAppServerTransport(_FakeProcess())

        with self.assertRaisesRegex(AppServerError, "app-server connection closed"):
            await transport.receive_json()

    def test_error_identity_remains_the_historical_facade_object(self) -> None:
        self.assertIs(FacadeAppServerError, AppServerError)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from typing import cast
from unittest.mock import patch

from imagent.applications.adapters.appserver.client import AppServerError as FacadeAppServerError
from imagent.applications.adapters.appserver.transport import (
    AppServerError,
    StdioAppServerTransport,
    WebSocketAppServerTransport,
)

_OVERSIZED_FRAME_MESSAGE = "app-server inbound frame exceeds configured byte limit"


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeStdout:
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = list(chunks)
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
            return chunk[:size]
        return chunk


class _FakeProcess:
    def __init__(self, *chunks: bytes) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(*chunks)
        self.returncode: int | None = None


class _FakeWebSocket:
    def __init__(self, *frames: str | bytes) -> None:
        self.frames = list(frames)
        self.sent: list[str] = []
        self.recv_calls = 0
        self.close_calls = 0
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str | bytes:
        self.recv_calls += 1
        if not self.frames:
            raise RuntimeError("closed")
        return self.frames.pop(0)

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class AppServerTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_inbound_frame_limit_defaults_and_validation_are_shared(self) -> None:
        stdio = StdioAppServerTransport(_FakeProcess())
        websocket = WebSocketAppServerTransport(_FakeWebSocket())

        self.assertEqual(stdio._max_inbound_frame_bytes, 64 * 1024 * 1024)
        self.assertEqual(websocket._max_inbound_frame_bytes, 64 * 1024 * 1024)
        for invalid in (0, -1, True, False, 1.0, "1", None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "must be a positive integer"):
                    StdioAppServerTransport(
                        _FakeProcess(),
                        max_inbound_frame_bytes=cast(int, invalid),
                    )
                with self.assertRaisesRegex(ValueError, "must be a positive integer"):
                    WebSocketAppServerTransport(
                        _FakeWebSocket(),
                        max_inbound_frame_bytes=cast(int, invalid),
                    )

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
        self.assertEqual(process.stdout.read_sizes, [65536])

    async def test_stdio_accepts_exact_limit_with_or_without_delimiter(self) -> None:
        delimited = StdioAppServerTransport(
            _FakeProcess(b'{"a":1}\n'),
            max_inbound_frame_bytes=7,
        )
        unterminated = StdioAppServerTransport(
            _FakeProcess(b'{"a":1}'),
            max_inbound_frame_bytes=7,
        )

        self.assertEqual(await delimited.receive_json(), {"a": 1})
        self.assertEqual(await unterminated.receive_json(), {"a": 1})

    async def test_stdio_rejects_single_chunk_oversize_before_decode_and_poison_reuse(
        self,
    ) -> None:
        secret = b"secret!!"
        process = _FakeProcess(secret + b"\n")
        transport = StdioAppServerTransport(process, max_inbound_frame_bytes=7)

        with (
            patch(
                "imagent.applications.adapters.appserver.transport.json.loads",
                side_effect=AssertionError("decoder must not run"),
            ) as loads,
            self.assertRaises(AppServerError) as raised,
        ):
            await transport.receive_json()

        self.assertEqual(str(raised.exception), _OVERSIZED_FRAME_MESSAGE)
        self.assertNotIn("secret", str(raised.exception))
        loads.assert_not_called()
        self.assertEqual(transport._buffer, bytearray())
        self.assertTrue(transport.is_closed())
        reads_after_rejection = list(process.stdout.read_sizes)

        with self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"):
            await transport.receive_json()
        with self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"):
            await transport.send_json({"secret": "must-not-send"})
        self.assertEqual(process.stdout.read_sizes, reads_after_rejection)
        self.assertEqual(process.stdin.writes, [])

    async def test_stdio_rejects_multichunk_oversize_before_delimiter(self) -> None:
        process = _FakeProcess(b"abcd", b"efgh", b"\n")
        transport = StdioAppServerTransport(process, max_inbound_frame_bytes=7)

        with (
            patch(
                "imagent.applications.adapters.appserver.transport.json.loads",
                side_effect=AssertionError("decoder must not run"),
            ) as loads,
            self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"),
        ):
            await transport.receive_json()

        loads.assert_not_called()
        self.assertEqual(process.stdout.read_sizes, [65536, 65536])
        self.assertEqual(transport._buffer, bytearray())
        self.assertTrue(transport.is_closed())

    async def test_stdio_rejects_unterminated_oversize_before_decode_or_eof_read(
        self,
    ) -> None:
        process = _FakeProcess(b"secret!!")
        transport = StdioAppServerTransport(process, max_inbound_frame_bytes=7)

        with (
            patch(
                "imagent.applications.adapters.appserver.transport.json.loads",
                side_effect=AssertionError("decoder must not run"),
            ) as loads,
            self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"),
        ):
            await transport.receive_json()

        loads.assert_not_called()
        self.assertEqual(process.stdout.read_sizes, [65536])
        self.assertEqual(transport._buffer, bytearray())
        self.assertTrue(transport.is_closed())

    async def test_stdio_handles_multiple_legal_frames_before_poisoning_oversize_suffix(
        self,
    ) -> None:
        process = _FakeProcess(b'{"a":1}\n{"b":2}\nsecret!!\n{"c":3}\n')
        transport = StdioAppServerTransport(process, max_inbound_frame_bytes=7)

        self.assertEqual(await transport.receive_json(), {"a": 1})
        self.assertEqual(await transport.receive_json(), {"b": 2})
        with self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"):
            await transport.receive_json()

        self.assertEqual(process.stdout.read_sizes, [65536])
        self.assertEqual(transport._buffer, bytearray())
        self.assertTrue(transport.is_closed())
        with self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"):
            await transport.receive_json()

    async def test_stdio_blank_frames_obey_limit_before_being_skipped(self) -> None:
        exact = StdioAppServerTransport(
            _FakeProcess(b'       \n{"a":1}\n'),
            max_inbound_frame_bytes=7,
        )
        oversized = StdioAppServerTransport(
            _FakeProcess(b'        \n{"a":1}\n'),
            max_inbound_frame_bytes=7,
        )

        self.assertEqual(await exact.receive_json(), {"a": 1})
        with self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"):
            await oversized.receive_json()

    async def test_stdio_transport_translates_closed_stream(self) -> None:
        transport = StdioAppServerTransport(_FakeProcess())

        with self.assertRaisesRegex(AppServerError, "app-server connection closed"):
            await transport.receive_json()

    async def test_websocket_text_limit_uses_utf8_bytes_before_decode(self) -> None:
        frame = '{"value":"\u00e9"}'
        encoded_size = len(frame.encode("utf-8"))
        exact = WebSocketAppServerTransport(
            _FakeWebSocket(frame),
            max_inbound_frame_bytes=encoded_size,
        )
        websocket = _FakeWebSocket(frame)
        oversized = WebSocketAppServerTransport(
            websocket,
            max_inbound_frame_bytes=encoded_size - 1,
        )

        self.assertEqual(await exact.receive_json(), {"value": "\u00e9"})
        with (
            patch(
                "imagent.applications.adapters.appserver.transport.json.loads",
                side_effect=AssertionError("decoder must not run"),
            ) as loads,
            self.assertRaises(AppServerError) as raised,
        ):
            await oversized.receive_json()

        self.assertEqual(str(raised.exception), _OVERSIZED_FRAME_MESSAGE)
        self.assertNotIn("value", str(raised.exception))
        loads.assert_not_called()
        self.assertTrue(oversized.is_closed())
        with self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"):
            await oversized.receive_json()
        with self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"):
            await oversized.send_json({"secret": "must-not-send"})
        self.assertEqual(websocket.recv_calls, 1)
        self.assertEqual(websocket.sent, [])

    async def test_websocket_binary_limit_uses_raw_bytes_before_decode(self) -> None:
        frame = b'{"a":1}'
        exact = WebSocketAppServerTransport(
            _FakeWebSocket(frame),
            max_inbound_frame_bytes=len(frame),
        )
        oversized = WebSocketAppServerTransport(
            _FakeWebSocket(frame),
            max_inbound_frame_bytes=len(frame) - 1,
        )

        self.assertEqual(await exact.receive_json(), {"a": 1})
        with (
            patch(
                "imagent.applications.adapters.appserver.transport.json.loads",
                side_effect=AssertionError("decoder must not run"),
            ) as loads,
            self.assertRaisesRegex(AppServerError, f"^{_OVERSIZED_FRAME_MESSAGE}$"),
        ):
            await oversized.receive_json()
        loads.assert_not_called()
        self.assertTrue(oversized.is_closed())

    async def test_websocket_preserves_send_close_and_closed_translation(self) -> None:
        websocket = _FakeWebSocket()
        transport = WebSocketAppServerTransport(websocket)

        await transport.send_json({"id": 1})
        with self.assertRaisesRegex(AppServerError, "app-server connection closed"):
            await transport.receive_json()
        await transport.close()

        self.assertEqual(websocket.sent, [json.dumps({"id": 1})])
        self.assertEqual(websocket.close_calls, 1)
        self.assertTrue(transport.is_closed())

    def test_error_identity_remains_the_historical_facade_object(self) -> None:
        self.assertIs(FacadeAppServerError, AppServerError)


if __name__ == "__main__":
    unittest.main()

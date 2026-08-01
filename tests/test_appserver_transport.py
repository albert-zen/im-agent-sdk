from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from imagent.adapters import ApplicationInputOutcomeUnknown
from imagent.applications.appserver_client.client import AppServerClient
from imagent.applications.appserver_client.supervisor import (
    AppServerSupervisor,
    MissingAppServerDependencyError,
)


class _ScriptedStdout:
    def __init__(self) -> None:
        self.lines: asyncio.Queue[bytes] = asyncio.Queue()
        self.buffer = bytearray()
        self.eof = False

    async def read(self, size: int = -1) -> bytes:
        while not self.buffer:
            if self.eof:
                return b""
            chunk = await self.lines.get()
            if not chunk:
                self.eof = True
                return b""
            self.buffer.extend(chunk)
        if size < 0 or size >= len(self.buffer):
            data = bytes(self.buffer)
            self.buffer.clear()
            return data
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data


class _ScriptedStdin:
    def __init__(self, process: _ScriptedProcess) -> None:
        self.process = process
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)
        while b"\n" in self.buffer:
            line, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            self.process.on_input(line.decode("utf-8"))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.process.closed = True


class _ScriptedProcess:
    def __init__(self, scripts: dict[str, list[dict]]) -> None:
        self.stdout = _ScriptedStdout()
        self.stderr = _ScriptedStdout()
        self.stdin = _ScriptedStdin(self)
        self.scripts = scripts
        self.sent: list[dict] = []
        self.closed = False
        self.returncode: int | None = None

    def on_input(self, raw: str) -> None:
        request = json.loads(raw)
        self.sent.append(request)
        method = request.get("method")
        if method in {None, "initialized"}:
            return
        for scripted in self.scripts.get(method, []):
            message = dict(scripted)
            if "method" not in message and "id" in request:
                message["id"] = request["id"]
            self.stdout.lines.put_nowait((json.dumps(message) + "\n").encode())

    def terminate(self) -> None:
        self.returncode = 0
        self.stdout.lines.put_nowait(b"")
        self.stderr.lines.put_nowait(b"")

    async def wait(self) -> int:
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


class _StubbornProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self._exited = asyncio.Event()

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode


class _TemporarilyUnkillableProcess(_StubbornProcess):
    def __init__(self) -> None:
        super().__init__()
        self.reject_kill = True

    def kill(self) -> None:
        if self.reject_kill:
            raise RuntimeError("simulated kill failure")
        super().kill()


def _client(process: _ScriptedProcess, *, request_timeout_s: float = 15.0) -> AppServerClient:
    return AppServerClient(
        supervisor=AppServerSupervisor(
            app_server_url="stdio://",
            spawn_process=lambda *_args: process,
        ),
        client_info={"name": "sdk-test", "title": "SDK Test", "version": "0"},
        request_timeout_s=request_timeout_s,
    )


class AppServerTransportLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_start_cancelled_after_dispatch_has_unknown_outcome(self) -> None:
        process = _ScriptedProcess({"initialize": [{"result": {"ok": True}}]})
        client = _client(process)
        task = asyncio.create_task(client.start_turn("thread-1", "run"))
        try:
            async with asyncio.timeout(1):
                while not any(item.get("method") == "turn/start" for item in process.sent):
                    await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
                await task
            self.assertIsInstance(raised.exception.cause, asyncio.CancelledError)
        finally:
            if not task.done():
                task.cancel()
            await client.close()

    async def test_turn_start_response_loss_has_unknown_outcome(self) -> None:
        process = _ScriptedProcess({"initialize": [{"result": {"ok": True}}]})
        client = _client(process, request_timeout_s=0.01)
        try:
            with self.assertRaises(ApplicationInputOutcomeUnknown) as raised:
                await client.start_turn("thread-1", "run")
            self.assertIn("timed out", str(raised.exception.cause))
            self.assertTrue(any(item.get("method") == "turn/start" for item in process.sent))
        finally:
            await client.close()

    async def test_missing_websocket_extra_fails_without_retry(self) -> None:
        sleeps: list[float] = []
        supervisor = AppServerSupervisor(
            app_server_url="ws://127.0.0.1:8765",
            sleep=lambda delay: sleeps.append(delay),
        )

        with (
            patch(
                "imagent.applications.appserver_client.supervisor._websocket_module",
                side_effect=MissingAppServerDependencyError(
                    "install the appserver optional dependency"
                ),
            ),
            self.assertRaisesRegex(
                MissingAppServerDependencyError,
                "optional dependency",
            ),
        ):
            await supervisor.connect_external()

        self.assertEqual(sleeps, [])

    async def test_failed_kill_retains_child_ownership_for_retry(self) -> None:
        process = _TemporarilyUnkillableProcess()
        supervisor = AppServerSupervisor(
            app_server_url="stdio://",
            spawn_process=lambda *_args: process,
            process_shutdown_timeout_s=0.01,
        )
        await supervisor.start()

        with self.assertRaisesRegex(RuntimeError, "simulated kill failure"):
            await supervisor.stop()

        self.assertIs(supervisor.process, process)
        process.reject_kill = False
        await supervisor.stop()
        self.assertIsNone(supervisor.process)

    async def test_stdio_supervisor_kills_child_that_ignores_terminate(self) -> None:
        process = _StubbornProcess()
        supervisor = AppServerSupervisor(
            app_server_url="stdio://",
            spawn_process=lambda *_args: process,
            process_shutdown_timeout_s=0.01,
        )

        await supervisor.start()
        await asyncio.wait_for(supervisor.stop(), timeout=0.2)

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertIsNone(supervisor.process)

    async def test_stdio_client_dispatches_and_replies_to_native_server_request(self) -> None:
        process = _ScriptedProcess(
            {
                "initialize": [{"result": {"ok": True}}],
                "thread/start": [
                    {
                        "result": {
                            "thread": {
                                "id": "thread-1",
                                "cwd": "D:/repo/app",
                                "status": "idle",
                            }
                        }
                    },
                    {
                        "id": 99,
                        "method": "item/tool/requestUserInput",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "questions": [{"id": "color", "question": "Favorite color?"}],
                        },
                    },
                ],
            }
        )
        client = _client(process)
        received = asyncio.Event()
        captured: list[dict] = []

        def capture(request: dict) -> None:
            captured.append(request)
            received.set()

        client.add_server_request_handler(capture)
        try:
            result = await client.start_thread(cwd="D:/repo/app")
            await asyncio.wait_for(received.wait(), timeout=1)
            await client.reply_to_transport_request(
                99,
                {"answers": {"color": {"answers": ["blue"]}}},
            )

            self.assertEqual(result["thread"]["id"], "thread-1")
            self.assertEqual(captured[0]["params"]["_transport_request_id"], 99)
            self.assertEqual(captured[0]["params"]["_connection_epoch"], 1)
            self.assertEqual(
                process.sent[-1],
                {
                    "id": 99,
                    "result": {"answers": {"color": {"answers": ["blue"]}}},
                },
            )
        finally:
            await client.close()

    async def test_slow_notification_handler_does_not_block_response_reader(self) -> None:
        process = _ScriptedProcess(
            {
                "initialize": [
                    {
                        "method": "thread/status/changed",
                        "params": {"threadId": "thread-1", "status": "idle"},
                    },
                    {"result": {"ok": True}},
                ],
                "thread/list": [{"result": {"threads": []}}],
            }
        )
        client = _client(process, request_timeout_s=0.2)
        handler_started = asyncio.Event()
        release_handler = asyncio.Event()

        async def slow_handler(_notification: dict) -> None:
            handler_started.set()
            await release_handler.wait()

        client.add_notification_handler(slow_handler)
        try:
            result = await client.list_threads()
            self.assertEqual(result, {"threads": []})
            self.assertTrue(handler_started.is_set())
        finally:
            release_handler.set()
            await client.close()

    async def test_request_resolution_notification_carries_dispatch_epoch(
        self,
    ) -> None:
        process = _ScriptedProcess(
            {
                "initialize": [{"result": {"ok": True}}],
                "thread/list": [
                    {"result": {"threads": []}},
                    {
                        "method": "serverRequest/resolved",
                        "params": {"requestId": 99},
                    },
                ],
            }
        )
        client = _client(process)
        received = asyncio.Event()
        captured: list[dict] = []

        def capture(notification: dict) -> None:
            captured.append(notification)
            received.set()

        client.add_notification_handler(capture)
        try:
            await client.list_threads()
            await asyncio.wait_for(received.wait(), timeout=1)
            self.assertEqual(
                captured[0]["params"],
                {
                    "requestId": 99,
                    "_connection_epoch": 1,
                },
            )
        finally:
            await client.close()

    async def test_stdio_eof_respawns_and_advances_connection_epoch(self) -> None:
        first = _ScriptedProcess({"initialize": [{"result": {"ok": True}}]})
        second = _ScriptedProcess(
            {
                "initialize": [{"result": {"ok": True}}],
                "thread/list": [{"result": {"threads": []}}],
            }
        )
        processes = iter((first, second))
        client = AppServerClient(
            supervisor=AppServerSupervisor(
                app_server_url="stdio://",
                spawn_process=lambda *_args: next(processes),
            ),
            client_info={"name": "sdk-test", "title": "SDK Test", "version": "0"},
        )
        try:
            await client.connect()
            self.assertEqual(client.connection_epoch, 1)
            first.stdout.lines.put_nowait(b"")
            await asyncio.sleep(0)

            self.assertEqual(await client.list_threads(), {"threads": []})
            self.assertEqual(client.connection_epoch, 2)
            self.assertEqual(second.sent[0]["method"], "initialize")
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()

"""Public resume ownership across initialization, callbacks and connection fences."""

from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Awaitable
from typing import Any
from unittest.mock import patch

from imagent.applications.adapters.appserver.client import AppServerClient, AppServerError
from imagent.applications.adapters.appserver.client.supervisor import AppServerSupervisor


class _Socket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | Exception] = asyncio.Queue()
        self.methods: list[str] = []
        self.resumes: list[dict[str, Any]] = []
        self.closed = False
        self.hold = False
        self.resume_sent = asyncio.Event()

    async def send(self, raw: str) -> None:
        request = json.loads(raw)
        self.methods.append(request["method"])
        if "id" not in request:
            return
        if request["method"] == "thread/resume":
            self.resumes.append(request)
            self.resume_sent.set()
            if self.hold:
                return
        await self.reply(request)

    async def reply(self, request: dict[str, Any]) -> None:
        result = {"thread": {"id": request.get("params", {}).get("threadId")}}
        await self.incoming.put(json.dumps({"id": request["id"], "result": result}))

    async def recv(self) -> str:
        item = await self.incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


class ResumeOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def client(self, *, timeout: float = 0.2) -> tuple[AppServerClient, list[_Socket]]:
        sockets: list[_Socket] = []

        async def factory(*args: object, **kwargs: object) -> _Socket:
            socket = _Socket()
            sockets.append(socket)
            return socket

        return AppServerClient(
            supervisor=AppServerSupervisor(
                app_server_url="ws://127.0.0.1:9999", websocket_factory=factory
            ),
            client_info={"name": "resume-proof", "title": "Resume proof", "version": "0"},
            request_timeout_s=timeout,
        ), sockets

    def resume(self, client: AppServerClient, method: str, thread: str) -> Awaitable[object]:
        if method == "resume":
            return client.resume_thread(thread_id=thread)
        if method == "recovery":
            return client.resume_thread_for_recovery(thread_id=thread)
        if method == "call":
            return client.call("thread/resume", {"threadId": thread})
        return client.call_with_dispatch_position("thread/resume", {"threadId": thread})

    async def test_ready_handler_can_await_every_public_resume_entrypoint(self) -> None:
        for method in ("resume", "recovery", "call", "positioned"):
            with self.subTest(method=method):
                client, sockets = self.client()
                finished = asyncio.Event()

                async def ready(epoch: int) -> None:
                    await self.resume(client, method, "thread")
                    finished.set()

                client.add_connection_ready_handler(ready)
                try:
                    await asyncio.wait_for(client.initialize(), 0.4)
                    self.assertTrue(finished.is_set())
                    self.assertEqual(
                        sockets[0].methods, ["initialize", "initialized", "thread/resume"]
                    )
                    self.assertFalse(client._codex_resume_tasks)
                finally:
                    await client.close()

    async def test_first_resume_initializes_before_same_or_other_thread_callback(self) -> None:
        for method in ("resume", "recovery", "call", "positioned"):
            for callback_thread in ("outer", "different"):
                with self.subTest(method=method, callback_thread=callback_thread):
                    client, sockets = self.client()

                    async def ready(epoch: int) -> None:
                        await client.resume_thread(thread_id=callback_thread)

                    client.add_connection_ready_handler(ready)
                    try:
                        await asyncio.wait_for(self.resume(client, method, "outer"), 0.4)
                        self.assertEqual(
                            [r["params"]["threadId"] for r in sockets[0].resumes],
                            [callback_thread, "outer"],
                        )
                        self.assertTrue(client.initialized)
                        self.assertFalse(client._codex_resume_tasks)
                    finally:
                        await client.close()

    async def test_ready_resume_rpc_timeout_releases_initializer_and_owner(self) -> None:
        client, sockets = self.client(timeout=0.02)

        async def ready(epoch: int) -> None:
            sockets[0].hold = True
            await client.resume_thread(thread_id="held")

        client.add_connection_ready_handler(ready)
        try:
            with self.assertRaises(AppServerError):
                await asyncio.wait_for(client.initialize(), 0.4)
            self.assertEqual(len(sockets[0].resumes), 1)
            self.assertFalse(client._initialize_lock.locked())
            self.assertFalse(client._codex_resume_tasks)
            self.assertFalse(client._pending_futures)
        finally:
            await client.close()

    async def test_readied_waiter_cannot_cross_reset_or_close_after_lock_wait(self) -> None:
        for boundary in ("reset", "close"):
            with self.subTest(boundary=boundary):
                client, sockets = self.client(timeout=2)
                await client.initialize()
                socket = sockets[0]
                socket.hold = True
                first = asyncio.create_task(client.resume_thread(thread_id="same"))
                await socket.resume_sent.wait()
                readied = asyncio.Event()
                ensure_ready = client._ensure_ready

                async def observe_ready() -> None:
                    await ensure_ready()
                    readied.set()

                try:
                    with patch.object(client, "_ensure_ready", observe_ready):
                        waiter = asyncio.create_task(
                            client.resume_thread_for_recovery(thread_id="same")
                        )
                        await asyncio.wait_for(readied.wait(), 0.4)
                        if boundary == "reset":
                            await socket.incoming.put(OSError("synthetic connection reset"))
                        else:
                            await client.close()
                        results = await asyncio.wait_for(
                            asyncio.gather(first, waiter, return_exceptions=True), 0.5
                        )
                    self.assertTrue(
                        all(isinstance(result, AppServerError) for result in results), results
                    )
                    self.assertEqual(sum(len(item.resumes) for item in sockets), 1)
                    self.assertFalse(client._codex_resume_tasks)
                    if boundary == "reset":
                        await client.initialize()
                        self.assertGreater(client.connection_epoch, 1)
                        self.assertEqual(sum(len(item.resumes) for item in sockets), 1)
                        await client.resume_thread(thread_id="new-epoch")
                        self.assertEqual(sum(len(item.resumes) for item in sockets), 2)
                        self.assertEqual(sockets[-1].resumes[-1]["params"]["threadId"], "new-epoch")
                finally:
                    await client.close()

    async def test_cancelled_initializer_resets_ready_handler_rpc_before_close(self) -> None:
        client, sockets = self.client(timeout=2)
        entered = asyncio.Event()

        async def ready(epoch: int) -> None:
            sockets[0].hold = True
            entered.set()
            await client.resume_thread(thread_id="held")

        client.add_connection_ready_handler(ready)
        initialization = asyncio.create_task(client.initialize())
        try:
            await entered.wait()
            await sockets[0].resume_sent.wait()
            initialization.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(initialization, 0.4)
            await client.close()
            self.assertEqual(len(sockets[0].resumes), 1)
            self.assertFalse(client._initialize_lock.locked())
            self.assertFalse(client._codex_resume_tasks)
            self.assertFalse(client._pending_futures)
        finally:
            await client.close()

from __future__ import annotations

import json
import unittest

import httpx

from imagent.applications import HttpT3Client


class HttpT3ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_shell_thread_and_dispatch_routes(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/orchestration/shell":
                return httpx.Response(200, json={"projects": [], "threads": []})
            if request.url.raw_path == b"/api/orchestration/threads/thread%2Fone":
                return httpx.Response(200, json={"thread": {"id": "thread/one"}})
            if request.url.path == "/api/orchestration/dispatch":
                return httpx.Response(200, json={"sequence": 2})
            return httpx.Response(404)

        client = HttpT3Client(
            "http://t3.local",
            lambda: "secret-token",
            transport=httpx.MockTransport(handler),
        )
        async with client:
            await client.shell_snapshot()
            await client.thread_detail("thread/one")
            await client.dispatch({"type": "thread.archive", "threadId": "one"})

        self.assertEqual(
            [request.method for request in requests],
            ["GET", "GET", "POST"],
        )
        self.assertTrue(
            all(request.headers["authorization"] == "Bearer secret-token" for request in requests)
        )
        self.assertEqual(
            json.loads(requests[-1].content),
            {"type": "thread.archive", "threadId": "one"},
        )

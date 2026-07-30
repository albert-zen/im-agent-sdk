from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

# Adapted from IMT3's MIT-licensed T3 client. The SDK creates threads explicitly,
# so the bootstrap-only WebSocket path is intentionally unnecessary here.


class T3ClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.reason = reason
        self.trace_id = trace_id


class HttpT3Client:
    """Authenticated client for the native T3 orchestration HTTP API."""

    def __init__(
        self,
        origin: str,
        token_provider: Callable[[], str],
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.origin = origin.rstrip("/")
        self._token_provider = token_provider
        self._client = httpx.AsyncClient(
            base_url=self.origin,
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> HttpT3Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def shell_snapshot(self) -> Mapping[str, object]:
        return await self._request("GET", "/api/orchestration/shell")

    async def thread_detail(self, thread_id: str) -> Mapping[str, object]:
        encoded = quote(thread_id, safe="")
        return await self._request(
            "GET",
            f"/api/orchestration/threads/{encoded}",
        )

    async def dispatch(
        self,
        command: Mapping[str, object],
    ) -> Mapping[str, object]:
        return await self._request(
            "POST",
            "/api/orchestration/dispatch",
            json=dict(command),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
    ) -> Mapping[str, object]:
        try:
            response = await self._client.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {self._token_provider()}",
                    "Accept": "application/json",
                },
                json=json,
            )
        except httpx.HTTPError as error:
            raise T3ClientError("Unable to connect to T3") from error
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {}
        if response.is_error:
            body = payload if isinstance(payload, dict) else {}
            reason = body.get("reason")
            raise T3ClientError(
                f"T3 request failed (HTTP {response.status_code})",
                status_code=response.status_code,
                code=_optional_string(body.get("code")),
                reason=_optional_string(reason),
                trace_id=_optional_string(body.get("traceId")),
            )
        if not isinstance(payload, dict):
            raise T3ClientError("T3 returned a non-object JSON response")
        return payload


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None

from __future__ import annotations

import inspect
import json
from typing import Any, Protocol, cast

JsonDict = dict[str, Any]


class AppServerError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class AppServerTransport(Protocol):
    async def send_json(self, payload: JsonDict) -> None: ...

    async def receive_json(self) -> JsonDict: ...

    async def close(self) -> None: ...

    def is_closed(self) -> bool: ...


class StdioAppServerTransport:
    """Newline-delimited JSON transport over an SDK-managed process."""

    def __init__(self, process: Any) -> None:
        self._process = process
        self._stdin: Any = getattr(process, "stdin", None)
        self._stdout: Any = getattr(process, "stdout", None)
        self._buffer = bytearray()
        if self._stdin is None or self._stdout is None:
            raise AppServerError("stdio app-server process missing stdin/stdout pipes")

    async def send_json(self, payload: JsonDict) -> None:
        data = (json.dumps(payload) + "\n").encode("utf-8")
        self._stdin.write(data)
        drain = getattr(self._stdin, "drain", None)
        if callable(drain):
            result = drain()
            if inspect.isawaitable(result):
                await result

    async def receive_json(self) -> JsonDict:
        while True:
            newline_index = self._buffer.find(b"\n")
            if newline_index >= 0:
                raw = bytes(self._buffer[:newline_index])
                del self._buffer[: newline_index + 1]
                if not raw.strip():
                    continue
                return json.loads(raw.decode("utf-8"))
            chunk = await self._read_chunk()
            if not chunk:
                if not self._buffer:
                    raise AppServerError("app-server connection closed")
                raw = bytes(self._buffer)
                self._buffer.clear()
                if not raw.strip():
                    raise AppServerError("app-server connection closed")
                return json.loads(raw.decode("utf-8"))
            self._buffer.extend(chunk)

    async def close(self) -> None:
        close = getattr(self._stdin, "close", None)
        if callable(close):
            close()

    def is_closed(self) -> bool:
        return getattr(self._process, "returncode", None) is not None

    async def _read_chunk(self) -> bytes:
        read = getattr(self._stdout, "read", None)
        if callable(read):
            chunk = read(65536)
            if inspect.isawaitable(chunk):
                chunk = await chunk
            return cast(bytes, chunk)
        return await self._stdout.readline()


class WebSocketAppServerTransport:
    """JSON message transport over an externally supplied WebSocket."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket

    async def send_json(self, payload: JsonDict) -> None:
        await self._websocket.send(json.dumps(payload))

    async def receive_json(self) -> JsonDict:
        try:
            raw = await self._websocket.recv()
        except Exception as exc:
            raise AppServerError("app-server connection closed") from exc
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def close(self) -> None:
        close = getattr(self._websocket, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    def is_closed(self) -> bool:
        return bool(getattr(self._websocket, "closed", False))

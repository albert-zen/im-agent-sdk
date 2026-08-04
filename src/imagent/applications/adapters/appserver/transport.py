from __future__ import annotations

import inspect
import json
from typing import Any, Protocol, cast

JsonDict = dict[str, Any]
_DEFAULT_MAX_INBOUND_FRAME_BYTES = 64 * 1024 * 1024
_OVERSIZED_INBOUND_FRAME_MESSAGE = "app-server inbound frame exceeds configured byte limit"


def _validated_max_inbound_frame_bytes(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("max_inbound_frame_bytes must be a positive integer")
    return value


def _raise_if_poisoned(poisoned: bool) -> None:
    if poisoned:
        raise AppServerError(_OVERSIZED_INBOUND_FRAME_MESSAGE)


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

    def __init__(
        self,
        process: Any,
        *,
        max_inbound_frame_bytes: int = _DEFAULT_MAX_INBOUND_FRAME_BYTES,
    ) -> None:
        self._process = process
        self._stdin: Any = getattr(process, "stdin", None)
        self._stdout: Any = getattr(process, "stdout", None)
        self._max_inbound_frame_bytes = _validated_max_inbound_frame_bytes(max_inbound_frame_bytes)
        self._buffer = bytearray()
        self._poisoned = False
        if self._stdin is None or self._stdout is None:
            raise AppServerError("stdio app-server process missing stdin/stdout pipes")

    async def send_json(self, payload: JsonDict) -> None:
        _raise_if_poisoned(self._poisoned)
        data = (json.dumps(payload) + "\n").encode("utf-8")
        self._stdin.write(data)
        drain = getattr(self._stdin, "drain", None)
        if callable(drain):
            result = drain()
            if inspect.isawaitable(result):
                await result

    async def receive_json(self) -> JsonDict:
        _raise_if_poisoned(self._poisoned)
        while True:
            newline_index = self._buffer.find(b"\n")
            if newline_index >= 0:
                if newline_index > self._max_inbound_frame_bytes:
                    self._reject_oversized_frame()
                raw = bytes(self._buffer[:newline_index])
                del self._buffer[: newline_index + 1]
                if not raw.strip():
                    continue
                return json.loads(raw.decode("utf-8"))
            if len(self._buffer) > self._max_inbound_frame_bytes:
                self._reject_oversized_frame()
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
        if callable(close := getattr(self._stdin, "close", None)):
            close()

    def is_closed(self) -> bool:
        return self._poisoned or getattr(self._process, "returncode", None) is not None

    def _reject_oversized_frame(self) -> None:
        self._buffer.clear()
        self._poisoned = True
        raise AppServerError(_OVERSIZED_INBOUND_FRAME_MESSAGE)

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

    def __init__(
        self,
        websocket: Any,
        *,
        max_inbound_frame_bytes: int = _DEFAULT_MAX_INBOUND_FRAME_BYTES,
    ) -> None:
        self._websocket = websocket
        self._max_inbound_frame_bytes = _validated_max_inbound_frame_bytes(max_inbound_frame_bytes)
        self._poisoned = False

    async def send_json(self, payload: JsonDict) -> None:
        _raise_if_poisoned(self._poisoned)
        await self._websocket.send(json.dumps(payload))

    async def receive_json(self) -> JsonDict:
        _raise_if_poisoned(self._poisoned)
        try:
            raw = await self._websocket.recv()
        except Exception as exc:
            raise AppServerError("app-server connection closed") from exc
        frame_size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))
        if frame_size > self._max_inbound_frame_bytes:
            self._poisoned = True
            raise AppServerError(_OVERSIZED_INBOUND_FRAME_MESSAGE)
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)

    async def close(self) -> None:
        if callable(close := getattr(self._websocket, "close", None)):
            if inspect.isawaitable(result := close()):
                await result

    def is_closed(self) -> bool:
        return self._poisoned or bool(getattr(self._websocket, "closed", False))

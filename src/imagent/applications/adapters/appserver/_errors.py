from __future__ import annotations

from typing import Any


class AppServerError(RuntimeError):
    """Shared wire/client failure type, exported only via the App Server client facade."""

    def __init__(self, message: str, *, code: int | None = None, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

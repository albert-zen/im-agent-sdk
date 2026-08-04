"""Canonical App Server target model shared by config and protocol layers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

DEFAULT_APP_SERVER_ENDPOINT = "unix://"
LEGACY_WEBSOCKET_ENDPOINT = "ws://127.0.0.1:8765"
STDIO_APP_SERVER_ENDPOINT = "stdio://"
EXTERNAL_CONNECTION_MODE = "external"
SPAWNED_STDIO_CONNECTION_MODE = "spawned-stdio"

AppServerOwnership = Literal["external", "bridge-child"]
AppServerTransportKind = Literal["unix-websocket", "tcp-websocket", "stdio-jsonl"]


class AppServerTargetConfigError(ValueError):
    pass


def default_app_server_endpoint(*, os_name: str | None = None) -> str:
    """Choose an external App Server target without changing lifecycle ownership."""

    platform_name = os.name if os_name is None else os_name
    if platform_name == "nt":
        return LEGACY_WEBSOCKET_ENDPOINT
    return DEFAULT_APP_SERVER_ENDPOINT


@dataclass(frozen=True, slots=True)
class AppServerTarget:
    endpoint: str
    ownership: AppServerOwnership
    transport: AppServerTransportKind

    @property
    def is_external(self) -> bool:
        return self.ownership == "external"

    @property
    def preserves_server_state(self) -> bool:
        return self.is_external

    @property
    def connection_mode(self) -> str:
        return EXTERNAL_CONNECTION_MODE if self.is_external else SPAWNED_STDIO_CONNECTION_MODE


def parse_app_server_target(endpoint: str) -> AppServerTarget:
    normalized = str(endpoint or "").strip()
    if normalized == STDIO_APP_SERVER_ENDPOINT:
        return AppServerTarget(
            endpoint=normalized,
            ownership="bridge-child",
            transport="stdio-jsonl",
        )
    if normalized.startswith("unix://"):
        parsed = urlsplit(normalized)
        if parsed.username is not None or parsed.password is not None:
            raise AppServerTargetConfigError(
                "App Server URLs must not contain userinfo credentials; use "
                "an explicit auth token or token file"
            )
        if parsed.query or parsed.fragment:
            raise AppServerTargetConfigError(
                "App Server URLs must not contain query or fragment credentials; use "
                "an explicit auth token or token file"
            )
        return AppServerTarget(
            endpoint=normalized,
            ownership="external",
            transport="unix-websocket",
        )
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() in {"ws", "wss"} and parsed.netloc:
        if parsed.username is not None or parsed.password is not None:
            raise AppServerTargetConfigError(
                "App Server URLs must not contain userinfo credentials; use "
                "an explicit auth token or token file"
            )
        if parsed.query or parsed.fragment:
            raise AppServerTargetConfigError(
                "App Server URLs must not contain query or fragment credentials; use "
                "an explicit auth token or token file"
            )
        return AppServerTarget(
            endpoint=normalized,
            ownership="external",
            transport="tcp-websocket",
        )
    raise AppServerTargetConfigError(
        "App Server endpoint must use unix://, ws://, wss://, or stdio://"
    )


def resolve_app_server_target(
    *,
    app_server_url: str | None = None,
    os_name: str | None = None,
) -> AppServerTarget:
    endpoint = str(app_server_url or "").strip()
    return parse_app_server_target(endpoint or default_app_server_endpoint(os_name=os_name))

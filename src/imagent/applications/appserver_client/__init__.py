from __future__ import annotations

from pathlib import Path

from ... import __version__
from ..adapters.appserver.transport import AppServerError
from .client import AppServerClient
from .handoff import (
    APP_SERVER_DISPATCH_POSITION_KEY,
    AppServerDispatchPosition,
    AppServerResponse,
)
from .supervisor import AppServerSupervisor


def codex_app_server_client(
    *,
    codex_bin: str = "codex",
    endpoint: str | None = None,
    auth_token: str | None = None,
    auth_token_file: str | Path | None = None,
    experimental_api_enabled: bool = False,
) -> AppServerClient:
    """Build the SDK-owned Codex App Server client and transport supervisor."""

    supervisor = AppServerSupervisor(
        codex_bin=codex_bin,
        app_server_url=endpoint,
        app_server_auth_token=auth_token,
        app_server_auth_token_file=auth_token_file,
    )
    return AppServerClient(
        supervisor=supervisor,
        client_info={
            "name": "im-agent-sdk",
            "title": "IM Agent SDK",
            "version": __version__,
        },
        experimental_api_enabled=experimental_api_enabled,
    )


__all__ = [
    "AppServerClient",
    "AppServerDispatchPosition",
    "AppServerError",
    "AppServerResponse",
    "AppServerSupervisor",
    "APP_SERVER_DISPATCH_POSITION_KEY",
    "codex_app_server_client",
]

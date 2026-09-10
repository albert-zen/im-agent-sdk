from __future__ import annotations

from pathlib import Path

from ..... import __version__
from .._errors import AppServerError
from ..transport import _DEFAULT_MAX_INBOUND_FRAME_BYTES
from ._client import AppServerClient
from ._handoff import (
    APP_SERVER_DISPATCH_POSITION_KEY,
    AppServerDispatchPosition,
    AppServerResponse,
)
from ._supervisor import AppServerSupervisor


def codex_app_server_client(
    *,
    codex_bin: str = "codex",
    endpoint: str | None = None,
    auth_token: str | None = None,
    auth_token_file: str | Path | None = None,
    experimental_api_enabled: bool = False,
    max_inbound_frame_bytes: int = _DEFAULT_MAX_INBOUND_FRAME_BYTES,
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
        max_inbound_frame_bytes=max_inbound_frame_bytes,
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

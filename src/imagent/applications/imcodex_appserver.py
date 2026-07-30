from __future__ import annotations

from importlib import import_module
from pathlib import Path


def imcodex_app_server_client(
    *,
    codex_bin: str = "codex",
    endpoint: str | None = None,
    auth_token: str | None = None,
    auth_token_file: str | Path | None = None,
    experimental_api_enabled: bool = False,
):
    """Build the proven IMCodex Codex App Server client and supervisor."""

    try:
        appserver = import_module("imcodex.appserver")
    except ImportError as error:
        raise RuntimeError(
            "Install im-agent-sdk[imcodex] to use the Codex App Server client"
        ) from error
    supervisor = appserver.AppServerSupervisor(
        codex_bin=codex_bin,
        app_server_url=endpoint,
        app_server_auth_token=auth_token,
        app_server_auth_token_file=auth_token_file,
    )
    return appserver.AppServerClient(
        supervisor=supervisor,
        client_info={
            "name": "im-agent-sdk",
            "title": "IM Agent SDK",
            "version": "0.1.0",
        },
        experimental_api_enabled=experimental_api_enabled,
    )

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ..presentation.artifact_materialization import (
    AppServerArtifactMaterializationLimits,
    AppServerArtifactMaterializer,
)
from .appserver._base import AppServerClient, _AppServerApplicationAdapter
from .appserver.requests import map_zen_appserver_request

__all__ = ["ZenApplicationAdapter"]


class ZenApplicationAdapter(_AppServerApplicationAdapter):
    def __init__(
        self,
        *,
        application_instance_id: str,
        client: AppServerClient,
        workspace_id: str,
        cwd: str,
        shared_filesystem_root: str | Path | None = None,
        event_buffer_max_pending: int = 1024,
        thread_start_options: Mapping[str, object] | None = None,
        artifact_materializer: AppServerArtifactMaterializer | None = None,
        artifact_materialization_limits: AppServerArtifactMaterializationLimits = (
            AppServerArtifactMaterializationLimits()
        ),
    ) -> None:
        super().__init__(
            application_instance_id=application_instance_id,
            kind="zen",
            display_name="Zen",
            client=client,
            workspace_id=workspace_id,
            cwd=cwd,
            shared_filesystem_root=shared_filesystem_root,
            server_request_mapper=map_zen_appserver_request,
            event_buffer_max_pending=event_buffer_max_pending,
            thread_start_options=thread_start_options,
            artifact_materializer=artifact_materializer,
            artifact_materialization_limits=artifact_materialization_limits,
        )

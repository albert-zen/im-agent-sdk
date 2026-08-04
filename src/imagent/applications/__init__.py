from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .appserver import CodexApplicationAdapter, ZenApplicationAdapter
    from .appserver_artifacts import (
        ApplicationArtifactMaterialization,
        ApplicationArtifactMaterializationCancelled,
        ApplicationArtifactMaterializationCapacityError,
        ApplicationArtifactMaterializationError,
        ApplicationArtifactMaterializationFailed,
        ApplicationArtifactMaterializationTimeout,
        AppServerArtifactCandidate,
        AppServerArtifactMaterializationLimits,
        AppServerArtifactMaterializer,
        AppServerArtifactSourceKind,
        AppServerCompletedItemFacts,
        AppServerCompletedItemKind,
        AppServerCompletedItemPhase,
        AppServerTurnTerminalFacts,
        AppServerTurnTerminalStatus,
    )
    from .appserver_client import codex_app_server_client
    from .presentation import (
        ApplicationPresentationCancelled,
        ApplicationPresentationCapacityError,
        ApplicationPresentationError,
        ApplicationPresentationFailed,
        ApplicationPresentationLimits,
        ApplicationPresentationTimeout,
        ApplicationTextPresentation,
        CodexLiveActivityFacts,
        CodexLiveActivityKind,
        CodexLiveActivityMethod,
        CodexLiveActivityPresenter,
        CodexPlanStep,
        T3ActivityFacts,
        T3ActivityPresenter,
    )
    from .t3 import T3ApplicationAdapter
    from .t3_client import HttpT3Client, T3ClientError

__all__ = [
    "AppServerArtifactCandidate",
    "AppServerArtifactMaterializationLimits",
    "AppServerArtifactMaterializer",
    "AppServerArtifactSourceKind",
    "AppServerCompletedItemFacts",
    "AppServerCompletedItemKind",
    "AppServerCompletedItemPhase",
    "AppServerTurnTerminalFacts",
    "AppServerTurnTerminalStatus",
    "ApplicationArtifactMaterialization",
    "ApplicationArtifactMaterializationCancelled",
    "ApplicationArtifactMaterializationCapacityError",
    "ApplicationArtifactMaterializationError",
    "ApplicationArtifactMaterializationFailed",
    "ApplicationArtifactMaterializationTimeout",
    "ApplicationPresentationCapacityError",
    "ApplicationPresentationCancelled",
    "ApplicationPresentationError",
    "ApplicationPresentationFailed",
    "ApplicationPresentationLimits",
    "ApplicationPresentationTimeout",
    "ApplicationTextPresentation",
    "CodexApplicationAdapter",
    "CodexLiveActivityFacts",
    "CodexLiveActivityKind",
    "CodexLiveActivityMethod",
    "CodexLiveActivityPresenter",
    "CodexPlanStep",
    "HttpT3Client",
    "T3ApplicationAdapter",
    "T3ActivityFacts",
    "T3ActivityPresenter",
    "T3ClientError",
    "ZenApplicationAdapter",
    "codex_app_server_client",
]

_APPSERVER_EXPORTS = frozenset({"CodexApplicationAdapter", "ZenApplicationAdapter"})
_ARTIFACT_EXPORTS = frozenset(
    {
        "AppServerArtifactCandidate",
        "AppServerArtifactMaterializationLimits",
        "AppServerArtifactMaterializer",
        "AppServerArtifactSourceKind",
        "AppServerCompletedItemFacts",
        "AppServerCompletedItemKind",
        "AppServerCompletedItemPhase",
        "AppServerTurnTerminalFacts",
        "AppServerTurnTerminalStatus",
        "ApplicationArtifactMaterialization",
        "ApplicationArtifactMaterializationCancelled",
        "ApplicationArtifactMaterializationCapacityError",
        "ApplicationArtifactMaterializationError",
        "ApplicationArtifactMaterializationFailed",
        "ApplicationArtifactMaterializationTimeout",
    }
)
_PRESENTATION_EXPORTS = frozenset(
    {
        "ApplicationPresentationCapacityError",
        "ApplicationPresentationCancelled",
        "ApplicationPresentationError",
        "ApplicationPresentationFailed",
        "ApplicationPresentationLimits",
        "ApplicationPresentationTimeout",
        "ApplicationTextPresentation",
        "CodexLiveActivityFacts",
        "CodexLiveActivityKind",
        "CodexLiveActivityMethod",
        "CodexLiveActivityPresenter",
        "CodexPlanStep",
        "T3ActivityFacts",
        "T3ActivityPresenter",
    }
)
_T3_EXPORTS = frozenset({"T3ApplicationAdapter"})
_T3_CLIENT_EXPORTS = frozenset({"HttpT3Client", "T3ClientError"})


def __getattr__(name: str) -> object:
    if name in _APPSERVER_EXPORTS:
        module = import_module("imagent.applications.appserver")
    elif name in _ARTIFACT_EXPORTS:
        module = import_module("imagent.applications.appserver_artifacts")
    elif name in _PRESENTATION_EXPORTS:
        module = import_module("imagent.applications.presentation")
    elif name in _T3_EXPORTS:
        module = import_module("imagent.applications.t3")
    elif name in _T3_CLIENT_EXPORTS:
        module = import_module("imagent.applications.t3_client")
    elif name == "codex_app_server_client":
        module = import_module("imagent.applications.appserver_client")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value

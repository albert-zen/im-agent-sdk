from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from .contract import (
    AcceptedTurn,
    AgentApplicationAdapter,
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    ApplicationInputDispatchHandler,
    ApplicationRef,
    ApplicationSummary,
    InputContinuationPreference,
    InputDisposition,
    Page,
    ProjectRef,
    ProjectSummary,
    ThreadHistory,
    ThreadRef,
    ThreadSnapshot,
    ThreadStatus,
    ThreadSummary,
    TurnCatchup,
    TurnHistoryEntry,
    TurnReplyCorrelationPolicy,
    TurnStatus,
    validate_thread_ref,
)

if TYPE_CHECKING:
    from .adapters.appserver.client import codex_app_server_client
    from .adapters.codex import CodexApplicationAdapter
    from .adapters.t3 import HttpT3Client, T3ApplicationAdapter, T3ClientError
    from .adapters.zen import ZenApplicationAdapter
    from .presentation import (
        ApplicationArtifactMaterialization,
        ApplicationArtifactMaterializationCancelled,
        ApplicationArtifactMaterializationCapacityError,
        ApplicationArtifactMaterializationError,
        ApplicationArtifactMaterializationFailed,
        ApplicationArtifactMaterializationTimeout,
        ApplicationPresentationCancelled,
        ApplicationPresentationCapacityError,
        ApplicationPresentationError,
        ApplicationPresentationFailed,
        ApplicationPresentationLimits,
        ApplicationPresentationTimeout,
        ApplicationTextPresentation,
        AppServerArtifactCandidate,
        AppServerArtifactMaterializationLimits,
        AppServerArtifactMaterializer,
        AppServerArtifactSourceKind,
        AppServerCompletedItemFacts,
        AppServerCompletedItemKind,
        AppServerCompletedItemPhase,
        AppServerTurnTerminalFacts,
        AppServerTurnTerminalStatus,
        CodexLiveActivityFacts,
        CodexLiveActivityKind,
        CodexLiveActivityMethod,
        CodexLiveActivityPresenter,
        CodexPlanStep,
        T3ActivityFacts,
        T3ActivityPresenter,
    )

__all__ = [
    "AcceptedTurn",
    "AgentApplicationAdapter",
    "AgentInput",
    "AgentMessage",
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
    "ApplicationInputDispatch",
    "ApplicationInputDispatchHandler",
    "ApplicationRef",
    "ApplicationSummary",
    "ApplicationTextPresentation",
    "CodexApplicationAdapter",
    "CodexLiveActivityFacts",
    "CodexLiveActivityKind",
    "CodexLiveActivityMethod",
    "CodexLiveActivityPresenter",
    "CodexPlanStep",
    "HttpT3Client",
    "InputContinuationPreference",
    "InputDisposition",
    "Page",
    "ProjectRef",
    "ProjectSummary",
    "ThreadHistory",
    "ThreadRef",
    "ThreadSnapshot",
    "ThreadStatus",
    "ThreadSummary",
    "TurnCatchup",
    "TurnHistoryEntry",
    "TurnReplyCorrelationPolicy",
    "TurnStatus",
    "T3ApplicationAdapter",
    "T3ActivityFacts",
    "T3ActivityPresenter",
    "T3ClientError",
    "ZenApplicationAdapter",
    "codex_app_server_client",
    "validate_thread_ref",
]

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
    if name == "CodexApplicationAdapter":
        module = import_module("imagent.applications.adapters.codex")
    elif name == "ZenApplicationAdapter":
        module = import_module("imagent.applications.adapters.zen")
    elif name in _ARTIFACT_EXPORTS:
        module = import_module("imagent.applications.presentation")
    elif name in _PRESENTATION_EXPORTS:
        module = import_module("imagent.applications.presentation")
    elif name in _T3_EXPORTS:
        module = import_module("imagent.applications.adapters.t3")
    elif name in _T3_CLIENT_EXPORTS:
        module = import_module("imagent.applications.adapters.t3")
    elif name == "codex_app_server_client":
        module = import_module("imagent.applications.adapters.appserver.client")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value

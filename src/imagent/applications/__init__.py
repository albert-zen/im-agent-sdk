from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from .contract import (
    MAX_WORKSPACE_ROOT_LENGTH,
    AcceptedTurn,
    AgentApplicationAdapter,
    AgentInput,
    AgentMessage,
    ApplicationInputDispatch,
    ApplicationInputDispatchHandler,
    ApplicationInputOutcomeUnknown,
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
    TurnRef,
    TurnReplyCorrelationPolicy,
    TurnStatus,
    WorkspaceIdentity,
    fingerprint_canonical_workspace_root,
    validate_agent_message,
    validate_application_summary,
    validate_project_ref,
    validate_project_summary,
    validate_thread_history,
    validate_thread_ref,
    validate_thread_summary,
    validate_turn_catchup,
    validate_turn_history_entry,
    validate_turn_ref,
    validate_workspace_identity,
)

if TYPE_CHECKING:
    from .adapters.appserver.client import codex_app_server_client
    from .adapters.codex import CodexApplicationAdapter
    from .adapters.deepseek_harness import (
        DeepSeekHarnessApplicationAdapter,
        HttpDeepSeekHarnessClient,
    )
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
    "ApplicationInputOutcomeUnknown",
    "ApplicationRef",
    "ApplicationSummary",
    "ApplicationTextPresentation",
    "CodexApplicationAdapter",
    "CodexLiveActivityFacts",
    "CodexLiveActivityKind",
    "CodexLiveActivityMethod",
    "CodexLiveActivityPresenter",
    "CodexPlanStep",
    "DeepSeekHarnessApplicationAdapter",
    "HttpDeepSeekHarnessClient",
    "HttpT3Client",
    "InputContinuationPreference",
    "InputDisposition",
    "MAX_WORKSPACE_ROOT_LENGTH",
    "Page",
    "ProjectRef",
    "ProjectSummary",
    "ThreadHistory",
    "ThreadRef",
    "ThreadSnapshot",
    "ThreadStatus",
    "ThreadSummary",
    "TurnRef",
    "TurnCatchup",
    "TurnHistoryEntry",
    "TurnReplyCorrelationPolicy",
    "TurnStatus",
    "WorkspaceIdentity",
    "T3ApplicationAdapter",
    "T3ActivityFacts",
    "T3ActivityPresenter",
    "T3ClientError",
    "ZenApplicationAdapter",
    "codex_app_server_client",
    "fingerprint_canonical_workspace_root",
    "validate_agent_message",
    "validate_application_summary",
    "validate_project_ref",
    "validate_project_summary",
    "validate_thread_ref",
    "validate_thread_history",
    "validate_thread_summary",
    "validate_turn_catchup",
    "validate_turn_history_entry",
    "validate_turn_ref",
    "validate_workspace_identity",
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
_DSH_EXPORTS = frozenset({"DeepSeekHarnessApplicationAdapter"})
_DSH_CLIENT_EXPORTS = frozenset({"HttpDeepSeekHarnessClient"})


def __getattr__(name: str) -> object:
    if name == "CodexApplicationAdapter":
        module = import_module("imagent.applications.adapters.codex")
    elif name == "ZenApplicationAdapter":
        module = import_module("imagent.applications.adapters.zen")
    elif name in _DSH_EXPORTS | _DSH_CLIENT_EXPORTS:
        module = import_module("imagent.applications.adapters.deepseek_harness")
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

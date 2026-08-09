from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from imagent.applications.capabilities import (
    ProjectMode,
    SupportLevel,
    ThreadDeletionCapability,
    validate_application_capabilities,
)
from imagent.applications.contract import (
    AgentApplicationAdapter,
    AgentInput,
    ApplicationInputDispatch,
    InputContinuationPreference,
    InputDisposition,
    TurnReplyCorrelationPolicy,
    validate_application_summary,
    validate_thread_ref,
)
from imagent.applications.events import AgentEvent, AgentEventType, validate_agent_event
from imagent.applications.operations import (
    ActivateNativeThread,
    ApplicationOperationFailed,
    CreateProject,
    CreateThread,
    DeleteThread,
    GetProject,
    GetThread,
    GetThreadHistory,
    GetThreadStatus,
    GetTurnCatchup,
    ListProjects,
    ListThreads,
    NativeThreadActivated,
    ProjectCreated,
    ProjectRead,
    ProjectsListed,
    ThreadCreated,
    ThreadDeleted,
    ThreadDeletionMode,
    ThreadHistoryRead,
    ThreadRead,
    ThreadsListed,
    ThreadStatusRead,
    TurnCatchupRead,
    validate_application_operation_result,
)
from imagent.interaction.channels.contract import ChannelAdapter, DeliverySupportLevel
from imagent.interaction.messages import InboundMessage, OutboundMessage, TextContent


@dataclass(frozen=True, slots=True)
class ContractCheck:
    name: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ContractReport:
    checks: tuple[ContractCheck, ...]

    @property
    def check_names(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks)


async def verify_channel_adapter(
    adapter: ChannelAdapter,
    sample_message: InboundMessage | None = None,
) -> ContractReport:
    checks: list[ContractCheck] = []
    first_identity = adapter.channel_instance_id
    second_identity = adapter.channel_instance_id
    if not first_identity or first_identity != second_identity:
        raise AssertionError("Channel adapter identity must be stable and non-empty")
    checks.append(ContractCheck("stable channel identity"))

    capabilities = adapter.capabilities
    profile = capabilities.delivery
    if profile.plain_text is DeliverySupportLevel.UNSUPPORTED:
        raise AssertionError("Channel adapter must support plain text natively or by fallback")
    if profile.max_text_length is not None and profile.max_text_length < 1:
        raise AssertionError("max_text_length must be positive")
    if profile.max_attachment_count is not None and profile.max_attachment_count < 1:
        raise AssertionError("max_attachment_count must be positive")
    if profile.max_attachment_size is not None and profile.max_attachment_size < 1:
        raise AssertionError("max_attachment_size must be positive")
    checks.append(ContractCheck("valid channel capabilities"))

    received_messages = []

    async def on_message(message):
        received_messages.append(message)

    async def on_admission(_conversation_ref, _message_id):
        return None

    await adapter.start(on_message, on_admission)
    await adapter.stop()
    checks.append(ContractCheck("start and stop lifecycle"))
    checks.append(ContractCheck("admission callback accepted at startup"))

    if sample_message is not None:
        outbound = OutboundMessage(
            delivery_id=f"contract:{sample_message.message_id}",
            conversation_ref=sample_message.conversation_ref,
            content=sample_message.content,
            created_at=sample_message.created_at,
            reply_to=sample_message.message_id,
        )
        receipt = await adapter.send(outbound)
        if receipt.status not in {
            "accepted_by_platform",
            "rejected_by_platform",
            "retryable_failure",
            "unknown",
        }:
            raise AssertionError("delivery receipt has an unknown status")
        checks.append(ContractCheck("delivery receipt semantics"))

    return ContractReport(tuple(checks))


async def verify_application_adapter(
    adapter: AgentApplicationAdapter,
    *,
    title: str = "IM Agent contract test",
) -> ContractReport:
    checks: list[ContractCheck] = []
    summary = adapter.summary
    capabilities = summary.capabilities
    validate_application_capabilities(capabilities)
    validate_application_summary(summary)
    checks.append(ContractCheck("valid application capabilities"))
    await adapter.start()
    checks.append(ContractCheck("application start lifecycle"))

    list_projects = ListProjects(
        operation_id="contract:project.list",
        application_ref=summary.ref,
        created_at=_now(),
    )
    projects_result = await adapter.execute(list_projects)
    validate_application_operation_result(list_projects, projects_result)
    projects = _require_result(projects_result, ProjectsListed).projects
    if not projects.items:
        raise AssertionError("application must expose at least one Project")
    if capabilities.projects.mode in {ProjectMode.FIXED, ProjectMode.FLAT}:
        if len(projects.items) != 1 or summary.workspace_identity is None:
            raise AssertionError("fixed/flat application must expose one workspace Project")
        if projects.items[0].ref != summary.workspace_identity.project_ref:
            raise AssertionError("workspace Project identity disagrees with application summary")
        if (
            projects.items[0].workspace_root_fingerprint
            != summary.workspace_identity.root_fingerprint
        ):
            raise AssertionError("workspace Project fingerprint disagrees with application summary")

    get_project = GetProject(
        operation_id="contract:project.get",
        application_ref=summary.ref,
        project_ref=projects.items[0].ref,
        created_at=_now(),
    )
    project_result = await adapter.execute(get_project)
    validate_application_operation_result(get_project, project_result)
    project_ref = _require_result(project_result, ProjectRead).project.ref
    checks.append(ContractCheck("project list and read"))

    if capabilities.projects.creation is SupportLevel.NATIVE:
        create_project = CreateProject(
            operation_id="contract:project.create",
            application_ref=summary.ref,
            cwd="/contract/created-project",
            display_name="Created Contract Project",
            created_at=_now(),
        )
        created_project_result = await adapter.execute(create_project)
        validate_application_operation_result(create_project, created_project_result)
        created_project = _require_result(created_project_result, ProjectCreated).project
        visible_result = await adapter.execute(
            ListProjects(
                operation_id="contract:project.list:created",
                application_ref=summary.ref,
                created_at=_now(),
            )
        )
        visible = _require_result(visible_result, ProjectsListed).projects.items
        if created_project.ref not in {project.ref for project in visible}:
            raise AssertionError("created Project is absent from authoritative listing")
        project_ref = created_project.ref
        checks.append(ContractCheck("declared project creation"))
    else:
        unsupported_create = CreateProject(
            operation_id="contract:project.create:unsupported",
            application_ref=summary.ref,
            cwd="/contract/unsupported-project",
            created_at=_now(),
        )
        unsupported_result = await adapter.execute(unsupported_create)
        validate_application_operation_result(unsupported_create, unsupported_result)
        if not isinstance(unsupported_result, ApplicationOperationFailed):
            raise AssertionError("unsupported project creation must fail explicitly")
        if unsupported_result.error.code != "unsupported":
            raise AssertionError("unsupported project creation returned the wrong error code")
        checks.append(ContractCheck("unsupported project creation is explicit"))

    list_before = ListThreads(
        operation_id="contract:thread.list:before",
        application_ref=summary.ref,
        project_ref=project_ref,
        created_at=_now(),
    )
    before_result = await adapter.execute(list_before)
    validate_application_operation_result(list_before, before_result)
    before = _require_result(before_result, ThreadsListed).threads

    create = CreateThread(
        operation_id="contract:thread.create",
        application_ref=summary.ref,
        project_ref=project_ref,
        title=title,
        created_at=_now(),
    )
    create_result = await adapter.execute(create)
    validate_application_operation_result(create, create_result)
    created = _require_result(create_result, ThreadCreated).thread
    validate_thread_ref(created.ref)
    if created.ref.project_ref != project_ref:
        raise AssertionError("created thread project scope differs from requested project")

    get_thread = GetThread(
        operation_id="contract:thread.get",
        application_ref=summary.ref,
        thread_ref=created.ref,
        created_at=_now(),
    )
    read_result = await adapter.execute(get_thread)
    validate_application_operation_result(get_thread, read_result)
    read = _require_result(read_result, ThreadRead).thread
    if read.ref != created.ref:
        raise AssertionError("created thread cannot be read by the same reference")

    list_after = ListThreads(
        operation_id="contract:thread.list:after",
        application_ref=summary.ref,
        project_ref=project_ref,
        created_at=_now(),
    )
    after_result = await adapter.execute(list_after)
    validate_application_operation_result(list_after, after_result)
    after = _require_result(after_result, ThreadsListed).threads
    if created.ref not in {item.ref for item in after.items}:
        raise AssertionError("created thread is absent from thread listing")
    if len(after.items) < len(before.items) + 1:
        raise AssertionError("thread listing did not grow after creation")
    checks.append(ContractCheck("thread create, read, and list round-trip"))

    status_operation = GetThreadStatus(
        operation_id="contract:thread.status",
        application_ref=summary.ref,
        thread_ref=created.ref,
        created_at=_now(),
    )
    status_result = await adapter.execute(status_operation)
    validate_application_operation_result(status_operation, status_result)
    status = _require_result(status_result, ThreadStatusRead).thread_status
    if status != created.status:
        raise AssertionError("created thread and native status disagree")
    checks.append(ContractCheck("thread status"))

    if capabilities.runtime.native_thread_activation is not SupportLevel.UNSUPPORTED:
        activate = ActivateNativeThread(
            operation_id="contract:thread.activate_native",
            application_ref=summary.ref,
            thread_ref=created.ref,
            created_at=_now(),
        )
        activate_result = await adapter.execute(activate)
        validate_application_operation_result(activate, activate_result)
        _require_result(activate_result, NativeThreadActivated)
        checks.append(ContractCheck("explicit native thread activation"))

    # The reusable kit proves only that an adapter preserves a stable,
    # non-empty bounded input identity. Derivation is Gateway input-dispatch
    # behavior and must not make this lower-layer test kit import Gateway.
    client_message_id = "contract-message-1"
    first_events = adapter.subscribe_thread(created.ref)
    second_events = adapter.subscribe_thread(created.ref)
    dispatches: list[ApplicationInputDispatch] = []

    async def record_dispatch(dispatch: ApplicationInputDispatch) -> None:
        dispatches.append(dispatch)

    accepted = await adapter.send_input(
        created.ref,
        AgentInput(
            client_message_id=client_message_id,
            content=(TextContent("contract message"),),
        ),
        continuation=InputContinuationPreference.PREFER_ACTIVE_TURN,
        before_dispatch=record_dispatch,
    )
    if accepted.turn_ref.thread_ref != created.ref:
        raise AssertionError("accepted turn belongs to a different thread")
    if accepted.client_message_id != client_message_id:
        raise AssertionError("client message ID was not preserved")
    if len(dispatches) != 1:
        raise AssertionError("input dispatch hook was not called exactly once")
    dispatch = dispatches[0]
    if dispatch.thread_ref != created.ref or dispatch.client_message_id != client_message_id:
        raise AssertionError("input dispatch identity did not match the input")
    if dispatch.disposition is not accepted.disposition:
        raise AssertionError("accepted input disposition changed after dispatch")
    if dispatch.correlation_policy is not accepted.correlation_policy:
        raise AssertionError("accepted correlation policy changed after dispatch")
    if accepted.disposition not in {InputDisposition.STARTED, InputDisposition.STEERED}:
        raise AssertionError("adapter returned an unknown input disposition")
    if accepted.disposition is InputDisposition.STARTED:
        if accepted.correlation_policy is not TurnReplyCorrelationPolicy.CREATE_NEW:
            raise AssertionError("started input did not create a new correlation")
        if dispatch.expected_turn_ref is not None:
            raise AssertionError("started input declared an expected active Turn")
    else:
        if accepted.correlation_policy is not TurnReplyCorrelationPolicy.PRESERVE_EXISTING:
            raise AssertionError("steered input did not preserve its existing correlation")
        if dispatch.expected_turn_ref is None:
            raise AssertionError("steered input omitted its expected active Turn")
        if accepted.turn_ref != dispatch.expected_turn_ref:
            raise AssertionError("steered input accepted a different Turn than it declared")
    checks.append(ContractCheck("stable client message ID round-trip"))
    checks.append(ContractCheck("truthful input dispatch result"))

    first_observation, second_observation = await asyncio.wait_for(
        asyncio.gather(
            _collect_turn_events(first_events, accepted.turn_ref),
            _collect_turn_events(second_events, accepted.turn_ref),
        ),
        timeout=2,
    )
    first_ids = tuple(event.event_id for event in first_observation)
    second_ids = tuple(event.event_id for event in second_observation)
    for event in first_observation:
        validate_agent_event(event, capabilities)
    if first_ids != second_ids:
        raise AssertionError("Thread subscribers did not receive the same canonical events")
    if not any(event.type is AgentEventType.MESSAGE_COMPLETED for event in first_observation):
        raise AssertionError("Turn stream did not contain a completed Agent message")
    if first_observation[-1].type not in {
        AgentEventType.TURN_COMPLETED,
        AgentEventType.TURN_FAILED,
        AgentEventType.TURN_INTERRUPTED,
    }:
        raise AssertionError("Turn stream did not end with an explicit terminal event")
    checks.append(ContractCheck("fan-out Turn event lifecycle"))

    if capabilities.runtime.history is not SupportLevel.UNSUPPORTED:
        catchup_operation = GetTurnCatchup(
            operation_id="contract:turn.catchup",
            application_ref=summary.ref,
            thread_ref=created.ref,
            limit=5,
            created_at=_now(),
        )
        catchup_result = await adapter.execute(catchup_operation)
        validate_application_operation_result(catchup_operation, catchup_result)
        catchup = _require_result(catchup_result, TurnCatchupRead).catchup

        history_operation = GetThreadHistory(
            operation_id="contract:thread.history",
            application_ref=summary.ref,
            thread_ref=created.ref,
            limit=3,
            page=1,
            created_at=_now(),
        )
        history_result = await adapter.execute(history_operation)
        validate_application_operation_result(history_operation, history_result)
        history = _require_result(history_result, ThreadHistoryRead).history
        if catchup.thread_ref != created.ref or history.thread_ref != created.ref:
            raise AssertionError("history result belongs to a different thread")
        if catchup.turn_ref is not None and catchup.turn_ref.thread_ref != created.ref:
            raise AssertionError("catch-up Turn belongs to a different thread")
        if any(turn.turn_ref.thread_ref != created.ref for turn in history.turns):
            raise AssertionError("history Turn belongs to a different thread")
        checks.append(ContractCheck("catch-up and history result scoping"))

    deletion = capabilities.threads.deletion
    if deletion is not ThreadDeletionCapability.UNSUPPORTED:
        delete = DeleteThread(
            operation_id="contract:thread.delete",
            application_ref=summary.ref,
            thread_ref=created.ref,
            mode=ThreadDeletionMode(deletion.value),
            created_at=_now(),
        )
        delete_result = await adapter.execute(delete)
        validate_application_operation_result(delete, delete_result)
        deleted = _require_result(delete_result, ThreadDeleted)
        if deleted.mode.value != deletion.value:
            raise AssertionError("adapter did not report its actual deletion mode")

        list_deleted = ListThreads(
            operation_id="contract:thread.list:deleted",
            application_ref=summary.ref,
            project_ref=project_ref,
            created_at=_now(),
        )
        deleted_result = await adapter.execute(list_deleted)
        validate_application_operation_result(list_deleted, deleted_result)
        deleted_listing = _require_result(deleted_result, ThreadsListed).threads
        if created.ref in {item.ref for item in deleted_listing.items}:
            raise AssertionError("deleted thread remains in thread listing")
        checks.append(ContractCheck("declared thread deletion"))

    await adapter.stop()
    checks.append(ContractCheck("application stop lifecycle"))
    return ContractReport(tuple(checks))


def _require_result(result, expected_type):
    if isinstance(result, ApplicationOperationFailed):
        raise AssertionError(f"operation failed: {result.error}")
    if not isinstance(result, expected_type):
        raise AssertionError(f"expected {expected_type}, got {type(result)}")
    return result


def _now() -> datetime:
    return datetime.now(UTC)


async def _collect_turn_events(
    events: AsyncIterator[AgentEvent],
    turn_ref,
) -> tuple[AgentEvent, ...]:
    observed: list[AgentEvent] = []
    try:
        async for event in events:
            if event.turn_ref not in {None, turn_ref}:
                continue
            observed.append(event)
            if event.type in {
                AgentEventType.TURN_COMPLETED,
                AgentEventType.TURN_FAILED,
                AgentEventType.TURN_INTERRUPTED,
            }:
                return tuple(observed)
    finally:
        close = getattr(events, "aclose", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
    raise AssertionError("Thread event stream ended before a terminal Turn event")


def sample_conversation():
    from imagent.interaction.messages import ConversationRef

    return ConversationRef(
        channel_instance_id="contract-channel",
        native_conversation_id="contract-conversation",
    )

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from imagent.adapters import AgentApplicationAdapter, ChannelAdapter
from imagent.contracts import (
    AgentInput,
    ChannelMessage,
    Operation,
    OperationResultStatus,
    OperationTarget,
    OperationType,
    Page,
    ProjectMode,
    ProjectSummary,
    SupportLevel,
    TextContent,
    ThreadDeletionCapability,
    ThreadStatus,
    ThreadSummary,
    derive_client_message_id,
    validate_application_capabilities,
    validate_thread_ref,
)


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
    sample_message: ChannelMessage | None = None,
) -> ContractReport:
    checks: list[ContractCheck] = []
    first_identity = adapter.channel_instance_id
    second_identity = adapter.channel_instance_id
    if not first_identity or first_identity != second_identity:
        raise AssertionError("Channel adapter identity must be stable and non-empty")
    checks.append(ContractCheck("stable channel identity"))

    capabilities = adapter.capabilities
    if capabilities.plain_text is SupportLevel.UNSUPPORTED:
        raise AssertionError("Channel adapter must support plain text natively or by fallback")
    if capabilities.max_text_length is not None and capabilities.max_text_length < 1:
        raise AssertionError("max_text_length must be positive")
    if capabilities.max_attachment_size is not None and capabilities.max_attachment_size < 1:
        raise AssertionError("max_attachment_size must be positive")
    checks.append(ContractCheck("valid channel capabilities"))

    received_messages = []
    received_operations = []

    async def on_message(message):
        received_messages.append(message)

    async def on_operation(operation):
        received_operations.append(operation)

    await adapter.start(on_message, on_operation)
    await adapter.stop()
    checks.append(ContractCheck("start and stop lifecycle"))

    if sample_message is not None:
        receipt = await adapter.send(sample_message)
        if receipt.status not in {
            "accepted_by_platform",
            "rejected_by_platform",
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
    checks.append(ContractCheck("valid application capabilities"))
    await adapter.start()
    checks.append(ContractCheck("application start lifecycle"))

    project_ref = None
    if capabilities.projects.mode is ProjectMode.MANAGED:
        projects_result = await adapter.execute(operation(OperationType.PROJECT_LIST, adapter))
        projects = succeeded_value(projects_result, Page)
        if not projects.items or not isinstance(projects.items[0], ProjectSummary):
            raise AssertionError("managed project adapter must expose a contract-test project")
        project_result = await adapter.execute(
            operation(
                OperationType.PROJECT_SELECT,
                adapter,
                project_ref=projects.items[0].ref,
            )
        )
        project = succeeded_value(project_result, ProjectSummary)
        project_ref = project.ref
        checks.append(ContractCheck("managed project list and read"))

    before = succeeded_value(
        await adapter.execute(
            operation(
                OperationType.THREAD_LIST,
                adapter,
                project_ref=project_ref,
            )
        ),
        Page,
    )
    created = succeeded_value(
        await adapter.execute(
            operation(
                OperationType.THREAD_CREATE,
                adapter,
                project_ref=project_ref,
                arguments={"title": title},
            )
        ),
        ThreadSummary,
    )
    validate_thread_ref(created.ref)
    if created.ref.project_ref != project_ref:
        raise AssertionError("created thread project scope differs from requested project")
    read = succeeded_value(
        await adapter.execute(
            operation(
                OperationType.THREAD_SWITCH,
                adapter,
                project_ref=project_ref,
                thread_ref=created.ref,
            )
        ),
        ThreadSummary,
    )
    if read.ref != created.ref:
        raise AssertionError("created thread cannot be read by the same reference")
    after = succeeded_value(
        await adapter.execute(
            operation(
                OperationType.THREAD_LIST,
                adapter,
                project_ref=project_ref,
            )
        ),
        Page,
    )
    if created.ref not in {item.ref for item in after.items}:
        raise AssertionError("created thread is absent from thread listing")
    if len(after.items) < len(before.items) + 1:
        raise AssertionError("thread listing did not grow after creation")
    checks.append(ContractCheck("thread create, read, and list round-trip"))

    status = succeeded_value(
        await adapter.execute(
            operation(
                OperationType.THREAD_STATUS,
                adapter,
                project_ref=project_ref,
                thread_ref=created.ref,
            )
        ),
        ThreadStatus,
    )
    if status != created.status:
        raise AssertionError("created thread and native status disagree")
    checks.append(ContractCheck("thread status"))

    client_message_id = derive_client_message_id(
        sample_conversation(),
        "contract-message-1",
    )
    accepted = await adapter.send_input(
        created.ref,
        AgentInput(
            client_message_id=client_message_id,
            content=(TextContent("contract message"),),
        ),
    )
    if accepted.thread_ref != created.ref:
        raise AssertionError("accepted turn belongs to a different thread")
    if accepted.client_message_id != client_message_id:
        raise AssertionError("client message ID was not preserved")
    checks.append(ContractCheck("stable client message ID round-trip"))

    if capabilities.threads.deletion is not ThreadDeletionCapability.UNSUPPORTED:
        succeeded_value(
            await adapter.execute(
                operation(
                    OperationType.THREAD_DELETE,
                    adapter,
                    project_ref=project_ref,
                    thread_ref=created.ref,
                )
            ),
            type(None),
        )
        deleted_listing = succeeded_value(
            await adapter.execute(
                operation(
                    OperationType.THREAD_LIST,
                    adapter,
                    project_ref=project_ref,
                )
            ),
            Page,
        )
        if created.ref in {item.ref for item in deleted_listing.items}:
            raise AssertionError("deleted thread remains in thread listing")
        checks.append(ContractCheck("declared thread deletion"))

    await adapter.stop()
    checks.append(ContractCheck("application stop lifecycle"))
    return ContractReport(tuple(checks))


def operation(
    operation_type: OperationType,
    adapter: AgentApplicationAdapter,
    *,
    project_ref=None,
    thread_ref=None,
    arguments=None,
) -> Operation:
    return Operation(
        operation_id=f"contract:{operation_type.value}",
        conversation_ref=sample_conversation(),
        actor="contract-user",
        type=operation_type,
        target=OperationTarget(
            application_ref=adapter.summary.ref,
            project_ref=project_ref,
            thread_ref=thread_ref,
        ),
        arguments=arguments or {},
        created_at=datetime.now(UTC),
    )


def succeeded_value(result, expected_type):
    if result.status is not OperationResultStatus.SUCCEEDED:
        raise AssertionError(f"operation failed: {result.error}")
    if not isinstance(result.value, expected_type):
        raise AssertionError(f"expected {expected_type}, got {type(result.value)}")
    return result.value


def sample_conversation():
    from imagent.contracts import ConversationRef

    return ConversationRef(
        channel_instance_id="contract-channel",
        native_conversation_id="contract-conversation",
    )

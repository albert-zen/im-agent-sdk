from __future__ import annotations

from dataclasses import dataclass

from imagent.adapters import AgentApplicationAdapter, ChannelAdapter
from imagent.contracts import (
    AgentInput,
    ChannelMessage,
    ProjectMode,
    SupportLevel,
    TextContent,
    ThreadDeletionCapability,
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
    capabilities = adapter.capabilities
    if summary.capabilities != capabilities:
        raise AssertionError("application summary and adapter capabilities differ")
    validate_application_capabilities(capabilities)
    checks.append(ContractCheck("valid application capabilities"))

    project_ref = None
    if capabilities.projects.mode is ProjectMode.MANAGED:
        projects = await adapter.list_projects()
        if not projects.items:
            raise AssertionError("managed project adapter must expose a contract-test project")
        project = await adapter.get_project(projects.items[0].ref)
        project_ref = project.ref
        checks.append(ContractCheck("managed project list and read"))

    before = await adapter.list_threads(project_ref)
    created = await adapter.create_thread(project_ref, title)
    validate_thread_ref(created.ref)
    if created.ref.project_ref != project_ref:
        raise AssertionError("created thread project scope differs from requested project")
    read = await adapter.get_thread(created.ref)
    if read.ref != created.ref:
        raise AssertionError("created thread cannot be read by the same reference")
    after = await adapter.list_threads(project_ref)
    if created.ref not in {item.ref for item in after.items}:
        raise AssertionError("created thread is absent from thread listing")
    if len(after.items) < len(before.items) + 1:
        raise AssertionError("thread listing did not grow after creation")
    checks.append(ContractCheck("thread create, read, and list round-trip"))

    snapshot = await adapter.read_thread(created.ref)
    if snapshot.thread.ref != created.ref:
        raise AssertionError("thread snapshot belongs to a different thread")
    status = await adapter.get_thread_status(created.ref)
    if status != snapshot.thread.status:
        raise AssertionError("thread status and snapshot status disagree")
    checks.append(ContractCheck("thread snapshot and status"))

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
        await adapter.delete_thread(created.ref)
        deleted_listing = await adapter.list_threads(project_ref)
        if created.ref in {item.ref for item in deleted_listing.items}:
            raise AssertionError("deleted thread remains in thread listing")
        checks.append(ContractCheck("declared thread deletion"))

    return ContractReport(tuple(checks))


def sample_conversation():
    from imagent.contracts import ConversationRef

    return ConversationRef(
        channel_instance_id="contract-channel",
        native_conversation_id="contract-conversation",
    )

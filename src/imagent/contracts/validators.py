from __future__ import annotations

import hashlib
import json

from .model import (
    ApplicationCapabilities,
    ConversationBinding,
    ConversationRef,
    Operation,
    OperationResult,
    OperationResultStatus,
    OperationType,
    ProjectMode,
    SupportLevel,
    ThreadRef,
)


class ContractViolation(ValueError):
    pass


def require_identifier(value: str, name: str) -> None:
    if not value or len(value) > 512:
        raise ContractViolation(f"{name} must be a non-empty string of at most 512 characters")


def validate_thread_ref(thread: ThreadRef) -> None:
    require_identifier(thread.application_instance_id, "application_instance_id")
    require_identifier(thread.native_thread_id, "native_thread_id")
    if (
        thread.project_ref is not None
        and thread.project_ref.application_instance_id != thread.application_instance_id
    ):
        raise ContractViolation("thread and project belong to different application instances")


def validate_application_capabilities(capabilities: ApplicationCapabilities) -> None:
    projects = capabilities.projects
    project_operations = (
        projects.discovery,
        projects.selection,
        projects.creation,
        projects.deletion,
    )
    if projects.mode is ProjectMode.MANAGED:
        if projects.discovery is SupportLevel.UNSUPPORTED:
            raise ContractViolation("managed project mode requires project discovery")
        if projects.selection is SupportLevel.UNSUPPORTED:
            raise ContractViolation("managed project mode requires project selection")
    elif any(level is not SupportLevel.UNSUPPORTED for level in project_operations):
        raise ContractViolation(
            f"{projects.mode.value} project mode cannot advertise project operations"
        )


def validate_binding(
    binding: ConversationBinding,
    capabilities: ApplicationCapabilities | None = None,
) -> None:
    require_identifier(binding.conversation_ref.channel_instance_id, "channel_instance_id")
    require_identifier(binding.conversation_ref.native_conversation_id, "native_conversation_id")
    if binding.revision < 0:
        raise ContractViolation("binding revision cannot be negative")

    application_id = (
        binding.application_ref.application_instance_id if binding.application_ref else None
    )
    if binding.project_ref is not None:
        if application_id != binding.project_ref.application_instance_id:
            raise ContractViolation("binding project belongs to a different application")
    if binding.thread_ref is not None:
        validate_thread_ref(binding.thread_ref)
        if application_id != binding.thread_ref.application_instance_id:
            raise ContractViolation("binding thread belongs to a different application")
    if (
        binding.project_ref is not None
        and binding.thread_ref is not None
        and binding.thread_ref.project_ref is not None
        and binding.project_ref != binding.thread_ref.project_ref
    ):
        raise ContractViolation("binding thread belongs to a different project")
    if (
        capabilities is not None
        and capabilities.projects.mode in (ProjectMode.FLAT, ProjectMode.FIXED)
        and binding.project_ref is not None
    ):
        raise ContractViolation(
            f"{capabilities.projects.mode.value} project mode cannot bind a project"
        )


def validate_operation(operation: Operation) -> None:
    require_identifier(operation.operation_id, "operation_id")
    require_identifier(operation.actor, "actor")
    target = operation.target
    requirements = {
        OperationType.APPLICATION_LIST: (),
        OperationType.PROJECT_LIST: ("application",),
        OperationType.PROJECT_SELECT: ("project",),
        OperationType.THREAD_CREATE: ("application",),
        OperationType.THREAD_LIST: ("application",),
        OperationType.THREAD_SWITCH: ("thread",),
        OperationType.THREAD_DELETE: ("thread",),
        OperationType.THREAD_STATUS: ("thread",),
        OperationType.THREAD_HISTORY: ("thread",),
        OperationType.TURN_CATCHUP: ("thread",),
        OperationType.TURN_INTERRUPT: ("thread",),
        OperationType.REQUEST_RESPOND: ("application",),
    }
    present = {
        "application": target.application_ref is not None,
        "project": target.project_ref is not None,
        "thread": target.thread_ref is not None,
    }
    for requirement in requirements[operation.type]:
        if not present[requirement]:
            raise ContractViolation(f"{operation.type.value} requires {requirement}_ref")
    if target.thread_ref is not None:
        validate_thread_ref(target.thread_ref)


def validate_operation_result(result: OperationResult) -> None:
    require_identifier(result.operation_id, "operation_id")
    if result.status is OperationResultStatus.SUCCEEDED and result.error is not None:
        raise ContractViolation("successful operation cannot contain an error")
    if result.status is OperationResultStatus.FAILED and result.error is None:
        raise ContractViolation("failed operation must contain an error")


def derive_client_message_id(
    conversation: ConversationRef,
    channel_message_id: str,
) -> str:
    require_identifier(channel_message_id, "channel_message_id")
    identity = json.dumps(
        [
            conversation.channel_instance_id,
            conversation.native_conversation_id,
            channel_message_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:client-message:sha256:{digest}"

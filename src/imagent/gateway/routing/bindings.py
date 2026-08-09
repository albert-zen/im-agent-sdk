"""Gateway-owned Conversation binding operation and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ForwardRef, TypeAlias

from ...applications.contract import ApplicationRef, ProjectRef, ThreadRef
from ...interaction.messages import ConversationRef
from ...interaction.operations import ContractViolation, require_identifier
from ..persistence.repository_contracts import BindingConflict, BindingRepository
from ..persistence.state_contracts import ConversationBinding
from .operations import (
    GatewayOperationType as _GatewayOperationType,
)
from .operations import (
    _complete_gateway_union as _complete_gateway_union,
)
from .operations import (
    _GatewayOperation,
    _GatewayOperationSucceeded,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _BindingChange:
    previous: ConversationBinding | None
    binding: ConversationBinding


@dataclass(frozen=True, slots=True, kw_only=True)
class _PreparedThreadBinding:
    previous: ConversationBinding | None
    desired: ConversationBinding
    expected_generation: int | None
    converge_without_write: bool


class _BindingRuntime:
    """Sole typed owner of Conversation binding repository mutation."""

    def __init__(self, repository: BindingRepository) -> None:
        self._repository = repository

    async def current(
        self,
        conversation_ref: ConversationRef,
    ) -> ConversationBinding | None:
        return await self._repository.get(conversation_ref)

    async def select_application(
        self,
        conversation_ref: ConversationRef,
        application_ref: ApplicationRef,
        *,
        expected_generation: int | None,
    ) -> _BindingChange:
        return await self._replace(
            ConversationBinding(
                conversation_ref=conversation_ref,
                application_ref=application_ref,
            ),
            expected_generation=expected_generation,
        )

    async def bind_project(
        self,
        conversation_ref: ConversationRef,
        application_ref: ApplicationRef,
        project_ref: ProjectRef,
        *,
        expected_generation: int | None,
    ) -> _BindingChange:
        return await self._replace(
            ConversationBinding(
                conversation_ref=conversation_ref,
                application_ref=application_ref,
                project_ref=project_ref,
            ),
            expected_generation=expected_generation,
        )

    async def prepare_thread_binding(
        self,
        conversation_ref: ConversationRef,
        application_ref: ApplicationRef,
        thread_ref: ThreadRef,
        *,
        expected_generation: int | None,
        converge_same_target: bool,
    ) -> _PreparedThreadBinding:
        previous = await self._repository.get(conversation_ref)
        desired = ConversationBinding(
            conversation_ref=conversation_ref,
            application_ref=application_ref,
            project_ref=thread_ref.project_ref,
            thread_ref=thread_ref,
        )
        converge_without_write = converge_same_target and _has_same_target(
            previous,
            desired,
        )
        if (
            converge_without_write
            and previous is not None
            and expected_generation not in {None, previous.generation, previous.generation - 1}
        ):
            raise BindingConflict(
                "same-target bind retry does not match the current "
                "or immediately preceding generation"
            )
        return _PreparedThreadBinding(
            previous=previous,
            desired=desired,
            expected_generation=expected_generation,
            converge_without_write=converge_without_write,
        )

    async def commit_thread_binding(
        self,
        prepared: _PreparedThreadBinding,
    ) -> _BindingChange:
        if prepared.converge_without_write:
            previous = prepared.previous
            if previous is None:
                raise RuntimeError("same-target binding convergence has no current binding")
            return _BindingChange(previous=previous, binding=previous)
        binding = await self._repository.put(
            prepared.desired,
            expected_generation=prepared.expected_generation,
        )
        return _BindingChange(previous=prepared.previous, binding=binding)

    async def binding_write_may_have_committed(
        self,
        prepared: _PreparedThreadBinding,
        error: BaseException,
    ) -> bool:
        """Verify an unknown write outcome, preserving uncertainty as a fence."""

        try:
            current = await self._repository.get(prepared.desired.conversation_ref)
        except BaseException as verification_error:
            error.add_note(
                "Binding outcome verification also failed; the prepared "
                f"route remains fenced: {verification_error!r}"
            )
            return True
        return _has_same_target(current, prepared.desired)

    async def clear_thread(
        self,
        conversation_ref: ConversationRef,
        *,
        expected_generation: int | None,
    ) -> _BindingChange:
        current = await self._repository.get(conversation_ref)
        if current is None:
            raise ValueError("Conversation has no binding")
        binding = await self._repository.put(
            ConversationBinding(
                conversation_ref=current.conversation_ref,
                application_ref=current.application_ref,
                project_ref=current.project_ref,
            ),
            expected_generation=expected_generation,
        )
        return _BindingChange(previous=current, binding=binding)

    async def _replace(
        self,
        desired: ConversationBinding,
        *,
        expected_generation: int | None,
    ) -> _BindingChange:
        previous = await self._repository.get(desired.conversation_ref)
        binding = await self._repository.put(
            desired,
            expected_generation=expected_generation,
        )
        return _BindingChange(previous=previous, binding=binding)


def _has_same_target(
    current: ConversationBinding | None,
    desired: ConversationBinding,
) -> bool:
    return (
        current is not None
        and current.application_ref == desired.application_ref
        and current.project_ref == desired.project_ref
        and current.thread_ref == desired.thread_ref
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class BindConversationToProject(_GatewayOperation):
    project_ref: ProjectRef
    expected_generation: int | None = None
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.CONVERSATION_BIND_PROJECT,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class BindConversationToThread(_GatewayOperation):
    thread_ref: ThreadRef
    expected_generation: int | None = None
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.CONVERSATION_BIND_THREAD,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ClearConversationThread(_GatewayOperation):
    expected_generation: int | None = None
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.CONVERSATION_CLEAR_THREAD,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationBound(_GatewayOperationSucceeded):
    type: _GatewayOperationType
    binding: ConversationBinding


ConversationBound.__annotations__["binding"] = ForwardRef(
    "ConversationBinding",
    module="imagent.gateway.persistence.state_contracts",
)

BindingOperation: TypeAlias = (
    BindConversationToProject | BindConversationToThread | ClearConversationThread
)


def _validate_binding_operation(operation: BindingOperation) -> None:
    """Validate the binding-specific fields after common Gateway checks."""

    if isinstance(operation, BindConversationToProject):
        require_identifier(operation.project_ref.application_instance_id, "application_instance_id")
        require_identifier(operation.project_ref.project_id, "project_id")
    elif isinstance(operation, BindConversationToThread):
        from ...applications.contract import validate_thread_ref

        validate_thread_ref(operation.thread_ref)


def _validate_binding_operation_result(operation: BindingOperation, result: object) -> None:
    """Validate a binding result without owning Gateway execution or CAS."""

    if not isinstance(result, ConversationBound):
        raise ContractViolation(f"{operation.type.value} must return ConversationBound")
    if result.binding.conversation_ref != operation.conversation_ref:
        raise ContractViolation("Gateway result belongs to a different Conversation")

    from ...gateway.persistence.state_contracts import validate_binding

    validate_binding(result.binding)
    if isinstance(operation, BindConversationToProject):
        if (
            result.binding.project_ref != operation.project_ref
            or result.binding.thread_ref is not None
        ):
            raise ContractViolation("conversation.bind_project returned an incompatible binding")
    elif isinstance(operation, BindConversationToThread):
        if result.binding.thread_ref != operation.thread_ref:
            raise ContractViolation("conversation.bind_thread returned a different thread")
    elif result.binding.thread_ref is not None:
        raise ContractViolation("conversation.clear_thread did not clear the thread")


__all__ = [
    "BindConversationToProject",
    "BindConversationToThread",
    "ClearConversationThread",
    "ConversationBound",
]


_complete_gateway_union()

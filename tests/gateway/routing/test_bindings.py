from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
import unittest
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import imagent.gateway.routing as routing_facade
from imagent.applications.contract import ApplicationRef, ProjectRef, ThreadRef
from imagent.gateway.persistence import BindingConflict, ConversationBinding
from imagent.gateway.persistence.memory import InMemoryBindingRepository
from imagent.gateway.routing import bindings as binding_owner
from imagent.gateway.routing.bindings import (
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationApplication,
    ClearConversationProject,
    ClearConversationThread,
    ConversationBound,
)
from imagent.gateway.routing.operations import (
    ApplicationsListed,
    GatewayOperation,
    GatewayOperationType,
    validate_gateway_operation,
    validate_gateway_operation_result,
)
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation

_IMPORT_ORDER_ASSERTIONS = textwrap.dedent(
    """
    import inspect
    import importlib.util
    import typing

    import imagent.gateway.routing as routing_facade
    import imagent.gateway.routing.operations as operations_owner
    from imagent.gateway.persistence import ConversationBinding
    from imagent.gateway.routing import bindings as binding_owner

    binding_names = (
        "BindConversationToProject",
        "BindConversationToThread",
        "ClearConversationApplication",
        "ClearConversationProject",
        "ClearConversationThread",
        "ConversationBound",
    )
    for name in binding_names:
        owner = getattr(binding_owner, name)
        assert getattr(routing_facade, name) is owner
        assert inspect.signature(getattr(routing_facade, name)) == inspect.signature(owner)
        assert owner.__module__ == "imagent.gateway.routing.bindings"
    assert importlib.util.find_spec("imagent.contracts") is None

    binding_hints = typing.get_type_hints(binding_owner.ConversationBound)
    assert binding_hints["binding"] is ConversationBinding
    assert binding_hints["type"] is operations_owner.GatewayOperationType
    """
).strip()

_IMPORT_ORDERS = {
    "canonical owner first": "import imagent.gateway.routing.operations\n",
    "binding owner first": "import imagent.gateway.routing.bindings\n",
    "routing facade first": "import imagent.gateway.routing\n",
}
ROOT = Path(__file__).resolve().parents[3]


class _FaultBindingRepository:
    def __init__(self) -> None:
        self.delegate = InMemoryBindingRepository()
        self.put_failure: str | None = None
        self.fail_get = False
        self.put_calls = 0

    async def get(self, conversation: ConversationRef) -> ConversationBinding | None:
        if self.fail_get:
            raise RuntimeError("binding verification unavailable")
        return await self.delegate.get(conversation)

    async def put(
        self,
        binding: ConversationBinding,
        expected_generation: int | None = None,
    ) -> ConversationBinding:
        self.put_calls += 1
        if self.put_failure == "before":
            raise RuntimeError("binding write failed")
        stored = await self.delegate.put(binding, expected_generation)
        if self.put_failure == "after":
            raise RuntimeError("binding write response was lost")
        return stored

    async def delete(
        self,
        conversation: ConversationRef,
        expected_generation: int | None = None,
    ) -> None:
        await self.delegate.delete(conversation, expected_generation)


class BindingOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conversation = ConversationRef("qq-primary", "c2c:user-1")
        self.application_id = "zen-local"
        self.application = ApplicationRef("zen-local")
        self.thread = ThreadRef(ProjectRef(self.application_id, "workspace"), "thread-1")
        self.other_thread = ThreadRef(ProjectRef(self.application_id, "workspace"), "thread-2")

    def _bind_thread(self) -> BindConversationToThread:
        return BindConversationToThread(
            operation_id="op-bind",
            conversation_ref=self.conversation,
            actor="user-1",
            thread_ref=self.thread,
            created_at=datetime.now(UTC),
        )

    def test_public_facades_are_exact_owner_objects(self) -> None:
        names = (
            "BindConversationToProject",
            "BindConversationToThread",
            "ClearConversationApplication",
            "ClearConversationProject",
            "ClearConversationThread",
            "ConversationBound",
        )
        for name in names:
            with self.subTest(name=name):
                owner = getattr(binding_owner, name)
                self.assertIs(getattr(routing_facade, name), owner)
                self.assertEqual(owner.__module__, "imagent.gateway.routing.bindings")
        self.assertIsNone(importlib.util.find_spec("imagent.contracts"))

    def test_clean_process_import_orders_preserve_identity_signatures_and_hints(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        for label, first_import in _IMPORT_ORDERS.items():
            with self.subTest(import_order=label):
                completed = subprocess.run(
                    [sys.executable, "-c", f"{first_import}{_IMPORT_ORDER_ASSERTIONS}"],
                    capture_output=True,
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{label} failed\nstdout={completed.stdout}\nstderr={completed.stderr}",
                )

    def test_historical_gateway_union_contains_owner_types(self) -> None:
        union_types = set(get_args(GatewayOperation))
        self.assertTrue(
            {
                BindConversationToProject,
                BindConversationToThread,
                ClearConversationApplication,
                ClearConversationProject,
                ClearConversationThread,
            }.issubset(union_types)
        )

    def test_owner_dataclasses_preserve_public_field_shape(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(BindConversationToProject)),
            (
                "operation_id",
                "conversation_ref",
                "actor",
                "created_at",
                "project_ref",
                "expected_generation",
                "type",
            ),
        )
        self.assertEqual(
            tuple(item.name for item in fields(BindConversationToThread)),
            (
                "operation_id",
                "conversation_ref",
                "actor",
                "created_at",
                "thread_ref",
                "expected_generation",
                "type",
            ),
        )

    def test_operation_validation_preserves_binding_error(self) -> None:
        operation = BindConversationToProject(
            operation_id="op-project",
            conversation_ref=self.conversation,
            actor="user-1",
            project_ref=ProjectRef("", "project-1"),
            created_at=datetime.now(UTC),
        )
        with self.assertRaisesRegex(ContractViolation, "application_instance_id"):
            validate_gateway_operation(operation)

    def test_result_validation_preserves_binding_postcondition_errors(self) -> None:
        operation = self._bind_thread()
        result = ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=datetime.now(UTC),
            binding=ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=self.application,
                project_ref=self.other_thread.project_ref,
                thread_ref=self.other_thread,
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "different thread"):
            validate_gateway_operation_result(operation, result)

    def test_result_validation_delegates_wrong_variant_to_binding_owner(self) -> None:
        operation = self._bind_thread()
        result = ApplicationsListed(
            operation_id=operation.operation_id,
            completed_at=datetime.now(UTC),
            applications=(),
        )
        object.__setattr__(result, "type", GatewayOperationType.CONVERSATION_BIND_THREAD)
        with patch.object(
            binding_owner,
            "_validate_binding_operation_result",
            wraps=binding_owner._validate_binding_operation_result,
        ) as owner_validator:
            with self.assertRaises(ContractViolation) as context:
                validate_gateway_operation_result(operation, result)
        self.assertEqual(
            str(context.exception),
            "conversation.bind_thread must return ConversationBound",
        )
        owner_validator.assert_called_once_with(operation, result)

    def test_clear_result_validation_preserves_thread_clear_error(self) -> None:
        operation = ClearConversationThread(
            operation_id="op-clear",
            conversation_ref=self.conversation,
            actor="user-1",
            created_at=datetime.now(UTC),
        )
        result = ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=datetime.now(UTC),
            binding=ConversationBinding(
                conversation_ref=self.conversation,
                application_ref=self.application,
                project_ref=self.thread.project_ref,
                thread_ref=self.thread,
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "did not clear the thread"):
            validate_gateway_operation_result(operation, result)

    def test_hierarchical_clear_results_preserve_retained_ancestors(self) -> None:
        project_clear = ClearConversationProject(
            operation_id="op-clear-project",
            conversation_ref=self.conversation,
            actor="user-1",
            expected_generation=7,
            created_at=datetime.now(UTC),
        )
        application_clear = ClearConversationApplication(
            operation_id="op-clear-application",
            conversation_ref=self.conversation,
            actor="user-1",
            expected_generation=8,
            created_at=datetime.now(UTC),
        )
        validate_gateway_operation(project_clear)
        validate_gateway_operation(application_clear)
        validate_gateway_operation_result(
            project_clear,
            ConversationBound(
                operation_id=project_clear.operation_id,
                type=project_clear.type,
                completed_at=datetime.now(UTC),
                binding=ConversationBinding(
                    conversation_ref=self.conversation,
                    application_ref=self.application,
                ),
            ),
        )
        validate_gateway_operation_result(
            application_clear,
            ConversationBound(
                operation_id=application_clear.operation_id,
                type=application_clear.type,
                completed_at=datetime.now(UTC),
                binding=ConversationBinding(conversation_ref=self.conversation),
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "incompatible binding"):
            validate_gateway_operation_result(
                project_clear,
                ConversationBound(
                    operation_id=project_clear.operation_id,
                    type=project_clear.type,
                    completed_at=datetime.now(UTC),
                    binding=ConversationBinding(conversation_ref=self.conversation),
                ),
            )

    def test_new_clear_preconditions_use_generation_only(self) -> None:
        for operation_type in (ClearConversationProject, ClearConversationApplication):
            with self.subTest(operation_type=operation_type.__name__):
                self.assertIn("expected_generation", operation_type.__annotations__)
                self.assertNotIn("expected_revision", operation_type.__annotations__)
                with self.assertRaisesRegex(ContractViolation, "expected_generation"):
                    validate_gateway_operation(
                        operation_type(
                            operation_id=f"op-{operation_type.__name__}",
                            conversation_ref=self.conversation,
                            actor="user-1",
                            expected_generation=-1,
                            created_at=datetime.now(UTC),
                        )
                    )
                for invalid in (True, 1.5, "1"):
                    with self.subTest(invalid=invalid):
                        with self.assertRaisesRegex(ContractViolation, "expected_generation"):
                            validate_gateway_operation(
                                operation_type(
                                    operation_id=f"op-invalid-{operation_type.__name__}",
                                    conversation_ref=self.conversation,
                                    actor="user-1",
                                    expected_generation=invalid,  # type: ignore[arg-type]
                                    created_at=datetime.now(UTC),
                                )
                            )


class BindingRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = _FaultBindingRepository()
        self.runtime = binding_owner._BindingRuntime(self.repository)
        self.application = ApplicationRef("zen-local")
        self.project = ProjectRef("zen-local", "project-1")
        self.thread = ThreadRef(self.project, "thread-1")
        self.other_thread = ThreadRef(self.project, "thread-2")
        self.conversation = ConversationRef("qq-primary", "c2c:user-1")
        self.other_conversation = ConversationRef("qq-primary", "c2c:user-2")

    async def _bind_thread(
        self,
        conversation: ConversationRef,
        thread: ThreadRef,
        *,
        expected_generation: int | None = 0,
        converge_same_target: bool = True,
    ) -> binding_owner._BindingChange:
        prepared = await self.runtime.prepare_thread_binding(
            conversation,
            self.application,
            thread,
            expected_generation=expected_generation,
            converge_same_target=converge_same_target,
        )
        return await self.runtime.commit_thread_binding(prepared)

    async def test_one_current_binding_allows_two_conversations_on_one_thread(
        self,
    ) -> None:
        first = await self._bind_thread(self.conversation, self.thread)
        second = await self._bind_thread(self.other_conversation, self.thread)

        self.assertIsNone(first.previous)
        self.assertIsNone(second.previous)
        self.assertEqual(first.binding.thread_ref, self.thread)
        self.assertEqual(second.binding.thread_ref, self.thread)
        self.assertEqual(
            await self.runtime.current(self.conversation),
            first.binding,
        )
        self.assertEqual(
            await self.runtime.current(self.other_conversation),
            second.binding,
        )

    async def test_runtime_forwards_cas_and_preserves_current_binding_on_conflict(
        self,
    ) -> None:
        selected = await self.runtime.select_application(
            self.conversation,
            self.application,
            expected_generation=0,
        )

        with self.assertRaisesRegex(BindingConflict, "expected generation 0"):
            await self.runtime.bind_project(
                self.conversation,
                self.application,
                self.project,
                expected_generation=0,
            )

        self.assertEqual(
            await self.runtime.current(self.conversation),
            selected.binding,
        )

    async def test_project_thread_and_clear_mutations_return_transition_facts(
        self,
    ) -> None:
        project = await self.runtime.bind_project(
            self.conversation,
            self.application,
            self.project,
            expected_generation=0,
        )
        thread = await self._bind_thread(
            self.conversation,
            self.thread,
            expected_generation=project.binding.generation,
        )
        cleared = await self.runtime.clear_thread(
            self.conversation,
            expected_generation=thread.binding.generation,
        )

        self.assertIsNone(project.previous)
        self.assertEqual(thread.previous, project.binding)
        self.assertEqual(cleared.previous, thread.binding)
        self.assertEqual(cleared.binding.application_ref, self.application)
        self.assertEqual(cleared.binding.project_ref, self.project)
        self.assertIsNone(cleared.binding.thread_ref)

    async def test_same_target_converges_for_each_accepted_generation_guard(self) -> None:
        initial = await self._bind_thread(self.conversation, self.thread)
        initial_put_calls = self.repository.put_calls

        for guard in (
            None,
            initial.binding.generation,
            initial.binding.generation - 1,
        ):
            with self.subTest(expected_generation=guard):
                prepared = await self.runtime.prepare_thread_binding(
                    self.conversation,
                    self.application,
                    self.thread,
                    expected_generation=guard,
                    converge_same_target=True,
                )
                self.assertTrue(prepared.converge_without_write)
                converged = await self.runtime.commit_thread_binding(prepared)
                self.assertEqual(converged.binding, initial.binding)
                self.assertEqual(self.repository.put_calls, initial_put_calls)

        with self.assertRaisesRegex(BindingConflict, "same-target bind retry"):
            await self.runtime.prepare_thread_binding(
                self.conversation,
                self.application,
                self.thread,
                expected_generation=initial.binding.generation + 2,
                converge_same_target=True,
            )
        self.assertEqual(self.repository.put_calls, initial_put_calls)

    async def test_generationless_nonforeground_same_target_remains_a_write(self) -> None:
        initial = await self._bind_thread(self.conversation, self.thread)
        prepared = await self.runtime.prepare_thread_binding(
            self.conversation,
            self.application,
            self.thread,
            expected_generation=None,
            converge_same_target=False,
        )

        self.assertFalse(prepared.converge_without_write)
        repeated = await self.runtime.commit_thread_binding(prepared)

        self.assertEqual(repeated.previous, initial.binding)
        self.assertEqual(repeated.binding.thread_ref, self.thread)
        self.assertEqual(repeated.binding.generation, initial.binding.generation + 1)

    async def test_stale_retry_never_overwrites_a_later_different_target(self) -> None:
        first = await self._bind_thread(self.conversation, self.thread)
        later = await self._bind_thread(
            self.conversation,
            self.other_thread,
            expected_generation=first.binding.generation,
        )
        stale = await self.runtime.prepare_thread_binding(
            self.conversation,
            self.application,
            self.thread,
            expected_generation=first.binding.generation - 1,
            converge_same_target=True,
        )

        self.assertFalse(stale.converge_without_write)
        with self.assertRaises(BindingConflict) as context:
            await self.runtime.commit_thread_binding(stale)
        self.assertFalse(
            await self.runtime.binding_write_may_have_committed(
                stale,
                context.exception,
            )
        )
        self.assertEqual(
            await self.runtime.current(self.conversation),
            later.binding,
        )

    async def test_unknown_write_outcome_requires_exact_target_verification(self) -> None:
        prepared = await self.runtime.prepare_thread_binding(
            self.conversation,
            self.application,
            self.thread,
            expected_generation=0,
            converge_same_target=True,
        )
        self.repository.put_failure = "after"

        with self.assertRaisesRegex(RuntimeError, "response was lost") as context:
            await self.runtime.commit_thread_binding(prepared)

        self.repository.put_failure = None
        self.assertTrue(
            await self.runtime.binding_write_may_have_committed(
                prepared,
                context.exception,
            )
        )
        current = await self.runtime.current(self.conversation)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.thread_ref, self.thread)

    async def test_failed_write_does_not_produce_verified_binding_authority(self) -> None:
        prepared = await self.runtime.prepare_thread_binding(
            self.conversation,
            self.application,
            self.thread,
            expected_generation=0,
            converge_same_target=True,
        )
        self.repository.put_failure = "before"

        with self.assertRaisesRegex(RuntimeError, "binding write failed") as context:
            await self.runtime.commit_thread_binding(prepared)

        self.repository.put_failure = None
        self.assertFalse(
            await self.runtime.binding_write_may_have_committed(
                prepared,
                context.exception,
            )
        )
        self.assertIsNone(await self.runtime.current(self.conversation))

    async def test_unverifiable_write_preserves_original_error_and_uncertainty(self) -> None:
        prepared = await self.runtime.prepare_thread_binding(
            self.conversation,
            self.application,
            self.thread,
            expected_generation=0,
            converge_same_target=True,
        )
        self.repository.put_failure = "after"

        with self.assertRaisesRegex(RuntimeError, "response was lost") as context:
            await self.runtime.commit_thread_binding(prepared)

        self.repository.fail_get = True
        self.assertTrue(
            await self.runtime.binding_write_may_have_committed(
                prepared,
                context.exception,
            )
        )
        self.assertEqual(
            context.exception.__notes__,
            [
                "Binding outcome verification also failed; the prepared route remains "
                "fenced: route binding verification: RuntimeError: "
                "binding verification unavailable"
            ],
        )


if __name__ == "__main__":
    unittest.main()

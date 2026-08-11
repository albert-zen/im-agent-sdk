from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args, get_type_hints
from unittest.mock import patch

import imagent
import imagent.gateway as gateway_facade
import imagent.gateway.actions as actions_owner
from imagent.applications.capabilities import ProjectMode, SupportLevel
from imagent.applications.contract import (
    ApplicationRef,
    ApplicationSummary,
    ProjectRef,
    ThreadRef,
)
from imagent.applications.operations import (
    ApplicationOperation,
    ApplicationOperationFailed,
    CreateThread,
    ThreadCreated,
)
from imagent.applications.requests import (
    MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH,
    MAX_INTERACTIVE_REQUEST_CHOICES,
    MAX_INTERACTIVE_REQUEST_QUESTIONS,
    ApprovalResponse,
    RequestRef,
    RequestResponse,
    UserInputResponse,
)
from imagent.gateway.actions import (
    ActionValue,
    ApplicationActions,
    ConversationActions,
    _new_application_actions,
    _new_conversation_actions,
)
from imagent.gateway.effect_execution import StoreBackedGatewayEffectExecutor
from imagent.gateway.outcomes import Failed, OutcomeUnknown, Partial, Succeeded
from imagent.gateway.persistence.effects import (
    ActionError,
    ActionErrorCode,
    ActionOutcome,
    BindingClearScope,
    CreateBindingWorkflowKind,
    CreateBindingWorkflowRequest,
    EffectCategory,
    EffectValue,
    KnownNativeOutcome,
    NativeMutationRequest,
    RouteDeleteCondition,
    StableReference,
    StoreMutationRequest,
)
from imagent.gateway.persistence.memory_store import MemoryGatewayStore
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.persistence.state_contracts import ConversationBinding
from imagent.gateway.persistence.store import GatewayStore
from imagent.gateway.routing.operations import _MAX_APPLICATION_LIST_ITEMS
from imagent.gateway.routing.projection_routes import derive_projection_route_id
from imagent.interaction.controllers import CommandInvocation, CommandRegistry, CommandResult
from imagent.interaction.media import (
    AttachmentContent,
    AttachmentHandle,
    AttachmentSourceKind,
)
from imagent.interaction.messages import ConversationRef, InboundMessage, TextContent
from imagent.interaction.operations import ContractError, ContractViolation, OperationErrorCode
from imagent.testing import FakeAgentApplicationAdapter


class _Runtime:
    def __init__(self) -> None:
        self.application = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
        self.binding: ConversationBinding | None = None
        self.operations: list[ApplicationOperation] = []
        self.request_calls: list[tuple[ConversationRef, str, RequestRef, RequestResponse]] = []
        self.authorized_requests: list[tuple[ConversationRef, RequestRef, RequestResponse]] = []
        self.reject_request = False
        self.wrong_request_result = False
        self.binding_reads = 0
        self.reconciled_route_ids: list[str | None] = []
        self.begun_route_ids: list[str] = []
        self.completed_route_ids: list[str] = []
        self.completed_route_statuses: list[bool | None] = []
        self.projection_route_leases: dict[object, str] = {}
        self.projection_reconciliation_error: ActionError | None = None
        self.projection_reconciliation_entered: asyncio.Event | None = None
        self.projection_reconciliation_release: asyncio.Event | None = None
        self.forced_operation_error: OperationErrorCode | None = None
        self.application_summaries: tuple[ApplicationSummary, ...] = (self.application.summary,)

    def list_applications(self):
        return self.application_summaries

    async def execute_application(self, operation: ApplicationOperation):
        self.operations.append(operation)
        if self.forced_operation_error is not None:
            return ApplicationOperationFailed(
                operation_id=operation.operation_id,
                type=operation.type,
                completed_at=datetime.now(UTC),
                error=ContractError(
                    code=self.forced_operation_error.value,
                    message="forced preflight failure",
                ),
            )
        return await self.application.execute(operation)

    async def reconcile_application(self, operation: ApplicationOperation):
        del operation
        return None

    async def get_binding(self, conversation_ref: ConversationRef):
        self.binding_reads += 1
        del conversation_ref
        return self.binding

    async def reconcile_projection_route(
        self,
        route_id: str | None,
        action_lease: object | None,
    ):
        if action_lease is not None:
            self.assert_projection_lease(action_lease, route_id)
        self.reconciled_route_ids.append(route_id)
        if self.projection_reconciliation_entered is not None:
            self.projection_reconciliation_entered.set()
        if self.projection_reconciliation_release is not None:
            await self.projection_reconciliation_release.wait()
        return self.projection_reconciliation_error

    def validate_projection_route(self, receipt: object) -> ActionError | None:
        del receipt
        return None

    def assert_projection_lease(
        self,
        lease: object,
        route_id: str | None,
    ) -> None:
        if self.projection_route_leases.get(lease) != route_id:
            raise AssertionError("projection reconciliation received the wrong route lease")

    async def begin_projection_route(self, route_id: str) -> object:
        self.begun_route_ids.append(route_id)
        lease = object()
        self.projection_route_leases[lease] = route_id
        return lease

    def complete_projection_route(
        self,
        lease: object,
        reconciled: bool | None,
    ) -> None:
        route_id = self.projection_route_leases.pop(lease)
        self.completed_route_ids.append(route_id)
        self.completed_route_statuses.append(reconciled)

    async def authorize_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> None:
        if self.reject_request:
            raise ValueError("request was not delivered to this Conversation")
        self.authorized_requests.append((conversation_ref, request_ref, response))

    async def invoke_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        operation_id: str,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> KnownNativeOutcome:
        self.request_calls.append((conversation_ref, operation_id, request_ref, response))
        if self.wrong_request_result:
            return Succeeded(
                EffectValue(reference=StableReference.from_value(self.application.summary.ref))
            )
        return Succeeded(EffectValue(reference=StableReference.from_value(request_ref)))

    async def reconcile_request_response(
        self,
        conversation_ref: ConversationRef,
        *,
        operation_id: str,
        request_ref: RequestRef,
        response: RequestResponse,
    ) -> KnownNativeOutcome | None:
        del conversation_ref, operation_id, request_ref, response
        return None


class _StrictEffects:
    def __init__(self) -> None:
        self.store_requests: list[StoreMutationRequest] = []
        self.store_replay_requests: list[StoreMutationRequest] = []
        self.store_outcomes: dict[str, ActionOutcome] = {}
        self.store_fingerprints: dict[str, object] = {}
        self.native_requests: list[NativeMutationRequest] = []
        self.workflow_requests: list[CreateBindingWorkflowRequest] = []
        self.partial_workflow = False
        self.unknown_workflow = False
        self.before_native: Callable[[], None] | None = None
        self.before_store: Callable[[StoreMutationRequest], None] | None = None
        self.native_outcomes: dict[str, ActionOutcome] = {}

    async def replay_store_mutation(
        self,
        request: StoreMutationRequest,
    ) -> ActionOutcome | None:
        self.store_replay_requests.append(request)
        action_key = request.fingerprint.action_key
        outcome = self.store_outcomes.get(action_key)
        if outcome is None:
            return None
        if self.store_fingerprints.get(action_key) != request.fingerprint:
            return Failed(ActionError(ActionErrorCode.CONFLICT))
        return outcome

    async def execute_store_mutation(
        self,
        request: StoreMutationRequest,
        *,
        preflight=None,
    ) -> ActionOutcome:
        self.store_requests.append(request)
        if self.before_store is not None:
            self.before_store(request)
        replay = self.store_outcomes.get(request.fingerprint.action_key)
        if replay is not None:
            return replay
        if preflight is not None:
            error = await preflight()
            if error is not None:
                outcome: ActionOutcome = Failed(error)
                self.store_fingerprints[request.fingerprint.action_key] = request.fingerprint
                self.store_outcomes[request.fingerprint.action_key] = outcome
                return outcome
        plan = request.plan
        reference = None
        conversation_ref = plan.conversation_ref if plan.binding_clear is not None else None
        if plan.binding_target is not None:
            target = plan.binding_target
            conversation_ref = target.conversation_ref
            selected = target.thread_ref or target.project_ref or target.application_ref
            if selected is not None:
                reference = StableReference.from_value(selected)
        outcome = Succeeded(
            EffectValue(
                reference=reference,
                conversation_ref=conversation_ref,
                binding_generation=(plan.expected_generation or 0) + 1,
                route_id=(
                    plan.route_upsert.route_id
                    if plan.route_upsert is not None
                    else plan.route_delete_id
                ),
            )
        )
        self.store_fingerprints[request.fingerprint.action_key] = request.fingerprint
        self.store_outcomes[request.fingerprint.action_key] = outcome
        return outcome

    async def execute_native_mutation(
        self,
        request: NativeMutationRequest,
        *,
        invoke,
        preflight=None,
        reconcile=None,
    ) -> ActionOutcome:
        del reconcile
        self.native_requests.append(request)
        replay = self.native_outcomes.get(request.fingerprint.action_key)
        if replay is not None:
            return replay
        if preflight is not None:
            error = await preflight()
            if error is not None:
                outcome: ActionOutcome = Failed(error)
                self.native_outcomes[request.fingerprint.action_key] = outcome
                return outcome
        try:
            if self.before_native is not None:
                self.before_native()
            outcome = await invoke("phase-native")
        except Exception:
            outcome = OutcomeUnknown(ActionError(ActionErrorCode.NATIVE_OUTCOME_UNKNOWN))
        self.native_outcomes[request.fingerprint.action_key] = outcome
        return outcome

    async def execute_create_binding_workflow(
        self,
        request: CreateBindingWorkflowRequest,
        *,
        invoke,
        preflight=None,
        reconcile=None,
    ) -> ActionOutcome:
        del reconcile
        self.workflow_requests.append(request)
        if preflight is not None:
            error = await preflight()
            if error is not None:
                return Failed(error)
        try:
            created = await invoke("phase-native-create")
        except Exception:
            return OutcomeUnknown(ActionError(ActionErrorCode.NATIVE_OUTCOME_UNKNOWN))
        if isinstance(created, Failed):
            return created
        assert isinstance(created, Succeeded)
        if self.unknown_workflow:
            return OutcomeUnknown(ActionError(ActionErrorCode.NATIVE_OUTCOME_UNKNOWN))
        if self.partial_workflow:
            return Partial(created.value, ActionError(ActionErrorCode.STALE_BINDING))
        return Succeeded(
            EffectValue(
                reference=created.value.reference,
                conversation_ref=request.conversation_ref,
                binding_generation=1,
            )
        )


class _OversizedMetadata(Mapping[str, object]):
    """Reject iteration so validation ordering is directly observable."""

    def __getitem__(self, key: str) -> object:
        raise AssertionError(f"oversized metadata was read at {key!r}")

    def __iter__(self):
        raise AssertionError("oversized metadata was iterated before rejection")

    def __len__(self) -> int:
        return 65


class _OversizedAnswers(Mapping[str, tuple[str, ...]]):
    """Prove the question bound is checked before answers are traversed."""

    def __getitem__(self, key: str) -> tuple[str, ...]:
        raise AssertionError(f"oversized answers were read at {key!r}")

    def __iter__(self):
        raise AssertionError("oversized answers were iterated before rejection")

    def __len__(self) -> int:
        return 33


async def _seed_thread(
    runtime: _Runtime,
    *,
    operation_id: str = "seed-thread",
) -> ThreadRef:
    operation = CreateThread(
        operation_id=operation_id,
        application_ref=runtime.application.summary.ref,
        project_ref=runtime.application.default_project_ref,
        created_at=datetime.now(UTC),
    )
    result = await runtime.application.execute(operation)
    assert isinstance(result, ThreadCreated)
    return result.thread.ref


def _contains_any(value: object) -> bool:
    return value is Any or any(_contains_any(argument) for argument in get_args(value))


class ScopedActionSurfaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.runtime = _Runtime()
        self.effects = _StrictEffects()
        self.conversation = ConversationRef("channel-a", "conversation-a")
        self.application_ref = self.runtime.application.summary.ref
        self.actions = _new_conversation_actions(
            self.conversation,
            actor="actor-a",
            gateway_id="gateway-a",
            runtime=self.runtime,
            effects=self.effects,
            foreground_route=True,
        )

    def test_public_facades_export_the_exact_scoped_contracts_only(self) -> None:
        for name in (
            "ActionResult",
            "ActionValue",
            "ApplicationActions",
            "ConversationActions",
            "ReadOutcome",
        ):
            with self.subTest(name=name):
                owner_value = getattr(actions_owner, name)
                self.assertIs(getattr(gateway_facade, name), owner_value)
                self.assertIs(getattr(imagent, name), owner_value)
                self.assertIn(name, imagent.__all__)

    async def test_local_registry_receives_the_exact_scoped_surface(self) -> None:
        registry = CommandRegistry()
        received: list[ConversationActions] = []

        @registry.command("probe")
        async def probe(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            del invocation
            received.append(actions)
            return CommandResult.text("ok")

        registry.freeze()
        outputs = await registry.handle(
            InboundMessage(
                message_id="message-probe",
                conversation_ref=self.conversation,
                sender="actor-a",
                content=(TextContent("/probe"),),
                created_at=datetime.now(UTC),
            ),
            self.actions,
        )
        self.assertIsNotNone(outputs)
        self.assertEqual(received, [self.actions])

    async def test_application_discovery_rejects_an_oversized_runtime_result(self) -> None:
        self.runtime.application_summaries = (self.runtime.application.summary,) * (
            _MAX_APPLICATION_LIST_ITEMS + 1
        )
        with self.assertRaisesRegex(ContractViolation, "more than 1000 applications"):
            await self.actions.list_applications()
        with self.assertRaisesRegex(ContractViolation, "more than 1000 applications"):
            await self.actions.get_application(self.application_ref)

        valid = self.runtime.application.summary
        self.runtime.application_summaries = (
            ApplicationSummary(
                ref=valid.ref,
                kind="",
                display_name=valid.display_name,
                capabilities=valid.capabilities,
                workspace_identity=valid.workspace_identity,
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "kind"):
            await self.actions.get_application(self.application_ref)

    async def test_binding_reads_validate_complete_frozen_conversation_scope(self) -> None:
        self.runtime.binding = ConversationBinding(
            conversation_ref=ConversationRef("channel-b", "conversation-b"),
            application_ref=self.application_ref,
            generation=1,
            updated_at=datetime.now(UTC),
        )
        with self.assertRaisesRegex(ContractViolation, "another Conversation"):
            await self.actions.get_binding()

        self.runtime.binding = ConversationBinding(
            conversation_ref=self.conversation,
            application_ref=ApplicationRef(""),
            generation=1,
            updated_at=datetime.now(UTC),
        )
        with self.assertRaisesRegex(ContractViolation, "application_instance_id"):
            await self.actions.get_binding()

    def test_surfaces_are_frozen_scopes_and_have_no_authority_escape(self) -> None:
        with self.assertRaises(TypeError):
            ConversationActions()
        with self.assertRaises(TypeError):
            ApplicationActions()
        with self.assertRaises(FrozenInstanceError):
            self.actions.actor = "actor-b"  # type: ignore[misc]

        conversation_methods = {
            name
            for name, value in inspect.getmembers(ConversationActions, inspect.isfunction)
            if not name.startswith("_")
        }
        application_methods = {
            name
            for name, value in inspect.getmembers(ApplicationActions, inspect.isfunction)
            if not name.startswith("_")
        }
        for method_name in conversation_methods:
            method = getattr(ConversationActions, method_name)
            signature = inspect.signature(method)
            self.assertNotIn("conversation_ref", signature.parameters)
            hints = get_type_hints(method)
            self.assertFalse(any(_contains_any(hint) for hint in hints.values()))
            self.assertNotRegex(
                repr(hints),
                r"Gateway(Store)?|Repository|Adapter|Client|Credential|Claim|Checkpoint",
            )
        for method_name in application_methods:
            hints = get_type_hints(getattr(ApplicationActions, method_name))
            self.assertFalse(any(_contains_any(hint) for hint in hints.values()))
        self.assertNotIn("respond_request", application_methods)
        self.assertTrue(
            {
                "select_application",
                "select_project",
                "bind_thread",
                "clear_thread",
                "clear_project",
                "clear_application",
                "observe_thread",
                "clear_observation",
                "respond_request",
            }.isdisjoint(application_methods)
        )
        for surface in (self.actions, self.actions._application(self.application_ref)):
            public_names = {name.casefold() for name in dir(surface) if not name.startswith("_")}
            for forbidden in (
                "gateway",
                "adapter",
                "store",
                "repository",
                "client",
                "claim",
                "credential",
                "checkpoint",
            ):
                self.assertNotIn(forbidden, public_names)

    async def test_application_mutation_uses_native_seam_and_never_changes_binding(self) -> None:
        direct = _new_application_actions(
            self.application_ref,
            principal="operator-a",
            gateway_id="gateway-a",
            runtime=self.runtime,
            effects=self.effects,
        )
        result = await direct.create_project(
            cwd="/workspace/project-a",
            display_name="Project A",
            action_id="native-request-1",
        )
        self.assertIsInstance(result, Succeeded)
        assert isinstance(result, Succeeded)
        self.assertIsInstance(result.value, ActionValue)
        self.assertIsInstance(result.value.ref, ProjectRef)
        self.assertEqual(len(self.effects.native_requests), 1)
        self.assertEqual(self.effects.store_requests, [])
        self.assertIsNone(self.runtime.binding)

        project_ref = result.value.ref
        assert isinstance(project_ref, ProjectRef)
        deleted = await direct.delete_project(project_ref, action_id="native-request-2")
        self.assertIsInstance(deleted, Succeeded)
        self.assertEqual(len(self.effects.native_requests), 2)
        self.assertEqual(self.effects.store_requests, [])
        self.assertIsNone(self.runtime.binding)

    async def test_store_actions_are_scoped_and_use_generation_vocabulary(self) -> None:
        thread = await _seed_thread(self.runtime)
        result = await self.actions.bind_thread(
            thread,
            expected_generation=9,
            action_id="bind-1",
        )
        self.assertIsInstance(result, Succeeded)
        request = self.effects.store_requests[-1]
        self.assertEqual(request.plan.conversation_ref, self.conversation)
        self.assertEqual(request.plan.expected_generation, 9)
        assert request.plan.binding_target is not None
        self.assertEqual(request.plan.binding_target.conversation_ref, self.conversation)
        self.assertEqual(request.plan.binding_target.thread_ref, thread)
        assert request.plan.route_upsert is not None
        self.assertEqual(request.plan.route_upsert.conversation_ref, self.conversation)
        self.assertEqual(request.plan.route_upsert.thread_ref, thread)
        self.assertFalse(hasattr(request.plan, "expected_revision"))

        conflict = await self.actions.bind_thread(
            thread,
            expected_generation=10,
            action_id="bind-1",
        )
        self.assertIsInstance(conflict, Failed)
        self.assertNotEqual(
            self.effects.store_requests[-1].fingerprint.payload_fingerprint,
            self.effects.store_replay_requests[-1].fingerprint.payload_fingerprint,
        )

        with self.assertRaises(ContractViolation):
            await self.actions.bind_thread(
                thread,
                expected_generation=10,
                action_id="",
            )
        self.assertEqual(len(self.effects.store_requests), 1)

        non_foreground_effects = _StrictEffects()
        non_foreground = _new_conversation_actions(
            self.conversation,
            actor="actor-a",
            gateway_id="gateway-a",
            runtime=self.runtime,
            effects=non_foreground_effects,
            foreground_route=False,
        )
        await non_foreground.bind_thread(
            thread,
            expected_generation=9,
            action_id="bind-1",
        )
        non_foreground_request = non_foreground_effects.store_requests[-1]
        self.assertIsNone(non_foreground_request.plan.route_upsert)
        self.assertNotEqual(
            request.fingerprint.payload_fingerprint,
            non_foreground_request.fingerprint.payload_fingerprint,
        )

    async def test_hierarchical_clears_are_transaction_intents_without_state_reads(self) -> None:
        for index, (method, scope) in enumerate(
            (
                (self.actions.clear_thread, BindingClearScope.THREAD),
                (self.actions.clear_project, BindingClearScope.PROJECT),
                (self.actions.clear_application, BindingClearScope.APPLICATION),
            ),
            start=1,
        ):
            result = await method(
                action_id=f"clear-{scope.value}",
                expected_generation=index,
            )
            self.assertIsInstance(result, Succeeded)
            plan = self.effects.store_requests[-1].plan
            self.assertEqual(plan.binding_clear, scope)
            self.assertIsNone(plan.binding_target)
            self.assertEqual(plan.conversation_ref, self.conversation)
            self.assertEqual(plan.expected_generation, index)
        self.assertEqual(self.runtime.binding_reads, 0)

    async def test_workflows_are_one_executor_call_and_preserve_partial_creation(self) -> None:
        project = self.runtime.application.default_project_ref
        self.effects.partial_workflow = True
        result = await self.actions.create_and_bind_thread(
            project,
            title="Thread A",
            action_id="workflow-1",
        )
        self.assertIsInstance(result, Partial)
        assert isinstance(result, Partial)
        self.assertIsInstance(result.value.ref, ThreadRef)
        self.assertEqual(result.error.code, ActionErrorCode.STALE_BINDING)
        self.assertEqual(len(self.effects.workflow_requests), 1)
        request = self.effects.workflow_requests[0]
        self.assertIs(request.kind, CreateBindingWorkflowKind.CREATE_AND_BIND_THREAD)
        self.assertEqual(request.conversation_ref, self.conversation)
        self.assertEqual(len(self.runtime.operations), 2)

        self.effects.unknown_workflow = True
        unknown = await self.actions.create_and_select_project(
            self.application_ref,
            cwd="/workspace/unknown",
            action_id="workflow-unknown",
        )
        self.assertIsInstance(unknown, OutcomeUnknown)
        self.assertNotIn(
            "project.delete",
            {operation.type.value for operation in self.runtime.operations},
        )

    async def test_create_payloads_are_rejected_before_snapshot_or_fingerprint(self) -> None:
        project = ProjectRef(self.application_ref.application_instance_id, "p1")
        oversized_context = (
            AttachmentContent(
                attachment_id="attachment-1",
                media_type="application/octet-stream",
                source=AttachmentHandle("handle-1"),
                metadata=_OversizedMetadata(),
            ),
        )
        direct = self.actions._application(self.application_ref)

        with self.assertRaisesRegex(ContractViolation, "at most 64 items"):
            await direct.create_thread(
                project,
                action_id="oversized-direct-context",
                initial_context=oversized_context,
            )
        self.assertEqual(self.effects.native_requests, [])

        with self.assertRaisesRegex(ContractViolation, "at most 64 items"):
            await self.actions.create_and_bind_thread(
                project,
                action_id="oversized-workflow-context",
                initial_context=oversized_context,
            )
        self.assertEqual(self.effects.workflow_requests, [])

        with patch.object(
            actions_owner,
            "_fingerprint",
            side_effect=AssertionError("invalid create payload reached fingerprinting"),
        ):
            with self.assertRaisesRegex(ContractViolation, "cwd"):
                await self.actions.create_and_select_project(
                    self.application_ref,
                    cwd="x" * 4097,
                    action_id="oversized-workflow-cwd",
                )

    async def test_native_and_workflow_preflights_enforce_capability_honesty(self) -> None:
        direct = self.actions._application(self.application_ref)
        thread = await _seed_thread(self.runtime)
        base = self.runtime.application.summary

        project_unsupported = replace(
            base,
            capabilities=replace(
                base.capabilities,
                projects=replace(
                    base.capabilities.projects,
                    creation=SupportLevel.UNSUPPORTED,
                ),
            ),
        )
        self.runtime.application_summaries = (project_unsupported,)
        project_result = await direct.create_project(
            cwd="/unsupported",
            action_id="unsupported-project",
        )

        thread_unsupported = replace(
            base,
            capabilities=replace(
                base.capabilities,
                threads=replace(
                    base.capabilities.threads,
                    creation=SupportLevel.UNSUPPORTED,
                ),
            ),
        )
        self.runtime.application_summaries = (thread_unsupported,)
        thread_result = await direct.create_thread(
            thread.project_ref,
            action_id="unsupported-thread",
        )

        self.runtime.application_summaries = (base,)
        attachment_result = await direct.create_thread(
            thread.project_ref,
            action_id="unsupported-attachment",
            initial_context=(
                AttachmentContent(
                    attachment_id="attachment-unsupported",
                    media_type="application/octet-stream",
                    source=AttachmentHandle("handle-unsupported"),
                ),
            ),
        )

        runtime_unsupported = replace(
            base,
            capabilities=replace(
                base.capabilities,
                runtime=replace(
                    base.capabilities.runtime,
                    native_thread_activation=SupportLevel.UNSUPPORTED,
                ),
            ),
        )
        self.runtime.application_summaries = (runtime_unsupported,)
        runtime_result = await direct.activate_native_thread(
            thread,
            action_id="unsupported-runtime",
        )

        self.runtime.application_summaries = (project_unsupported,)
        project_workflow = await self.actions.create_and_select_project(
            self.application_ref,
            cwd="/unsupported-workflow",
            action_id="unsupported-project-workflow",
        )
        self.runtime.application_summaries = (thread_unsupported,)
        thread_workflow = await self.actions.create_and_bind_thread(
            thread.project_ref,
            action_id="unsupported-thread-workflow",
        )

        for result in (
            project_result,
            thread_result,
            attachment_result,
            runtime_result,
            project_workflow,
            thread_workflow,
        ):
            with self.subTest(result=result):
                self.assertIsInstance(result, Failed)
                assert isinstance(result, Failed)
                self.assertEqual(result.error.code, ActionErrorCode.UNSUPPORTED)
        self.assertEqual(self.runtime.operations, [])

    async def test_resource_preflight_preserves_stale_error_classification(self) -> None:
        self.runtime.forced_operation_error = OperationErrorCode.REQUEST_STALE
        result = await self.actions.select_project(
            self.runtime.application.default_project_ref,
            action_id="stale-project-preflight",
        )
        self.assertIsInstance(result, Failed)
        assert isinstance(result, Failed)
        self.assertEqual(result.error.code, ActionErrorCode.NATIVE_REJECTED)
        self.assertEqual(
            result.error.operation_error_code,
            OperationErrorCode.REQUEST_STALE,
        )
        self.assertEqual(len(self.effects.store_requests), 1)
        preflight_operations = len(self.runtime.operations)
        self.runtime.forced_operation_error = None
        replay = await self.actions.select_project(
            self.runtime.application.default_project_ref,
            action_id="stale-project-preflight",
        )
        self.assertEqual(replay, result)
        self.assertEqual(len(self.runtime.operations), preflight_operations)

    async def test_route_producers_require_streaming_capability(self) -> None:
        thread = await _seed_thread(self.runtime)
        base = self.runtime.application.summary
        self.runtime.application_summaries = (
            replace(
                base,
                capabilities=replace(
                    base.capabilities,
                    runtime=replace(
                        base.capabilities.runtime,
                        streaming=SupportLevel.UNSUPPORTED,
                    ),
                ),
            ),
        )
        results = (
            await self.actions.observe_thread(
                thread,
                action_id="unsupported-stream-observe",
            ),
            await self.actions.bind_thread(
                thread,
                action_id="unsupported-stream-bind",
            ),
            await self.actions.create_and_bind_thread(
                thread.project_ref,
                action_id="unsupported-stream-workflow",
            ),
        )
        for result in results:
            self.assertEqual(
                result,
                Failed(
                    ActionError(
                        ActionErrorCode.UNSUPPORTED,
                        OperationErrorCode.UNSUPPORTED,
                    )
                ),
            )
        self.assertEqual(self.runtime.operations, [])

        non_foreground_effects = _StrictEffects()
        non_foreground = _new_conversation_actions(
            self.conversation,
            actor="actor-a",
            gateway_id="gateway-a",
            runtime=self.runtime,
            effects=non_foreground_effects,
            foreground_route=False,
        )
        bound = await non_foreground.bind_thread(
            thread,
            action_id="non-foreground-bind-without-streaming",
        )
        self.assertIsInstance(bound, Succeeded)

    async def test_request_response_bounds_precede_snapshot_and_executor_admission(self) -> None:
        request_ref = RequestRef(self.application_ref, "request-bounds")
        with self.assertRaisesRegex(ContractViolation, "questions exceed"):
            await self.actions.respond_request(
                request_ref,
                UserInputResponse(_OversizedAnswers()),
                action_id="response-too-many-questions",
            )
        with self.assertRaisesRegex(ContractViolation, "maximum length"):
            await self.actions.respond_request(
                request_ref,
                UserInputResponse(
                    {"question": ("x" * (MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH + 1),)}
                ),
                action_id="response-answer-too-long",
            )
        self.assertEqual(self.effects.native_requests, [])
        self.assertEqual(self.runtime.authorized_requests, [])
        self.assertEqual(self.runtime.request_calls, [])

        boundary_responses = (
            UserInputResponse(
                {
                    f"question-{index}": ("answer",)
                    for index in range(MAX_INTERACTIVE_REQUEST_QUESTIONS)
                }
            ),
            UserInputResponse(
                {
                    "question": tuple(
                        f"answer-{index}" for index in range(MAX_INTERACTIVE_REQUEST_CHOICES)
                    )
                }
            ),
            UserInputResponse({"question": ("x" * MAX_INTERACTIVE_REQUEST_ANSWER_LENGTH,)}),
        )
        for index, response in enumerate(boundary_responses):
            result = await self.actions.respond_request(
                request_ref,
                response,
                action_id=f"response-boundary-{index}",
            )
            self.assertIsInstance(result, Succeeded)

    async def test_route_only_actions_name_scope_without_a_synthetic_binding(self) -> None:
        thread = await _seed_thread(self.runtime)
        self.effects.before_store = lambda request: self.assertIn(
            request.plan.route_upsert.route_id if request.plan.route_upsert is not None else None,
            self.runtime.begun_route_ids,
        )
        observed = await self.actions.observe_thread(thread, action_id="observe-1")
        self.assertIsInstance(observed, Succeeded)
        assert isinstance(observed, Succeeded)
        replayed = await self.actions.observe_thread(thread, action_id="observe-1")
        self.assertIsInstance(replayed, Succeeded)
        assert isinstance(replayed, Succeeded)
        self.effects.before_store = None
        await self.actions.clear_observation(thread, action_id="clear-observation-1")
        self.assertEqual(
            self.runtime.reconciled_route_ids[:3],
            [observed.value.route_id, observed.value.route_id, observed.value.route_id],
        )
        self.assertEqual(
            self.runtime.begun_route_ids,
            [observed.value.route_id],
        )
        self.assertEqual(self.runtime.completed_route_ids, self.runtime.begun_route_ids)
        for request in self.effects.store_requests:
            with self.subTest(action_kind=request.fingerprint.action_kind):
                self.assertEqual(request.plan.conversation_ref, self.conversation)
                self.assertIsNone(request.plan.binding_target)
        foreground_clear = self.effects.store_requests[-1]
        self.assertIs(
            foreground_clear.plan.route_delete_condition,
            RouteDeleteCondition.UNLESS_BOUND_TO_ROUTE_THREAD,
        )

        non_foreground_effects = _StrictEffects()
        non_foreground = _new_conversation_actions(
            self.conversation,
            actor="actor-a",
            gateway_id="gateway-a",
            runtime=self.runtime,
            effects=non_foreground_effects,
            foreground_route=False,
        )
        await non_foreground.clear_observation(
            thread,
            action_id="clear-observation-1",
        )
        non_foreground_clear = non_foreground_effects.store_requests[-1]
        self.assertIsNone(non_foreground_clear.plan.route_delete_condition)
        self.assertNotEqual(
            foreground_clear.fingerprint.payload_fingerprint,
            non_foreground_clear.fingerprint.payload_fingerprint,
        )

    async def test_projection_activation_failure_turns_durable_route_success_partial(self) -> None:
        thread = await _seed_thread(self.runtime)
        self.runtime.projection_reconciliation_error = ActionError(
            ActionErrorCode.CAPACITY_EXHAUSTED,
            OperationErrorCode.CAPACITY_EXHAUSTED,
        )

        result = await self.actions.observe_thread(thread, action_id="observe-capacity")

        self.assertIsInstance(result, Partial)
        assert isinstance(result, Partial)
        self.assertEqual(
            result.value.route_id,
            derive_projection_route_id(thread, self.conversation),
        )
        self.assertEqual(result.error.code, ActionErrorCode.CAPACITY_EXHAUSTED)
        self.assertEqual(
            result.error.operation_error_code,
            OperationErrorCode.CAPACITY_EXHAUSTED,
        )
        self.assertEqual(self.runtime.completed_route_statuses, [False])

        self.runtime.projection_reconciliation_error = None
        replayed = await self.actions.observe_thread(thread, action_id="observe-capacity")

        self.assertIsInstance(replayed, Succeeded)
        self.assertEqual(
            self.runtime.completed_route_ids,
            [derive_projection_route_id(thread, self.conversation)],
        )
        self.assertEqual(self.runtime.completed_route_statuses, [False])

    async def test_post_receipt_cancellation_joins_projection_reconciliation(self) -> None:
        thread = await _seed_thread(self.runtime)
        self.runtime.projection_reconciliation_entered = asyncio.Event()
        self.runtime.projection_reconciliation_release = asyncio.Event()

        action = asyncio.create_task(
            self.actions.observe_thread(thread, action_id="observe-cancelled")
        )
        await self.runtime.projection_reconciliation_entered.wait()
        action.cancel()
        await asyncio.sleep(0)
        self.assertFalse(action.done())

        self.runtime.projection_reconciliation_release.set()
        result = await action

        self.assertIsInstance(result, Succeeded)
        self.assertEqual(
            self.runtime.completed_route_ids,
            [derive_projection_route_id(thread, self.conversation)],
        )
        self.assertEqual(self.runtime.completed_route_statuses, [True])

    async def test_repeated_post_receipt_cancellation_keeps_route_barrier_closed(self) -> None:
        thread = await _seed_thread(self.runtime)
        self.runtime.projection_reconciliation_entered = asyncio.Event()
        self.runtime.projection_reconciliation_release = asyncio.Event()

        action = asyncio.create_task(
            self.actions.observe_thread(thread, action_id="observe-cancelled-twice")
        )
        await self.runtime.projection_reconciliation_entered.wait()
        action.cancel()
        await asyncio.sleep(0)
        action.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await action
        self.assertEqual(
            self.runtime.completed_route_ids,
            [derive_projection_route_id(thread, self.conversation)],
        )
        self.assertEqual(self.runtime.completed_route_statuses, [False])

    async def test_respond_request_exists_only_on_authorized_conversation_scope(self) -> None:
        request_ref = RequestRef(self.application_ref, "request-1")
        result = await self.actions.respond_request(
            request_ref,
            ApprovalResponse("approve"),
            action_id="respond-1",
        )
        self.assertIsInstance(result, Succeeded)
        self.assertEqual(self.runtime.authorized_requests[0][0], self.conversation)
        self.assertEqual(self.runtime.request_calls[0][0], self.conversation)
        self.assertIs(self.effects.native_requests[-1].category, EffectCategory.REQUEST_RESPONSE)
        direct = self.actions._application(self.application_ref)
        self.assertFalse(hasattr(direct, "respond_request"))

    async def test_request_authorization_precedes_the_durable_native_fence(self) -> None:
        self.runtime.reject_request = True
        result = await self.actions.respond_request(
            RequestRef(self.application_ref, "request-2"),
            ApprovalResponse("approve"),
            action_id="respond-2",
        )
        self.assertIsInstance(result, Failed)
        self.assertEqual(len(self.effects.native_requests), 1)
        self.assertEqual(self.runtime.request_calls, [])


class ScopedActionStoreIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = _Runtime()
        self.effects = _StrictEffects()
        self.conversation = ConversationRef("channel-a", "conversation-a")
        self.application_ref = self.runtime.application.summary.ref
        self.actions = _new_conversation_actions(
            self.conversation,
            actor="actor-a",
            gateway_id="gateway-a",
            runtime=self.runtime,
            effects=self.effects,
            foreground_route=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _factories(self) -> tuple[Callable[[], GatewayStore], ...]:
        path = Path(self.temporary.name) / "actions.sqlite3"
        return (
            MemoryGatewayStore,
            lambda: SQLiteGatewayStore(path),
        )

    async def test_route_policy_invariants_cross_the_real_effect_seam(self) -> None:
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway-actions",
                    owner_token="owner-actions",
                    lease_duration_seconds=30,
                )
                try:
                    runtime = _Runtime()
                    thread = await _seed_thread(runtime)
                    conversation = ConversationRef("channel", "foreground")
                    actions = _new_conversation_actions(
                        conversation,
                        actor="actor",
                        gateway_id="gateway-actions",
                        runtime=runtime,
                        effects=StoreBackedGatewayEffectExecutor(session),
                        foreground_route=True,
                    )

                    bound = await actions.bind_thread(thread, action_id="bind")
                    self.assertIsInstance(bound, Succeeded)
                    binding = await session.get(conversation)
                    assert binding is not None
                    self.assertEqual(binding.thread_ref, thread)
                    routes = await session.list_projection_routes(thread)
                    foreground = tuple(
                        route for route in routes if route.conversation_ref == conversation
                    )
                    self.assertEqual(len(foreground), 1)

                    await actions.observe_thread(
                        thread,
                        action_id="observe",
                        reply_to_message_id="reply-context",
                    )
                    protected = await actions.clear_observation(
                        thread,
                        action_id="clear-protected",
                    )
                    self.assertIsInstance(protected, Succeeded)
                    routes = await session.list_projection_routes(thread)
                    foreground = tuple(
                        route for route in routes if route.conversation_ref == conversation
                    )
                    self.assertEqual(len(foreground), 1)
                    self.assertEqual(foreground[0].reply_to_message_id, "reply-context")

                    cleared_binding = await actions.clear_thread(
                        action_id="clear-binding",
                        expected_generation=binding.generation,
                    )
                    self.assertIsInstance(cleared_binding, Succeeded)
                    replayed = await actions.clear_observation(
                        thread,
                        action_id="clear-protected",
                    )
                    self.assertEqual(replayed, protected)
                    self.assertEqual(
                        len(
                            tuple(
                                route
                                for route in await session.list_projection_routes(thread)
                                if route.conversation_ref == conversation
                            )
                        ),
                        1,
                    )
                    deleted = await actions.clear_observation(
                        thread,
                        action_id="clear-after-unbind",
                    )
                    self.assertIsInstance(deleted, Succeeded)
                    self.assertFalse(
                        any(
                            route.conversation_ref == conversation
                            for route in await session.list_projection_routes(thread)
                        )
                    )

                    observer = ConversationRef("channel", "observer")
                    observer_actions = _new_conversation_actions(
                        observer,
                        actor="actor",
                        gateway_id="gateway-actions",
                        runtime=runtime,
                        effects=StoreBackedGatewayEffectExecutor(session),
                        foreground_route=False,
                    )
                    await observer_actions.bind_thread(thread, action_id="bind")
                    self.assertFalse(
                        any(
                            route.conversation_ref == observer
                            for route in await session.list_projection_routes(thread)
                        )
                    )
                    await observer_actions.observe_thread(thread, action_id="observe")
                    await observer_actions.clear_observation(thread, action_id="clear")
                    self.assertFalse(
                        any(
                            route.conversation_ref == observer
                            for route in await session.list_projection_routes(thread)
                        )
                    )
                    observer_binding = await session.get(observer)
                    assert observer_binding is not None
                    self.assertEqual(observer_binding.thread_ref, thread)
                finally:
                    await session.release_runtime()
                    await store.close()

    async def test_missing_resources_never_mutate_real_store_state(self) -> None:
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway-resource-preflight",
                    owner_token="owner-resource-preflight",
                    lease_duration_seconds=30,
                )
                try:
                    runtime = _Runtime()
                    thread = await _seed_thread(runtime)
                    conversation = ConversationRef("channel", "resource-preflight")
                    actions = _new_conversation_actions(
                        conversation,
                        actor="actor",
                        gateway_id="gateway-resource-preflight",
                        runtime=runtime,
                        effects=StoreBackedGatewayEffectExecutor(session),
                        foreground_route=False,
                    )
                    bound = await actions.bind_thread(thread, action_id="bind-existing")
                    self.assertIsInstance(bound, Succeeded)
                    before = await session.get(conversation)
                    assert before is not None

                    missing_application = ApplicationRef("missing-application")
                    missing_project = ProjectRef(
                        runtime.application.summary.ref.application_instance_id,
                        "missing-project",
                    )
                    missing_thread = ThreadRef(
                        runtime.application.default_project_ref,
                        "missing-thread",
                    )
                    foreign_project = ProjectRef("foreign-application", "foreign-project")
                    foreign_thread = ThreadRef(foreign_project, "foreign-thread")
                    results = (
                        await actions.select_application(
                            missing_application,
                            action_id="select-missing-application",
                        ),
                        await actions.select_project(
                            missing_project,
                            action_id="select-missing-project",
                        ),
                        await actions.bind_thread(
                            missing_thread,
                            action_id="bind-missing-thread",
                        ),
                        await actions.observe_thread(
                            missing_thread,
                            action_id="observe-missing-thread",
                        ),
                        await actions.select_project(
                            foreign_project,
                            action_id="select-foreign-project",
                        ),
                        await actions.bind_thread(
                            foreign_thread,
                            action_id="bind-foreign-thread",
                        ),
                        await actions.observe_thread(
                            foreign_thread,
                            action_id="observe-foreign-thread",
                        ),
                    )
                    for result in results:
                        self.assertIsInstance(result, Failed)
                        assert isinstance(result, Failed)
                        self.assertEqual(
                            result.error.operation_error_code,
                            OperationErrorCode.NOT_FOUND,
                        )
                    self.assertEqual(await session.get(conversation), before)
                    self.assertEqual(
                        await session.list_projection_routes(missing_thread),
                        (),
                    )

                    operation_count = len(runtime.operations)
                    runtime.application_summaries = ()
                    replayed_binding = await actions.bind_thread(
                        thread,
                        action_id="bind-existing",
                    )
                    self.assertEqual(replayed_binding, bound)
                    self.assertEqual(len(runtime.operations), operation_count)

                    runtime.application_summaries = (
                        replace(runtime.application.summary, ref=missing_application),
                    )
                    replayed_missing = await actions.select_application(
                        missing_application,
                        action_id="select-missing-application",
                    )
                    self.assertEqual(replayed_missing, results[0])
                    self.assertEqual(await session.get(conversation), before)
                finally:
                    await session.release_runtime()
                    await store.close()

    async def test_unsupported_streaming_never_creates_route_or_native_thread(self) -> None:
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway-streaming-preflight",
                    owner_token="owner-streaming-preflight",
                    lease_duration_seconds=30,
                )
                try:
                    runtime = _Runtime()
                    thread = await _seed_thread(runtime)
                    base = runtime.application.summary
                    runtime.application_summaries = (
                        replace(
                            base,
                            capabilities=replace(
                                base.capabilities,
                                runtime=replace(
                                    base.capabilities.runtime,
                                    streaming=SupportLevel.UNSUPPORTED,
                                ),
                            ),
                        ),
                    )
                    conversation = ConversationRef("channel", "streaming-preflight")
                    actions = _new_conversation_actions(
                        conversation,
                        actor="actor",
                        gateway_id="gateway-streaming-preflight",
                        runtime=runtime,
                        effects=StoreBackedGatewayEffectExecutor(session),
                        foreground_route=True,
                    )
                    results = (
                        await actions.observe_thread(
                            thread,
                            action_id="observe-without-streaming",
                        ),
                        await actions.bind_thread(
                            thread,
                            action_id="bind-without-streaming",
                        ),
                        await actions.create_and_bind_thread(
                            thread.project_ref,
                            action_id="create-bind-without-streaming",
                        ),
                    )
                    self.assertTrue(all(isinstance(result, Failed) for result in results))
                    self.assertEqual(runtime.operations, [])
                    self.assertIsNone(await session.get(conversation))
                    self.assertEqual(await session.list_projection_routes(thread), ())
                finally:
                    await session.release_runtime()
                    await store.close()

    async def test_native_terminal_replay_precedes_changed_capability_preflight(self) -> None:
        for factory in self._factories():
            store = factory()
            with self.subTest(store=type(store).__name__):
                session = await store.acquire_runtime(
                    gateway_id="gateway-native-preflight-replay",
                    owner_token="owner-native-preflight-replay",
                    lease_duration_seconds=30,
                )
                try:
                    runtime = _Runtime()
                    actions = _new_conversation_actions(
                        ConversationRef("channel", "native-preflight-replay"),
                        actor="actor",
                        gateway_id="gateway-native-preflight-replay",
                        runtime=runtime,
                        effects=StoreBackedGatewayEffectExecutor(session),
                        foreground_route=False,
                    )
                    first = await actions.create_project(
                        runtime.application.summary.ref,
                        cwd="/replay-project",
                        action_id="create-replay-project",
                    )
                    self.assertIsInstance(first, Succeeded)
                    self.assertEqual(len(runtime.operations), 1)

                    runtime.application_summaries = ()
                    replay = await actions.create_project(
                        runtime.application.summary.ref,
                        cwd="/replay-project",
                        action_id="create-replay-project",
                    )
                    self.assertEqual(replay, first)
                    self.assertEqual(len(runtime.operations), 1)
                finally:
                    await session.release_runtime()
                    await store.close()

    async def test_request_terminal_receipt_replays_before_reauthorization(self) -> None:
        request_ref = RequestRef(self.application_ref, "request-replay")
        first = await self.actions.respond_request(
            request_ref,
            ApprovalResponse("approve"),
            action_id="respond-replay",
        )
        self.assertIsInstance(first, Succeeded)
        self.runtime.reject_request = True
        replay = await self.actions.respond_request(
            request_ref,
            ApprovalResponse("approve"),
            action_id="respond-replay",
        )
        self.assertEqual(replay, first)
        self.assertEqual(len(self.runtime.authorized_requests), 1)
        self.assertEqual(len(self.runtime.request_calls), 1)

    async def test_request_success_must_name_the_authorized_request(self) -> None:
        self.runtime.wrong_request_result = True
        result = await self.actions.respond_request(
            RequestRef(self.application_ref, "request-3"),
            ApprovalResponse("approve"),
            action_id="respond-3",
        )
        self.assertIsInstance(result, OutcomeUnknown)

    async def test_native_payload_is_snapshotted_before_fingerprinting_and_fencing(self) -> None:
        metadata: dict[str, object] = {"nested": {"value": "original"}}
        answers = {"question-1": ("original",)}

        def mutate_inputs() -> None:
            nested = metadata["nested"]
            assert isinstance(nested, dict)
            nested["value"] = "mutated"
            answers["question-1"] = ("mutated",)

        direct = self.actions._application(self.application_ref)
        self.effects.before_native = mutate_inputs
        capabilities = replace(
            self.runtime.application.summary.capabilities,
            attachment_sources=(AttachmentSourceKind.ATTACHMENT_HANDLE,),
        )
        self.runtime.application_summaries = (
            replace(self.runtime.application.summary, capabilities=capabilities),
        )
        project = self.runtime.application.default_project_ref
        await direct.create_thread(
            project,
            action_id="snapshot-thread",
            initial_context=(
                AttachmentContent(
                    attachment_id="attachment-1",
                    media_type="text/plain",
                    source=AttachmentHandle("handle-1"),
                    metadata=metadata,
                ),
            ),
        )
        created = self.runtime.operations[-1]
        assert isinstance(created, CreateThread)
        created_attachment = created.initial_context[0]
        assert isinstance(created_attachment, AttachmentContent)
        created_nested = created_attachment.metadata["nested"]
        assert isinstance(created_nested, Mapping)
        self.assertEqual(
            created_nested["value"],
            "original",
        )

        metadata["nested"] = {"value": "second mutation"}
        answers["question-1"] = ("original",)
        self.effects.before_native = mutate_inputs
        await self.actions.respond_request(
            RequestRef(self.application_ref, "request-snapshot"),
            UserInputResponse(answers),
            action_id="snapshot-response",
        )
        invoked_response = self.runtime.request_calls[-1][3]
        assert isinstance(invoked_response, UserInputResponse)
        self.assertEqual(invoked_response.answers["question-1"], ("original",))


if __name__ == "__main__":
    unittest.main()

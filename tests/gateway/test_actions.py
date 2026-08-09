from __future__ import annotations

import inspect
import tempfile
import unittest
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args, get_type_hints
from unittest.mock import patch

import imagent
import imagent.gateway as gateway_facade
import imagent.gateway.actions as actions_owner
from imagent.applications.capabilities import ProjectMode
from imagent.applications.contract import ApplicationSummary, ProjectRef, ThreadRef
from imagent.applications.operations import ApplicationOperation, CreateThread
from imagent.applications.requests import (
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
from imagent.interaction.controllers import CommandInvocation, CommandRegistry, CommandResult
from imagent.interaction.media import AttachmentContent, AttachmentHandle
from imagent.interaction.messages import ConversationRef, InboundMessage, TextContent
from imagent.interaction.operations import ContractViolation
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
        self.application_summaries: tuple[ApplicationSummary, ...] = (self.application.summary,)

    def list_applications(self):
        return self.application_summaries

    async def execute_application(self, operation: ApplicationOperation):
        self.operations.append(operation)
        return await self.application.execute(operation)

    async def reconcile_application(self, operation: ApplicationOperation):
        del operation
        return None

    async def get_binding(self, conversation_ref: ConversationRef):
        self.binding_reads += 1
        if self.binding is not None and self.binding.conversation_ref != conversation_ref:
            raise AssertionError("ConversationActions substituted its Conversation")
        return self.binding

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
        self.native_requests: list[NativeMutationRequest] = []
        self.workflow_requests: list[CreateBindingWorkflowRequest] = []
        self.partial_workflow = False
        self.unknown_workflow = False
        self.before_native: Callable[[], None] | None = None
        self.native_outcomes: dict[str, ActionOutcome] = {}

    async def execute_store_mutation(self, request: StoreMutationRequest) -> ActionOutcome:
        self.store_requests.append(request)
        plan = request.plan
        reference = None
        conversation_ref = plan.conversation_ref if plan.binding_clear is not None else None
        if plan.binding_target is not None:
            target = plan.binding_target
            conversation_ref = target.conversation_ref
            selected = target.thread_ref or target.project_ref or target.application_ref
            if selected is not None:
                reference = StableReference.from_value(selected)
        return Succeeded(
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
        del preflight, reconcile
        self.workflow_requests.append(request)
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
        thread = ThreadRef(ProjectRef(self.application_ref.application_instance_id, "p1"), "t1")
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

        await self.actions.bind_thread(
            thread,
            expected_generation=10,
            action_id="bind-1",
        )
        self.assertNotEqual(
            self.effects.store_requests[-2].fingerprint.payload_fingerprint,
            self.effects.store_requests[-1].fingerprint.payload_fingerprint,
        )

        with self.assertRaises(ContractViolation):
            await self.actions.bind_thread(
                thread,
                expected_generation=10,
                action_id="",
            )
        self.assertEqual(len(self.effects.store_requests), 2)

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
        self.assertEqual(len(self.runtime.operations), 1)

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

    async def test_route_only_actions_name_scope_without_a_synthetic_binding(self) -> None:
        thread = ThreadRef(ProjectRef(self.application_ref.application_instance_id, "p1"), "t1")
        await self.actions.observe_thread(thread, action_id="observe-1")
        await self.actions.clear_observation(thread, action_id="clear-observation-1")
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
                    application_ref = runtime.application.summary.ref
                    thread = ThreadRef(
                        ProjectRef(application_ref.application_instance_id, "project"),
                        "thread",
                    )
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
        project = ProjectRef(self.application_ref.application_instance_id, "p1")
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

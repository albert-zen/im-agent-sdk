from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import imagent
import imagent.contracts as contracts_facade
import imagent.gateway as gateway_facade
import imagent.gateway.input as input_facade
from imagent.applications.capabilities import ProjectMode
from imagent.applications.contract import AgentApplicationAdapter, AgentInput, ProjectRef
from imagent.applications.operations import GetProject, GetThread, GetThreadHistory
from imagent.gateway import (
    GatewayExtensions,
    GatewayLimits,
    GatewayRepositories,
    ImAgentGateway,
    MissingBindingError,
    StaleBindingError,
)
from imagent.gateway.actions import ActionResult, ConversationActions
from imagent.gateway.input import InboundFailurePhase
from imagent.gateway.outcomes import Partial, Succeeded
from imagent.gateway.persistence import InMemoryIdempotencyRepository
from imagent.gateway.persistence.effects import ActionErrorCode
from imagent.gateway.persistence.memory import InMemoryProjectionRouteRepository
from imagent.gateway.persistence.memory_store import MemoryGatewayStore
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.persistence.state_contracts import (
    ConversationBinding,
    ThreadProjectionRoute,
)
from imagent.gateway.routing.projection_routes import ProjectionPolicy
from imagent.interaction.controllers import (
    CommandExecutionSafety,
    CommandInvocation,
    CommandRegistry,
    CommandResult,
)
from imagent.interaction.messages import (
    ConversationRef,
    InboundMessage,
    OutboundMessage,
    TextContent,
)
from imagent.interaction.operations import (
    ContractViolation,
    OperationErrorCode,
    operation_error,
)
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter
from tests._command_support import common_command_registry


class _RecordingTransformer:
    def __init__(self) -> None:
        self.messages: list[InboundMessage] = []

    async def transform_content(self, message: InboundMessage):
        self.messages.append(message)
        return message.content


class _RecordingApplication(FakeAgentApplicationAdapter):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.operations: list[object] = []

    async def execute(self, operation):
        self.operations.append(operation)
        return await super().execute(operation)


class _StaticBindingRepository:
    def __init__(self, binding: ConversationBinding) -> None:
        self.binding = binding
        self.write_attempts = 0

    async def get(self, conversation: ConversationRef) -> ConversationBinding | None:
        del conversation
        return self.binding

    async def put(
        self,
        binding: ConversationBinding,
        expected_generation: int | None = None,
    ) -> ConversationBinding:
        del binding, expected_generation
        self.write_attempts += 1
        raise AssertionError("ordinary dispatch must not mutate its binding repository")

    async def delete(
        self,
        conversation: ConversationRef,
        expected_generation: int | None = None,
    ) -> None:
        del conversation, expected_generation
        self.write_attempts += 1
        raise AssertionError("ordinary dispatch must not mutate its binding repository")


class _ExplicitOnboardingController:
    def __init__(self, application: FakeAgentApplicationAdapter) -> None:
        self._application = application
        self.messages: list[InboundMessage] = []
        self.actions: list[ConversationActions] = []

    async def handle(
        self,
        message: InboundMessage,
        actions: ConversationActions,
    ) -> None:
        self.messages.append(message)
        self.actions.append(actions)
        binding = await actions.get_binding()
        if binding is not None and binding.thread_ref is not None:
            return None
        identity = message.conversation_ref.native_conversation_id
        project = await actions.create_and_select_project(
            self._application.summary.ref,
            cwd=f"/explicit/{identity}",
            action_id=f"{message.message_id}:create-and-select-project",
        )
        if not isinstance(project, Succeeded) or not isinstance(project.value.ref, ProjectRef):
            raise RuntimeError(f"explicit Project onboarding failed: {project!r}")
        thread = await actions.create_and_bind_thread(
            project.value.ref,
            title=f"Thread for {identity}",
            action_id=f"{message.message_id}:create-and-bind-thread",
        )
        if not isinstance(thread, Succeeded):
            raise RuntimeError(f"explicit Thread onboarding failed: {thread!r}")
        return None


class _RecordingFailurePresenter:
    def __init__(self) -> None:
        self.phases: list[InboundFailurePhase] = []

    async def present_failure(
        self,
        phase: InboundFailurePhase,
        *,
        conversation_ref: ConversationRef,
        delivery_id: str,
        reply_to_message_id: str,
    ) -> OutboundMessage:
        self.phases.append(phase)
        return OutboundMessage(
            delivery_id=delivery_id,
            conversation_ref=conversation_ref,
            content=(TextContent("binding required"),),
            created_at=datetime.now(UTC),
            reply_to=reply_to_message_id,
        )


class PolicyFreeOrdinaryInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_binding_is_exact_typed_failure_without_effects(self) -> None:
        for binding_depth in ("none", "application", "project"):
            with self.subTest(binding_depth=binding_depth):
                store = MemoryGatewayStore()
                session = await store.acquire_runtime(
                    gateway_id=f"missing-{binding_depth}",
                    owner_token="owner-1",
                    lease_duration_seconds=30,
                )
                channel = FakeChannelAdapter()
                application = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
                conversation = ConversationRef("fake-channel", "conversation-1")
                stored_binding = None
                if binding_depth != "none":
                    stored_binding = await session.put(
                        ConversationBinding(
                            conversation_ref=conversation,
                            application_ref=application.summary.ref,
                            project_ref=(
                                application.default_project_ref
                                if binding_depth == "project"
                                else None
                            ),
                        )
                    )
                transformer = _RecordingTransformer()
                gateway = ImAgentGateway(
                    channels=[channel],
                    applications=[application],
                    repositories=GatewayRepositories(bindings=session),
                    extensions=GatewayExtensions(
                        inbound_content_transformer=transformer,
                    ),
                )
                message = _message(conversation, "unbound-message", "ordinary text")
                before = (
                    len(application._projects),
                    len(application._threads),
                    len(application._inputs),
                )

                await gateway.start()
                try:
                    for _ in range(2):
                        with self.assertRaises(MissingBindingError) as raised:
                            await channel.emit_message(message)
                        self.assertEqual(
                            operation_error(raised.exception).code,
                            OperationErrorCode.MISSING_BINDING.value,
                        )
                finally:
                    await gateway.stop()

                self.assertEqual(
                    (
                        len(application._projects),
                        len(application._threads),
                        len(application._inputs),
                    ),
                    before,
                )
                self.assertEqual(await session.get(conversation), stored_binding)
                self.assertEqual(await session.list_projection_routes(), ())
                self.assertEqual(transformer.messages, [])
                self.assertEqual(channel.sent, [])
                await session.close()
                await store.close()

    async def test_missing_binding_uses_existing_pre_acceptance_presentation_path(
        self,
    ) -> None:
        store = MemoryGatewayStore()
        session = await store.acquire_runtime(
            gateway_id="missing-binding-presentation",
            owner_token="owner-1",
            lease_duration_seconds=30,
        )
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
        presenter = _RecordingFailurePresenter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=session),
            extensions=GatewayExtensions(inbound_failure_presenter=presenter),
        )
        conversation = ConversationRef("fake-channel", "conversation-1")
        message = _message(conversation, "present-missing", "ordinary text")

        await gateway.start()
        try:
            await channel.emit_message(message)
            await channel.emit_message(message)
        finally:
            await gateway.stop()

        self.assertEqual(presenter.phases, [InboundFailurePhase.PRE_ACCEPTANCE])
        self.assertEqual(len(channel.sent), 1)
        self.assertIsNone(await session.get(conversation))
        self.assertEqual(await session.list_projection_routes(), ())
        self.assertEqual(application._threads, {})
        self.assertEqual(application._inputs, [])
        await session.close()
        await store.close()

    async def test_stale_native_ancestry_is_typed_and_side_effect_free_in_memory_and_sqlite(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for store_kind in ("memory", "sqlite"):
                for stale_kind in ("project", "thread", "application"):
                    with self.subTest(store=store_kind, stale=stale_kind):
                        store = (
                            MemoryGatewayStore()
                            if store_kind == "memory"
                            else SQLiteGatewayStore(Path(directory) / f"{stale_kind}.sqlite3")
                        )
                        session = await store.acquire_runtime(
                            gateway_id=f"stale-{store_kind}-{stale_kind}",
                            owner_token="owner-1",
                            lease_duration_seconds=30,
                        )
                        channel = FakeChannelAdapter()
                        application = _RecordingApplication(
                            application_instance_id=f"application-{store_kind}-{stale_kind}",
                            project_mode=ProjectMode.MANAGED,
                        )
                        project_ref = application.default_project_ref
                        thread = await application.create_thread(project_ref)
                        conversation = ConversationRef("fake-channel", "conversation-1")
                        binding = await session.put(
                            ConversationBinding(
                                conversation_ref=conversation,
                                application_ref=application.summary.ref,
                                project_ref=project_ref,
                                thread_ref=thread.ref,
                            )
                        )
                        if stale_kind == "project":
                            application._projects.pop(project_ref)
                        elif stale_kind == "thread":
                            await application.delete_thread(thread.ref)
                        configured_applications: list[AgentApplicationAdapter] = (
                            [] if stale_kind == "application" else [application]
                        )
                        transformer = _RecordingTransformer()
                        gateway = ImAgentGateway(
                            channels=[channel],
                            applications=configured_applications,
                            repositories=GatewayRepositories(bindings=session),
                            extensions=GatewayExtensions(
                                controller=(
                                    common_command_registry() if store_kind == "memory" else None
                                ),
                                inbound_content_transformer=transformer,
                            ),
                        )
                        message = _message(
                            conversation,
                            f"stale-{store_kind}-{stale_kind}",
                            "must not dispatch",
                        )

                        await gateway.start()
                        try:
                            for _ in range(2):
                                with self.assertRaises(StaleBindingError) as raised:
                                    await channel.emit_message(message)
                                self.assertEqual(
                                    operation_error(raised.exception).code,
                                    OperationErrorCode.STALE_BINDING.value,
                                )
                        finally:
                            await gateway.stop()

                        self.assertEqual(await session.get(conversation), binding)
                        self.assertEqual(await session.list_projection_routes(), ())
                        self.assertEqual(transformer.messages, [])
                        self.assertEqual(application._inputs, [])
                        self.assertEqual(channel.sent, [])
                        read_types = tuple(type(operation) for operation in application.operations)
                        if stale_kind == "project":
                            self.assertEqual(read_types, (GetProject, GetProject))
                        elif stale_kind == "thread":
                            self.assertEqual(
                                read_types,
                                (GetProject, GetThread, GetProject, GetThread),
                            )
                        else:
                            self.assertEqual(read_types, ())
                        await session.close()
                        await store.close()

    async def test_coherent_session_cannot_be_mixed_with_another_repository(self) -> None:
        store = MemoryGatewayStore()
        session = await store.acquire_runtime(
            gateway_id="mixed-session",
            owner_token="owner-1",
            lease_duration_seconds=30,
        )
        with self.assertRaisesRegex(ValueError, "cannot be mixed"):
            ImAgentGateway(
                channels=[],
                applications=[],
                repositories=GatewayRepositories(
                    bindings=session,
                    idempotency=InMemoryIdempotencyRepository(),
                ),
            )
        await session.close()
        await store.close()

    async def test_explicit_controller_onboarding_converges_on_one_dispatch_path(
        self,
    ) -> None:
        store = MemoryGatewayStore()
        session = await store.acquire_runtime(
            gateway_id="explicit-onboarding",
            owner_token="owner-1",
            lease_duration_seconds=30,
        )
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
        controller = _ExplicitOnboardingController(application)
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=session),
            extensions=GatewayExtensions(controller=controller),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )
        conversation = ConversationRef("fake-channel", "conversation-1")
        message = _message(conversation, "onboard-and-dispatch", "same ordinary message")

        await gateway.start()
        try:
            await channel.emit_message(message)
            await channel.emit_message(message)
        finally:
            await gateway.stop()

        binding = await session.get(conversation)
        self.assertIsNotNone(binding)
        assert binding is not None and binding.thread_ref is not None
        self.assertEqual(controller.messages, [message])
        self.assertIs(controller.messages[0], message)
        self.assertEqual(len(application._projects), 2)
        self.assertEqual(len(application._threads), 1)
        self.assertEqual(len(application._inputs), 1)
        self.assertEqual(application._inputs[0][0], binding.thread_ref)
        self.assertEqual(application._inputs[0][1].content, message.content)
        routes = await session.list_projection_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].conversation_ref, conversation)
        self.assertEqual(routes[0].thread_ref, binding.thread_ref)
        self._assert_scoped_surface_has_no_runtime_escape(controller.actions[0])
        await session.close()
        await store.close()

    async def test_common_new_activates_projection_and_delivers_later_authoritative_output(
        self,
    ) -> None:
        store = MemoryGatewayStore()
        session = await store.acquire_runtime(
            gateway_id="common-new-observation",
            owner_token="owner-1",
            lease_duration_seconds=30,
        )
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
        conversation = ConversationRef("fake-channel", "conversation-1")
        await session.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=application.summary.ref,
                project_ref=application.default_project_ref,
            )
        )
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=session),
            extensions=GatewayExtensions(controller=common_command_registry()),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )

        await gateway.start()
        try:
            await channel.emit_message(_message(conversation, "new-command", "/new Task"))
            binding = await session.get(conversation)
            self.assertIsNotNone(binding)
            assert binding is not None and binding.thread_ref is not None
            thread_ref = binding.thread_ref
            await _wait_until(lambda: gateway.get_projection_health(thread_ref) is not None)
            sent_before_native_output = len(channel.sent)
            await application.send_input(
                thread_ref,
                AgentInput(
                    client_message_id="later-authoritative-output",
                    content=(TextContent("native work"),),
                ),
            )
            await _wait_until(lambda: len(channel.sent) >= sent_before_native_output + 2)
        finally:
            await gateway.stop()

        self.assertGreaterEqual(len(channel.sent), 3)
        self.assertEqual(len(await session.list_projection_routes()), 1)
        await session.close()
        await store.close()

    async def test_registry_route_replay_reconciles_and_activation_failure_is_partial(
        self,
    ) -> None:
        store = MemoryGatewayStore()
        session = await store.acquire_runtime(
            gateway_id="registry-route-replay",
            owner_token="owner-1",
            lease_duration_seconds=30,
        )
        channel = FakeChannelAdapter()
        application = _RecordingApplication(project_mode=ProjectMode.MANAGED)
        first_thread = await application.create_thread(application.default_project_ref)
        replay_thread = await application.create_thread(application.default_project_ref)
        blocked_thread = await application.create_thread(application.default_project_ref)
        first_conversation = ConversationRef("fake-channel", "existing-observer")
        command_conversation = ConversationRef("fake-channel", "command-conversation")
        await session.put_projection_route(
            ThreadProjectionRoute(
                route_id="existing-route",
                thread_ref=first_thread.ref,
                conversation_ref=first_conversation,
            )
        )
        registry = CommandRegistry()
        outcomes: list[ActionResult] = []

        @registry.command("replay", safety=CommandExecutionSafety.EFFECTFUL)
        async def replay_route(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            del invocation
            outcome = await actions.observe_thread(
                replay_thread.ref,
                action_id="fixed-replayed-observation",
            )
            outcomes.append(outcome)
            return CommandResult.text(type(outcome).__name__)

        @registry.command("blocked", safety=CommandExecutionSafety.EFFECTFUL)
        async def blocked_route(
            invocation: CommandInvocation,
            actions: ConversationActions,
        ) -> CommandResult:
            del invocation
            outcome = await actions.observe_thread(
                blocked_thread.ref,
                action_id="capacity-blocked-observation",
            )
            outcomes.append(outcome)
            return CommandResult.text(type(outcome).__name__)

        registry.freeze()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=session),
            limits=GatewayLimits(projection_max_active_threads=1),
            extensions=GatewayExtensions(controller=registry),
            projection_policy=ProjectionPolicy.ALL_OBSERVERS,
        )

        await gateway.start()
        try:
            await channel.emit_message(_message(command_conversation, "replay-1", "/replay"))
            self.assertIsInstance(outcomes[-1], Partial)
            assert isinstance(outcomes[-1], Partial)
            self.assertEqual(outcomes[-1].error.code, ActionErrorCode.CAPACITY_EXHAUSTED)
            await session.delete_projection_routes(first_thread.ref)
            await gateway._projection_runtime.reconcile_action_route(None)
            await channel.emit_message(_message(command_conversation, "replay-2", "/replay"))
            await channel.emit_message(_message(command_conversation, "replay-3", "/replay"))
            self.assertIsInstance(outcomes[-2], Succeeded)
            self.assertIsInstance(outcomes[-1], Succeeded)
            history_reads = [
                operation
                for operation in application.operations
                if isinstance(operation, GetThreadHistory)
                and operation.thread_ref == replay_thread.ref
            ]
            self.assertGreaterEqual(len(history_reads), 2)
            await channel.emit_message(_message(command_conversation, "blocked-1", "/blocked"))
        finally:
            await gateway.stop()

        self.assertIsInstance(outcomes[-1], Partial)
        assert isinstance(outcomes[-1], Partial)
        self.assertEqual(outcomes[-1].error.code, ActionErrorCode.CAPACITY_EXHAUSTED)
        self.assertIsNone(gateway.get_projection_health(blocked_thread.ref))
        routes = await session.list_projection_routes()
        self.assertIn(blocked_thread.ref, {route.thread_ref for route in routes})
        await session.close()
        await store.close()

    async def test_two_conversations_keep_binding_route_and_dispatch_ancestry_isolated(
        self,
    ) -> None:
        store = MemoryGatewayStore()
        session = await store.acquire_runtime(
            gateway_id="conversation-isolation",
            owner_token="owner-1",
            lease_duration_seconds=30,
        )
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
        controller = _ExplicitOnboardingController(application)
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(bindings=session),
            extensions=GatewayExtensions(controller=controller),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )
        conversation_a = ConversationRef("fake-channel", "conversation-a")
        conversation_b = ConversationRef("fake-channel", "conversation-b")
        message_a = _message(conversation_a, "message-a", "input a")
        message_b = _message(conversation_b, "message-b", "input b")

        await gateway.start()
        try:
            await channel.emit_message(message_a)
            await channel.emit_message(message_b)
        finally:
            await gateway.stop()

        binding_a = await session.get(conversation_a)
        binding_b = await session.get(conversation_b)
        self.assertIsNotNone(binding_a)
        self.assertIsNotNone(binding_b)
        assert binding_a is not None and binding_a.thread_ref is not None
        assert binding_b is not None and binding_b.thread_ref is not None
        self.assertNotEqual(binding_a.project_ref, binding_b.project_ref)
        self.assertNotEqual(binding_a.thread_ref, binding_b.thread_ref)
        self.assertEqual(
            tuple(thread_ref for thread_ref, _ in application._inputs),
            (binding_a.thread_ref, binding_b.thread_ref),
        )
        self.assertEqual(
            tuple(agent_input.content for _, agent_input in application._inputs),
            (message_a.content, message_b.content),
        )
        routes = await session.list_projection_routes()
        self.assertEqual(
            {(route.conversation_ref, route.thread_ref) for route in routes},
            {
                (conversation_a, binding_a.thread_ref),
                (conversation_b, binding_b.thread_ref),
            },
        )
        await session.close()
        await store.close()

    async def test_foreign_resource_ancestry_cannot_enter_ordinary_dispatch(self) -> None:
        channel = FakeChannelAdapter()
        application_a = FakeAgentApplicationAdapter(
            application_instance_id="application-a",
            project_mode=ProjectMode.MANAGED,
        )
        application_b = FakeAgentApplicationAdapter(
            application_instance_id="application-b",
            project_mode=ProjectMode.MANAGED,
        )
        foreign_thread = await application_b.create_thread(application_b.default_project_ref)
        conversation = ConversationRef("fake-channel", "conversation-1")

        stored = ConversationBinding(
            conversation_ref=conversation,
            application_ref=application_a.summary.ref,
            project_ref=application_a.default_project_ref,
            thread_ref=foreign_thread.ref,
        )
        bindings = _StaticBindingRepository(stored)
        routes = InMemoryProjectionRouteRepository()
        transformer = _RecordingTransformer()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application_a, application_b],
            repositories=GatewayRepositories(
                bindings=bindings,
                idempotency=InMemoryIdempotencyRepository(),
                projections=routes,
            ),
            extensions=GatewayExtensions(inbound_content_transformer=transformer),
        )
        message = _message(conversation, "foreign-resource", "ordinary text")

        await gateway.start()
        try:
            with self.assertRaisesRegex(ContractViolation, "different application"):
                await channel.emit_message(message)
        finally:
            await gateway.stop()

        self.assertEqual(bindings.binding, stored)
        self.assertEqual(bindings.write_attempts, 0)
        self.assertEqual(await routes.list_projection_routes(), ())
        self.assertEqual(transformer.messages, [])
        self.assertEqual(application_a._inputs, [])
        self.assertEqual(application_b._inputs, [])

    async def test_foreign_conversation_binding_cannot_enter_ordinary_dispatch(self) -> None:
        channel = FakeChannelAdapter()
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.MANAGED)
        thread = await application.create_thread(application.default_project_ref)
        conversation = ConversationRef("fake-channel", "conversation-1")
        foreign_conversation = ConversationRef("fake-channel", "conversation-2")
        stored = ConversationBinding(
            conversation_ref=foreign_conversation,
            application_ref=application.summary.ref,
            project_ref=application.default_project_ref,
            thread_ref=thread.ref,
        )
        bindings = _StaticBindingRepository(stored)
        routes = InMemoryProjectionRouteRepository()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                idempotency=InMemoryIdempotencyRepository(),
                projections=routes,
            ),
        )
        message = _message(conversation, "foreign-conversation", "ordinary text")

        await gateway.start()
        try:
            with self.assertRaisesRegex(ContractViolation, "different Conversation"):
                await channel.emit_message(message)
        finally:
            await gateway.stop()

        self.assertEqual(bindings.write_attempts, 0)
        self.assertEqual(await routes.list_projection_routes(), ())
        self.assertEqual(application._inputs, [])

    def test_missing_binding_type_has_exact_public_and_negative_surfaces(self) -> None:
        self.assertIs(MissingBindingError, input_facade.MissingBindingError)
        self.assertIs(MissingBindingError, gateway_facade.MissingBindingError)
        self.assertFalse(hasattr(imagent, "MissingBindingError"))
        self.assertFalse(hasattr(contracts_facade, "MissingBindingError"))
        self.assertEqual(
            operation_error(MissingBindingError()).code,
            OperationErrorCode.MISSING_BINDING.value,
        )
        self.assertIs(StaleBindingError, input_facade.StaleBindingError)
        self.assertIs(StaleBindingError, gateway_facade.StaleBindingError)
        self.assertFalse(hasattr(imagent, "StaleBindingError"))
        self.assertFalse(hasattr(contracts_facade, "StaleBindingError"))
        self.assertEqual(
            operation_error(StaleBindingError()).code,
            OperationErrorCode.STALE_BINDING.value,
        )

    def _assert_scoped_surface_has_no_runtime_escape(
        self,
        actions: ConversationActions,
    ) -> None:
        public_names = {name for name in dir(actions) if not name.startswith("_")}
        self.assertTrue({"conversation_ref", "actor"}.issubset(public_names))
        self.assertTrue(
            public_names.isdisjoint(
                {
                    "adapter",
                    "application_adapter",
                    "effects",
                    "gateway",
                    "lease",
                    "receipt",
                    "repositories",
                    "repository",
                    "runtime",
                    "session",
                    "store",
                }
            )
        )


def _message(
    conversation_ref: ConversationRef,
    message_id: str,
    text: str,
) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation_ref,
        sender="user-1",
        content=(TextContent(text),),
        created_at=datetime.now(UTC),
    )


async def _wait_until(predicate, *, attempts: int = 1000) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")

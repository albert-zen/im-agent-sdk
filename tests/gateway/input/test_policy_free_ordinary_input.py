from __future__ import annotations

import unittest
from datetime import UTC, datetime

import imagent
import imagent.contracts as contracts_facade
import imagent.gateway as gateway_facade
import imagent.gateway.input as input_facade
from imagent.applications.capabilities import ProjectMode
from imagent.applications.contract import ProjectRef
from imagent.gateway import (
    GatewayExtensions,
    GatewayRepositories,
    ImAgentGateway,
    MissingBindingError,
)
from imagent.gateway.actions import ConversationActions
from imagent.gateway.input import InboundFailurePhase
from imagent.gateway.outcomes import Succeeded
from imagent.gateway.persistence import InMemoryIdempotencyRepository
from imagent.gateway.persistence.memory import InMemoryProjectionRouteRepository
from imagent.gateway.persistence.memory_store import MemoryGatewayStore
from imagent.gateway.persistence.state_contracts import ConversationBinding
from imagent.gateway.routing.projection_routes import ProjectionPolicy
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


class _RecordingTransformer:
    def __init__(self) -> None:
        self.messages: list[InboundMessage] = []

    async def transform_content(self, message: InboundMessage):
        self.messages.append(message)
        return message.content


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

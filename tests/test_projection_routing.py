from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from imagent.adapters import (
    ProjectionCheckpointConflict,
    ProjectionRouteConflict,
    ProjectionRouteRepository,
)
from imagent.bindings import InMemoryBindingRepository
from imagent.contracts import (
    ActivateNativeThread,
    AgentInput,
    ApplicationRef,
    BindConversationToThread,
    ContractViolation,
    ConversationBinding,
    ConversationBound,
    ConversationRef,
    InboundMessage,
    NativeThreadActivated,
    ObserveThread,
    ProjectionPolicy,
    ProjectMode,
    TextContent,
    ThreadObserved,
    ThreadProjectionRoute,
    ThreadRef,
)
from imagent.gateway import GatewayRepositories, ImAgentGateway
from imagent.projections import (
    InMemoryProjectionRouteRepository,
    ProjectionWorkerState,
    derive_projection_route_id,
)
from imagent.storage import SQLiteGatewayState
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class ProjectionRouteRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_preserves_checkpoint_and_rejects_checkpoint_change(
        self,
    ) -> None:
        for repository in await self._repositories():
            with self.subTest(repository=type(repository).__name__):
                thread = _thread_ref()
                conversation = ConversationRef("fake-channel", "conversation")
                route = _projection_route(thread, conversation)
                await repository.put_projection_route(route)
                checkpointed_at = datetime.now(UTC)
                advanced = await repository.advance_projection_checkpoint(
                    route.route_id,
                    expected_agent_item_id=None,
                    agent_item_id="agent-item-2",
                    checkpointed_at=checkpointed_at,
                )

                refreshed = await repository.put_projection_route(
                    ThreadProjectionRoute(
                        route_id=route.route_id,
                        thread_ref=thread,
                        conversation_ref=conversation,
                        reply_to_message_id="new-default-reply",
                    )
                )
                self.assertEqual(
                    refreshed.checkpoint_agent_item_id,
                    advanced.checkpoint_agent_item_id,
                )
                self.assertEqual(refreshed.checkpointed_at, checkpointed_at)
                self.assertEqual(
                    refreshed.reply_to_message_id,
                    "new-default-reply",
                )

                with self.assertRaises(ProjectionCheckpointConflict):
                    await repository.put_projection_route(
                        ThreadProjectionRoute(
                            route_id=route.route_id,
                            thread_ref=thread,
                            conversation_ref=conversation,
                            checkpoint_agent_item_id="agent-item-1",
                            checkpointed_at=checkpointed_at,
                        )
                    )
            await _close_if_needed(repository)

    async def test_checkpoint_advance_is_compare_and_swap(
        self,
    ) -> None:
        for repository in await self._repositories():
            with self.subTest(repository=type(repository).__name__):
                thread = _thread_ref()
                conversation = ConversationRef("fake-channel", "conversation")
                route = _projection_route(thread, conversation)
                await repository.put_projection_route(route)
                outcomes = await asyncio.gather(
                    repository.advance_projection_checkpoint(
                        route.route_id,
                        expected_agent_item_id=None,
                        agent_item_id="agent-item-a",
                        checkpointed_at=datetime.now(UTC),
                    ),
                    repository.advance_projection_checkpoint(
                        route.route_id,
                        expected_agent_item_id=None,
                        agent_item_id="agent-item-b",
                        checkpointed_at=datetime.now(UTC),
                    ),
                    return_exceptions=True,
                )
                self.assertEqual(
                    sum(isinstance(item, ThreadProjectionRoute) for item in outcomes),
                    1,
                )
                self.assertEqual(
                    sum(isinstance(item, ProjectionCheckpointConflict) for item in outcomes),
                    1,
                )
                winner = next(item for item in outcomes if isinstance(item, ThreadProjectionRoute))
                with self.assertRaises(ProjectionCheckpointConflict):
                    await repository.advance_projection_checkpoint(
                        route.route_id,
                        expected_agent_item_id=None,
                        agent_item_id="late-old-item",
                        checkpointed_at=datetime.now(UTC),
                    )
                stored = (await repository.list_projection_routes(thread))[0]
                self.assertEqual(
                    stored.checkpoint_agent_item_id,
                    winner.checkpoint_agent_item_id,
                )
            await _close_if_needed(repository)

    async def test_correlation_deletion_requires_a_selector(self) -> None:
        for repository in await self._repositories():
            with self.subTest(repository=type(repository).__name__):
                with self.assertRaises(ValueError):
                    await repository.delete_turn_reply_correlations()
            await _close_if_needed(repository)

    async def test_route_id_cannot_be_reused_for_different_endpoints(
        self,
    ) -> None:
        for repository in await self._repositories():
            with self.subTest(repository=type(repository).__name__):
                original = _projection_route(
                    _thread_ref(),
                    ConversationRef("fake-channel", "first"),
                )
                await repository.put_projection_route(original)
                conflicting = ThreadProjectionRoute(
                    route_id=original.route_id,
                    thread_ref=ThreadRef("fake-agent", "other-thread"),
                    conversation_ref=ConversationRef(
                        "fake-channel",
                        "second",
                    ),
                )
                with self.assertRaises(ProjectionRouteConflict):
                    await repository.put_projection_route(conflicting)
                with self.assertRaises(ProjectionRouteConflict):
                    await repository.put_projection_route(
                        ThreadProjectionRoute(
                            route_id="different-route-id",
                            thread_ref=original.thread_ref,
                            conversation_ref=original.conversation_ref,
                        )
                    )
                stored_routes = await repository.list_projection_routes()
                self.assertEqual(len(stored_routes), 1)
                self.assertEqual(stored_routes[0].route_id, original.route_id)
                self.assertEqual(stored_routes[0].thread_ref, original.thread_ref)
                self.assertEqual(
                    stored_routes[0].conversation_ref,
                    original.conversation_ref,
                )
            await _close_if_needed(repository)

    async def test_checkpoint_advance_validates_candidate_route(self) -> None:
        for repository in await self._repositories():
            with self.subTest(repository=type(repository).__name__):
                route = _projection_route(
                    _thread_ref(),
                    ConversationRef("fake-channel", "conversation"),
                )
                await repository.put_projection_route(route)
                with self.assertRaises(ContractViolation):
                    await repository.advance_projection_checkpoint(
                        route.route_id,
                        expected_agent_item_id=None,
                        agent_item_id="",
                        checkpointed_at=datetime.now(UTC),
                    )
            await _close_if_needed(repository)

    async def _repositories(self) -> tuple[ProjectionRouteRepository, ...]:
        self._temporary_directory = tempfile.TemporaryDirectory()
        sqlite = SQLiteGatewayState(Path(self._temporary_directory.name) / "gateway.sqlite3")
        return InMemoryProjectionRouteRepository(), sqlite

    async def asyncTearDown(self) -> None:
        temporary_directory = getattr(self, "_temporary_directory", None)
        if temporary_directory is not None:
            temporary_directory.cleanup()


class ProjectionRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_observation_and_native_activation_are_independent(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread = await application.create_thread()
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        gateway = ImAgentGateway(
            channels=[],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
                projections=projections,
            ),
        )
        await gateway.start()
        try:
            bound = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="bind-thread",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(bound, ConversationBound)
            self.assertEqual(await projections.list_projection_routes(), ())
            self.assertEqual(application.activated_threads, [])

            observed = await gateway.execute_gateway(
                ObserveThread(
                    operation_id="observe-thread",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread.ref,
                    reply_to_message_id="observe-message",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(observed, ThreadObserved)
            current_binding = await bindings.get(conversation)
            assert current_binding is not None
            self.assertEqual(
                current_binding.thread_ref,
                thread.ref,
            )
            self.assertEqual(application.activated_threads, [])
            self.assertEqual(
                (await projections.list_projection_routes())[0].conversation_ref,
                conversation,
            )

            activated = await gateway.execute_application(
                ActivateNativeThread(
                    operation_id="activate-thread",
                    application_ref=ApplicationRef("fake-agent"),
                    thread_ref=thread.ref,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(activated, NativeThreadActivated)
            self.assertEqual(application.activated_threads, [thread.ref])
        finally:
            await gateway.stop()

    async def test_foreground_switch_a_to_b_to_a_reconciles_missed_output(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread_a = await application.create_thread(title="A")
        thread_b = await application.create_thread(title="B")
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=ApplicationRef("fake-agent"),
                thread_ref=thread_a.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(conversation, "a-first"))
            await _wait_for_deliveries(channel, 2)

            first_binding = await bindings.get(conversation)
            assert first_binding is not None
            switched_b = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="bind-b",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread_b.ref,
                    expected_revision=first_binding.revision,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(switched_b, ConversationBound)
            health_a = gateway.get_projection_health(thread_a.ref)
            assert health_a is not None
            self.assertIs(health_a.state, ProjectionWorkerState.STOPPED)
            await channel.on_message(_inbound(conversation, "b-first"))
            await _wait_for_deliveries(channel, 4)

            await application.send_input(
                thread_a.ref,
                AgentInput(
                    client_message_id="background-a",
                    content=(TextContent("background"),),
                ),
            )
            await asyncio.sleep(0)
            self.assertEqual(len(channel.sent), 4)

            current = await bindings.get(conversation)
            assert current is not None
            switched_a = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="bind-a-again",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread_a.ref,
                    expected_revision=current.revision,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(switched_a, ConversationBound)
            observed = await gateway.execute_gateway(
                ObserveThread(
                    operation_id="observe-a-again",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread_a.ref,
                    reply_to_message_id="return-a",
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(observed, ThreadObserved)
            await _wait_for_deliveries(channel, 6)
            self.assertEqual(channel.sent[-1].reply_to, "return-a")
            async with asyncio.timeout(1):
                while True:
                    health_a = gateway.get_projection_health(thread_a.ref)
                    if health_a is not None and health_a.state is ProjectionWorkerState.RUNNING:
                        break
                    await asyncio.sleep(0)

            await application.send_input(
                thread_a.ref,
                AgentInput(
                    client_message_id="live-a-again",
                    content=(TextContent("live again"),),
                ),
            )
            await _wait_for_deliveries(channel, 8)
        finally:
            await gateway.stop()

    async def test_remembered_last_recipient_receives_background_thread_output(
        self,
    ) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread_a = await application.create_thread(title="A")
        thread_b = await application.create_thread(title="B")
        conversation = ConversationRef("fake-channel", "conversation")
        bindings = InMemoryBindingRepository()
        await bindings.put(
            ConversationBinding(
                conversation_ref=conversation,
                application_ref=ApplicationRef("fake-agent"),
                thread_ref=thread_a.ref,
            )
        )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
            projection_policy=ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
        )
        await gateway.start()
        try:
            await channel.on_message(_inbound(conversation, "a-input"))
            await _wait_for_deliveries(channel, 2)
            current = await bindings.get(conversation)
            assert current is not None
            switched = await gateway.execute_gateway(
                BindConversationToThread(
                    operation_id="bind-b",
                    conversation_ref=conversation,
                    actor="user",
                    thread_ref=thread_b.ref,
                    expected_revision=current.revision,
                    created_at=datetime.now(UTC),
                )
            )
            self.assertIsInstance(switched, ConversationBound)

            await application.send_input(
                thread_a.ref,
                AgentInput(
                    client_message_id="background-a",
                    content=(TextContent("background"),),
                ),
            )
            await _wait_for_deliveries(channel, 4)
            self.assertTrue(all(item.conversation_ref == conversation for item in channel.sent))
        finally:
            await gateway.stop()

    async def test_concurrent_threads_keep_independent_projection_routes(self) -> None:
        application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
        thread_a = await application.create_thread(title="A")
        thread_b = await application.create_thread(title="B")
        first = ConversationRef("fake-channel", "first")
        second = ConversationRef("fake-channel", "second")
        bindings = InMemoryBindingRepository()
        for conversation, thread in ((first, thread_a), (second, thread_b)):
            await bindings.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("fake-agent"),
                    thread_ref=thread.ref,
                )
            )
        channel = FakeChannelAdapter()
        gateway = ImAgentGateway(
            channels=[channel],
            applications=[application],
            repositories=GatewayRepositories(
                bindings=bindings,
            ),
        )
        await gateway.start()
        try:
            await asyncio.gather(
                channel.on_message(_inbound(first, "first-input")),
                channel.on_message(_inbound(second, "second-input")),
            )
            await _wait_for_deliveries(channel, 4)
        finally:
            await gateway.stop()

        self.assertEqual(
            [item.conversation_ref for item in channel.sent].count(first),
            2,
        )
        self.assertEqual(
            [item.conversation_ref for item in channel.sent].count(second),
            2,
        )

    async def test_restart_rebuilds_projection_from_route_and_authoritative_history(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.sqlite3"
            application = FakeAgentApplicationAdapter(project_mode=ProjectMode.FLAT)
            thread = await application.create_thread()
            conversation = ConversationRef("fake-channel", "conversation")
            first_state = SQLiteGatewayState(path)
            await first_state.put(
                ConversationBinding(
                    conversation_ref=conversation,
                    application_ref=ApplicationRef("fake-agent"),
                    thread_ref=thread.ref,
                )
            )
            first_channel = FakeChannelAdapter()
            first_gateway = ImAgentGateway(
                channels=[first_channel],
                applications=[application],
                repositories=GatewayRepositories(
                    bindings=first_state,
                    idempotency=first_state,
                    projections=first_state,
                ),
            )
            await first_gateway.start()
            try:
                await first_channel.on_message(_inbound(conversation, "before-restart"))
                await _wait_for_deliveries(first_channel, 2)
            finally:
                await first_gateway.stop()
                await first_state.close()

            await application.send_input(
                thread.ref,
                AgentInput(
                    client_message_id="while-bridge-stopped",
                    content=(TextContent("continue"),),
                ),
            )

            second_state = SQLiteGatewayState(path)
            second_channel = FakeChannelAdapter()
            second_gateway = ImAgentGateway(
                channels=[second_channel],
                applications=[application],
                repositories=GatewayRepositories(
                    bindings=second_state,
                    idempotency=second_state,
                    projections=second_state,
                ),
            )
            await second_gateway.start()
            try:
                await _wait_for_deliveries(second_channel, 2)
                self.assertTrue(all(message.reply_to is None for message in second_channel.sent))
            finally:
                await second_gateway.stop()
                await second_state.close()


def _inbound(conversation: ConversationRef, message_id: str) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        conversation_ref=conversation,
        sender="user",
        content=(TextContent("run"),),
        created_at=datetime.now(UTC),
    )


async def _wait_for_deliveries(
    channel: FakeChannelAdapter,
    count: int,
) -> None:
    async with asyncio.timeout(1):
        while len(channel.sent) < count:
            await asyncio.sleep(0)


def _thread_ref() -> ThreadRef:
    return ThreadRef(
        application_instance_id="fake-agent",
        native_thread_id="thread-1",
    )


def _projection_route(
    thread_ref: ThreadRef,
    conversation_ref: ConversationRef,
) -> ThreadProjectionRoute:
    return ThreadProjectionRoute(
        route_id=derive_projection_route_id(thread_ref, conversation_ref),
        thread_ref=thread_ref,
        conversation_ref=conversation_ref,
    )


async def _close_if_needed(repository: ProjectionRouteRepository) -> None:
    if isinstance(repository, SQLiteGatewayState):
        await repository.close()

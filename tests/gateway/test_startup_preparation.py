from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.reference_consumer.application import ReferenceApplication
from examples.reference_consumer.interaction import ReferenceChannel
from imagent import (
    Gateway,
    GatewayExtensions,
    GatewayLimits,
    MemoryGatewayStore,
    ProjectionPolicy,
    SQLiteGatewayStore,
    Succeeded,
)
from imagent.applications.contract import ProjectRef, ThreadRef
from imagent.gateway.lifecycle import GatewayLifecycleFailure
from imagent.gateway.startup import StartupConversationActions, StartupPreparation
from imagent.interaction.controllers import MarkdownRequestPresenter


class StartupPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_foreground_binding_is_cleared_before_any_owner_starts(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "bridge.sqlite3"
            app = ReferenceApplication()
            channel = ReferenceChannel()
            conversation = channel.conversation("paw").ref
            first = Gateway(
                gateway_id="prep",
                channels=[channel],
                applications=[app],
                store=SQLiteGatewayStore(database),
                projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
            )
            async with first:
                actions = first.actions(conversation, actor="owner")
                project = await actions.create_and_select_project(
                    app.ref, cwd=directory, action_id="project"
                )
                assert isinstance(project, Succeeded) and isinstance(project.value.ref, ProjectRef)
                created = await actions.create_and_bind_thread(
                    project.value.ref, action_id="thread"
                )
                assert isinstance(created, Succeeded) and isinstance(created.value.ref, ThreadRef)
                other = channel.conversation("ordinary").ref
                bound = await first.actions(other, actor="owner").bind_thread(
                    created.value.ref, action_id="other-bind"
                )
                self.assertIsInstance(bound, Succeeded)
            await app.receive_native_text(
                created.value.ref, client_message_id="offline", text="offline result"
            )
            await app.receive_native_approval_request(created.value.ref, prompt="offline approval")
            channel2 = ReferenceChannel()
            retained: list[StartupConversationActions] = []

            async def prepare(actions: StartupConversationActions) -> None:
                self.assertFalse(app.started)
                self.assertFalse(channel2.started)
                self.assertFalse(restarted.running)
                self.assertFalse(hasattr(actions, "create_thread"))
                binding = await actions.get_binding()
                assert binding is not None and binding.thread_ref == created.value.ref
                result = await actions.clear_thread(
                    action_id="paw-restore:g1", expected_generation=binding.generation
                )
                self.assertIsInstance(result, Succeeded)
                retained.append(actions)

            restarted = Gateway(
                gateway_id="prep",
                channels=[channel2],
                applications=[app],
                store=SQLiteGatewayStore(database),
                projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
                extensions=GatewayExtensions(
                    request_presenter=MarkdownRequestPresenter(),
                    startup_preparations=(
                        StartupPreparation(
                            conversation_ref=conversation, actor="paw", prepare=prepare
                        ),
                    ),
                ),
            )
            async with restarted:
                binding = await restarted.actions(conversation, actor="owner").get_binding()
                assert binding is not None
                self.assertIsNone(binding.thread_ref)
                await channel2.wait_for_text(
                    "Neutral response: offline result", conversation_ref=other
                )
                async with asyncio.timeout(2.0):
                    while not any(
                        "offline approval" in getattr(content, "text", "")
                        for message in channel2.sent
                        for content in message.content
                    ):
                        await asyncio.sleep(0)
                self.assertTrue(channel2.sent)
                self.assertFalse(
                    any(item.conversation_ref == conversation for item in channel2.sent)
                )
                with self.assertRaises(RuntimeError):
                    await retained[0].get_binding()

    async def test_failure_timeout_and_cancellation_never_start_owners(self) -> None:
        for mode in ("failure", "timeout", "cancel"):
            with self.subTest(mode=mode):
                app = ReferenceApplication()
                channel = ReferenceChannel()
                entered = asyncio.Event()
                release = asyncio.Event()
                retained: list[StartupConversationActions] = []

                async def prepare(actions: StartupConversationActions) -> None:
                    retained.append(actions)
                    entered.set()
                    if mode == "failure":
                        raise ValueError("private callback detail")
                    await release.wait()

                gateway = Gateway(
                    gateway_id="failure-" + mode,
                    channels=[channel],
                    applications=[app],
                    store=MemoryGatewayStore(),
                    limits=GatewayLimits(lifecycle_owner_timeout_seconds=0.03),
                    extensions=GatewayExtensions(
                        startup_preparations=(
                            StartupPreparation(
                                conversation_ref=channel.conversation("scope").ref,
                                actor="owner",
                                prepare=prepare,
                            ),
                        )
                    ),
                )
                task = asyncio.create_task(gateway.start())
                await entered.wait()
                if mode == "cancel":
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with self.assertRaises(GatewayLifecycleFailure) as failure:
                        await task
                    self.assertNotIn("private callback detail", str(failure.exception))
                self.assertFalse(app.started)
                self.assertFalse(channel.started)
                self.assertFalse(gateway.running)
                self.assertEqual(channel.sent, ())
                with self.assertRaises(RuntimeError):
                    await retained[0].get_binding()
                with self.assertRaises(RuntimeError):
                    await gateway.start()

    async def test_preparations_are_ordered_and_child_tasks_cannot_use_scope(self) -> None:
        app = ReferenceApplication()
        channel = ReferenceChannel()
        order: list[str] = []

        async def first(actions: StartupConversationActions) -> None:
            order.append("first")
            with self.assertRaises(RuntimeError):
                await asyncio.create_task(actions.get_binding())
            self.assertIsNone(await actions.get_binding())

        async def second(actions: StartupConversationActions) -> None:
            order.append("second")
            self.assertIsNone(await actions.get_binding())

        preparations = tuple(
            StartupPreparation(
                conversation_ref=channel.conversation("scope").ref, actor="owner", prepare=handler
            )
            for handler in (first, second)
        )
        with self.assertRaisesRegex(ValueError, "capacity"):
            Gateway(
                gateway_id="capacity",
                channels=[channel],
                applications=[app],
                store=MemoryGatewayStore(),
                limits=GatewayLimits(startup_preparation_max_items=1),
                extensions=GatewayExtensions(startup_preparations=preparations),
            )
        self.assertEqual(order, [])
        gateway = Gateway(
            gateway_id="ordered",
            channels=[channel],
            applications=[app],
            store=MemoryGatewayStore(),
            extensions=GatewayExtensions(startup_preparations=preparations),
        )
        async with gateway:
            self.assertEqual(order, ["first", "second"])

    async def test_cancellation_resistant_callback_loses_scope_at_hard_deadline(self) -> None:
        app = ReferenceApplication()
        channel = ReferenceChannel()
        release = asyncio.Event()
        finished = asyncio.Event()
        blocked: list[bool] = []

        async def prepare(actions: StartupConversationActions) -> None:
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            try:
                await actions.clear_thread(action_id="too-late")
            except RuntimeError:
                blocked.append(True)
            finally:
                finished.set()

        gateway = Gateway(
            gateway_id="resistant-prepare",
            channels=[channel],
            applications=[app],
            store=MemoryGatewayStore(),
            limits=GatewayLimits(lifecycle_owner_timeout_seconds=0.02),
            extensions=GatewayExtensions(
                startup_preparations=(
                    StartupPreparation(
                        conversation_ref=channel.conversation("scope").ref,
                        actor="owner",
                        prepare=prepare,
                    ),
                )
            ),
        )
        try:
            with self.assertRaises(GatewayLifecycleFailure):
                await asyncio.wait_for(gateway.start(), 0.5)
            self.assertFalse(channel.started)
            self.assertFalse(app.started)
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), 0.5)
        self.assertEqual(blocked, [True])

    async def test_stop_joins_preparation_without_starting_native_owners(self) -> None:
        app = ReferenceApplication()
        channel = ReferenceChannel()
        entered = asyncio.Event()

        async def prepare(actions: StartupConversationActions) -> None:
            self.assertIsNone(await actions.get_binding())
            entered.set()
            await asyncio.Event().wait()

        gateway = Gateway(
            gateway_id="stop-preparation",
            channels=[channel],
            applications=[app],
            store=MemoryGatewayStore(),
            extensions=GatewayExtensions(
                startup_preparations=(
                    StartupPreparation(
                        conversation_ref=channel.conversation("scope").ref,
                        actor="owner",
                        prepare=prepare,
                    ),
                )
            ),
        )
        starting = asyncio.create_task(gateway.start())
        await entered.wait()
        await asyncio.wait_for(gateway.stop(), 0.5)
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertFalse(channel.started)
        self.assertFalse(app.started)

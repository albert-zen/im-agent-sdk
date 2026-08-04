from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import ForwardRef, get_type_hints

import imagent.contracts as contracts_facade
import imagent.gateway as gateway_facade
import imagent.gateway.persistence as persistence_facade
import imagent.gateway.routing as routing_facade
import imagent.gateway.routing.projection_routes as owner
from imagent.applications.contract import ApplicationRef, ThreadRef
from imagent.gateway.persistence import ConversationBinding, ThreadProjectionRoute
from imagent.gateway.persistence import state_contracts as state_contract_owner
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryProjectionRouteRepository,
)
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation

ROOT = Path(__file__).resolve().parents[3]

_IMPORT_ORDER_ASSERTIONS = textwrap.dedent(
    """
    import importlib.util
    import typing

    import imagent.contracts as contracts_facade
    import imagent.gateway as gateway_facade
    import imagent.gateway.persistence as persistence_facade
    import imagent.gateway.routing as routing_facade
    import imagent.gateway.routing.projection_routes as owner
    from imagent.gateway.persistence import ThreadProjectionRoute

    for name in ("ObserveThread", "ProjectionPolicy", "ThreadObserved"):
        assert getattr(routing_facade, name) is getattr(owner, name)
        assert getattr(owner, name).__module__ == "imagent.gateway.routing.projection_routes"
    for module in (contracts_facade,):
        for name in ("ObserveThread", "ThreadObserved"):
            assert not hasattr(module, name)
            assert name not in getattr(module, "__all__", ())
    assert not hasattr(persistence_facade, "ProjectionPolicy")
    assert "ProjectionPolicy" not in persistence_facade.__all__
    assert importlib.util.find_spec("imagent.contracts.operations") is None
    assert importlib.util.find_spec("imagent.contracts.validators") is None
    try:
        from imagent.contracts import ObserveThread
    except ImportError:
        pass
    else:
        raise AssertionError("historical ObserveThread import unexpectedly succeeded")
    hints = typing.get_type_hints(owner.ThreadObserved)
    assert hints["route"] is ThreadProjectionRoute
    root_hints = typing.get_type_hints(gateway_facade.ImAgentGateway._observe_thread)
    assert root_hints["operation"] is owner.ObserveThread
    assert root_hints["return"] is owner.ThreadObserved
    """
).strip()

_IMPORT_ORDERS = {
    "projection-route owner first": "import imagent.gateway.routing.projection_routes\n",
    "routing facade first": "import imagent.gateway.routing\n",
    "gateway root first": "import imagent.gateway\n",
}


class ProjectionRouteContractOwnershipTests(unittest.TestCase):
    def test_public_facade_identity_and_historical_negative_surface(self) -> None:
        self.assertEqual(
            owner.__all__,
            ["ObserveThread", "ProjectionPolicy", "ThreadObserved"],
        )
        for name in owner.__all__:
            with self.subTest(name=name):
                value = getattr(owner, name)
                self.assertIs(getattr(routing_facade, name), value)
                self.assertEqual(value.__module__, owner.__name__)
        for module in (contracts_facade,):
            for name in ("ObserveThread", "ThreadObserved"):
                with self.subTest(module=module.__name__, name=name):
                    self.assertFalse(hasattr(module, name))
                    self.assertNotIn(name, getattr(module, "__all__", ()))
        self.assertIsNone(importlib.util.find_spec("imagent.contracts.operations"))
        self.assertIsNone(importlib.util.find_spec("imagent.contracts.validators"))
        self.assertFalse(hasattr(persistence_facade, "ProjectionPolicy"))
        self.assertFalse(hasattr(state_contract_owner, "ProjectionPolicy"))
        self.assertNotIn("ProjectionPolicy", persistence_facade.__all__)
        for module_name in (
            "imagent.contracts",
            "imagent.contracts.operations",
            "imagent.contracts.validators",
        ):
            for name in ("ObserveThread", "ThreadObserved"):
                with self.subTest(module=module_name, name=name):
                    with self.assertRaises(ImportError):
                        exec(f"from {module_name} import {name}")
        with self.assertRaises(ImportError):
            exec("from imagent.gateway.persistence import ProjectionPolicy")

    def test_runtime_hints_resolve_direct_route_owner_types(self) -> None:
        self.assertIs(
            get_type_hints(owner.ThreadObserved)["route"],
            ThreadProjectionRoute,
        )
        self.assertNotIsInstance(owner.ThreadObserved.__annotations__["route"], ForwardRef)
        root_hints = get_type_hints(gateway_facade.ImAgentGateway._observe_thread)
        self.assertIs(root_hints["operation"], owner.ObserveThread)
        self.assertIs(root_hints["return"], owner.ThreadObserved)

    def test_clean_process_import_orders_keep_the_historical_surface_absent(self) -> None:
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

    def test_observe_specific_validation_is_owned_with_the_route_values(self) -> None:
        conversation = ConversationRef("channel-a", "conversation-a")
        thread = ThreadRef("application-a", "thread-a")
        operation = owner.ObserveThread(
            operation_id="observe-a",
            conversation_ref=conversation,
            actor="user-a",
            thread_ref=thread,
            reply_to_message_id="message-a",
            created_at=datetime.now(UTC),
        )
        owner._validate_observe_operation(operation)
        with self.assertRaisesRegex(ContractViolation, "reply_to_message_id"):
            owner._validate_observe_operation(
                owner.ObserveThread(
                    operation_id="observe-invalid",
                    conversation_ref=conversation,
                    actor="user-a",
                    thread_ref=thread,
                    reply_to_message_id="",
                    created_at=datetime.now(UTC),
                )
            )
        result = owner.ThreadObserved(
            operation_id=operation.operation_id,
            completed_at=datetime.now(UTC),
            route=ThreadProjectionRoute(
                route_id="route-a",
                thread_ref=thread,
                conversation_ref=conversation,
                reply_to_message_id="message-a",
            ),
        )
        owner._validate_observe_operation_result(operation, result)
        with self.assertRaisesRegex(ContractViolation, "different Conversation"):
            owner._validate_observe_operation_result(
                operation,
                owner.ThreadObserved(
                    operation_id=operation.operation_id,
                    completed_at=datetime.now(UTC),
                    route=ThreadProjectionRoute(
                        route_id="route-b",
                        thread_ref=thread,
                        conversation_ref=ConversationRef("channel-a", "conversation-b"),
                        reply_to_message_id="message-a",
                    ),
                ),
            )

    def test_route_identity_depends_only_on_stable_endpoints(self) -> None:
        thread = ThreadRef("application-a", "thread-a")
        conversation = ConversationRef("channel-a", "conversation-a")
        route_id = owner.derive_projection_route_id(thread, conversation)

        self.assertEqual(route_id, owner.derive_projection_route_id(thread, conversation))
        self.assertNotEqual(
            route_id,
            owner.derive_projection_route_id(
                thread,
                ConversationRef("channel-a", "conversation-b"),
            ),
        )
        self.assertNotEqual(
            route_id,
            owner.derive_projection_route_id(
                ThreadRef("application-a", "thread-b"),
                conversation,
            ),
        )
        self.assertTrue(route_id.startswith("imagent:projection:sha256:"))


class ProjectionRouteAuthorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.application = ApplicationRef("application-a")
        self.thread = ThreadRef("application-a", "thread-a")
        self.other_thread = ThreadRef("application-a", "thread-b")
        self.first = ConversationRef("channel-a", "conversation-a")
        self.second = ConversationRef("channel-a", "conversation-b")

    async def test_foreground_only_requires_current_binding_equality(self) -> None:
        bindings = InMemoryBindingRepository()
        projections = InMemoryProjectionRouteRepository()
        authority = owner._ProjectionRouteAuthority(
            bindings=bindings,
            projections=projections,
            policy=owner.ProjectionPolicy.FOREGROUND_ONLY,
        )
        refreshed = await authority.refresh_route(
            self.thread,
            self.first,
            reply_to_message_id=None,
        )
        self.assertEqual(await authority.active_routes(self.thread), ())
        await bindings.put(
            ConversationBinding(
                conversation_ref=self.first,
                application_ref=self.application,
                thread_ref=self.thread,
            )
        )
        self.assertEqual(await authority.active_routes(self.thread), (refreshed.route,))
        await bindings.put(
            ConversationBinding(
                conversation_ref=self.first,
                application_ref=self.application,
                thread_ref=self.other_thread,
            )
        )
        self.assertEqual(await authority.active_routes(self.thread), ())
        self.assertFalse(await authority.route_is_active(refreshed.route))

    async def test_refresh_preserves_checkpoint_and_stable_identity(self) -> None:
        projections = InMemoryProjectionRouteRepository()
        authority = owner._ProjectionRouteAuthority(
            bindings=InMemoryBindingRepository(),
            projections=projections,
            policy=owner.ProjectionPolicy.ALL_OBSERVERS,
        )
        first = await authority.refresh_route(
            self.thread,
            self.first,
            reply_to_message_id="message-a",
        )
        checkpointed = await projections.advance_projection_checkpoint(
            first.route.route_id,
            expected_agent_item_id=None,
            agent_item_id="agent-item-a",
            checkpointed_at=datetime.now(UTC),
        )
        refreshed = await authority.refresh_route(
            self.thread,
            self.first,
            reply_to_message_id="message-b",
        )

        self.assertTrue(first.created)
        self.assertFalse(refreshed.created)
        self.assertEqual(refreshed.route.route_id, first.route.route_id)
        self.assertEqual(refreshed.route.reply_to_message_id, "message-b")
        self.assertEqual(
            refreshed.route.checkpoint_agent_item_id,
            checkpointed.checkpoint_agent_item_id,
        )
        self.assertEqual(refreshed.route.checkpointed_at, checkpointed.checkpointed_at)

    async def test_remembered_and_all_observer_policies_remain_isolated(self) -> None:
        remembered_routes = InMemoryProjectionRouteRepository()
        remembered = owner._ProjectionRouteAuthority(
            bindings=InMemoryBindingRepository(),
            projections=remembered_routes,
            policy=owner.ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
        )
        first = await remembered.refresh_route(
            self.thread,
            self.first,
            reply_to_message_id=None,
        )
        second = await remembered.refresh_route(
            self.thread,
            self.second,
            reply_to_message_id=None,
        )
        self.assertEqual(second.removed_routes, (first.route,))
        self.assertEqual(
            await remembered_routes.list_projection_routes(self.thread),
            (second.route,),
        )

        all_routes = InMemoryProjectionRouteRepository()
        all_observers = owner._ProjectionRouteAuthority(
            bindings=InMemoryBindingRepository(),
            projections=all_routes,
            policy=owner.ProjectionPolicy.ALL_OBSERVERS,
        )
        all_first = await all_observers.refresh_route(
            self.thread,
            self.first,
            reply_to_message_id=None,
        )
        all_second = await all_observers.refresh_route(
            self.thread,
            self.second,
            reply_to_message_id=None,
        )
        self.assertEqual(all_second.removed_routes, ())
        self.assertEqual(
            await all_routes.list_projection_routes(self.thread),
            (all_first.route, all_second.route),
        )


if __name__ == "__main__":
    unittest.main()

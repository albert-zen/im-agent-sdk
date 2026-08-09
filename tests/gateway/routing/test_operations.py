from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import subprocess
import sys
import textwrap
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args, get_type_hints
from unittest.mock import patch

import imagent.contracts as contracts_facade
import imagent.gateway as gateway_facade
import imagent.gateway.projection as projection_facade
import imagent.gateway.projection.request_correlation as request_owner
import imagent.gateway.routing as routing_facade
import imagent.gateway.routing.operations as operations_owner
import imagent.gateway.routing.projection_routes as projection_routes_owner
from imagent.applications.contract import ApplicationRef, ApplicationSummary, ProjectRef, ThreadRef
from imagent.applications.requests import ApprovalResponse, RequestRef
from imagent.contracts import (
    ApplicationsListed,
    BindConversationToProject,
    BindConversationToThread,
    ClearConversationThread,
    ContractError,
    ConversationBound,
    ConversationRef,
    GatewayOperation,
    GatewayOperationFailed,
    GatewayOperationResult,
    ListApplications,
    SelectApplication,
    validate_gateway_operation_result,
)
from imagent.gateway.concurrency import KeyedLockCapacityError
from imagent.gateway.persistence import ConversationBinding, ThreadProjectionRoute
from imagent.gateway.projection import RequestResponseRouted, RespondToRequest
from imagent.gateway.routing import ObserveThread, ThreadObserved
from imagent.interaction.operations import OperationErrorCode, operation_error

ROOT = Path(__file__).resolve().parents[3]

_IMPORT_ORDER_ASSERTIONS = textwrap.dedent(
    """
    import inspect
    import importlib.util
    import typing

    import imagent.contracts as contracts_facade
    import imagent.gateway as gateway_facade
    import imagent.gateway.projection as projection_facade
    import imagent.gateway.projection.request_correlation as request_owner
    import imagent.gateway.routing as routing_facade
    import imagent.gateway.routing.operations as operations_owner
    import imagent.gateway.routing.projection_routes as projection_routes_owner
    import imagent.interaction.controllers as controllers_facade

    aggregate_value_names = (
        "ApplicationsListed",
        "GatewayOperation",
        "GatewayOperationFailed",
        "GatewayOperationResult",
        "GatewayOperationType",
        "ListApplications",
        "SelectApplication",
    )
    for name in aggregate_value_names:
        owner = getattr(operations_owner, name)
        assert getattr(contracts_facade, name) is owner
        assert getattr(routing_facade, name) is owner
        assert getattr(gateway_facade, name) is owner
        assert gateway_facade.__dict__[name] is owner
        if callable(owner):
            assert inspect.signature(getattr(contracts_facade, name)) == inspect.signature(owner)

    aggregate_validator_names = (
        "validate_gateway_operation",
        "validate_gateway_operation_result",
    )
    assert gateway_facade._GATEWAY_OPERATION_EXPORTS == frozenset(
        aggregate_value_names + aggregate_validator_names
    )
    assert not hasattr(gateway_facade, "unsupported_gateway_operation")
    for name in aggregate_validator_names:
        owner = getattr(operations_owner, name)
        assert getattr(contracts_facade, name) is owner
        assert getattr(routing_facade, name) is owner
        assert getattr(gateway_facade, name) is owner
        assert gateway_facade.__dict__[name] is owner
        assert inspect.signature(getattr(contracts_facade, name)) == inspect.signature(owner)

    for name in ("ObserveThread", "ThreadObserved"):
        assert getattr(routing_facade, name) is getattr(projection_routes_owner, name)
        assert getattr(operations_owner, name) is getattr(projection_routes_owner, name)
        assert not hasattr(contracts_facade, name)

    for name in ("RespondToRequest", "RequestResponseRouted"):
        assert getattr(projection_facade, name) is getattr(request_owner, name)
        assert getattr(operations_owner, name) is getattr(request_owner, name)
        assert not hasattr(contracts_facade, name)

    assert importlib.util.find_spec("imagent.contracts.operations") is None
    assert importlib.util.find_spec("imagent.contracts.validators") is None
    assert not hasattr(routing_facade, "GatewayOperationExecutor")
    assert not hasattr(gateway_facade, "GatewayOperationExecutor")
    for public_name, private_name in (
        ("list_applications", "_list_applications"),
        ("select_application", "_select_application"),
        ("bind_conversation_to_project", "_bind_conversation_to_project"),
        ("bind_conversation_to_thread", "_bind_conversation_to_thread"),
        ("clear_conversation_thread", "_clear_conversation_thread"),
        ("observe_thread", "_observe_thread"),
        ("respond_to_request", "_route_request_response"),
    ):
        assert not hasattr(gateway_facade.ImAgentGateway, public_name)
        assert callable(getattr(gateway_facade.ImAgentGateway, private_name))
    assert "GatewayOperationExecutor" not in operations_owner.__all__
    assert "GatewayOperationExecutor" not in routing_facade.__all__
    controller_hints = typing.get_type_hints(controllers_facade.ControllerActions.execute_gateway)
    assert controller_hints["operation"] is contracts_facade.GatewayOperation
    assert controller_hints["return"] is contracts_facade.GatewayOperationResult

    assert typing.get_args(contracts_facade.GatewayOperation)
    assert typing.get_args(contracts_facade.GatewayOperationResult)
    assert len(typing.get_args(operations_owner.GatewayOperation)) == 7
    assert len(typing.get_args(operations_owner.GatewayOperationResult)) == 5
    assert typing.get_type_hints(contracts_facade.__getattr__)["return"] is object
    assert typing.get_type_hints(gateway_facade.__getattr__)["return"] is object
    root_delegate_hints = {
        "_observe_thread": {
            "operation": projection_routes_owner.ObserveThread,
            "return": projection_routes_owner.ThreadObserved,
        },
        "_route_request_response": {
            "operation": request_owner.RespondToRequest,
            "return": request_owner.RequestResponseRouted,
        },
    }
    for method_name, expected in root_delegate_hints.items():
        hints = typing.get_type_hints(getattr(gateway_facade.ImAgentGateway, method_name))
        assert all(hints[name] is value for name, value in expected.items())
    assert typing.get_type_hints(
        contracts_facade.validate_gateway_operation
    ) == typing.get_type_hints(operations_owner.validate_gateway_operation)
    assert typing.get_type_hints(
        contracts_facade.validate_gateway_operation_result
    ) == typing.get_type_hints(operations_owner.validate_gateway_operation_result)
    assert (
        typing.get_type_hints(projection_routes_owner.ThreadObserved)["route"].__name__
        == "ThreadProjectionRoute"
    )
    """
).strip()

_IMPORT_ORDERS = {
    "canonical owner first": "import imagent.gateway.routing.operations\n",
    "request owner first": "import imagent.gateway.projection.request_correlation\n",
    "projection facade first": "import imagent.gateway.projection\n",
    "projection-route owner first": "import imagent.gateway.routing.projection_routes\n",
    "controllers first": "import imagent.interaction.controllers\n",
    "gateway first": "import imagent.gateway\n",
    "imagent.contracts first": "import imagent.contracts\n",
    "routing first": "import imagent.gateway.routing\n",
}

_GATEWAY_DELEGATE_NAMES = (
    ("list_applications", "_list_applications"),
    ("select_application", "_select_application"),
    ("bind_conversation_to_project", "_bind_conversation_to_project"),
    ("bind_conversation_to_thread", "_bind_conversation_to_thread"),
    ("clear_conversation_thread", "_clear_conversation_thread"),
    ("observe_thread", "_observe_thread"),
    ("respond_to_request", "_route_request_response"),
)


def _contract_error(error: Exception) -> ContractError:
    if isinstance(error, KeyedLockCapacityError):
        return ContractError(
            code=OperationErrorCode.CAPACITY_EXHAUSTED.value,
            message=str(error),
            retryable=True,
        )
    return operation_error(error)


class _DelegateProbe:
    def __init__(self, conversation: ConversationRef, thread: ThreadRef) -> None:
        self.conversation = conversation
        self.thread = thread
        self.application = ApplicationRef(thread.project_ref.application_instance_id)
        self.project = thread.project_ref
        self.calls: list[str] = []

    def _list_applications(
        self,
        operation: ListApplications,
        *,
        completed_at: datetime,
    ) -> tuple[ApplicationSummary, ...]:
        del operation, completed_at
        self.calls.append("list")
        return ()

    def _binding_result(
        self,
        operation: BindConversationToProject
        | BindConversationToThread
        | ClearConversationThread
        | SelectApplication,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        self.calls.append(operation.type.value)
        if isinstance(operation, SelectApplication):
            binding = ConversationBinding(
                conversation_ref=operation.conversation_ref,
                application_ref=operation.application_ref,
            )
        elif isinstance(operation, BindConversationToProject):
            binding = ConversationBinding(
                conversation_ref=operation.conversation_ref,
                application_ref=self.application,
                project_ref=operation.project_ref,
            )
        elif isinstance(operation, BindConversationToThread):
            binding = ConversationBinding(
                conversation_ref=operation.conversation_ref,
                application_ref=self.application,
                project_ref=operation.thread_ref.project_ref,
                thread_ref=operation.thread_ref,
            )
        else:
            binding = ConversationBinding(
                conversation_ref=operation.conversation_ref,
                application_ref=self.application,
            )
        return ConversationBound(
            operation_id=operation.operation_id,
            type=operation.type,
            completed_at=completed_at,
            binding=binding,
        )

    async def _select_application(
        self,
        operation: SelectApplication,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        return self._binding_result(operation, completed_at=completed_at)

    async def _bind_conversation_to_project(
        self,
        operation: BindConversationToProject,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        return self._binding_result(operation, completed_at=completed_at)

    async def _bind_conversation_to_thread(
        self,
        operation: BindConversationToThread,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        return self._binding_result(operation, completed_at=completed_at)

    async def _clear_conversation_thread(
        self,
        operation: ClearConversationThread,
        *,
        completed_at: datetime,
    ) -> ConversationBound:
        return self._binding_result(operation, completed_at=completed_at)

    async def _observe_thread(
        self,
        operation: ObserveThread,
        *,
        completed_at: datetime,
    ) -> ThreadObserved:
        self.calls.append(operation.type.value)
        return ThreadObserved(
            operation_id=operation.operation_id,
            completed_at=completed_at,
            route=ThreadProjectionRoute(
                route_id="route-1",
                thread_ref=operation.thread_ref,
                conversation_ref=operation.conversation_ref,
                reply_to_message_id=operation.reply_to_message_id,
            ),
        )

    async def _route_request_response(
        self,
        operation: RespondToRequest,
        *,
        completed_at: datetime,
    ) -> RequestResponseRouted:
        self.calls.append(operation.type.value)
        return RequestResponseRouted(
            operation_id=operation.operation_id,
            completed_at=completed_at,
            request_ref=operation.request_ref,
        )


class GatewayOperationsOwnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.conversation = ConversationRef("qq-primary", "c2c:user-1")
        self.application = ApplicationRef("zen-local")
        self.project = ProjectRef("zen-local", "repo-1")
        self.thread = ThreadRef(self.project, "thread-1")

    def _executor(
        self,
        probe: _DelegateProbe | None = None,
        *,
        capacity: int = 4,
    ) -> tuple[operations_owner._GatewayOperationExecutor, _DelegateProbe]:
        actual_probe = probe or _DelegateProbe(self.conversation, self.thread)
        return (
            operations_owner._GatewayOperationExecutor(
                delegates=actual_probe,
                max_active_conversation_keys=capacity,
                contract_error=_contract_error,
            ),
            actual_probe,
        )

    def test_public_aggregate_facades_are_exact_owner_objects(self) -> None:
        value_names = (
            "ApplicationsListed",
            "GatewayOperation",
            "GatewayOperationFailed",
            "GatewayOperationResult",
            "GatewayOperationType",
            "ListApplications",
            "SelectApplication",
        )
        for name in value_names:
            with self.subTest(name=name):
                owner = getattr(operations_owner, name)
                self.assertIs(getattr(contracts_facade, name), owner)
                self.assertIs(getattr(routing_facade, name), owner)
                self.assertIs(getattr(gateway_facade, name), owner)
        for name in ("validate_gateway_operation", "validate_gateway_operation_result"):
            with self.subTest(name=name):
                owner = getattr(operations_owner, name)
                self.assertIs(getattr(contracts_facade, name), owner)
                self.assertIs(getattr(routing_facade, name), owner)
                self.assertIs(getattr(gateway_facade, name), owner)
        for name in ("RespondToRequest", "RequestResponseRouted"):
            with self.subTest(request_name=name):
                owner = getattr(request_owner, name)
                self.assertIs(getattr(projection_facade, name), owner)
                self.assertIs(getattr(operations_owner, name), owner)
                self.assertFalse(hasattr(contracts_facade, name))
        self.assertIsNone(importlib.util.find_spec("imagent.contracts.operations"))
        self.assertIsNone(importlib.util.find_spec("imagent.contracts.validators"))
        self.assertFalse(hasattr(routing_facade, "GatewayOperationExecutor"))
        self.assertFalse(hasattr(gateway_facade, "GatewayOperationExecutor"))
        self.assertNotIn("GatewayOperationExecutor", operations_owner.__all__)
        self.assertNotIn("GatewayOperationExecutor", routing_facade.__all__)

    def test_gateway_root_keeps_one_private_delegate_path(self) -> None:
        source = (ROOT / "src/imagent/gateway/__init__.py").read_text()
        module = ast.parse(source)
        gateway_class = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "ImAgentGateway"
        )
        method_names = [
            node.name
            for node in gateway_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertEqual(len(method_names), len(set(method_names)))
        root_class = gateway_facade.ImAgentGateway
        protocol = operations_owner._GatewayOperationDelegates
        for public_name, private_name in _GATEWAY_DELEGATE_NAMES:
            with self.subTest(delegate=public_name):
                self.assertNotIn(public_name, root_class.__dict__)
                self.assertFalse(hasattr(root_class, public_name))
                self.assertTrue(callable(getattr(root_class, private_name)))
                self.assertNotIn(public_name, protocol.__dict__)
                self.assertTrue(callable(getattr(protocol, private_name)))
        self.assertIn("execute_gateway", root_class.__dict__)
        self.assertNotIn("_respond_to_request", root_class.__dict__)
        self.assertNotIn("_converge_native_request_failure", root_class.__dict__)
        self.assertNotIn("_transition_request_state", root_class.__dict__)

    def test_clean_process_import_orders_preserve_identities_signatures_and_hints(self) -> None:
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

    def test_closed_aggregate_unions_include_exact_owner_values(self) -> None:
        self.assertEqual(
            set(get_args(GatewayOperation)),
            {
                ListApplications,
                SelectApplication,
                BindConversationToProject,
                BindConversationToThread,
                ClearConversationThread,
                ObserveThread,
                RespondToRequest,
            },
        )
        self.assertEqual(
            set(get_args(GatewayOperationResult)),
            {
                ApplicationsListed,
                ConversationBound,
                ThreadObserved,
                RequestResponseRouted,
                GatewayOperationFailed,
            },
        )

    def test_owner_annotations_remain_exact(self) -> None:
        self.assertEqual(
            inspect.signature(operations_owner.validate_gateway_operation),
            inspect.signature(contracts_facade.validate_gateway_operation),
        )
        self.assertEqual(
            get_type_hints(operations_owner.validate_gateway_operation),
            get_type_hints(contracts_facade.validate_gateway_operation),
        )
        self.assertEqual(
            get_type_hints(operations_owner.validate_gateway_operation_result),
            get_type_hints(contracts_facade.validate_gateway_operation_result),
        )
        self.assertIs(get_type_hints(contracts_facade.__getattr__)["return"], object)

    async def test_typed_dispatch_delegates_to_each_owner_method(self) -> None:
        probe = _DelegateProbe(self.conversation, self.thread)
        executor, _ = self._executor(probe)
        for public_name, private_name in _GATEWAY_DELEGATE_NAMES:
            with self.subTest(delegate=public_name):
                self.assertFalse(hasattr(probe, public_name))
                self.assertTrue(callable(getattr(probe, private_name)))
        created_at = datetime.now(UTC)
        request_ref = RequestRef(self.application, "request-1")
        operations = (
            ListApplications(
                operation_id="list",
                conversation_ref=self.conversation,
                actor="user-1",
                created_at=created_at,
            ),
            SelectApplication(
                operation_id="select",
                conversation_ref=self.conversation,
                actor="user-1",
                application_ref=self.application,
                created_at=created_at,
            ),
            BindConversationToProject(
                operation_id="project",
                conversation_ref=self.conversation,
                actor="user-1",
                project_ref=self.project,
                created_at=created_at,
            ),
            BindConversationToThread(
                operation_id="thread",
                conversation_ref=self.conversation,
                actor="user-1",
                thread_ref=self.thread,
                created_at=created_at,
            ),
            ClearConversationThread(
                operation_id="clear",
                conversation_ref=self.conversation,
                actor="user-1",
                created_at=created_at,
            ),
            ObserveThread(
                operation_id="observe",
                conversation_ref=self.conversation,
                actor="user-1",
                thread_ref=self.thread,
                created_at=created_at,
            ),
            RespondToRequest(
                operation_id="respond",
                conversation_ref=self.conversation,
                actor="user-1",
                request_ref=request_ref,
                response=ApprovalResponse("yes"),
                created_at=created_at,
            ),
        )
        results = [await executor.execute(operation) for operation in operations]
        self.assertTrue(all(not isinstance(result, GatewayOperationFailed) for result in results))
        self.assertEqual(
            probe.calls,
            [
                "list",
                "application.select",
                "conversation.bind_project",
                "conversation.bind_thread",
                "conversation.clear_thread",
                "thread.observe",
                "conversation.respond_request",
            ],
        )

    async def test_capacity_rejects_new_conversation_before_delegate_side_effect(self) -> None:
        executor, probe = self._executor(capacity=1)
        other_conversation = ConversationRef("qq-primary", "c2c:user-2")
        operation = ListApplications(
            operation_id="capacity",
            conversation_ref=other_conversation,
            actor="user-2",
            created_at=datetime.now(UTC),
        )
        async with executor.hold_conversation(self.conversation):
            result = await executor.execute(operation)
        self.assertIsInstance(result, GatewayOperationFailed)
        assert isinstance(result, GatewayOperationFailed)
        self.assertEqual(result.error.code, OperationErrorCode.CAPACITY_EXHAUSTED.value)
        self.assertEqual(probe.calls, [])
        self.assertEqual(executor.conversation_locks.active_key_count, 0)

    def test_invalid_conversation_capacities_fail_explicitly(self) -> None:
        probe = _DelegateProbe(self.conversation, self.thread)
        for invalid in (0, -1):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "max_active_keys"):
                    operations_owner._GatewayOperationExecutor(
                        delegates=probe,
                        max_active_conversation_keys=invalid,
                        contract_error=_contract_error,
                    )

    def test_aggregate_result_validator_delegates_exact_owner_branches(self) -> None:
        observe = ObserveThread(
            operation_id="observe",
            conversation_ref=self.conversation,
            actor="user-1",
            thread_ref=self.thread,
            created_at=datetime.now(UTC),
        )
        observed = ThreadObserved(
            operation_id="observe",
            completed_at=datetime.now(UTC),
            route=ThreadProjectionRoute(
                route_id="route-1",
                thread_ref=self.thread,
                conversation_ref=self.conversation,
            ),
        )
        with patch.object(
            projection_routes_owner,
            "_validate_observe_operation_result",
            wraps=projection_routes_owner._validate_observe_operation_result,
        ) as observe_validator:
            validate_gateway_operation_result(observe, observed)
        observe_validator.assert_called_once_with(observe, observed)

        respond = RespondToRequest(
            operation_id="respond",
            conversation_ref=self.conversation,
            actor="user-1",
            request_ref=RequestRef(self.application, "request-1"),
            response=ApprovalResponse("yes"),
            created_at=datetime.now(UTC),
        )
        routed = RequestResponseRouted(
            operation_id="respond",
            completed_at=datetime.now(UTC),
            request_ref=respond.request_ref,
        )
        with patch.object(
            request_owner,
            "_validate_respond_operation_result",
            wraps=request_owner._validate_respond_operation_result,
        ) as response_validator:
            validate_gateway_operation_result(respond, routed)
        response_validator.assert_called_once_with(respond, routed)


if __name__ == "__main__":
    unittest.main()

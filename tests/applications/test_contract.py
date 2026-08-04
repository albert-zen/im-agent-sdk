from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import unittest
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import cast, get_type_hints

import imagent.adapters as compatibility
import imagent.applications as facade
from imagent.applications import contract as owner
from imagent.applications.events import AgentEvent
from imagent.contracts import (
    AcceptedTurn,
    AgentInput,
    ApplicationInputDispatch,
    ApplicationOperation,
    ApplicationOperationResult,
    ApplicationSummary,
    InputContinuationPreference,
    InteractiveRequest,
    ThreadRef,
)


def _summary_getter() -> Callable[..., object]:
    getter = cast(property, vars(owner.AgentApplicationAdapter)["summary"]).fget
    assert getter is not None
    return cast(Callable[..., object], getter)


class ApplicationContractOwnershipTests(unittest.TestCase):
    def test_owner_and_public_facades_export_exact_objects(self) -> None:
        names = (
            "AgentApplicationAdapter",
            "ApplicationInputDispatchHandler",
        )
        for name in names:
            with self.subTest(name=name):
                owner_object = getattr(owner, name)
                self.assertIs(getattr(facade, name), owner_object)
                self.assertIs(getattr(compatibility, name), owner_object)
                self.assertIn(name, facade.__all__)

        self.assertEqual(
            facade.AgentApplicationAdapter.__module__,
            "imagent.applications.contract",
        )

    def test_compatibility_module_has_no_application_implementations(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        adapters_path = repository_root / "src" / "imagent" / "adapters.py"
        module = ast.parse(adapters_path.read_text(encoding="utf-8"))

        class_names = {node.name for node in ast.walk(module) if isinstance(node, ast.ClassDef)}
        assigned_names = {
            target.id
            for node in ast.walk(module)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
            if isinstance(target, ast.Name)
        }
        self.assertNotIn("AgentApplicationAdapter", class_names)
        self.assertNotIn("ApplicationInputDispatchHandler", class_names)
        self.assertNotIn("AgentApplicationAdapter", assigned_names)
        self.assertNotIn("ApplicationInputDispatchHandler", assigned_names)

    def test_protocol_signatures_and_defaults_are_unchanged(self) -> None:
        expected_signatures = {
            "summary": "(self) -> 'ApplicationSummary'",
            "start": "(self) -> 'None'",
            "stop": "(self) -> 'None'",
            "execute": "(self, operation: 'ApplicationOperation') -> 'ApplicationOperationResult'",
            "send_input": (
                "(self, thread_ref: 'ThreadRef', message: 'AgentInput', *, "
                "continuation: 'InputContinuationPreference' = "
                "<InputContinuationPreference.PREFER_ACTIVE_TURN: 'prefer_active_turn'>, "
                "before_dispatch: 'ApplicationInputDispatchHandler | None' = None) "
                "-> 'AcceptedTurn'"
            ),
            "list_pending_requests": "(self) -> 'tuple[InteractiveRequest, ...]'",
            "subscribe_thread": (
                "(self, thread_ref: 'ThreadRef', after_cursor: 'str | None' = None) "
                "-> 'AsyncIterator[AgentEvent]'"
            ),
        }
        for name, expected in expected_signatures.items():
            with self.subTest(name=name):
                member = getattr(owner.AgentApplicationAdapter, name)
                if name == "summary":
                    member = _summary_getter()
                self.assertEqual(str(inspect.signature(member)), expected)

        self.assertIs(
            inspect.signature(owner.AgentApplicationAdapter.send_input)
            .parameters["continuation"]
            .default,
            InputContinuationPreference.PREFER_ACTIVE_TURN,
        )

    def test_runtime_type_hints_resolve_to_existing_contract_values(self) -> None:
        self.assertEqual(
            get_type_hints(_summary_getter()),
            {"return": ApplicationSummary},
        )
        self.assertEqual(
            get_type_hints(owner.AgentApplicationAdapter.execute),
            {
                "operation": ApplicationOperation,
                "return": ApplicationOperationResult,
            },
        )
        self.assertEqual(
            get_type_hints(owner.AgentApplicationAdapter.send_input),
            {
                "thread_ref": ThreadRef,
                "message": AgentInput,
                "continuation": InputContinuationPreference,
                "before_dispatch": Callable[[ApplicationInputDispatch], Awaitable[None]] | None,
                "return": AcceptedTurn,
            },
        )
        self.assertEqual(
            get_type_hints(owner.AgentApplicationAdapter.list_pending_requests),
            {"return": tuple[InteractiveRequest, ...]},
        )
        self.assertEqual(
            get_type_hints(owner.AgentApplicationAdapter.subscribe_thread),
            {
                "thread_ref": ThreadRef,
                "after_cursor": str | None,
                "return": AsyncIterator[AgentEvent],
            },
        )

    def test_cold_contract_import_does_not_initialize_concrete_adapters(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        environment = os.environ.copy()
        source_root = str(repository_root / "src")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_root
            if not existing_pythonpath
            else os.pathsep.join((source_root, existing_pythonpath))
        )
        code = """
import sys

import imagent

gateway_modules_before = {
    name for name in sys.modules if name == 'imagent.gateway' or name.startswith('imagent.gateway.')
}

from imagent.applications.contract import (
    AgentApplicationAdapter,
    ApplicationInputDispatchHandler,
)
import imagent.applications as applications
import imagent.adapters as adapters

assert applications.AgentApplicationAdapter is AgentApplicationAdapter
assert applications.ApplicationInputDispatchHandler is ApplicationInputDispatchHandler
assert adapters.AgentApplicationAdapter is AgentApplicationAdapter
assert adapters.ApplicationInputDispatchHandler is ApplicationInputDispatchHandler
assert {
    name for name in sys.modules if name == 'imagent.gateway' or name.startswith('imagent.gateway.')
} == gateway_modules_before
assert 'imagent.applications.appserver' not in sys.modules
assert 'imagent.applications.appserver_client' not in sys.modules
assert 'imagent.applications.t3' not in sys.modules
assert 'imagent.applications.t3_client' not in sys.modules
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import subprocess
import sys
import unittest
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, get_type_hints

import imagent.applications as facade
from imagent.applications import contract as owner
from imagent.applications.contract import (
    AcceptedTurn,
    AgentInput,
    ApplicationInputDispatch,
    ApplicationSummary,
    InputContinuationPreference,
    ThreadRef,
)
from imagent.applications.events import AgentEvent, AgentEventType
from imagent.applications.operations import ApplicationOperation, ApplicationOperationResult
from imagent.applications.requests import InteractiveRequest
from imagent.interaction.messages import MessageRole, TextContent
from imagent.interaction.operations import ContractViolation
from scripts.validate_component_map import load_component_map

_APPLICATION_MODEL_FAMILY = (
    "Page",
    "ApplicationRef",
    "ProjectRef",
    "ThreadRef",
    "TurnRef",
    "WorkspaceIdentity",
    "InputContinuationPreference",
    "InputDisposition",
    "TurnReplyCorrelationPolicy",
    "ThreadStatus",
    "ApplicationSummary",
    "ProjectSummary",
    "ThreadSummary",
    "AgentInput",
    "AgentMessage",
    "TurnStatus",
    "TurnCatchup",
    "TurnHistoryEntry",
    "ThreadHistory",
    "ThreadSnapshot",
    "AcceptedTurn",
    "ApplicationInputDispatch",
    "fingerprint_canonical_workspace_root",
    "validate_agent_message",
    "validate_application_summary",
    "validate_project_ref",
    "validate_project_summary",
    "validate_thread_ref",
    "validate_thread_history",
    "validate_thread_summary",
    "validate_turn_catchup",
    "validate_turn_history_entry",
    "validate_turn_ref",
    "validate_workspace_identity",
)


def _summary_getter() -> Callable[..., object]:
    getter = cast(property, vars(owner.AgentApplicationAdapter)["summary"]).fget
    assert getter is not None
    return cast(Callable[..., object], getter)


class ApplicationContractOwnershipTests(unittest.TestCase):
    def test_unknown_input_outcome_has_one_exact_owner_and_signature(self) -> None:
        owner_object = owner.ApplicationInputOutcomeUnknown
        cause = RuntimeError("native dispatch was ambiguous")
        error = owner_object("input outcome is unknown", cause)

        self.assertIs(owner_object, getattr(facade, "ApplicationInputOutcomeUnknown"))
        self.assertIs(RuntimeError, owner_object.__bases__[0])
        self.assertEqual(owner_object.__module__, "imagent.applications.contract")
        self.assertEqual(str(error), "input outcome is unknown")
        self.assertEqual(error.args, ("input outcome is unknown",))
        self.assertIs(error.cause, cause)
        self.assertEqual(
            str(inspect.signature(owner_object)),
            "(message: 'str', cause: 'BaseException') -> 'None'",
        )
        with self.assertRaises(ModuleNotFoundError):
            __import__("imagent.contracts")

    def test_complete_application_model_family_has_one_owner_and_exact_aliases(self) -> None:
        for name in _APPLICATION_MODEL_FAMILY:
            with self.subTest(name=name):
                owner_object = getattr(owner, name)
                self.assertIs(getattr(facade, name), owner_object)
                self.assertEqual(owner_object.__module__, "imagent.applications.contract")
                self.assertIn(name, owner.__all__)
                self.assertIn(name, facade.__all__)

    def test_model_family_is_frozen_slotted_and_history_live_item_is_shared(self) -> None:
        model_names = tuple(
            name for name in _APPLICATION_MODEL_FAMILY if name != "validate_thread_ref"
        )
        for name in model_names:
            with self.subTest(name=name):
                model = getattr(owner, name)
                if is_dataclass(model):
                    params = getattr(model, "__dataclass_params__", None)
                    self.assertTrue(getattr(params, "frozen", False))
                    self.assertTrue(hasattr(model, "__slots__"))

        thread = owner.ThreadRef(owner.ProjectRef("app-1", "workspace"), "thread-1")
        message = owner.AgentMessage(
            agent_item_id="item-1",
            thread_ref=thread,
            role=MessageRole.ASSISTANT,
            content=(TextContent("answer"),),
            created_at=datetime.now(UTC),
        )
        catchup = owner.TurnCatchup(
            thread_ref=thread,
            turn_ref=owner.TurnRef(thread, "turn-1"),
            status=owner.TurnStatus.COMPLETED,
            messages=(message,),
        )
        history = owner.ThreadHistory(
            thread_ref=thread,
            turns=(
                owner.TurnHistoryEntry(
                    turn_ref=owner.TurnRef(thread, "turn-1"),
                    status=owner.TurnStatus.COMPLETED,
                    agent_messages=(message,),
                ),
            ),
        )
        snapshot = owner.ThreadSnapshot(
            thread=owner.ThreadSummary(
                ref=thread,
                status=owner.ThreadStatus.COMPLETED,
            ),
            messages=(message,),
        )
        event = AgentEvent(
            event_id="event-1",
            application_instance_id="app-1",
            project_ref=thread.project_ref,
            type=AgentEventType.MESSAGE_COMPLETED,
            data={"message": message},
            created_at=message.created_at,
            thread_ref=thread,
            turn_ref=owner.TurnRef(thread, "turn-1"),
        )

        self.assertIs(catchup.messages[0], message)
        self.assertIs(history.turns[0].agent_messages[0], message)
        self.assertIs(snapshot.messages[0], message)
        self.assertIs(event.data["message"], message)
        with self.assertRaises(FrozenInstanceError):
            message.agent_item_id = "changed"  # type: ignore[misc]

    def test_thread_scope_validation_and_historical_model_have_no_duplicate_definitions(
        self,
    ) -> None:
        with self.assertRaisesRegex(ContractViolation, "project_id"):
            owner.validate_thread_ref(owner.ThreadRef(owner.ProjectRef("app-2", ""), "thread-1"))

        repository_root = Path(__file__).resolve().parents[2]
        model_path = repository_root / "src" / "imagent" / "contracts" / "model.py"
        self.assertFalse(model_path.exists())
        state_path = (
            repository_root / "src" / "imagent" / "gateway" / "persistence" / "state_contracts.py"
        )
        tree = ast.parse(state_path.read_text(encoding="utf-8"))
        class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
        self.assertTrue(class_names.isdisjoint(_APPLICATION_MODEL_FAMILY))
        from imagent.interaction import messages

        self.assertFalse(hasattr(messages, "AgentMessage"))
        self.assertNotIn("imagent.applications", messages.__dict__)

    def test_resource_refs_use_only_the_normative_nested_identity_fields(self) -> None:
        project = owner.ProjectRef("app-1", "project-1")
        thread = owner.ThreadRef(project, "thread-1")
        turn = owner.TurnRef(thread, "turn-1")

        self.assertEqual(
            tuple(field.name for field in fields(owner.ProjectRef)),
            (
                "application_instance_id",
                "project_id",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(owner.ThreadRef)),
            (
                "project_ref",
                "thread_id",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(owner.TurnRef)),
            (
                "thread_ref",
                "turn_id",
            ),
        )
        self.assertFalse(hasattr(project, "native_project_id"))
        self.assertFalse(hasattr(thread, "application_instance_id"))
        self.assertEqual(turn.thread_ref.project_ref, project)

    def test_workspace_identity_is_stable_id_plus_canonical_root_fingerprint(self) -> None:
        first_ref = owner.ProjectRef("app-1", "workspace-1")
        same_ref = owner.ProjectRef("app-1", "workspace-1")
        replacement_ref = owner.ProjectRef("app-1", "workspace-2")
        first_fingerprint = owner.fingerprint_canonical_workspace_root("/repo/root")
        moved_fingerprint = owner.fingerprint_canonical_workspace_root("/repo/moved")

        self.assertEqual(first_ref, same_ref)
        self.assertNotEqual(first_ref, replacement_ref)
        self.assertNotEqual(first_fingerprint, moved_fingerprint)
        self.assertEqual(
            owner.ProjectSummary(first_ref, "First display").ref,
            owner.ProjectSummary(first_ref, "Renamed display").ref,
        )
        owner.validate_workspace_identity(owner.WorkspaceIdentity(first_ref, first_fingerprint))
        with self.assertRaisesRegex(ContractViolation, "lowercase SHA-256"):
            owner.validate_workspace_identity(owner.WorkspaceIdentity(first_ref, "A" * 64))
        with self.assertRaisesRegex(ContractViolation, "between 1 and 4096"):
            owner.fingerprint_canonical_workspace_root("")
        with self.assertRaisesRegex(ContractViolation, "valid UTF-8"):
            owner.fingerprint_canonical_workspace_root("\ud800")

    def test_owner_and_public_facades_export_exact_objects(self) -> None:
        names = (
            "AgentApplicationAdapter",
            "ApplicationInputDispatchHandler",
        )
        for name in names:
            with self.subTest(name=name):
                owner_object = getattr(owner, name)
                self.assertIs(getattr(facade, name), owner_object)
                self.assertIsNone(importlib.util.find_spec("imagent.adapters"))
                self.assertIn(name, facade.__all__)

        self.assertEqual(
            facade.AgentApplicationAdapter.__module__,
            "imagent.applications.contract",
        )

    def test_compatibility_module_has_no_application_implementations(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        adapters_path = repository_root / "src" / "imagent" / "adapters.py"
        self.assertFalse(adapters_path.exists())

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

import imagent.applications as applications

gateway_modules_after_root = {
    name for name in sys.modules if name == 'imagent.gateway' or name.startswith('imagent.gateway.')
}
assert not gateway_modules_after_root
assert 'imagent.applications.adapters.codex' not in sys.modules
assert 'imagent.applications.adapters.zen' not in sys.modules
assert 'imagent.applications.adapters.appserver.client' not in sys.modules
assert 'imagent.applications.adapters.t3' not in sys.modules

import importlib.util
import imagent.applications.contract

assert not {
    name for name in sys.modules if name == 'imagent.gateway' or name.startswith('imagent.gateway.')
}
assert 'imagent.applications.adapters.codex' not in sys.modules
assert 'imagent.applications.adapters.zen' not in sys.modules
assert 'imagent.applications.adapters.appserver.client' not in sys.modules
assert 'imagent.applications.adapters.t3' not in sys.modules
assert importlib.util.find_spec('imagent.adapters') is None
assert importlib.util.find_spec('imagent.contracts') is None

from imagent.applications.contract import AgentApplicationAdapter, ApplicationInputDispatchHandler
assert applications.AgentApplicationAdapter is AgentApplicationAdapter
assert applications.ApplicationInputDispatchHandler is ApplicationInputDispatchHandler
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

    def test_retired_application_imports_fail_in_clean_import_orders(self) -> None:
        self.assertIsNone(importlib.util.find_spec("imagent.contracts"))
        self.assertIsNone(importlib.util.find_spec("imagent.adapters"))
        orders = (
            "import imagent.applications.contract",
            "import imagent.applications.requests",
            "import imagent.applications.operations",
        )
        for order in orders:
            code = f"""
{order}
import importlib.util
assert importlib.util.find_spec('imagent.contracts') is None
assert importlib.util.find_spec('imagent.adapters') is None
"""
            with self.subTest(order=order):
                completed = subprocess.run(
                    [sys.executable, "-c", code],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_retired_facade_exports_are_absent_from_the_root_facade(self) -> None:
        for module, symbol in _retired_facade_exports():
            if module != "imagent.applications":
                continue
            with self.subTest(name=symbol):
                self.assertNotIn(symbol, facade.__all__)
                self.assertFalse(hasattr(facade, symbol))

    def test_every_retired_export_fails_source_import_in_a_clean_process(self) -> None:
        retired = _retired_facade_exports()
        self.assertTrue(retired)
        source = [
            "import importlib.util",
            "import sys",
        ]
        for module, symbol in retired:
            source.append(
                "try:\n"
                f"    from {module} import {symbol}\n"
                "except ImportError:\n"
                "    pass\n"
                "else:\n"
                f"    raise AssertionError('{module}:{symbol} is still source-importable')\n"
            )
        repository_root = Path(__file__).resolve().parents[2]
        environment = os.environ.copy()
        source_root = str(repository_root / "src")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_root
            if not existing_pythonpath
            else os.pathsep.join((source_root, existing_pythonpath))
        )
        completed = subprocess.run(
            [sys.executable, "-c", "\n".join(source)],
            check=False,
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


def _retired_facade_exports() -> list[tuple[str, str]]:
    """Return the map-declared retired Applications exports as (module, symbol) pairs."""
    declared = load_component_map()["structural_status"]["retired_application_public_exports"]
    references = set(declared["current"]) - set(declared["target"])
    retired: set[tuple[str, str]] = set()
    for reference in references:
        module, separator, symbol = reference.partition(":")
        if separator and module and symbol:
            retired.add((module, symbol))
    return sorted(retired)


if __name__ == "__main__":
    unittest.main()

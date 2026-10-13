from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from jsonschema import Draft202012Validator

from imagent import Failed, Gateway, MemoryGatewayStore, SQLiteGatewayStore, Succeeded
from imagent.applications import CodexApplicationAdapter, ZenApplicationAdapter
from imagent.applications.capabilities import (
    ThreadCreationProfile,
    validate_thread_creation_profile,
)
from imagent.applications.contract import ProjectRef
from imagent.gateway.persistence.effects import ActionErrorCode
from imagent.interaction.messages import ConversationRef
from imagent.interaction.operations import ContractViolation
from imagent.testing import FakeChannelAdapter
from tests.applications.adapters.test_zen import _ZenInputClient


class ThreadCreationProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_profile_uses_fenced_workflow_without_changing_default(self) -> None:
        client = _ZenInputClient()
        app = ZenApplicationAdapter(
            application_instance_id="zen",
            client=cast(Any, client),
            workspace_id="repo",
            cwd="/repo",
            thread_start_options={"approval_policy": "never"},
            thread_creation_profiles={"ask": {"approval_policy": "on-request"}},
        )
        channel = FakeChannelAdapter()
        gateway = Gateway(
            gateway_id="profiles",
            channels=[channel],
            applications=[app],
            store=MemoryGatewayStore(),
        )
        async with gateway:
            actions = gateway.application(app.summary.ref, principal="owner")
            result = await actions.create_thread(
                ProjectRef("zen", "repo"), action_id="first", creation_profile="ask"
            )
            self.assertIsInstance(result, Succeeded)
            self.assertEqual(client.created_threads[-1]["approval_policy"], "on-request")
            replay = await actions.create_thread(
                ProjectRef("zen", "repo"), action_id="first", creation_profile="ask"
            )
            self.assertIsInstance(replay, Succeeded)
            self.assertEqual(len(client.created_threads), 1)
            default = await actions.create_thread(ProjectRef("zen", "repo"), action_id="second")
            self.assertIsInstance(default, Succeeded)
            self.assertEqual(client.created_threads[-1]["approval_policy"], "never")

    async def test_concurrent_profiles_are_copied_and_unknown_selection_is_closed(self) -> None:
        for adapter_type in (ZenApplicationAdapter, CodexApplicationAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                client = _ZenInputClient()
                options: dict[str, Any] = {
                    "ask": {"approval_policy": "on-request", "config": {"nested": ["original"]}},
                    "full": {"approval_policy": "never"},
                }
                app = adapter_type(
                    application_instance_id="app",
                    client=cast(Any, client),
                    workspace_id="repo",
                    cwd="/repo",
                    thread_creation_profiles=options,
                )
                options["ask"]["config"]["nested"][0] = "changed"
                options["ask"]["approval_policy"] = "never"
                channel = FakeChannelAdapter()
                gateway = Gateway(
                    gateway_id="parallel",
                    channels=[channel],
                    applications=[app],
                    store=MemoryGatewayStore(),
                )
                async with gateway:
                    project = ProjectRef("app", "repo")
                    a = gateway.actions(
                        ConversationRef(channel.channel_instance_id, "a"), actor="a"
                    )
                    b = gateway.actions(
                        ConversationRef(channel.channel_instance_id, "b"), actor="b"
                    )
                    results = await asyncio.gather(
                        a.create_and_bind_thread(
                            project, action_id="a-create", creation_profile="ask"
                        ),
                        b.create_and_bind_thread(
                            project, action_id="b-create", creation_profile="full"
                        ),
                    )
                    for result in results:
                        self.assertIsInstance(result, Succeeded)
                    self.assertEqual(
                        {row["approval_policy"] for row in client.created_threads},
                        {"never", "on-request"},
                    )
                    ask = next(
                        row
                        for row in client.created_threads
                        if row["approval_policy"] == "on-request"
                    )
                    self.assertEqual(ask["config"], {"nested": ["original"]})
                    unknown = await a.create_thread(
                        project, action_id="unknown", creation_profile="missing"
                    )
                    self.assertIsInstance(unknown, Failed)
                    self.assertEqual(len(client.created_threads), 2)
                    conflict = await a.create_and_bind_thread(
                        project, action_id="a-create", creation_profile="full"
                    )
                    self.assertIsInstance(conflict, Failed)
                    assert isinstance(conflict, Failed)
                    self.assertIs(conflict.error.code, ActionErrorCode.CONFLICT)
                    self.assertEqual(len(client.created_threads), 2)

    async def test_sqlite_restart_same_profile_id_changed_config_conflicts(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "profiles.sqlite3"
            for index, policy in enumerate(("on-request", "on-request", "never")):
                client = _ZenInputClient()
                app = ZenApplicationAdapter(
                    application_instance_id="app",
                    client=cast(Any, client),
                    workspace_id="repo",
                    cwd="/repo",
                    thread_creation_profiles={"selected": {"approval_policy": policy}},
                )
                channel = FakeChannelAdapter()
                gateway = Gateway(
                    gateway_id="durable-profile",
                    channels=[channel],
                    applications=[app],
                    store=SQLiteGatewayStore(database),
                )
                async with gateway:
                    actions = gateway.application(app.summary.ref, principal="owner")
                    result = await actions.create_thread(
                        ProjectRef("app", "repo"), action_id="stable", creation_profile="selected"
                    )
                    if index == 2:
                        self.assertIsInstance(result, Failed)
                        assert isinstance(result, Failed)
                        self.assertIs(result.error.code, ActionErrorCode.CONFLICT)
                    else:
                        self.assertIsInstance(result, Succeeded)
                    self.assertEqual(len(client.created_threads), 1 if index == 0 else 0)

    def test_profile_descriptor_python_and_schema_bounds(self) -> None:
        schema = json.loads(Path("schemas/v1/capabilities.schema.json").read_text())
        validator = Draft202012Validator(dict(schema, **{"$ref": "#/$defs/ThreadCreationProfile"}))
        for profile_id, fingerprint, valid in (
            ("ask", "a" * 64, True),
            ("", "a" * 64, False),
            ("x" * 129, "a" * 64, False),
            ("ask", "A" * 64, False),
            ("ask", "a" * 63, False),
            ("ask", "a" * 64 + "\n", False),
        ):
            with self.subTest(profile_id=profile_id, fingerprint=fingerprint):
                descriptor = ThreadCreationProfile(profile_id, fingerprint)
                if valid:
                    validate_thread_creation_profile(descriptor)
                else:
                    with self.assertRaises(ContractViolation):
                        validate_thread_creation_profile(descriptor)
                self.assertEqual(
                    not list(
                        validator.iter_errors({"profileId": profile_id, "fingerprint": fingerprint})
                    ),
                    valid,
                )

    def test_registered_profiles_reject_unbounded_or_non_json_configuration(self) -> None:
        for profiles in (
            {"ask": {"cwd": "/escape"}},
            {"ask": {"approval_policy": "never", "approvalPolicy": "on-request"}},
            {"ask": {"value": float("nan")}},
            {"ask": {"value": "x" * 65537}},
            {str(index): {} for index in range(33)},
        ):
            with self.subTest(profiles=tuple(profiles)), self.assertRaises(ValueError):
                ZenApplicationAdapter(
                    application_instance_id="app",
                    client=cast(Any, _ZenInputClient()),
                    workspace_id="repo",
                    cwd="/repo",
                    thread_creation_profiles=profiles,
                )

    async def test_unknown_profile_rejection_survives_registration_and_restart(self) -> None:
        for workflow in (False, True):
            with self.subTest(workflow=workflow), TemporaryDirectory() as directory:
                database = Path(directory) / "unknown-profile.sqlite3"
                for index in range(3):
                    client = _ZenInputClient()
                    app = ZenApplicationAdapter(
                        application_instance_id="app",
                        client=cast(Any, client),
                        workspace_id="repo",
                        cwd="/repo",
                        thread_creation_profiles=(
                            {"later": {"approval_policy": "on-request"}} if index == 2 else {}
                        ),
                    )
                    channel = FakeChannelAdapter()
                    gateway = Gateway(
                        gateway_id="unknown-profile",
                        channels=[channel],
                        applications=[app],
                        store=SQLiteGatewayStore(database),
                    )
                    async with gateway:
                        actions = gateway.actions(
                            ConversationRef(channel.channel_instance_id, "room"), actor="owner"
                        )
                        create = (
                            actions.create_and_bind_thread if workflow else actions.create_thread
                        )
                        result = await create(
                            ProjectRef("app", "repo"),
                            action_id="same-intent",
                            creation_profile="later",
                        )
                        self.assertIsInstance(result, Failed)
                        assert isinstance(result, Failed)
                        self.assertIs(
                            result.error.code,
                            ActionErrorCode.CONFLICT if index == 2 else ActionErrorCode.UNSUPPORTED,
                        )
                        self.assertEqual(client.created_threads, [])
                        binding = await actions.get_binding()
                        self.assertIsNone(binding)
                        replay = await create(
                            ProjectRef("app", "repo"),
                            action_id="same-intent",
                            creation_profile="later",
                        )
                        self.assertEqual(replay, result)
                        changed = await create(
                            ProjectRef("app", "repo"),
                            action_id="same-intent",
                            creation_profile="different",
                        )
                        self.assertIsInstance(changed, Failed)
                        assert isinstance(changed, Failed)
                        self.assertIs(changed.error.code, ActionErrorCode.CONFLICT)
                        self.assertEqual(client.created_threads, [])
                        if index == 2:
                            fresh = await create(
                                ProjectRef("app", "repo"),
                                action_id="new-intent",
                                creation_profile="later",
                            )
                            self.assertIsInstance(fresh, Succeeded)
                            self.assertEqual(len(client.created_threads), 1)

    def test_profile_schema_requires_supported_creation(self) -> None:
        schema = json.loads(Path("schemas/v1/capabilities.schema.json").read_text())
        validator = Draft202012Validator(dict(schema, **{"$ref": "#/$defs/ThreadCapabilities"}))
        descriptor = {"profileId": "ask", "fingerprint": "a" * 64}
        for creation, profiles, valid in (
            ("unsupported", [], True),
            ("unsupported", [descriptor], False),
            ("native", [descriptor], True),
            ("fallback", [descriptor], True),
        ):
            with self.subTest(creation=creation, profiles=profiles):
                payload = {
                    "listing": "native",
                    "creation": creation,
                    "reading": "native",
                    "deletion": "unsupported",
                    "creationProfiles": profiles,
                }
                self.assertEqual(not list(validator.iter_errors(payload)), valid)

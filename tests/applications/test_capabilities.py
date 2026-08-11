from __future__ import annotations

import importlib.util
import unittest
from dataclasses import fields, replace
from subprocess import run
from sys import executable
from typing import Any, cast

import imagent.applications as applications
from imagent.applications import capabilities
from imagent.interaction.media import AttachmentSourceKind
from imagent.interaction.operations import ContractViolation


def _capabilities(mode: capabilities.ProjectMode) -> capabilities.ApplicationCapabilities:
    project_support = (
        capabilities.SupportLevel.NATIVE
        if mode is capabilities.ProjectMode.MANAGED
        else capabilities.SupportLevel.FALLBACK
    )
    return capabilities.ApplicationCapabilities(
        projects=capabilities.ProjectCapabilities(
            mode=mode,
            discovery=project_support,
            reading=project_support,
        ),
        threads=capabilities.ThreadCapabilities(
            listing=capabilities.SupportLevel.NATIVE,
            creation=capabilities.SupportLevel.NATIVE,
            reading=capabilities.SupportLevel.NATIVE,
            deletion=capabilities.ThreadDeletionCapability.ARCHIVE,
        ),
        runtime=capabilities.RuntimeCapabilities(
            history=capabilities.SupportLevel.NATIVE,
            streaming=capabilities.SupportLevel.NATIVE,
            replay_from_cursor=capabilities.SupportLevel.UNSUPPORTED,
            interruption=capabilities.SupportLevel.NATIVE,
            interactive_requests=capabilities.SupportLevel.NATIVE,
        ),
    )


class ApplicationCapabilitiesTests(unittest.TestCase):
    def test_capability_import_keeps_concrete_adapters_lazy_and_exact(self) -> None:
        result = run(
            [
                executable,
                "-c",
                "import sys; "
                "from imagent.applications import capabilities; "
                "assert not any(name.startswith('imagent.gateway') for name in sys.modules); "
                "assert 'imagent.applications.adapters.codex' not in sys.modules; "
                "assert 'imagent.applications.adapters.zen' not in sys.modules; "
                "from imagent.applications import CodexApplicationAdapter; "
                "from imagent.applications.adapters.codex import "
                "CodexApplicationAdapter as owner; "
                "assert CodexApplicationAdapter is owner",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_owner_has_exact_finite_exports_and_facades_are_negative(self) -> None:
        exported = (
            "ApplicationCapabilities",
            "EventSequenceScope",
            "ProjectCapabilities",
            "ProjectMode",
            "RuntimeCapabilities",
            "SupportLevel",
            "ThreadCapabilities",
            "ThreadDeletionCapability",
            "validate_application_capabilities",
        )
        for name in exported:
            with self.subTest(name=name):
                self.assertTrue(hasattr(capabilities, name))
                self.assertIn(name, capabilities.__all__)
                self.assertNotIn(name, applications.__all__)
        self.assertIsNone(importlib.util.find_spec("imagent.contracts"))

    def test_accepts_all_project_modes(self) -> None:
        for mode in capabilities.ProjectMode:
            with self.subTest(mode=mode):
                capabilities.validate_application_capabilities(_capabilities(mode))

    def test_rejects_inconsistent_capabilities(self) -> None:
        flat_with_discovery = _capabilities(capabilities.ProjectMode.FLAT)
        flat_with_discovery = capabilities.ApplicationCapabilities(
            projects=capabilities.ProjectCapabilities(
                mode=capabilities.ProjectMode.FLAT,
                discovery=capabilities.SupportLevel.NATIVE,
                reading=capabilities.SupportLevel.UNSUPPORTED,
            ),
            threads=flat_with_discovery.threads,
            runtime=flat_with_discovery.runtime,
        )
        with self.assertRaisesRegex(ContractViolation, "adapter projection"):
            capabilities.validate_application_capabilities(flat_with_discovery)

        duplicate_sources = capabilities.ApplicationCapabilities(
            projects=_capabilities(capabilities.ProjectMode.FLAT).projects,
            threads=_capabilities(capabilities.ProjectMode.FLAT).threads,
            runtime=_capabilities(capabilities.ProjectMode.FLAT).runtime,
            attachment_sources=(
                AttachmentSourceKind.LOCAL_PATH,
                AttachmentSourceKind.LOCAL_PATH,
            ),
        )
        with self.assertRaisesRegex(ContractViolation, "must be unique"):
            capabilities.validate_application_capabilities(duplicate_sources)

    def test_rejects_every_malformed_capability_discriminant(self) -> None:
        valid = _capabilities(capabilities.ProjectMode.MANAGED)
        for field in fields(valid.projects):
            with self.subTest(owner="projects", field=field.name):
                malformed = replace(
                    valid,
                    projects=replace(
                        valid.projects,
                        **{field.name: cast(Any, "bogus")},
                    ),
                )
                with self.assertRaises(ContractViolation):
                    capabilities.validate_application_capabilities(malformed)

        for field in fields(valid.threads):
            with self.subTest(owner="threads", field=field.name):
                malformed = replace(
                    valid,
                    threads=replace(
                        valid.threads,
                        **{field.name: cast(Any, "bogus")},
                    ),
                )
                with self.assertRaises(ContractViolation):
                    capabilities.validate_application_capabilities(malformed)

        for field in fields(valid.runtime):
            with self.subTest(owner="runtime", field=field.name):
                malformed = replace(
                    valid,
                    runtime=replace(
                        valid.runtime,
                        **{field.name: cast(Any, "bogus")},
                    ),
                )
                with self.assertRaises(ContractViolation):
                    capabilities.validate_application_capabilities(malformed)

    def test_rejects_malformed_capability_structure_and_attachment_sources(self) -> None:
        valid = _capabilities(capabilities.ProjectMode.MANAGED)
        malformed_values = (
            cast(Any, object()),
            replace(valid, projects=cast(Any, object())),
            replace(valid, threads=cast(Any, object())),
            replace(valid, runtime=cast(Any, object())),
            replace(valid, attachment_sources=cast(Any, [AttachmentSourceKind.LOCAL_PATH])),
            replace(valid, attachment_sources=(cast(Any, "bogus"),)),
        )
        for malformed in malformed_values:
            with self.subTest(malformed=malformed):
                with self.assertRaises(ContractViolation):
                    capabilities.validate_application_capabilities(malformed)

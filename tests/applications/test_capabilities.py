from __future__ import annotations

import unittest
from subprocess import run
from sys import executable

import imagent.applications as applications
from imagent import contracts
from imagent.applications import capabilities
from imagent.interaction.media import AttachmentSourceKind
from imagent.interaction.operations import ContractViolation


def _capabilities(mode: capabilities.ProjectMode) -> capabilities.ApplicationCapabilities:
    project_support = (
        capabilities.SupportLevel.NATIVE
        if mode is capabilities.ProjectMode.MANAGED
        else capabilities.SupportLevel.UNSUPPORTED
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
                self.assertFalse(hasattr(contracts, name))
                self.assertNotIn(name, contracts.__all__)
                self.assertNotIn(name, applications.__all__)

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
        with self.assertRaisesRegex(ContractViolation, "flat project mode"):
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

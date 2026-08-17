from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from textwrap import dedent

import yaml

from scripts.prepare_github_release import prepare_release

ROOT = Path(__file__).resolve().parents[2]
DEPENDENCY_BOUNDARY_PATHS = (
    ROOT / "pyproject.toml",
    ROOT / "uv.lock",
)
DEPENDENCY_BOUNDARY_TREES = (
    ROOT / ".github" / "workflows",
    ROOT / "src" / "imagent",
)
DEPENDENCY_BOUNDARY_TEXT_SUFFIXES = {".py", ".yaml", ".yml"}


def _is_dependency_boundary_text(path: Path) -> bool:
    return path.suffix in DEPENDENCY_BOUNDARY_TEXT_SUFFIXES or path.name == "py.typed"


class PackageIndependenceTests(unittest.TestCase):
    def test_version_typing_marker_and_profile_inventory_are_exact(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        init_source = (ROOT / "src" / "imagent" / "__init__.py").read_text(encoding="utf-8")
        version = metadata["project"]["version"]
        self.assertIn(f'__version__ = "{version}"', init_source)
        self.assertTrue((ROOT / "src" / "imagent" / "py.typed").is_file())

        smoke_source = (ROOT / "scripts" / "smoke_clean_install.py").read_text(encoding="utf-8")
        for profile in ("base", "qq", "telegram", "feishu", "weixin", "appserver"):
            self.assertIn(f'"{profile}": (', smoke_source)

    def test_release_mirror_has_one_owner_and_no_historical_module(self) -> None:
        expected = ROOT / "tests" / "engineering" / "test_release.py"
        self.assertTrue(expected.is_file())
        self.assertFalse((ROOT / "tests" / "test_package_independence.py").exists())
        self.assertEqual(
            set((ROOT / "tests").rglob("test_release.py")),
            {expected},
        )

    def test_sdk_dependency_graph_has_no_consumer_package_reference(self) -> None:
        paths = list(DEPENDENCY_BOUNDARY_PATHS)
        for root in DEPENDENCY_BOUNDARY_TREES:
            paths.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and _is_dependency_boundary_text(path)
            )

        violations = []
        for path in paths:
            if "__pycache__" in path.parts:
                continue
            content = path.read_text(encoding="utf-8")
            if "imcodex" in content.casefold():
                violations.append(path.relative_to(ROOT).as_posix())

        self.assertEqual(violations, [])

    def test_dependency_boundary_scan_ignores_non_source_metadata(self) -> None:
        self.assertFalse(_is_dependency_boundary_text(Path(".DS_Store")))
        self.assertTrue(_is_dependency_boundary_text(Path("adapter.py")))
        self.assertTrue(_is_dependency_boundary_text(Path("ci.yml")))
        self.assertTrue(_is_dependency_boundary_text(Path("py.typed")))

    def test_console_script_resolves_to_client_tool_owner_without_historical_package(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            metadata["project"]["scripts"]["imagent-send"],
            "imagent.interaction.client_tools.send:main",
        )
        self.assertTrue((ROOT / "src" / "imagent" / "interaction" / "client_tools").is_dir())
        self.assertFalse((ROOT / "src" / "imagent" / "cli").exists())

        environment = os.environ.copy()
        source_root = str(ROOT / "src")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_root
            if not existing_pythonpath
            else os.pathsep.join((source_root, existing_pythonpath))
        )
        code = r"""
import importlib
import importlib.util
import importlib.util
import sys

assert importlib.util.find_spec("imagent.cli") is None
try:
    importlib.import_module("imagent.cli")
except ModuleNotFoundError:
    pass
else:
    raise AssertionError("historical imagent.cli package is importable")

from imagent.interaction.client_tools.send import main

assert main.__module__ == "imagent.interaction.client_tools.send"
assert not any(
    name == "imagent.gateway" or name.startswith("imagent.gateway.")
    for name in sys.modules
)
assert not any(
    name == "imagent.applications" or name.startswith("imagent.applications.")
    for name in sys.modules
)
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_wheel_includes_the_one_reference_consumer_entry_point(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        wheel = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]
        self.assertEqual(
            wheel["force-include"],
            {"examples/reference_consumer": "examples/reference_consumer"},
        )
        self.assertEqual(
            {path.name for path in (ROOT / "examples" / "reference_consumer").glob("*.py")},
            {"application.py", "gateway.py", "interaction.py", "main.py"},
        )
        self.assertEqual(
            set((ROOT / "examples").rglob("main.py")),
            {ROOT / "examples" / "reference_consumer" / "main.py"},
        )

        smoke_source = (ROOT / "scripts" / "smoke_clean_install.py").read_text(encoding="utf-8")
        self.assertIn("REFERENCE_CONSUMER_CHECK,", smoke_source)
        self.assertIn("for name, (extra, code) in CASES.items():", smoke_source)
        self.assertNotIn("+ REFERENCE_CONSUMER_CHECK\n        + (", smoke_source)

    def test_github_release_workflow_is_tag_fenced_and_registry_free(self) -> None:
        workflow_text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)
        self.assertEqual(workflow["on"]["push"]["tags"], ["v0.1.0a1"])
        self.assertEqual(workflow["permissions"], {"contents": "write"})
        steps = workflow["jobs"]["release"]["steps"]
        used_actions = [step["uses"] for step in steps if "uses" in step]
        self.assertEqual(
            used_actions,
            [
                "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
                "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
                "astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e",
            ],
        )
        self.assertEqual(steps[0]["with"]["persist-credentials"], "false")
        commands = "\n".join(step.get("run", "") for step in steps)
        for fragment in (
            'git merge-base --is-ancestor "$GITHUB_SHA" origin/main',
            'gh release view "$GITHUB_REF_NAME"',
            "scripts/prepare_github_release.py",
            "--build-constraint build-constraints.txt --require-hashes",
            "--locked --no-install-project",
            "dist/SHA256SUMS",
            "--verify-tag",
            "--prerelease",
        ):
            self.assertIn(fragment, commands)
        uv_run_lines = [line.strip() for line in commands.splitlines() if "uv run" in line]
        self.assertTrue(uv_run_lines)
        self.assertTrue(all("uv run --no-sync" in line for line in uv_run_lines))
        self.assertNotIn("pypi", commands.casefold())
        self.assertNotIn("uv publish", commands.casefold())
        self.assertNotIn("twine", commands.casefold())

    def test_github_release_checkout_is_complete_and_needs_no_later_fetch(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        steps = workflow["jobs"]["release"]["steps"]
        checkout_index, checkout = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("uses", "").startswith("actions/checkout@")
        )

        self.assertEqual(checkout["with"]["persist-credentials"], "false")
        self.assertEqual(checkout["with"]["fetch-depth"], "0")

        later_commands = "\n".join(step.get("run", "") for step in steps[checkout_index + 1 :])
        self.assertIsNone(
            re.search(r"(?im)^\s*git\s+fetch(?:\s|$)", later_commands),
            "persist-credentials: false leaves a later git fetch without checkout authentication",
        )
        self.assertIn(
            'git merge-base --is-ancestor "$GITHUB_SHA" origin/main',
            later_commands,
        )

    def test_github_release_revalidates_peeled_tag_commit_before_publish(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        steps = workflow["jobs"]["release"]["steps"]
        publish_index, publish_step = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Publish GitHub prerelease"
        )
        expected_publish = dedent(
            """\
            wheel="$(find dist -maxdepth 1 -type f -name '*.whl' -print -quit)"
            test "$GITHUB_REF_NAME" = "v0.1.0a1"
            read -r object_type object_sha < <(
              gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/v0.1.0a1" \\
                --jq '.object.type + " " + .object.sha'
            )
            for _ in 1 2 3 4 5 6 7 8; do
              case "$object_type" in
                commit)
                  break
                  ;;
                tag)
                  read -r object_type object_sha < <(
                    gh api "repos/$GITHUB_REPOSITORY/git/tags/$object_sha" \\
                      --jq '.object.type + " " + .object.sha'
                  )
                  ;;
                *)
                  echo "Release tag resolved to unsupported object type: $object_type" >&2
                  exit 1
                  ;;
              esac
            done
            test "$object_type" = "commit"
            if [ "$object_sha" != "$GITHUB_SHA" ]; then
              echo "Release tag moved from $GITHUB_SHA to $object_sha; refusing to publish." >&2
              exit 1
            fi

            gh release create "$GITHUB_REF_NAME" \\
              "$wheel" dist/SHA256SUMS \\
              --repo "$GITHUB_REPOSITORY" \\
              --verify-tag \\
              --prerelease \\
              --title "IM Agent SDK ${GITHUB_REF_NAME#v}" \\
              --notes-file dist/RELEASE_NOTES.md
            """
        )

        self.assertEqual(publish_index, len(steps) - 1)
        self.assertEqual(publish_step["env"], {"GH_TOKEN": "${{ github.token }}"})
        self.assertEqual(publish_step["run"], expected_publish)
        self.assertLess(
            expected_publish.index('if [ "$object_sha" != "$GITHUB_SHA" ]; then'),
            expected_publish.index('gh release create "$GITHUB_REF_NAME"'),
        )

        bash = shutil.which("bash")
        if os.name == "nt":
            git = shutil.which("git")
            if git is None:
                self.fail("git is required to locate Git Bash")
            git_bash = Path(git).resolve().parents[1] / "bin" / "bash.exe"
            self.assertTrue(git_bash.is_file())
            bash = str(git_bash)
        if bash is None:
            self.fail("bash is required to validate the release workflow")

        source_commit = "a" * 40
        annotated_tag = "b" * 40
        moved_commit = "c" * 40
        fake_commands = dedent(
            f"""\
            set -euo pipefail
            find() {{
              printf '%s\\n' 'dist/im_agent_sdk-0.1.0a1-py3-none-any.whl'
            }}
            gh() {{
              case "$1:$TAG_SCENARIO:$2" in
                api:lightweight:repos/*/git/ref/tags/v0.1.0a1)
                  printf '%s\\n' 'commit {source_commit}'
                  ;;
                api:annotated:repos/*/git/ref/tags/v0.1.0a1)
                  printf '%s\\n' 'tag {annotated_tag}'
                  ;;
                api:annotated:repos/*/git/tags/{annotated_tag})
                  printf '%s\\n' 'commit {source_commit}'
                  ;;
                api:moved:repos/*/git/ref/tags/v0.1.0a1)
                  printf '%s\\n' 'commit {moved_commit}'
                  ;;
                release:*:create)
                  printf '%s\\n' published
                  ;;
                *)
                  return 97
                  ;;
              esac
            }}
            """
        )
        environment = {
            **os.environ,
            "GITHUB_REF_NAME": "v0.1.0a1",
            "GITHUB_REPOSITORY": "albert-zen/im-agent-sdk",
            "GITHUB_SHA": source_commit,
        }
        for scenario in ("lightweight", "annotated"):
            with self.subTest(scenario=scenario):
                completed = subprocess.run(
                    [bash],
                    input=fake_commands + publish_step["run"],
                    cwd=ROOT,
                    env={**environment, "TAG_SCENARIO": scenario},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "published\n")

        moved = subprocess.run(
            [bash],
            input=fake_commands + publish_step["run"],
            cwd=ROOT,
            env={**environment, "TAG_SCENARIO": "moved"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(moved.returncode, 0)
        self.assertNotIn("published", moved.stdout)
        self.assertIn(f"Release tag moved from {source_commit} to {moved_commit}", moved.stderr)

    def test_ci_workflow_actions_are_pinned_to_full_commit_shas(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        steps = workflow["jobs"]["validate"]["steps"]
        used_actions = [step["uses"] for step in steps if "uses" in step]
        self.assertEqual(
            used_actions,
            [
                "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
                "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
                "astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e",
            ],
        )
        for action in used_actions:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_build_backend_graph_is_exact_and_hash_constrained(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["build-system"]["requires"], ["hatchling==1.32.0"])
        constraints = (ROOT / "build-constraints.txt").read_text(encoding="utf-8")
        for requirement in (
            "hatchling==1.32.0",
            "packaging==26.3",
            "pathspec==1.1.1",
            "pluggy==1.6.0",
            "tomlkit==0.15.1",
            "trove-classifiers==2026.6.1.19",
        ):
            self.assertIn(requirement, constraints)
        self.assertEqual(constraints.count("--hash=sha256:"), 12)
        for workflow_name in ("ci.yml", "release.yml"):
            workflow = yaml.load(
                (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8"),
                Loader=yaml.BaseLoader,
            )
            steps = workflow["jobs"][next(iter(workflow["jobs"]))]["steps"]
            commands = "\n".join(step.get("run", "") for step in steps)
            self.assertIn("--locked --no-install-project", commands)
            self.assertIn("--build-constraint build-constraints.txt --require-hashes", commands)
            uv_run_lines = [line.strip() for line in commands.splitlines() if "uv run" in line]
            self.assertTrue(uv_run_lines)
            self.assertTrue(all("uv run --no-sync" in line for line in uv_run_lines))

    def test_release_evidence_binds_wheel_version_commit_and_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "im_agent_sdk-0.1.0a1-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "im_agent_sdk-0.1.0a1.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: im-agent-sdk\nVersion: 0.1.0a1\n",
                )
                archive.writestr("imagent/py.typed", "")
                archive.writestr("examples/reference_consumer/main.py", "")

            checksum, notes = prepare_release(
                wheel=wheel,
                tag="v0.1.0a1",
                source_commit="a" * 40,
                repository="albert-zen/im-agent-sdk",
                output_directory=root / "release",
            )

            digest = checksum.read_text(encoding="ascii").split()[0]
            self.assertEqual(len(digest), 64)
            notes_text = notes.read_text(encoding="utf-8")
            self.assertIn("`" + ("a" * 40) + "`", notes_text)
            self.assertIn(f"SHA-256: `{digest}`", notes_text)
            self.assertIn(
                "/releases/download/v0.1.0a1/im_agent_sdk-0.1.0a1-py3-none-any.whl",
                notes_text,
            )
            self.assertIn("not published to PyPI", notes_text)

            with self.assertRaisesRegex(ValueError, "does not match package version tag"):
                prepare_release(
                    wheel=wheel,
                    tag="v0.1.0a2",
                    source_commit="a" * 40,
                    repository="albert-zen/im-agent-sdk",
                    output_directory=root / "wrong-tag",
                )

            invalid_cases = (
                ("bad commit", wheel, "v0.1.0a1", "A" * 40, "albert-zen/im-agent-sdk"),
                ("bad repository", wheel, "v0.1.0a1", "a" * 40, "im-agent-sdk"),
                (
                    "bad filename",
                    root / "renamed.whl",
                    "v0.1.0a1",
                    "a" * 40,
                    "albert-zen/im-agent-sdk",
                ),
            )
            (root / "renamed.whl").write_bytes(wheel.read_bytes())
            for label, invalid_wheel, tag, commit, repository in invalid_cases:
                with self.subTest(label=label), self.assertRaises(ValueError):
                    prepare_release(
                        wheel=invalid_wheel,
                        tag=tag,
                        source_commit=commit,
                        repository=repository,
                        output_directory=root / label.replace(" ", "-"),
                    )

            invalid_metadata_wheel = root / "im_agent_sdk-0.1.0a1-py3-none-any.whl"
            for label, metadata_text, members in (
                (
                    "bad metadata name",
                    "Metadata-Version: 2.4\nName: other\nVersion: 0.1.0a1\n",
                    ("imagent/py.typed", "examples/reference_consumer/main.py"),
                ),
                (
                    "bad metadata version",
                    "Metadata-Version: 2.4\nName: im-agent-sdk\nVersion: 0.1.0a2\n",
                    ("imagent/py.typed", "examples/reference_consumer/main.py"),
                ),
                (
                    "missing member",
                    "Metadata-Version: 2.4\nName: im-agent-sdk\nVersion: 0.1.0a1\n",
                    ("imagent/py.typed",),
                ),
            ):
                with zipfile.ZipFile(invalid_metadata_wheel, "w") as archive:
                    archive.writestr("im_agent_sdk-0.1.0a1.dist-info/METADATA", metadata_text)
                    for member in members:
                        archive.writestr(member, "")
                with self.subTest(label=label), self.assertRaises(ValueError):
                    prepare_release(
                        wheel=invalid_metadata_wheel,
                        tag="v0.1.0a1",
                        source_commit="a" * 40,
                        repository="albert-zen/im-agent-sdk",
                        output_directory=root / label.replace(" ", "-"),
                    )

    def test_top_level_facade_is_finite_lazy_and_exact_in_a_clean_process(self) -> None:
        environment = os.environ.copy()
        source_root = str(ROOT / "src")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_root
            if not existing_pythonpath
            else os.pathsep.join((source_root, existing_pythonpath))
        )
        code = r"""
import importlib
import importlib.util
import sys
import typing

import imagent

value_exports = {
    "ActionResult": "imagent.gateway.actions",
    "ActionValue": "imagent.gateway.actions",
    "ApplicationActions": "imagent.gateway.actions",
    "ConversationActions": "imagent.gateway.actions",
    "ReadOutcome": "imagent.gateway.actions",
    "Failed": "imagent.gateway.outcomes",
    "Gateway": "imagent.gateway.runtime",
    "GatewayExtensions": "imagent.gateway.composition",
    "GatewayLimits": "imagent.gateway.composition",
    "GatewayStore": "imagent.gateway.persistence",
    "MemoryGatewayStore": "imagent.gateway.persistence",
    "OutcomeUnknown": "imagent.gateway.outcomes",
    "Partial": "imagent.gateway.outcomes",
    "ProjectionPolicy": "imagent.gateway.routing",
    "SQLiteGatewayStore": "imagent.gateway.persistence",
    "Succeeded": "imagent.gateway.outcomes",
    "CommandArgumentContract": "imagent.interaction.controllers",
    "CommandDefinition": "imagent.interaction.controllers",
    "CommandExecutionSafety": "imagent.interaction.controllers",
    "CommandHandler": "imagent.interaction.controllers",
    "CommandLimits": "imagent.interaction.controllers",
    "CommandRegistry": "imagent.interaction.controllers",
    "CommandResult": "imagent.interaction.controllers",
    "include_common_commands": "imagent.interaction.controllers",
}
assert imagent.__all__ == [
    "ActionResult",
    "ActionValue",
    "ApplicationActions",
    "CommandArgumentContract",
    "CommandDefinition",
    "CommandExecutionSafety",
    "CommandHandler",
    "CommandLimits",
    "CommandRegistry",
    "CommandResult",
    "ConversationActions",
    "Failed",
    "Gateway",
    "GatewayExtensions",
    "GatewayLimits",
    "GatewayStore",
    "MemoryGatewayStore",
    "OutcomeUnknown",
    "Partial",
    "ProjectionPolicy",
    "ReadOutcome",
    "SQLiteGatewayStore",
    "Succeeded",
    "include_common_commands",
]
for retired in (
    "imagent.adapters",
    "imagent.contracts",
    "imagent.delivery_coordination",
    "imagent.delivery_planning",
    "imagent.diagnostics",
    "imagent.events",
):
    assert importlib.util.find_spec(retired) is None
assert not hasattr(imagent, "projections")
assert typing.get_type_hints(imagent.__getattr__) == {"name": str, "return": object}
assert all(name not in imagent.__dict__ for name in value_exports)
assert not any(
    name == "imagent.gateway" or name.startswith("imagent.gateway.")
    for name in sys.modules
)
assert not any(
    name in sys.modules
    for name in ("websockets", "PIL", "Crypto", "lark_channel")
)
assert not hasattr(imagent, "unsupported_root_export")

for name, module_name in value_exports.items():
    first = getattr(imagent, name)
    owner = getattr(importlib.import_module(module_name), name)
    assert first is owner
    assert getattr(imagent, name) is owner
    assert imagent.__dict__[name] is owner
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

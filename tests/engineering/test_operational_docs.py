from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECIPES = (
    "bindings.md",
    "restart-and-replay.md",
    "interactive-requests.md",
    "media-and-artifacts.md",
    "proactive-delivery.md",
    "diagnostics.md",
)
REQUIRED_RECIPE_SECTIONS = (
    "## Prerequisites",
    "## Public API path",
    "## Owner and authority",
    "## Typed failure modes",
    "## Diagnostics",
    "## Executable evidence",
)


class OperationalDocumentationTests(unittest.TestCase):
    def test_python_snippets_are_syntactically_executable(self) -> None:
        documents = [ROOT / "docs" / "recipes" / filename for filename in RECIPES]
        documents.extend(
            (
                ROOT / "docs" / "onboarding" / "troubleshooting.md",
                ROOT / "docs" / "onboarding" / "upgrade-and-rollback.md",
            )
        )
        for document in documents:
            text = document.read_text(encoding="utf-8")
            snippets = re.findall(r"```python\n(.*?)\n```", text, flags=re.DOTALL)
            for index, snippet in enumerate(snippets):
                with self.subTest(document=document.name, snippet=index):
                    compile(
                        snippet,
                        f"{document}#python-{index}",
                        "exec",
                        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                    )

    def test_every_focused_recipe_names_the_operational_contract(self) -> None:
        recipe_root = ROOT / "docs" / "recipes"
        self.assertEqual(
            sorted(path.name for path in recipe_root.glob("*.md")),
            sorted(RECIPES),
        )
        for filename in RECIPES:
            text = (recipe_root / filename).read_text(encoding="utf-8")
            with self.subTest(recipe=filename):
                positions = [text.index(section) for section in REQUIRED_RECIPE_SECTIONS]
                self.assertEqual(positions, sorted(positions))
                self.assertRegex(text, r"tests\.[a-zA-Z0-9_.]+")
                self.assertIn("../components/", text)

    def test_troubleshooting_is_keyed_by_typed_failures_and_owners(self) -> None:
        text = (ROOT / "docs" / "onboarding" / "troubleshooting.md").read_text(encoding="utf-8")
        for owner in (
            "## Gateway action outcomes",
            "## Gateway input and routing failures",
            "## Application and interactive-request failures",
            "## Media, artifact, and delivery failures",
            "## Store and lifecycle failures",
        ):
            self.assertIn(owner, text)
        for code in (
            "`conflict`",
            "`capacity_exhausted`",
            "`missing_binding`",
            "`stale_binding`",
            "`request_stale`",
            "`OutcomeUnknown`",
        ):
            self.assertIn(code, text)
        self.assertIn("Never branch on provider exception text", text)

    def test_alpha_installation_provenance_is_exact_and_github_only(self) -> None:
        text = (ROOT / "docs" / "onboarding" / "upgrade-and-rollback.md").read_text(
            encoding="utf-8"
        )
        expected_hash = "123ebe9c7b086c9187bc05ce961f4942a6f8664de6a752f4e289d761c4e98d76"
        tag_object = "82bab131ad292f4e5e036704c3a9e26de074d18d"
        peeled_commit = "a72b24a2558c8b2aa48b05214588da4fc1a434db"
        self.assertIn("v0.1.0a1", text)
        self.assertIn(f"| Annotated tag object | `{tag_object}` |", text)
        self.assertIn(f"| Peeled tag commit | `{peeled_commit}` |", text)
        self.assertIn("'refs/tags/v0.1.0a1^{}'", text)
        self.assertIn(f"{tag_object} refs/tags/v0.1.0a1", text)
        self.assertIn(f"{peeled_commit} refs/tags/v0.1.0a1^{{}}", text)
        self.assertIn(expected_hash, text)
        self.assertIn("direct_url.json", text)
        self.assertIn("github.com/albert-zen/im-agent-sdk/releases/download/", text)
        self.assertIn("GitHub authentication with read access", text)
        self.assertIn("gh release download v0.1.0a1", text)
        self.assertIn("--repo albert-zen/im-agent-sdk", text)
        self.assertIn("browser asset URL is not an anonymously", text)
        self.assertRegex(text, r"does not claim that the package is published\s+on PyPI")
        self.assertIsNone(re.search(r"pip install\s+im-agent-sdk(?:\s|$)", text))
        self.assertNotIn('pip install "im-agent-sdk @ https://github.com/', text)


if __name__ == "__main__":
    unittest.main()

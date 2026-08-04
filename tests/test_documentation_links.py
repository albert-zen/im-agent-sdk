from __future__ import annotations

import unittest

from scripts.check_doc_links import ROOT, main, markdown_files

ENGINEERING_LEAVES = (
    "testing-and-conformance",
    "schema-conformance",
    "repository-maintainability",
    "agentkit",
    "release",
)


class DocumentationLinkTests(unittest.TestCase):
    def test_canonical_guidance_routes_engineering_support_changes(self) -> None:
        guidance = (
            ROOT / "AGENTS.md",
            ROOT / "README.md",
            ROOT / "plugins" / "agentkit" / "skills" / "agentkit" / "SKILL.md",
        )
        for path in guidance:
            with self.subTest(path=path):
                self.assertIn("docs/engineering/", path.read_text(encoding="utf-8"))

    def test_engineering_leaves_have_meaningful_design_and_testing_pages(self) -> None:
        for leaf in ENGINEERING_LEAVES:
            for page in ("design.md", "testing.md"):
                path = ROOT / "docs" / "engineering" / leaf / page
                with self.subTest(path=path):
                    content = path.read_text(encoding="utf-8")
                    self.assertGreater(len(content), 400)
                    self.assertTrue(content.startswith("# "))
                    self.assertIn("## ", content)

    def test_transitional_broad_pages_point_to_engineering_authority(self) -> None:
        transitional = (
            ROOT / "docs" / "components" / "testing-and-conformance",
            ROOT / "docs" / "components" / "repository-maintainability",
        )
        for directory in transitional:
            for page in ("design.md", "testing.md"):
                path = directory / page
                with self.subTest(path=path):
                    self.assertIn("../../engineering/", path.read_text(encoding="utf-8"))

    def test_markdown_inventory_includes_engineering_tree(self) -> None:
        files = set(markdown_files())
        expected = {
            ROOT / "docs" / "engineering" / leaf / page
            for leaf in ENGINEERING_LEAVES
            for page in ("design.md", "testing.md")
        }
        self.assertTrue(expected <= files)

    def test_all_local_markdown_links_resolve(self) -> None:
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from metasrc.errors import AgentError
from metasrc.validation.labels import discover_papers, resolve_label_target, resolve_paper


class GlobalLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name, text in (("a", "% \\label{ignored}\n\\label{x}"), ("b", "\\label{y}")):
            directory = self.root / "papers" / name
            directory.mkdir(parents=True)
            (directory / "main.tex").write_text(text, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_discovery_and_selected_paper_match(self) -> None:
        self.assertEqual([paper.name for paper in discover_papers(self.root)], ["a", "b"])
        with self.assertRaisesRegex(AgentError, "not in selected paper"):
            resolve_label_target("x", resolve_paper("b", self.root), self.root)

    def test_unrelated_duplicate_blocks_resolution(self) -> None:
        (self.root / "papers" / "b" / "other.tex").write_text("\\label{x}", encoding="utf-8")
        with self.assertRaisesRegex(AgentError, "Duplicate TeX labels"):
            resolve_label_target("y", resolve_paper("b", self.root), self.root)


if __name__ == "__main__":
    unittest.main()


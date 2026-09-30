from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from metasrc.errors import AgentError
from metasrc.validation.inputs import validate_inputs


class InputResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "papers" / "alpha").mkdir(parents=True)
        (self.root / "papers" / "alpha" / "main.tex").write_text(
            "\\label{thm:alpha}\n", encoding="utf-8"
        )
        (self.root / "markdown").mkdir()
        (self.root / "markdown" / "note.MD").write_text("arbitrary note", encoding="utf-8")
        self.specs = {
            "id": {"type": "math_target", "required": True},
            "paper": {"type": "paper_name", "required": False},
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_markdown_is_whole_file_and_rejects_paper(self) -> None:
        result = validate_inputs({"id": "markdown/note.MD"}, self.specs, self.root)
        self.assertEqual(result.target.kind, "markdown_file")
        self.assertEqual(result.target.scope, "whole_file")
        with self.assertRaisesRegex(AgentError, "must be omitted"):
            validate_inputs({"id": "markdown/note.MD", "paper": "alpha"}, self.specs, self.root)

    def test_label_requires_matching_paper(self) -> None:
        with self.assertRaisesRegex(AgentError, "required"):
            validate_inputs({"id": "thm:alpha"}, self.specs, self.root)
        result = validate_inputs({"id": "thm:alpha", "paper": "alpha"}, self.specs, self.root)
        self.assertEqual(result.target.line, 1)


if __name__ == "__main__":
    unittest.main()


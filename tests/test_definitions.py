from __future__ import annotations

import unittest

from metasrc.definitions import (
    COMMON_PERMISSION_INSTRUCTION, LEGACY_READ_ONLY_INSTRUCTION,
    RED_PERMISSION_INSTRUCTION, load_agent,
)
from metasrc.paths import REPO_ROOT


class DefinitionMigrationTests(unittest.TestCase):
    def test_permissions_and_required_papers(self) -> None:
        paper_wide = {
            "merge", "refine-tex-directory", "evaluate-references",
            "prelim-finder", "todo-reviewer",
        }
        for path in (REPO_ROOT / "agents").glob("*.json"):
            config = load_agent(path.stem)
            permission = RED_PERMISSION_INSTRUCTION if config["risk"] == "red" else COMMON_PERMISSION_INSTRUCTION
            self.assertIn(permission, config["prompt"])
            self.assertNotIn(LEGACY_READ_ONLY_INSTRUCTION, config["prompt"])
            if config["id"] in paper_wide:
                self.assertEqual(config["params"]["paper"], {"type": "paper_name", "required": True})
                self.assertNotIn("papers/template/", config["prompt"])

    def test_math_agents_have_conditional_paper_last(self) -> None:
        for agent_id in ("conjecture-evaluator", "proof-engine", "proof-reviewer", "proof-revisor"):
            config = load_agent(agent_id)
            self.assertEqual(config["params"]["id"]["type"], "math_target")
            self.assertEqual(list(config["params"])[-1], "paper")
            self.assertEqual(config["params"]["paper"], {"type": "paper_name", "required": False})


if __name__ == "__main__":
    unittest.main()


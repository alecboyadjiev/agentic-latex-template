from __future__ import annotations

import unittest

from metasrc.cli import format_agent_help
from metasrc.definitions import load_agent
from metasrc.errors import AgentError
from metasrc.paths import REPO_ROOT
from metasrc.validation.inputs import parse_param_assignments


class ParameterParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.specs = {
            "id": {"type": "string", "required": True},
            "changes": {"type": "string", "required": True},
        }

    def test_bare_values_fill_parameters_in_order(self) -> None:
        self.assertEqual(
            parse_param_assignments(["target", "revise this"], self.specs),
            {"id": "target", "changes": "revise this"},
        )

    def test_named_and_bare_values_can_be_mixed(self) -> None:
        self.assertEqual(
            parse_param_assignments(
                ["changes=revise this", "target"], self.specs
            ),
            {"changes": "revise this", "id": "target"},
        )

    def test_assignment_with_unknown_id_is_a_positional_value(self) -> None:
        self.assertEqual(
            parse_param_assignments(["x=y"], {"task": self.specs["id"]}),
            {"task": "x=y"},
        )

    def test_extra_value_is_rejected(self) -> None:
        with self.assertRaisesRegex(AgentError, "Too many --param values"):
            parse_param_assignments(["target", "changes", "extra"], self.specs)


class PromptContractTests(unittest.TestCase):
    def test_all_agent_definitions_validate(self) -> None:
        for path in (REPO_ROOT / "agents").glob("*.json"):
            with self.subTest(agent=path.stem):
                load_agent(path.stem)

    def test_parameterized_agents_use_concise_ids(self) -> None:
        expected = {
            "conjecture-evaluator": ["id", "paper"],
            "prompt-engineer": ["id", "task"],
            "prompt-revisor": ["id", "issues"],
            "proof-engine": ["id", "paper"],
            "proof-reviewer": ["id", "paper"],
            "proof-revisor": ["id", "changes", "paper"],
            "spec-designer": ["task"],
            "spec-updater": ["file", "changes"],
        }
        for agent_id, params in expected.items():
            with self.subTest(agent=agent_id):
                self.assertEqual(list(load_agent(agent_id)["params"]), params)

    def test_help_shows_ordered_parameters_and_both_forms(self) -> None:
        help_text = format_agent_help(load_agent("prompt-engineer"))
        self.assertIn("id: agent_id; required", help_text)
        self.assertIn('--p "<id>" --p "<task>"', help_text)
        self.assertIn('--p "id=<id>" --p "task=<task>"', help_text)


if __name__ == "__main__":
    unittest.main()

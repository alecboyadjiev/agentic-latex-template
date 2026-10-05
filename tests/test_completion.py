from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from metasrc.errors import AgentError
from metasrc.sessions.completion import (
    commit_completed_message, parse_completion_envelope,
)


class CompletionTests(unittest.TestCase):
    def test_exact_envelope_is_required(self) -> None:
        value = parse_completion_envelope('{"task":"completed","message":"artifact"}')
        self.assertEqual(value.task, "completed")
        with self.assertRaises(AgentError):
            parse_completion_envelope('{"task":"completed","message":"x","extra":1}')
        with self.assertRaises(AgentError):
            parse_completion_envelope('{"task":"complete","message":"x"}')
        with self.assertRaises(AgentError):
            parse_completion_envelope('{"task":"completed","message":"  "}')

    def test_not_completed_never_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"id": "sample", "output": {"format": "markdown"}}
            result = commit_completed_message(
                parse_completion_envelope({"task": "not completed", "message": "blocked"}),
                config, {}, root,
            )
            self.assertFalse(result.committed)
            self.assertFalse((root / "reports").exists())

    def test_report_is_reserved_only_at_commit_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"id": "sample", "output": {"format": "markdown"}}
            envelope = parse_completion_envelope({"task": "completed", "message": "# Done"})
            first = commit_completed_message(envelope, config, {}, root)
            self.assertEqual(first.artifact.read_text(encoding="utf-8"), "# Done\n")
            second = commit_completed_message(
                envelope, config, {}, root, already_committed=True,
                existing_artifact=first.artifact,
            )
            self.assertEqual(second.artifact, first.artifact)
            self.assertEqual(len(list((root / "reports" / "sample").glob("*.md"))), 1)

    def test_json_agent_message_uses_existing_validator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "agents").mkdir()
            config = {
                "id": "maker", "params": {"id": {"type": "agent_id", "required": True}},
                "output": {"format": "json_agent", "path": "agents/{id}.json"},
            }
            with self.assertRaises(AgentError):
                commit_completed_message(
                    parse_completion_envelope({"task": "completed", "message": json.dumps({"id": "bad"})}),
                    config, {"id": "created"}, root,
                )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys
import unittest

from metasrc.paths import REPO_ROOT
from metasrc.sessions.console import _emit_progress_update, _read_tty_line, run_console


class FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def request(self, operation: str, **arguments):
        self.calls.append((operation, arguments))
        if operation == "open":
            return {
                "closed": False, "status": "incomplete", "progress": "",
                "transcript": [
                    {"role": "user", "text": "task"},
                    {"role": "agent", "text": "need input"},
                ],
                "notes": [],
            }
        if operation == "message":
            return {"message": "Started a new turn."}
        if operation == "list":
            return {"menu": {"sessions": {}}}
        raise AssertionError(operation)


class SessionCliTests(unittest.TestCase):
    def test_tty_input_drains_buffer_without_waiting_for_status_refresh(self) -> None:
        keys = iter("responsive\n")
        status_calls = 0

        def status():
            nonlocal status_calls
            status_calls += 1
            return {"closed": False, "status": "executing", "progress": ""}

        output = io.StringIO()
        line, _ = _read_tty_line(
            status, output, key_reader=lambda: next(keys, None),
        )
        self.assertEqual(line, "responsive")
        self.assertEqual(status_calls, 1)
        self.assertIn("responsive", output.getvalue())

    def test_progress_updates_are_emitted_in_full_without_fixed_width_clipping(self) -> None:
        output = io.StringIO()
        first = "a" * 120
        complete = first + "b" * 120
        displayed, width = _emit_progress_update(output, "", first, 0)
        displayed, width = _emit_progress_update(output, displayed, complete, width)
        self.assertEqual(displayed, complete)
        self.assertEqual(output.getvalue().replace("\n", ""), complete)
        self.assertEqual(width, 0)

    def test_public_help_exposes_controls_but_not_supervisor_mode(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", "agent.py", "--help"], cwd=REPO_ROOT,
            text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--sessions", result.stdout)
        self.assertIn("--session SESSION", result.stdout)
        self.assertIn("--interrupt INTERRUPT", result.stdout)
        self.assertIn("--delete DELETE", result.stdout)
        self.assertNotIn("--session-supervisor", result.stdout)
        self.assertNotIn("--agent-worker", result.stdout)

    def test_non_tty_console_prints_full_transcript_and_dispatches_input(self) -> None:
        client = FakeClient()
        output = io.StringIO()
        code = run_console(
            client, 1, input_stream=io.StringIO("continue\n/exit\n"),
            output_stream=output,
        )
        self.assertEqual(code, 0)
        rendered = output.getvalue()
        self.assertIn("You:\ntask", rendered)
        self.assertIn("Agent:\nneed input", rendered)
        self.assertIn("Living agents: none", rendered)
        self.assertIn(("message", {"short_id": 1, "message": "continue"}), client.calls)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest

from metasrc.definitions import COMPLETION_CONTRACT_INSTRUCTION
from metasrc.providers.base import (
    Conversation, ProviderEvent, ProviderThread, ProviderTurn,
)
from metasrc.sessions.supervisor import SessionSupervisor


class FakeInteractiveProvider:
    provider_id = "fake"

    def __init__(self):
        self.deleted: list[str] = []
        self.steered: list[tuple[str, str, str]] = []
        self.interrupted: list[tuple[str, str]] = []

    def start_thread(self, settings):
        return ProviderThread("thread-1")

    def resume_thread(self, thread_id, settings):
        return ProviderThread(thread_id)

    def read_thread(self, thread_id, include_turns=True):
        return Conversation(thread_id, ())

    def start_turn(self, thread_id, message, settings, output_schema):
        self.output_schema = output_schema
        return ProviderTurn("turn-1")

    def steer_turn(self, thread_id, turn_id, message):
        self.steered.append((thread_id, turn_id, message))

    def interrupt_turn(self, thread_id, turn_id):
        self.interrupted.append((thread_id, turn_id))

    def delete_thread(self, thread_id):
        self.deleted.append(thread_id)

    def next_event(self, timeout=None):
        time.sleep(min(timeout or 0, 0.01))
        return None

    def close(self):
        pass


class SupervisorTests(unittest.TestCase):
    def _workspace(self, root: Path) -> None:
        (root / "agents").mkdir()
        (root / "agent-data").mkdir()
        config = {
            "id": "sample", "risk": "green", "description": "sample",
            "params": {},
            "prompt": (
                "All supplied inputs are valid; use them directly.\n"
                "You may write inside agent-data/ and are read-only elsewhere.\n\n"
                "Create a sample report. Completion criteria: the report is self-contained.\n\n"
                + COMPLETION_CONTRACT_INSTRUCTION
            ),
            "output": {"format": "markdown"},
        }
        (root / "agents" / "sample.json").write_text(json.dumps(config), encoding="utf-8")

    def test_completed_event_commits_once_deletes_chat_and_releases_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._workspace(root)
            provider = FakeInteractiveProvider()
            supervisor = SessionSupervisor(root, provider=provider)
            try:
                launched = supervisor.launch({
                    "agent": "sample", "params": {}, "model": "gpt-5.6-sol",
                    "reasoning": "high", "log": False,
                })
                self.assertEqual(launched["short_id"], 1)
                supervisor.handle_event(ProviderEvent(
                    "turn_completed", "thread-1", "turn-1", "completed",
                    json.dumps({"task": "completed", "message": "# Report"}),
                ))
                self.assertEqual(supervisor.registry.snapshot()["sessions"], {})
                self.assertEqual(provider.deleted, ["thread-1"])
                closed = supervisor.open_session(1, launched["session_key"])
                self.assertTrue(closed["closed"])
                self.assertEqual(closed["final_message"], "# Report")
                reports = list((root / "reports" / "sample").glob("*.md"))
                self.assertEqual(len(reports), 1)
                self.assertEqual(reports[0].read_text(encoding="utf-8"), "# Report\n")
                receipt = root / ".agent-runs" / "receipts" / f"{launched['session_key']}.json"
                self.assertTrue(receipt.is_file())
                self.assertNotIn("short_id", receipt.read_text(encoding="utf-8"))
                replacement = supervisor.registry.allocate("sample", root)
                self.assertEqual(replacement["short_id"], 1)
            finally:
                supervisor.close()

    def test_not_completed_remains_interactable_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._workspace(root)
            provider = FakeInteractiveProvider()
            supervisor = SessionSupervisor(root, provider=provider)
            try:
                launched = supervisor.launch({
                    "agent": "sample", "params": {}, "model": "gpt-5.6-sol",
                    "reasoning": "high", "log": False,
                })
                supervisor.handle_event(ProviderEvent(
                    "turn_completed", "thread-1", "turn-1", "completed",
                    json.dumps({"task": "not completed", "message": "Need evidence"}),
                ))
                state = supervisor.registry.resolve(1, launched["session_key"])
                self.assertEqual(state["status"], "incomplete")
                self.assertFalse((root / "reports").exists())
                supervisor.send_message(1, "Continue")
                self.assertEqual(supervisor.registry.resolve(1)["status"], "executing")
            finally:
                supervisor.close()

    def test_reasoning_summary_is_complete_and_survives_supervisor_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._workspace(root)
            provider = FakeInteractiveProvider()
            supervisor = SessionSupervisor(root, provider=provider)
            launched = supervisor.launch({
                "agent": "sample", "params": {}, "model": "gpt-5.6-sol",
                "reasoning": "high", "log": False,
            })
            first = "a" * 500
            second = "b" * 500
            supervisor.handle_event(ProviderEvent(
                "progress", "thread-1", "turn-1", "inProgress", first,
            ))
            supervisor.handle_event(ProviderEvent(
                "progress", "thread-1", "turn-1", "inProgress", second,
            ))
            self.assertEqual(supervisor.open_session(1)["progress"], first + second)
            supervisor.close()

            recovered = SessionSupervisor(root, provider=FakeInteractiveProvider())
            try:
                self.assertEqual(
                    recovered.open_session(1, launched["session_key"])["progress"],
                    first + second,
                )
            finally:
                recovered.close()


if __name__ == "__main__":
    unittest.main()

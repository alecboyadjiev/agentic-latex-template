from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from metasrc.errors import AgentError
from metasrc.sessions.console import render_menu
from metasrc.sessions.registry import SessionRegistry


class SessionRegistryTests(unittest.TestCase):
    def test_lowest_free_id_and_uuid_protect_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = SessionRegistry(root)
            first = registry.allocate("one", root)
            second = registry.allocate("two", root)
            self.assertEqual((first["short_id"], second["short_id"]), (1, 2))
            registry.remove(1, first["session_key"])
            replacement = registry.allocate("three", root)
            self.assertEqual(replacement["short_id"], 1)
            with self.assertRaisesRegex(AgentError, "reused|no longer"):
                registry.update(1, first["session_key"], status="incomplete")

    def test_concurrent_allocation_is_unique(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = SessionRegistry(root)
            with ThreadPoolExecutor(max_workers=8) as executor:
                states = list(executor.map(lambda value: registry.allocate(str(value), root), range(8)))
            self.assertEqual(sorted(state["short_id"] for state in states), list(range(1, 9)))

    def test_state_invariants_and_menu_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = SessionRegistry(root)
            state = registry.allocate("proof-reviewer", root)
            with self.assertRaisesRegex(AgentError, "active turn"):
                registry.update(
                    state["short_id"], state["session_key"], status="executing",
                    active_turn_id=None,
                )
            state = registry.update(
                state["short_id"], state["session_key"], status="executing",
                active_turn_id="turn-1",
            )
            menu = render_menu(registry.snapshot(), unicode=False, width=100)
            self.assertIn("proof-reviewer", menu)
            self.assertIn("executing", menu)
            registry.remove(state["short_id"], state["session_key"])
            self.assertEqual(render_menu(registry.snapshot()), "Living agents: none")


if __name__ == "__main__":
    unittest.main()

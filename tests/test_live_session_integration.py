from __future__ import annotations

import os
import time
import unittest

from metasrc.paths import REPO_ROOT
from metasrc.providers.base import ProviderSettings
from metasrc.providers.codex_app_server import CodexAppServerProvider
from metasrc.sessions.completion import COMPLETION_SCHEMA, parse_completion_envelope


@unittest.skipUnless(
    os.environ.get("AGENT_SESSION_INTEGRATION") == "1",
    "set AGENT_SESSION_INTEGRATION=1 to use the authenticated Codex installation",
)
class LiveSessionIntegrationTests(unittest.TestCase):
    def test_persistent_thread_turn_and_delete(self) -> None:
        data = (REPO_ROOT / "agent-data").resolve()
        settings = ProviderSettings(
            "gpt-5.6-sol", "low", REPO_ROOT, data, (data,),
        )
        provider = CodexAppServerProvider(request_timeout=90)
        thread = None
        try:
            thread = provider.start_thread(settings)
            turn = provider.start_turn(
                thread.id,
                "Do not use tools. Reply with task not completed and message integration check.",
                settings,
                COMPLETION_SCHEMA,
            )
            deadline = time.monotonic() + 180
            completed = None
            while time.monotonic() < deadline:
                event = provider.next_event(timeout=5)
                if event and event.kind == "turn_completed" and event.turn_id == turn.id:
                    completed = event
                    break
            self.assertIsNotNone(completed)
            self.assertEqual(completed.status, "completed")
            envelope = parse_completion_envelope(completed.message or "")
            self.assertEqual(envelope.task, "not completed")
        finally:
            if thread is not None:
                provider.delete_thread(thread.id)
            provider.close()


if __name__ == "__main__":
    unittest.main()

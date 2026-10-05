from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from metasrc.providers.base import ProviderSettings
from metasrc.providers.codex_app_server import CodexAppServerProvider
from metasrc.sessions.completion import COMPLETION_SCHEMA


FAKE_SERVER = r'''
import json, pathlib, sys
log = pathlib.Path(sys.argv[1])
requests = []
thread_id = "thread-1"
turn_id = "turn-1"
for raw in sys.stdin:
    value = json.loads(raw)
    requests.append(value)
    log.write_text(json.dumps(requests), encoding="utf-8")
    method = value.get("method")
    if "id" not in value:
        continue
    request_id = value["id"]
    if method == "initialize":
        result = {"userAgent": "fake"}
    elif method in {"thread/start", "thread/resume"}:
        result = {"thread": {"id": thread_id, "status": "idle"}}
    elif method == "thread/read":
        result = {"thread": {"id": thread_id, "turns": [{"id": turn_id, "status": "completed", "items": [
            {"id": "u", "type": "userMessage", "content": [{"type": "text", "text": "hello"}]},
            {"id": "a", "type": "agentMessage", "text": "world"}
        ]}]}}
    elif method == "turn/start":
        result = {"turn": {"id": turn_id, "status": "inProgress", "items": []}}
    else:
        result = {}
    print(json.dumps({"id": request_id, "result": result}), flush=True)
    if method == "turn/start":
        print(json.dumps({"method": "turn/started", "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": "inProgress", "items": []}}}), flush=True)
        print(json.dumps({"method": "item/reasoning/summaryTextDelta", "params": {"threadId": thread_id, "turnId": turn_id, "itemId": "r", "summaryIndex": 0, "delta": "checking"}}), flush=True)
        message = json.dumps({"task": "not completed", "message": "more"})
        print(json.dumps({"method": "turn/completed", "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": "completed", "items": [{"id": "a", "type": "agentMessage", "text": message}]}}}), flush=True)
'''


class CodexAppServerTests(unittest.TestCase):
    def test_jsonl_mapping_events_and_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "fake_server.py"
            log = root / "requests.json"
            script.write_text(textwrap.dedent(FAKE_SERVER), encoding="utf-8")
            data = root / "agent-data"
            data.mkdir()
            settings = ProviderSettings(
                "gpt-5.6-sol", "high", root, data, (data,),
            )
            provider = CodexAppServerProvider(
                command=[sys.executable, "-u", str(script), str(log)], request_timeout=5,
            )
            try:
                thread = provider.start_thread(settings)
                turn = provider.start_turn(thread.id, "hello", settings, COMPLETION_SCHEMA)
                events = [provider.next_event(2) for _ in range(3)]
                self.assertEqual([event.kind for event in events], [
                    "turn_started", "progress", "turn_completed",
                ])
                self.assertEqual(events[1].message, "checking")
                self.assertIn("not completed", events[2].message)
                conversation = provider.read_thread(thread.id)
                self.assertEqual([(item.role, item.text) for item in conversation.messages], [
                    ("user", "hello"), ("agent", "world"),
                ])
                provider.steer_turn(thread.id, turn.id, "extra")
                provider.interrupt_turn(thread.id, turn.id)
                provider.delete_thread(thread.id)
            finally:
                provider.close()
            requests = json.loads(log.read_text(encoding="utf-8"))
            methods = [item.get("method") for item in requests]
            self.assertEqual(methods[:3], ["initialize", "initialized", "thread/start"])
            turn_request = next(item for item in requests if item.get("method") == "turn/start")
            self.assertEqual(turn_request["params"]["outputSchema"], COMPLETION_SCHEMA)
            self.assertEqual(
                turn_request["params"]["sandboxPolicy"]["writableRoots"], [str(data)]
            )
            steer = next(item for item in requests if item.get("method") == "turn/steer")
            self.assertEqual(steer["params"]["expectedTurnId"], turn.id)


if __name__ == "__main__":
    unittest.main()

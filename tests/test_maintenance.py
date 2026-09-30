from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from metasrc.maintenance.base import MaintenanceResult, default_registry
from metasrc.maintenance.runner import run_maintenance
from metasrc.orchestration import execute_agent
from metasrc.providers.base import ModelResponse


class FakeTask:
    def __init__(self, task_id: str, seen: list[str]):
        self.id = task_id
        self.seen = seen

    def run(self, workspace_root: Path) -> MaintenanceResult:
        self.seen.append(self.id)
        return MaintenanceResult(self.id, "unchanged", "now", 0, {})


class MaintenanceTests(unittest.TestCase):
    def test_default_registry_only_has_pdf_cache(self) -> None:
        self.assertEqual([task.id for task in default_registry()], ["pdf-cache"])

    def test_tasks_run_in_registered_order(self) -> None:
        seen: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            results = run_maintenance(
                Path(temporary), [FakeTask("one", seen), FakeTask("two", seen)]
            )
        self.assertEqual(seen, ["one", "two"])
        self.assertEqual([result.id for result in results], seen)

    def test_maintenance_immediately_precedes_provider(self) -> None:
        events: list[str] = []

        class Provider:
            provider_id = "fake"

            def validate(self, request) -> None:
                events.append("validate")

            def invoke(self, request) -> ModelResponse:
                events.append("invoke")
                stdout = request.run_directory / "stdout.log"
                stderr = request.run_directory / "stderr.log"
                stdout.write_text("", encoding="utf-8")
                stderr.write_text("", encoding="utf-8")
                return ModelResponse("artifact", "fake", request.model, 0, stdout, stderr, None)

        class Task(FakeTask):
            def run(self, workspace_root: Path) -> MaintenanceResult:
                events.append("maintenance")
                return MaintenanceResult(self.id, "unchanged", "now", 0, {})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "agents").mkdir()
            (root / "agent-data").mkdir()
            definition = {
                "id": "sample", "risk": "green", "description": "sample",
                "params": {},
                "prompt": (
                    "All supplied inputs are valid; use them directly.\n"
                    "You may write inside agent-data/ and are read-only elsewhere.\n\n"
                    "Return ONLY a sample report."
                ),
                "output": {"format": "markdown"},
            }
            (root / "agents" / "sample.json").write_text(json.dumps(definition), encoding="utf-8")
            execute_agent(
                "sample", {}, "gpt-5.6-sol", "high", False, False, root,
                provider=Provider(), maintenance_tasks=[Task("task", [])],
            )
        self.assertEqual(events[-2:], ["maintenance", "invoke"])


if __name__ == "__main__":
    unittest.main()

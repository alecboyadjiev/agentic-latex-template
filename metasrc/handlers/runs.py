from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path


def create_run_directory(workspace_root: Path, agent_id: str) -> Path:
    runs = workspace_root / ".agent-runs"
    runs.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = runs / f"{timestamp}-{agent_id}"
    run_dir.mkdir()
    return run_dir


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_initial_run(run_dir: Path, prompt: str, metadata: dict) -> None:
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    atomic_json(run_dir / "run.json", metadata)


def update_run(run_dir: Path, **updates: object) -> dict:
    path = run_dir / "run.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata.update(updates)
    atomic_json(path, metadata)
    return metadata


def append_manager_log(run_dir: Path, message: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    with (run_dir / "manager.log").open("a", encoding="utf-8") as stream:
        stream.write(f"[{timestamp}] {message}\n")


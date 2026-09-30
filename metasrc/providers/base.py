from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ModelRequest:
    prompt: str
    model: str
    reasoning: str
    workspace_root: Path
    working_directory: Path
    writable_roots: tuple[Path, ...]
    output_schema: dict | None
    run_directory: Path
    stream_output: bool
    enable_event_log: bool


@dataclass(frozen=True)
class ModelResponse:
    final_text: str
    provider_id: str
    model: str
    exit_code: int
    stdout_path: Path
    stderr_path: Path
    events_path: Path | None


class ModelProvider(Protocol):
    provider_id: str

    def validate(self, request: ModelRequest) -> None: ...
    def invoke(self, request: ModelRequest) -> ModelResponse: ...


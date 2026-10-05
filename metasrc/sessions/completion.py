from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from metasrc.errors import AgentError
from metasrc.handlers.outputs import get_output_path, normalize_result, write_result


COMPLETION_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "enum": ["completed", "not completed"]},
        "message": {"type": "string", "minLength": 1},
    },
    "required": ["task", "message"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CompletionEnvelope:
    task: str
    message: str


@dataclass(frozen=True)
class CommitResult:
    committed: bool
    artifact: Path | None
    summary: str


def parse_completion_envelope(value: str | dict) -> CompletionEnvelope:
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AgentError(f"Completion response is not valid JSON: {exc}") from exc
    else:
        data = value
    if not isinstance(data, dict) or set(data) != {"task", "message"}:
        raise AgentError("Completion response must contain exactly 'task' and 'message'.")
    task, message = data.get("task"), data.get("message")
    if task not in {"completed", "not completed"}:
        raise AgentError("Completion task must be 'completed' or 'not completed'.")
    if not isinstance(message, str) or not message.strip():
        raise AgentError("Completion message must be a nonempty string.")
    return CompletionEnvelope(task, message)


def commit_completed_message(
    envelope: CompletionEnvelope,
    config: dict,
    params: dict[str, str],
    workspace_root: Path,
    *,
    already_committed: bool = False,
    existing_artifact: Path | None = None,
    destination_override: Path | None = None,
) -> CommitResult:
    if envelope.task != "completed":
        return CommitResult(False, None, envelope.message)
    if already_committed:
        return CommitResult(True, existing_artifact, envelope.message)
    output_format = config["output"]["format"]
    if output_format == "direct":
        return CommitResult(True, None, envelope.message)
    destination = destination_override or get_output_path(
        config, params, reserve=output_format == "markdown", workspace_root=workspace_root
    )
    assert destination is not None
    try:
        normalized = normalize_result(envelope.message, output_format, destination)
        overwrite = output_format == "markdown" or config["output"].get("overwrite", False)
        if destination.exists() and not overwrite:
            try:
                if destination.read_text(encoding="utf-8") == normalized:
                    return CommitResult(True, destination, envelope.message)
            except OSError:
                pass
        write_result(normalized, destination, overwrite)
    except Exception:
        if output_format == "markdown" and destination.exists() and destination.stat().st_size == 0:
            destination.unlink()
        raise
    return CommitResult(True, destination, envelope.message)

from __future__ import annotations

import shutil

from metasrc.errors import AgentError
from metasrc.providers.base import ModelRequest


DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING = "high"
VALID_MODELS = {"gpt-5.6-sol"}
VALID_REASONING = {"low", "medium", "high", "xhigh"}


def validate_model_settings(model: str, reasoning: str) -> None:
    if model not in VALID_MODELS:
        raise AgentError(f"Invalid model {model!r}. Allowed models: {', '.join(sorted(VALID_MODELS))}")
    if reasoning not in VALID_REASONING:
        raise AgentError(
            f"Invalid reasoning level {reasoning!r}. Allowed levels: "
            + ", ".join(sorted(VALID_REASONING))
        )


def locate_codex() -> str:
    executable = shutil.which("codex") or shutil.which("codex.cmd")
    if executable is None:
        raise AgentError("Could not find Codex CLI on PATH. Check: codex --version")
    return executable


class CodexCliProvider:
    provider_id = "codex-cli"

    def validate(self, request: ModelRequest) -> None:
        validate_model_settings(request.model, request.reasoning)
        workspace = request.workspace_root.resolve(strict=True)
        if not workspace.is_dir():
            raise AgentError(f"Workspace is not a directory: {workspace}")
        if not request.writable_roots:
            raise AgentError("A model request requires at least one writable root.")
        roots: list[Path] = []
        for root in request.writable_roots:
            if root.is_symlink() or not root.is_dir():
                raise AgentError(f"Writable root must be an existing non-symlink directory: {root}")
            resolved = root.resolve(strict=True)
            try:
                resolved.relative_to(workspace)
            except ValueError as exc:
                raise AgentError(f"Writable root escapes the workspace: {resolved}") from exc
            if resolved == workspace:
                raise AgentError("Repository root may not be model-writable.")
            roots.append(resolved)
        if len(set(roots)) != len(roots):
            raise AgentError("Writable roots may not repeat.")
        for index, first in enumerate(roots):
            for second in roots[index + 1:]:
                try:
                    second.relative_to(first)
                    raise AgentError(f"Writable roots overlap: {first} and {second}")
                except ValueError:
                    pass
                try:
                    first.relative_to(second)
                    raise AgentError(f"Writable roots overlap: {first} and {second}")
                except ValueError:
                    pass
        working = request.working_directory.resolve(strict=True)
        if working not in roots:
            raise AgentError("Provider working directory must equal an approved writable root.")
        run = request.run_directory.resolve(strict=False)
        try:
            run.relative_to(workspace / ".agent-runs")
        except ValueError as exc:
            raise AgentError("Run directory is outside the active workspace run root.") from exc

    def invoke(self, request: ModelRequest):
        raise AgentError(
            "One-shot Codex execution has been retired; launch through the session supervisor."
        )

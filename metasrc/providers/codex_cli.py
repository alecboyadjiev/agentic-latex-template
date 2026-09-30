from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import threading

from metasrc.errors import AgentError
from metasrc.providers.base import ModelRequest, ModelResponse


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

    def build_command(self, request: ModelRequest) -> list[str]:
        final_message = request.run_directory / "final.txt"
        command = [
            locate_codex(), "exec", "--ephemeral", "--cd", str(request.working_directory),
            "--sandbox", "workspace-write", "-c", 'approval_policy="never"',
            "--model", request.model, "-c", f'model_reasoning_effort="{request.reasoning}"',
            "--output-last-message", str(final_message),
        ]
        for root in request.writable_roots:
            if root.resolve() != request.working_directory.resolve():
                command.extend(("--add-dir", str(root)))
        if request.enable_event_log:
            command.append("--json")
        command.append("-")
        return command

    def invoke(self, request: ModelRequest) -> ModelResponse:
        command = self.build_command(request)
        run_dir = request.run_directory
        final_message = run_dir / "final.txt"
        stderr_path = run_dir / "stderr.log"
        events_path = run_dir / "events.jsonl" if request.enable_event_log else None
        stdout_path = events_path or (run_dir / "stdout.log")

        with stderr_path.open("w", encoding="utf-8") as error_log:
            process = subprocess.Popen(
                command, cwd=request.working_directory, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
            )
            assert process.stdin is not None and process.stdout is not None and process.stderr is not None

            def relay_stderr() -> None:
                for line in process.stderr:
                    error_log.write(line)
                    error_log.flush()
                    if request.stream_output:
                        print(line, end="", file=sys.stderr, flush=True)

            thread = threading.Thread(target=relay_stderr, daemon=True)
            thread.start()
            process.stdin.write(request.prompt)
            process.stdin.close()
            with stdout_path.open("w", encoding="utf-8") as output:
                for line in process.stdout:
                    output.write(line)
                    output.flush()
                    if request.stream_output:
                        print(line, end="", flush=True)
            exit_code = process.wait()
            thread.join()
        if exit_code:
            raise AgentError(f"Codex exited with status {exit_code}. See: {stderr_path}")
        if not final_message.is_file():
            raise AgentError("Codex succeeded but did not produce a final message.")
        return ModelResponse(
            final_text=final_message.read_text(encoding="utf-8"),
            provider_id=self.provider_id, model=request.model, exit_code=exit_code,
            stdout_path=stdout_path, stderr_path=stderr_path, events_path=events_path,
        )

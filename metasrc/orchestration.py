from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Sequence

from metasrc.definitions import load_agent, render_template
from metasrc.errors import AgentError
from metasrc.handlers.outputs import get_output_path
from metasrc.handlers.runs import (
    append_manager_log, create_run_directory, update_run, write_initial_run,
)
from metasrc.maintenance.base import MaintenanceTask
from metasrc.maintenance.runner import MaintenanceError, run_maintenance
from metasrc.providers.base import ModelProvider, ModelRequest
from metasrc.providers.codex_cli import CodexCliProvider, validate_model_settings
from metasrc.sessions.completion import (
    COMPLETION_SCHEMA, commit_completed_message, parse_completion_envelope,
)
from metasrc.validation.inputs import ValidatedInputs, validate_inputs


@dataclass(frozen=True)
class PreparedInvocation:
    config: dict
    inputs: ValidatedInputs
    prompt: str
    destination: Path | None
    overwrite: bool
    request: ModelRequest


def _runtime_context(
    workspace: Path, inputs: ValidatedInputs, writable_roots: tuple[Path, ...]
) -> str:
    relative_writes = [root.relative_to(workspace).as_posix() for root in writable_roots]
    context = {
        "workspace_root": str(workspace),
        "repository_relative_base": str(workspace),
        "paper": inputs.paper.as_metadata() if inputs.paper else None,
        "mathematical_target": inputs.target.as_metadata() if inputs.target else None,
        "writable_roots": relative_writes,
        "permission": (
            "Return handler-owned final artifacts in the final response. Writes in "
            "agent-data/ are only task-supporting data; manuscript edits are allowed "
            "only when the resolved paper is also listed as writable."
        ),
    }
    if inputs.target and inputs.target.kind == "markdown_file":
        context["whole_file_note_limit"] = (
            "The complete Markdown note is the target. It may contain multiple or no "
            "clearly identifiable objects; do not invent a narrower indexed target."
        )
    return "\n\nHandler-resolved runtime context:\n" + json.dumps(context, indent=2)


def _permission_roots(
    workspace: Path, config: dict, inputs: ValidatedInputs
) -> tuple[tuple[Path, ...], Path]:
    agent_data = workspace / "agent-data"
    if agent_data.is_symlink() or not agent_data.is_dir():
        raise AgentError(f"Required agent-data directory is missing or symlinked: {agent_data}")
    roots: list[Path] = [agent_data.resolve(strict=True)]
    if config["risk"] == "red":
        if inputs.paper is None:
            raise AgentError("A manuscript-writing agent requires a resolved paper.")
        rendered = render_template(config["write_scope"], inputs.values)
        scope = (workspace / rendered).resolve(strict=True)
        if scope != inputs.paper.path:
            raise AgentError(
                f"Dynamic write_scope {rendered!r} disagrees with resolved paper "
                f"{inputs.paper.relative_path!r}."
            )
        roots.append(inputs.paper.path)
        working = inputs.paper.path
    else:
        working = roots[0]
    return tuple(roots), working


def prepare_invocation(
    agent_id: str,
    supplied: dict[str, str],
    model: str,
    reasoning: str,
    enable_json_log: bool,
    stream_output: bool,
    workspace_root: Path,
    provider: ModelProvider | None = None,
    reserve_output: bool = False,
) -> PreparedInvocation:
    workspace = workspace_root.resolve(strict=True)
    validate_model_settings(model, reasoning)
    config = load_agent(agent_id, workspace)
    inputs = validate_inputs(supplied, config.get("params", {}), workspace)
    roots, working = _permission_roots(workspace, config, inputs)
    prompt = render_template(config["prompt"], inputs.values)
    prompt += _runtime_context(workspace, inputs, roots)
    is_report = config["output"]["format"] == "markdown"
    destination = get_output_path(
        config, inputs.values, reserve=reserve_output and is_report,
        workspace_root=workspace,
    )
    overwrite = is_report or config["output"].get("overwrite", False)
    if destination is not None and destination.exists() and not overwrite:
        raise AgentError(f"Refusing to overwrite existing file:\n{destination}")
    placeholder = workspace / ".agent-runs" / "pending"
    request = ModelRequest(
        prompt=prompt, model=model, reasoning=reasoning, workspace_root=workspace,
        working_directory=working, writable_roots=roots, output_schema=COMPLETION_SCHEMA,
        run_directory=placeholder, stream_output=stream_output,
        enable_event_log=enable_json_log,
    )
    (provider or CodexCliProvider()).validate(request)
    return PreparedInvocation(config, inputs, prompt, destination, overwrite, request)


def execute_agent(
    agent_id: str,
    supplied: dict[str, str],
    model: str,
    reasoning: str,
    enable_json_log: bool,
    stream_output: bool,
    workspace_root: Path,
    provider: ModelProvider | None = None,
    maintenance_tasks: Sequence[MaintenanceTask] | None = None,
) -> dict:
    active_provider = provider or CodexCliProvider()
    prepared = prepare_invocation(
        agent_id, supplied, model, reasoning, enable_json_log, stream_output,
        workspace_root, active_provider, reserve_output=False,
    )
    run_dir = create_run_directory(prepared.request.workspace_root, agent_id)
    request = replace(prepared.request, run_directory=run_dir)
    active_provider.validate(request)
    metadata = {
        "agent": agent_id,
        "risk": prepared.config["risk"],
        "provider": active_provider.provider_id,
        "model": model,
        "reasoning": reasoning,
        "workspace": str(request.workspace_root),
        "paper": prepared.inputs.paper.as_metadata() if prepared.inputs.paper else None,
        "target": prepared.inputs.target.as_metadata() if prepared.inputs.target else None,
        "writable_roots": [str(path) for path in request.writable_roots],
        "maintenance": [],
        "output": str(prepared.destination) if prepared.destination else None,
        "status": "running",
    }
    write_initial_run(run_dir, prepared.prompt, metadata)
    log = lambda message: append_manager_log(run_dir, message)
    log(f"START agent={agent_id} risk={prepared.config['risk']}")
    try:
        results = run_maintenance(request.workspace_root, maintenance_tasks, log)
        update_run(run_dir, maintenance=[item.as_dict() for item in results])
        response = active_provider.invoke(request)
        envelope = parse_completion_envelope(response.final_text)
        if envelope.task != "completed":
            update_run(run_dir, status="incomplete", exit_code=response.exit_code)
            log("COMPLETE not completed")
            return {"agent": agent_id, "status": "incomplete", "output": None, "run": str(run_dir)}
        committed = commit_completed_message(
            envelope, prepared.config, prepared.inputs.values,
            prepared.request.workspace_root,
        )
        prepared = replace(prepared, destination=committed.artifact)
        update_run(
            run_dir, status="success", exit_code=response.exit_code,
            stdout=str(response.stdout_path), stderr=str(response.stderr_path),
            events=str(response.events_path) if response.events_path else None,
        )
        log("COMPLETE success")
    except MaintenanceError as exc:
        recorded = [item.as_dict() for item in exc.completed] + [exc.result.as_dict()]
        update_run(run_dir, status="failed", maintenance=recorded, error=str(exc))
        log(f"FAILED {type(exc).__name__}: {exc}")
        _remove_empty_reservation(prepared)
        raise
    except Exception as exc:
        update_run(run_dir, status="failed", error=f"{type(exc).__name__}: {exc}")
        log(f"FAILED {type(exc).__name__}: {exc}")
        _remove_empty_reservation(prepared)
        raise
    if stream_output:
        print("SUCCESS")
        if prepared.destination:
            print(f"Output : {prepared.destination}")
        else:
            print(f"Writes : {prepared.inputs.paper.path if prepared.inputs.paper else request.working_directory}")
        print(f"Run    : {run_dir}")
    return {
        "agent": agent_id, "status": "success",
        "output": str(prepared.destination) if prepared.destination else None,
        "run": str(run_dir),
    }


def _remove_empty_reservation(prepared: PreparedInvocation) -> None:
    if (
        prepared.config["output"]["format"] == "markdown"
        and prepared.destination is not None and prepared.destination.exists()
        and prepared.destination.stat().st_size == 0
    ):
        prepared.destination.unlink()

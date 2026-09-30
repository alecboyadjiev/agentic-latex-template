from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import tempfile

from metasrc.definitions import render_template, validate_agent_config
from metasrc.errors import AgentError


def path_safe_target(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    if not safe:
        raise AgentError(f"Target {value!r} has no path-safe characters.")
    return safe


def next_report_path(agent_id: str, reserve: bool, workspace_root: Path) -> Path:
    reports = (workspace_root / "reports").resolve()
    directory = (reports / agent_id).resolve()
    try:
        directory.relative_to(reports)
    except ValueError as exc:
        raise AgentError("Report directory escapes reports/.") from exc
    if reserve:
        directory.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now().strftime("%d-%m")
    for index in range(1, 1_000_000):
        candidate = directory / f"{prefix} ({index}).md"
        if reserve:
            try:
                with candidate.open("x", encoding="utf-8"):
                    pass
                return candidate
            except FileExistsError:
                continue
        elif not candidate.exists():
            return candidate
    raise AgentError(f"Could not allocate a report name in {directory}")


def get_output_path(
    config: dict,
    params: dict[str, str],
    reserve: bool = False,
    workspace_root: Path | None = None,
) -> Path | None:
    workspace = (workspace_root or Path.cwd()).resolve()
    output = config["output"]
    if output["format"] == "markdown":
        return next_report_path(config["id"], reserve, workspace)
    if output["format"] == "direct":
        return None
    path_params = dict(params)
    for name, value in params.items():
        if config.get("params", {}).get(name, {}).get("type") in {"tex_label", "math_target"}:
            path_params[name] = path_safe_target(value)
    relative = Path(render_template(output["path"], path_params))
    if relative.is_absolute() or relative.drive:
        raise AgentError("Output paths must be repository-relative.")
    destination = (workspace / relative).resolve()
    try:
        destination.relative_to(workspace)
        if output["format"] == "json_agent":
            destination.relative_to((workspace / "agents").resolve())
        elif destination != (workspace / "README.md").resolve():
            raise ValueError
    except ValueError as exc:
        raise AgentError(f"Output path is outside its handler-owned destination: {destination}") from exc
    if output["format"] == "json_agent" and destination.suffix.lower() != ".json":
        raise AgentError("JSON agent output must end in .json.")
    return destination


def normalize_result(result: str, output_format: str, destination: Path) -> str:
    if not result.strip():
        raise AgentError("Agent returned an empty artifact.")
    if output_format in {"markdown", "file"}:
        return result.rstrip() + "\n"
    try:
        generated = json.loads(result)
    except json.JSONDecodeError as exc:
        raise AgentError(f"Agent output was not a bare valid JSON definition: {exc}") from exc
    validate_agent_config(generated, expected_id=destination.stem)
    return json.dumps(generated, indent=2, ensure_ascii=False) + "\n"


def write_result(result: str, destination: Path, overwrite: bool) -> None:
    if destination.exists() and not overwrite:
        raise AgentError(f"Refusing to overwrite existing file:\n{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(result)
        except FileExistsError as exc:
            raise AgentError(f"Refusing to overwrite existing file:\n{destination}") from exc
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as stream:
            stream.write(result)
            temporary = Path(stream.name)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

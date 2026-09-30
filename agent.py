from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path


DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING = "high"
VALID_MODELS = {"gpt-5.6-sol"}
VALID_REASONING = {"low", "medium", "high", "xhigh"}
VALID_PARAM_TYPES = {
    "agent_id",
    "choice",
    "repo_file",
    "slug",
    "string",
    "tex_label",
}
VALID_OUTPUT_FORMATS = {"direct", "file", "json_agent", "markdown"}
VALID_RISK_LEVELS = {"green", "yellow", "red"}
VALID_AGENT_KEYS = {
    "id",
    "risk",
    "description",
    "params",
    "prompt",
    "output",
    "write_scope",
}
VALID_OUTPUT_KEYS = {"format", "path", "overwrite"}
COMMON_PARAM_SPEC_KEYS = {
    "type",
    "required",
    "default",
    "min_length",
    "max_length",
    "pattern",
}
TYPE_SPECIFIC_PARAM_KEYS = {
    "agent_id": {"must_exist"},
    "choice": {"choices"},
    "repo_file": {"extensions"},
    "slug": set(),
    "string": set(),
    "tex_label": set(),
}
VALID_INPUTS_INSTRUCTION = "All supplied inputs are valid; use them directly."
READ_ONLY_INSTRUCTION = "You are read-only; do not attempt writes."

REPO_ROOT = Path(__file__).resolve().parent
AGENTS_DIR = REPO_ROOT / "agents"
REPORTS_DIR = REPO_ROOT / "reports"
RUNS_DIR = REPO_ROOT / ".agent-runs"
BATCHES_DIR = RUNS_DIR / "batches"
LAUNCHES_DIR = RUNS_DIR / "launches"

AGENT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
PARAM_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
SLUG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
TEX_LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._/-]*")
TEX_LABEL_COMMAND_PATTERN = re.compile(
    r"\\label\s*\{\s*([^{}\s]+)\s*\}"
)


class AgentError(Exception):
    pass


def emit_status(message: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"[{timestamp}] {message}", flush=True)


def parse_param_assignments(
    items: list[str], specs: dict[str, dict]
) -> dict[str, str]:
    """Parse mixed named and positional parameters in declaration order."""
    result: dict[str, str] = {}
    for item in items:
        key: str | None = None
        value = item
        if "=" in item:
            candidate, candidate_value = item.split("=", 1)
            candidate = candidate.strip()
            if candidate in specs:
                key = candidate
                value = candidate_value

        if key is None:
            key = next((name for name in specs if name not in result), None)
            if key is None:
                expected = ", ".join(specs) or "none"
                raise AgentError(
                    f"Too many --param values. This agent's parameters, in "
                    f"order, are: {expected}."
                )

        if key in result:
            raise AgentError(f"Parameter {key!r} was supplied more than once.")
        result[key] = value
    return result


def format_param_details(name: str, spec: dict) -> str:
    details = [spec.get("type", "string")]
    if spec.get("required", False):
        details.append("required")
    else:
        details.append(f"default={spec['default']!r}")
    if "min_length" in spec:
        details.append(f"min length {spec['min_length']}")
    if "max_length" in spec:
        details.append(f"max length {spec['max_length']}")
    if "choices" in spec:
        details.append("choices: " + ", ".join(spec["choices"]))
    if "extensions" in spec:
        details.append("extensions: " + ", ".join(spec["extensions"]))
    if spec.get("type") == "agent_id":
        details.append("must exist" if spec.get("must_exist") else "must be new")
    return f"  {name}: " + "; ".join(details)


def format_agent_help(config: dict) -> str:
    agent_id = config["id"]
    specs = config.get("params", {})
    lines = [f"{agent_id} - {config['description']}", "Parameters (in order):"]
    if specs:
        lines.extend(format_param_details(name, spec) for name, spec in specs.items())
    else:
        lines.append("  (none)")

    positional = f"  python agent.py --agent {agent_id}"
    named = positional
    for name in specs:
        positional += f' --p "<{name}>"'
        named += f' --p "{name}=<{name}>"'
    lines.extend(["Usage:", positional])
    if specs:
        lines.append(named)
    return "\n".join(lines)


def requested_help_agent(argv: list[str]) -> str | None:
    for index, argument in enumerate(argv):
        if argument in {"--agent", "--a"} and index + 1 < len(argv):
            return argv[index + 1]
        if argument.startswith("--agent=") or argument.startswith("--a="):
            return argument.split("=", 1)[1]
    return None


def build_help_epilog(argv: list[str]) -> str:
    requested = requested_help_agent(argv)
    if requested:
        configs = [load_agent(requested)]
    else:
        configs = [load_agent(path.stem) for path in sorted(AGENTS_DIR.glob("*.json"))]
    heading = (
        "Selected prompt:\n"
        if requested
        else "Available prompts (parameters are positional in the shown order):\n"
    )
    return heading + "\n\n".join(format_agent_help(config) for config in configs)


def validate_agent_id(agent_id: str) -> None:
    if AGENT_ID_PATTERN.fullmatch(agent_id) is None:
        raise AgentError(
            "Invalid agent ID. Use letters, digits, underscores, hyphens, "
            "or periods, starting with a letter or digit."
        )


def get_template_fields(template: str, location: str) -> set[str]:
    fields: set[str] = set()
    try:
        for _, field_name, format_spec, conversion in (
            string.Formatter().parse(template)
        ):
            if field_name is None:
                continue
            if PARAM_NAME_PATTERN.fullmatch(field_name) is None:
                raise AgentError(
                    f"{location} uses invalid placeholder {{{field_name}}}. "
                    "Use a bare parameter name."
                )
            if format_spec or conversion:
                raise AgentError(
                    f"{location} placeholder {{{field_name}}} may not use "
                    "a format specifier or conversion."
                )
            fields.add(field_name)
    except ValueError as e:
        raise AgentError(f"Invalid template syntax in {location}: {e}") from e
    return fields


def validate_agent_config(
    config: object,
    expected_id: str | None = None,
) -> dict:
    if not isinstance(config, dict):
        raise AgentError("Agent definition must be a JSON object.")
    unknown_config_keys = set(config) - VALID_AGENT_KEYS
    if unknown_config_keys:
        raise AgentError(
            "Agent JSON has unknown field(s): "
            + ", ".join(sorted(unknown_config_keys))
        )

    agent_id = config.get("id")
    if not isinstance(agent_id, str):
        raise AgentError("Agent JSON must contain a string field 'id'.")
    validate_agent_id(agent_id)
    if expected_id is not None and agent_id != expected_id:
        raise AgentError(
            f"Agent ID mismatch. Expected {expected_id!r}, "
            f"but JSON declares {agent_id!r}."
        )

    description = config.get("description")
    if not isinstance(description, str) or not description.strip():
        raise AgentError("Agent JSON requires a nonempty 'description'.")

    prompt = config.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AgentError("Agent JSON requires a nonempty string 'prompt'.")
    risk = config.get("risk")
    if risk not in VALID_RISK_LEVELS:
        raise AgentError(
            "Agent JSON requires risk to be one of: "
            + ", ".join(sorted(VALID_RISK_LEVELS))
        )
    expected_prefix = VALID_INPUTS_INSTRUCTION
    if risk in {"green", "yellow"}:
        expected_prefix += "\n" + READ_ONLY_INSTRUCTION
    if not prompt.startswith(expected_prefix + "\n\n"):
        raise AgentError(
            "Agent prompt must start with the canonical valid-input line and "
            "the risk-appropriate read-only line."
        )
    if prompt.count(VALID_INPUTS_INSTRUCTION) != 1:
        raise AgentError("Agent prompt must contain the valid-input line exactly once.")
    expected_read_only_count = 1 if risk in {"green", "yellow"} else 0
    if prompt.count(READ_ONLY_INSTRUCTION) != expected_read_only_count:
        raise AgentError(
            "Agent prompt has an invalid number of canonical read-only lines."
        )
    if re.search(r"\b(?:handler|handlers|sandbox|orchestration)\b", prompt, re.I):
        raise AgentError(
            "Agent prompts may not expose execution machinery such as handlers, "
            "sandboxes, or orchestration."
        )

    write_scope = config.get("write_scope")
    if risk == "red":
        if write_scope != "paper":
            raise AgentError("Red agents must declare write_scope as 'paper'.")
    elif write_scope is not None:
        raise AgentError("Only red agents may declare write_scope.")

    specs = config.get("params", {})
    if not isinstance(specs, dict):
        raise AgentError("Agent JSON field 'params' must be an object.")

    for name, spec in specs.items():
        if PARAM_NAME_PATTERN.fullmatch(name) is None:
            raise AgentError(f"Invalid parameter name {name!r}.")
        if not isinstance(spec, dict):
            raise AgentError(f"Parameter specification {name!r} must be an object.")

        param_type = spec.get("type", "string")
        if param_type not in VALID_PARAM_TYPES:
            raise AgentError(
                f"Unsupported parameter type {param_type!r} for {name!r}."
            )
        allowed_spec_keys = (
            COMMON_PARAM_SPEC_KEYS | TYPE_SPECIFIC_PARAM_KEYS[param_type]
        )
        unknown_spec_keys = set(spec) - allowed_spec_keys
        if unknown_spec_keys:
            raise AgentError(
                f"Parameter {name!r} has unknown specification field(s): "
                + ", ".join(sorted(unknown_spec_keys))
            )

        required = spec.get("required", False)
        if not isinstance(required, bool):
            raise AgentError(f"Parameter {name!r} 'required' must be boolean.")
        if required and "default" in spec:
            raise AgentError(
                f"Parameter {name!r} cannot be required and have a default."
            )
        if not required and "default" not in spec:
            raise AgentError(
                f"Parameter {name!r} must be required or declare a default."
            )
        if "default" in spec and not isinstance(spec["default"], str):
            raise AgentError(f"Parameter {name!r} default must be a string.")

        min_length = spec.get("min_length")
        max_length = spec.get("max_length")
        if min_length is not None and (
            type(min_length) is not int or min_length < 0
        ):
            raise AgentError(
                f"Parameter {name!r} min_length must be a nonnegative integer."
            )
        if max_length is not None and (
            type(max_length) is not int or max_length < 0
        ):
            raise AgentError(
                f"Parameter {name!r} max_length must be a nonnegative integer."
            )
        if (
            min_length is not None
            and max_length is not None
            and min_length > max_length
        ):
            raise AgentError(
                f"Parameter {name!r} min_length may not exceed max_length."
            )

        pattern = spec.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise AgentError(f"Parameter {name!r} pattern must be a string.")
            try:
                re.compile(pattern)
            except re.error as e:
                raise AgentError(
                    f"Parameter {name!r} has invalid regex pattern: {e}"
                ) from e

        if param_type == "choice":
            choices = spec.get("choices")
            if (
                not isinstance(choices, list)
                or not choices
                or any(
                    not isinstance(choice, str) or not choice
                    for choice in choices
                )
            ):
                raise AgentError(f"Parameter {name!r} has invalid choices.")
            if len(set(choices)) != len(choices):
                raise AgentError(f"Parameter {name!r} choices must be unique.")

        if param_type == "repo_file":
            extensions = spec.get("extensions")
            if (
                extensions is not None
                and (
                    not isinstance(extensions, list)
                    or not extensions
                    or any(
                        not isinstance(extension, str)
                        or not re.fullmatch(r"\.[A-Za-z0-9]+", extension)
                        for extension in extensions
                    )
                )
            ):
                raise AgentError(
                    f"Parameter {name!r} extensions must be a nonempty list "
                    "of file suffixes such as '.md'."
                )
            if extensions is not None and len(
                {extension.lower() for extension in extensions}
            ) != len(extensions):
                raise AgentError(
                    f"Parameter {name!r} extensions must be unique "
                    "case-insensitively."
                )

        if param_type == "agent_id" and not isinstance(
            spec.get("must_exist", False), bool
        ):
            raise AgentError(f"Parameter {name!r} 'must_exist' must be boolean.")

        if "default" in spec:
            validate_single_param(name, spec["default"], spec)

    output = config.get("output")
    if not isinstance(output, dict):
        raise AgentError("Agent JSON must contain an 'output' object.")
    unknown_output_keys = set(output) - VALID_OUTPUT_KEYS
    if unknown_output_keys:
        raise AgentError(
            "Agent output has unknown field(s): "
            + ", ".join(sorted(unknown_output_keys))
        )
    output_format = output.get("format")
    if output_format not in VALID_OUTPUT_FORMATS:
        raise AgentError(
            "output.format must be one of: "
            + ", ".join(sorted(VALID_OUTPUT_FORMATS))
        )
    prompt_fields = get_template_fields(prompt, "prompt")
    output_fields: set[str] = set()
    output_path = output.get("path")

    if output_format in {"markdown", "direct"}:
        if output_path is not None:
            raise AgentError(
                f"{output_format} agents must omit output.path."
            )
        if "overwrite" in output:
            raise AgentError(
                f"{output_format} agents must omit output.overwrite."
            )
    else:
        if not isinstance(output_path, str) or not output_path.strip():
            raise AgentError("JSON agent output.path must be a nonempty string.")
        if not isinstance(output.get("overwrite", False), bool):
            raise AgentError("output.overwrite must be boolean.")
        output_fields = get_template_fields(output_path, "output.path")
    unknown = (prompt_fields | output_fields) - set(specs)
    if unknown:
        raise AgentError(
            "Template references undefined parameter(s): "
            + ", ".join(sorted(unknown))
        )
    unused = set(specs) - (prompt_fields | output_fields)
    if unused:
        raise AgentError(
            "Agent declares unused parameter(s): "
            + ", ".join(sorted(unused))
        )

    unsafe_path_fields = {
        name
        for name in output_fields
        if specs[name].get("type", "string")
        not in {"agent_id", "slug", "tex_label"}
    }
    if unsafe_path_fields:
        raise AgentError(
            "Output paths may interpolate only agent_id, slug, or tex_label "
            "parameters. Invalid field(s): "
            + ", ".join(sorted(unsafe_path_fields))
        )

    if output_format == "json_agent":
        normalized_path = output_path.replace("\\", "/")
        if not normalized_path.startswith("agents/"):
            raise AgentError("JSON agent output must be inside agents/.")
        if not normalized_path.endswith(".json"):
            raise AgentError("JSON agent output must use a .json suffix.")

    if output_format == "file" and output_path.replace("\\", "/") != "README.md":
        raise AgentError("File-output agents may target only root README.md.")

    if output_format == "markdown" and risk != "green":
        raise AgentError("Markdown-report agents must use green risk.")
    if output_format == "file" and risk != "yellow":
        raise AgentError("README file-output agents must use yellow risk.")
    if output_format == "json_agent" and risk != "yellow":
        raise AgentError("JSON-agent output must use yellow risk.")
    if output_format == "direct" and risk != "red":
        raise AgentError("Direct-write agents must use red risk.")
    if risk == "red" and output_format != "direct":
        raise AgentError("Red agents must use direct output.")

    return config


def load_agent(agent_id: str) -> dict:
    validate_agent_id(agent_id)
    path = AGENTS_DIR / f"{agent_id}.json"
    if not path.is_file():
        raise AgentError(f"Unknown agent {agent_id!r}. Expected file:\n{path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise AgentError(f"Invalid JSON in {path}: {e}") from e
    return validate_agent_config(config, expected_id=agent_id)


def strip_tex_comments(text: str) -> str:
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        comment_at: int | None = None
        for index, character in enumerate(line):
            if character != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                comment_at = index
                break
        if comment_at is None:
            output.append(line)
        elif line.endswith(("\n", "\r")):
            output.append(line[:comment_at] + "\n")
        else:
            output.append(line[:comment_at])
    return "".join(output)


def find_tex_label_locations(label: str, workspace_root: Path) -> list[str]:
    paper_root = workspace_root / "paper"
    if not paper_root.is_dir():
        raise AgentError(f"Cannot validate TeX labels: missing {paper_root}")

    locations: list[str] = []
    for path in sorted(paper_root.rglob("*.tex")):
        text = strip_tex_comments(path.read_text(encoding="utf-8"))
        for match in TEX_LABEL_COMMAND_PATTERN.finditer(text):
            if match.group(1) == label:
                line = text.count("\n", 0, match.start()) + 1
                locations.append(f"{path.relative_to(workspace_root)}:{line}")
    return locations


def validate_single_param(
    name: str,
    value: str,
    spec: dict,
    workspace_root: Path = REPO_ROOT,
) -> str:
    if not isinstance(value, str):
        raise AgentError(f"Parameter {name!r} must be a string.")
    param_type = spec.get("type", "string")

    if param_type == "string":
        if not value.strip():
            raise AgentError(f"Parameter {name!r} may not be blank.")
    elif param_type == "slug":
        if SLUG_PATTERN.fullmatch(value) is None:
            raise AgentError(
                f"Parameter {name!r} must be a path-safe slug. Got {value!r}."
            )
    elif param_type == "agent_id":
        validate_agent_id(value)
        agent_path = workspace_root / "agents" / f"{value}.json"
        if spec.get("must_exist") is True and not agent_path.is_file():
            raise AgentError(
                f"Parameter {name!r} must name an existing agent: {value!r}."
            )
        if spec.get("must_exist") is False and agent_path.exists():
            raise AgentError(
                f"Parameter {name!r} must name a new agent, but {value!r} "
                "already exists."
            )
    elif param_type == "choice":
        choices = spec["choices"]
        if value not in choices:
            raise AgentError(
                f"Invalid value for {name!r}: {value!r}. "
                f"Allowed values: {', '.join(choices)}"
            )
    elif param_type == "repo_file":
        relative_path = Path(value)
        if not value.strip() or relative_path.is_absolute() or relative_path.drive:
            raise AgentError(
                f"Parameter {name!r} must be a repository-relative file path."
            )
        workspace = workspace_root.resolve()
        try:
            resolved_path = (workspace / relative_path).resolve(strict=True)
            resolved_path.relative_to(workspace)
        except (OSError, ValueError) as e:
            raise AgentError(
                f"Parameter {name!r} must resolve to an existing file inside "
                f"the workspace: {value!r}."
            ) from e
        if not resolved_path.is_file():
            raise AgentError(
                f"Parameter {name!r} must resolve to a file: {value!r}."
            )
        extensions = spec.get("extensions")
        if extensions is not None and resolved_path.suffix.lower() not in {
            extension.lower() for extension in extensions
        }:
            raise AgentError(
                f"Parameter {name!r} must use one of these suffixes: "
                + ", ".join(extensions)
            )
        try:
            with resolved_path.open("rb") as input_file:
                input_file.read(1)
        except OSError as e:
            raise AgentError(
                f"Parameter {name!r} is not readable: {value!r}."
            ) from e
    elif param_type == "tex_label":
        if TEX_LABEL_PATTERN.fullmatch(value) is None:
            raise AgentError(
                f"Parameter {name!r} is not a valid TeX label key: {value!r}."
            )
        locations = find_tex_label_locations(value, workspace_root)
        if not locations:
            raise AgentError(f"TeX label {value!r} was not found under paper/.")
        if len(locations) > 1:
            raise AgentError(
                f"TeX label {value!r} is ambiguous. Found at:\n"
                + "\n".join(locations)
            )
    else:
        raise AgentError(
            f"Unsupported parameter type {param_type!r} for {name!r}."
        )

    min_length = spec.get("min_length")
    if min_length is not None and len(value) < min_length:
        raise AgentError(f"Parameter {name!r} must have length >= {min_length}.")
    max_length = spec.get("max_length")
    if max_length is not None and len(value) > max_length:
        raise AgentError(f"Parameter {name!r} must have length <= {max_length}.")
    pattern = spec.get("pattern")
    if pattern is not None and re.fullmatch(pattern, value) is None:
        raise AgentError(
            f"Parameter {name!r} does not satisfy pattern {pattern!r}."
        )
    return value


def validate_params(
    supplied: dict[str, str],
    specs: dict,
    workspace_root: Path = REPO_ROOT,
) -> dict[str, str]:
    if not isinstance(supplied, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in supplied.items()
    ):
        raise AgentError("Agent parameters must map strings to strings.")
    unknown = set(supplied) - set(specs)
    if unknown:
        raise AgentError(
            "Unknown parameter(s): " + ", ".join(sorted(unknown))
        )

    validated: dict[str, str] = {}
    for name, spec in specs.items():
        if name in supplied:
            value = supplied[name]
        elif spec.get("required", False):
            raise AgentError(f"Missing required parameter: {name!r}")
        else:
            value = spec["default"]
        validated[name] = validate_single_param(
            name, value, spec, workspace_root
        )
    return validated


class StrictFormatDict(dict):
    def __missing__(self, key):
        raise AgentError(f"Template references unknown parameter {{{key}}}.")


def render_template(template: str, params: dict[str, str]) -> str:
    try:
        return template.format_map(StrictFormatDict(params))
    except ValueError as e:
        raise AgentError(f"Invalid template syntax: {e}") from e


def path_safe_tex_label(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    if not sanitized:
        raise AgentError(f"TeX label {value!r} has no path-safe characters.")
    return sanitized


def next_report_path(
    agent_id: str,
    reserve: bool,
    workspace_root: Path = REPO_ROOT,
) -> Path:
    reports_root = (workspace_root / "reports").resolve()
    report_dir = (reports_root / agent_id).resolve()
    report_dir.relative_to(reports_root)
    if reserve:
        report_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = datetime.now().strftime("%d-%m")

    for index in range(1, 1_000_000):
        candidate = report_dir / f"{date_prefix} ({index}).md"
        if reserve:
            try:
                with candidate.open("x", encoding="utf-8"):
                    pass
                return candidate
            except FileExistsError:
                continue
        if not candidate.exists():
            return candidate

    raise AgentError(f"Could not allocate a report name in {report_dir}")


def get_output_path(
    config: dict,
    params: dict[str, str],
    reserve: bool = False,
    workspace_root: Path = REPO_ROOT,
) -> Path | None:
    if config["output"]["format"] == "markdown":
        return next_report_path(
            config["id"], reserve=reserve, workspace_root=workspace_root
        )
    if config["output"]["format"] == "direct":
        return None

    specs = config.get("params", {})
    path_params = dict(params)
    for name, value in params.items():
        if specs[name].get("type", "string") == "tex_label":
            path_params[name] = path_safe_tex_label(value)

    relative_path = Path(render_template(config["output"]["path"], path_params))
    if relative_path.is_absolute():
        raise AgentError("Output paths must be relative to the repository root.")
    destination = (workspace_root / relative_path).resolve()

    try:
        destination.relative_to(workspace_root)
    except ValueError as e:
        raise AgentError(
            f"Output path escapes repository root:\n{destination}"
        ) from e

    try:
        if config["output"]["format"] == "json_agent":
            destination.relative_to((workspace_root / "agents").resolve())
        elif destination != (workspace_root / "README.md").resolve():
            raise ValueError
    except ValueError as e:
        target = "agents/" if config["output"]["format"] == "json_agent" else "README.md"
        raise AgentError(f"Output path is outside {target}:\n{destination}") from e

    if (
        config["output"]["format"] == "json_agent"
        and destination.suffix.lower() != ".json"
    ):
        raise AgentError("JSON agent output must end in .json.")
    return destination


def validate_model_settings(model: str, reasoning: str) -> None:
    if model not in VALID_MODELS:
        raise AgentError(
            f"Invalid model {model!r}.\n"
            f"Allowed models: {', '.join(sorted(VALID_MODELS))}"
        )
    if reasoning not in VALID_REASONING:
        raise AgentError(
            f"Invalid reasoning level {reasoning!r}.\n"
            f"Allowed levels: {', '.join(sorted(VALID_REASONING))}"
        )


def locate_codex() -> str:
    codex = shutil.which("codex") or shutil.which("codex.cmd")
    if codex is None:
        raise AgentError(
            "Could not find Codex CLI on PATH.\n"
            "Check installation with:\n    codex --version"
        )
    return codex


def run_codex(
    prompt: str,
    model: str,
    reasoning: str,
    run_dir: Path,
    enable_json_log: bool,
    stream_output: bool,
    workspace_root: Path,
    risk: str,
) -> str:
    final_message_file = run_dir / "final.txt"
    stderr_file = run_dir / "stderr.log"
    events_file = run_dir / "events.jsonl" if enable_json_log else None
    working_root = workspace_root / "paper" if risk == "red" else workspace_root
    sandbox = "workspace-write" if risk == "red" else "read-only"
    command = [
        locate_codex(),
        "exec",
        "--ephemeral",
        "--cd",
        str(working_root),
        "--sandbox",
        sandbox,
        "-c",
        'approval_policy="never"',
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning}"',
        "--output-last-message",
        str(final_message_file),
    ]
    if enable_json_log:
        command.append("--json")
    command.append("-")

    if stream_output:
        print(flush=True)
        print("=" * 72, flush=True)
        print(f"AGENT MODEL : {model}", flush=True)
        print(f"REASONING   : {reasoning}", flush=True)
        print(f"REPOSITORY  : {REPO_ROOT}", flush=True)
        print(f"JSON LOG    : {'ON' if enable_json_log else 'OFF'}", flush=True)
        print("=" * 72, flush=True)
        print(flush=True)

    with stderr_file.open("w", encoding="utf-8") as error_log:
        process = subprocess.Popen(
            command,
            cwd=working_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        def relay_stderr() -> None:
            for line in process.stderr:
                error_log.write(line)
                error_log.flush()
                if stream_output:
                    print(line, end="", file=sys.stderr, flush=True)

        stderr_thread = threading.Thread(target=relay_stderr, daemon=True)
        stderr_thread.start()

        # Only the rendered task text is sent; no parameter dictionary or
        # command-line parameter metadata is exposed to the model.
        process.stdin.write(prompt)
        process.stdin.close()

        if enable_json_log:
            assert events_file is not None
            with events_file.open("w", encoding="utf-8") as event_log:
                for line in process.stdout:
                    if stream_output:
                        print(line, end="", flush=True)
                    event_log.write(line)
                    event_log.flush()
        else:
            with (run_dir / "stdout.log").open("w", encoding="utf-8") as output_log:
                for line in process.stdout:
                    if stream_output:
                        print(line, end="", flush=True)
                    output_log.write(line)
                    output_log.flush()

        return_code = process.wait()
        stderr_thread.join()
        error_log.flush()

    if return_code != 0:
        raise AgentError(
            f"Codex exited with status {return_code}.\nSee:\n{stderr_file}"
        )
    if not final_message_file.is_file():
        raise AgentError("Codex succeeded but did not produce a final message.")
    return final_message_file.read_text(encoding="utf-8")


def normalize_result(
    result: str,
    output_format: str,
    destination: Path,
) -> str:
    if not result.strip():
        raise AgentError("Agent returned an empty artifact.")
    if output_format in {"markdown", "file"}:
        return result.rstrip() + "\n"

    try:
        generated_config = json.loads(result)
    except json.JSONDecodeError as e:
        raise AgentError(
            "Agent output was not a bare, valid JSON agent definition: "
            f"{e}"
        ) from e
    validate_agent_config(generated_config, expected_id=destination.stem)
    return json.dumps(
        generated_config,
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def write_result(
    result: str,
    destination: Path,
    overwrite: bool,
) -> None:
    if destination.exists() and not overwrite:
        raise AgentError(f"Refusing to overwrite existing file:\n{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if not overwrite:
        try:
            with destination.open("x", encoding="utf-8") as output_file:
                output_file.write(result)
        except FileExistsError as e:
            raise AgentError(
                f"Refusing to overwrite existing file:\n{destination}"
            ) from e
        return

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(result)
            temporary_path = Path(temporary_file.name)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def execute_agent(
    agent_id: str,
    supplied: dict[str, str],
    model: str,
    reasoning: str,
    enable_json_log: bool,
    stream_output: bool,
    workspace_root: Path = REPO_ROOT,
) -> dict:
    validate_model_settings(model, reasoning)
    config = load_agent(agent_id)
    params = validate_params(
        supplied, config.get("params", {}), workspace_root
    )
    prompt = render_template(config["prompt"], params)
    is_report = config["output"]["format"] == "markdown"
    is_direct = config["output"]["format"] == "direct"
    destination = get_output_path(
        config, params, reserve=is_report, workspace_root=workspace_root
    )
    overwrite = is_report or config["output"].get("overwrite", False)

    if destination is not None and destination.exists() and not overwrite:
        raise AgentError(f"Refusing to overwrite existing file:\n{destination}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = RUNS_DIR / f"{timestamp}-{agent_id}"
    run_dir.mkdir()
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    metadata = {
        "agent": agent_id,
        "model": model,
        "reasoning": reasoning,
        "output": str(destination) if destination is not None else None,
        "risk": config["risk"],
        "workspace": str(workspace_root),
        "json_logging": enable_json_log,
    }
    (run_dir / "run.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    try:
        if stream_output:
            emit_status(
                f"START agent={agent_id} risk={config['risk']} "
                f"workspace={workspace_root}"
            )
            if destination is not None:
                emit_status(f"TARGET {destination}")
            emit_status("MODEL starting Codex execution")
        result = run_codex(
            prompt=prompt,
            model=model,
            reasoning=reasoning,
            run_dir=run_dir,
            enable_json_log=enable_json_log,
            stream_output=stream_output,
            workspace_root=workspace_root,
            risk=config["risk"],
        )
        if not is_direct:
            assert destination is not None
            if stream_output:
                emit_status("VALIDATE checking returned artifact")
            normalized = normalize_result(
                result,
                config["output"]["format"],
                destination,
            )
            if stream_output:
                emit_status(f"WRITE saving validated artifact to {destination}")
            write_result(normalized, destination, overwrite=overwrite)
            if stream_output:
                emit_status("WRITE complete")
        elif stream_output:
            emit_status("MODEL completed direct-write agent")
    except Exception as e:
        if (
            is_report
            and destination is not None
            and destination.exists()
            and destination.stat().st_size == 0
        ):
            destination.unlink()
        if stream_output:
            emit_status(f"FAILED {type(e).__name__}: {e}")
        raise

    if stream_output:
        emit_status("COMPLETE agent finished successfully")
        print(flush=True)
        print("=" * 72, flush=True)
        print("SUCCESS", flush=True)
        if destination is not None:
            print(f"Output : {destination}", flush=True)
        else:
            print(f"Writes : {workspace_root / config['write_scope']}", flush=True)
        print(f"Run    : {run_dir}", flush=True)
        print("=" * 72, flush=True)

    return {
        "agent": agent_id,
        "status": "success",
        "output": str(destination) if destination is not None else None,
        "run": str(run_dir),
    }


def load_batch_jobs(
    path: Path,
    default_model: str,
    default_reasoning: str,
    default_log: bool,
) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise AgentError(f"Cannot read batch file {path}: {e}") from e

    if isinstance(data, dict):
        unknown_batch_keys = set(data) - {"jobs"}
        if unknown_batch_keys:
            raise AgentError(
                "Batch JSON has unknown top-level field(s): "
                + ", ".join(sorted(unknown_batch_keys))
            )
        jobs = data.get("jobs")
    else:
        jobs = data
    if not isinstance(jobs, list) or not jobs:
        raise AgentError("Batch JSON must be a nonempty array or a jobs array.")

    normalized: list[dict] = []
    allowed_keys = {"agent", "params", "model", "reasoning", "log"}
    for index, job in enumerate(jobs, start=1):
        if not isinstance(job, dict):
            raise AgentError(f"Batch job {index} must be an object.")
        unknown = set(job) - allowed_keys
        if unknown:
            raise AgentError(
                f"Batch job {index} has unknown field(s): "
                + ", ".join(sorted(unknown))
            )
        agent_id = job.get("agent")
        params = job.get("params", {})
        if not isinstance(agent_id, str):
            raise AgentError(f"Batch job {index} requires string field 'agent'.")
        if (
            not isinstance(params, dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in params.items()
            )
        ):
            raise AgentError(
                f"Batch job {index} params must map strings to strings."
            )

        model = job.get("model", default_model)
        reasoning = job.get("reasoning", default_reasoning)
        enable_log = job.get("log", default_log)
        if not isinstance(model, str) or not isinstance(reasoning, str):
            raise AgentError(f"Batch job {index} model/reasoning must be strings.")
        if not isinstance(enable_log, bool):
            raise AgentError(f"Batch job {index} log must be boolean.")

        validate_model_settings(model, reasoning)
        config = load_agent(agent_id)
        if config["output"]["format"] != "markdown":
            raise AgentError(
                f"Batch job {index} uses {agent_id!r}, which is not a report agent."
            )
        validated = validate_params(params, config.get("params", {}))
        render_template(config["prompt"], validated)
        get_output_path(config, validated, reserve=False)
        normalized.append(
            {
                "agent": agent_id,
                "params": params,
                "model": model,
                "reasoning": reasoning,
                "log": enable_log,
            }
        )
    return normalized


def run_batch(jobs: list[dict], result_dir: Path) -> int:
    result_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {
            executor.submit(
                execute_agent,
                job["agent"],
                job["params"],
                job["model"],
                job["reasoning"],
                job["log"],
                False,
            ): index
            for index, job in enumerate(jobs, start=1)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                outcome = future.result()
                outcome["job"] = index
            except Exception as e:
                outcome = {
                    "job": index,
                    "agent": jobs[index - 1]["agent"],
                    "status": "failed",
                    "error": str(e),
                }
            results.append(outcome)

    results.sort(key=lambda item: item["job"])
    (result_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return 1 if any(item["status"] == "failed" for item in results) else 0


def launch_detached_batch(
    batch_path: Path,
    model: str,
    reasoning: str,
    enable_log: bool,
) -> Path:
    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    result_dir = BATCHES_DIR / timestamp
    result_dir.mkdir()
    manager_log = result_dir / "manager.log"
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        "--batch-worker",
        str(batch_path.resolve()),
        "--batch-result-dir",
        str(result_dir),
        "--model",
        model,
        "--reasoning",
        reasoning,
    ]
    if enable_log:
        command.append("--log")

    popen_options: dict = {
        "cwd": REPO_ROOT,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        popen_options["start_new_session"] = True

    with manager_log.open("w", encoding="utf-8") as log_file:
        subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            **popen_options,
        )
    return result_dir


def launch_detached_agent(
    agent_id: str,
    param_items: list[str],
    model: str,
    reasoning: str,
    enable_log: bool,
    workspace_root: Path = REPO_ROOT,
) -> Path:
    LAUNCHES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    launch_dir = Path(
        tempfile.mkdtemp(prefix=f"{timestamp}-{agent_id}-", dir=LAUNCHES_DIR)
    )
    manager_log = launch_dir / "manager.log"
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        "--agent",
        agent_id,
        "--agent-worker",
        "--workspace-root",
        str(workspace_root),
        "--model",
        model,
        "--reasoning",
        reasoning,
    ]
    for item in param_items:
        command.extend(("--param", item))
    if enable_log:
        command.append("--log")

    popen_options: dict = {
        "cwd": REPO_ROOT,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        popen_options["start_new_session"] = True

    with manager_log.open("w", encoding="utf-8") as log_file:
        subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            **popen_options,
        )
    return launch_dir


def ask_yes(prompt: str, default: bool) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    if not answer:
        return default
    return answer in {"y", "yes"}


def print_warning(level: str, message: str) -> None:
    color = "\033[93m" if level == "yellow" else "\033[91m"
    print(f"{color}{level.upper()} WARNING\033[0m: {message}")


def git_output(arguments: list[str], cwd: Path = REPO_ROOT) -> str:
    process = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        raise AgentError(
            f"Git command failed: git {' '.join(arguments)}\n"
            + process.stderr.strip()
        )
    return process.stdout


def create_agent_worktree(agent_id: str) -> tuple[Path, str]:
    if git_output(["status", "--porcelain"]).strip():
        raise AgentError(
            "Worktree mode requires a clean main checkout so the new worktree "
            "contains the current agent definitions. Commit or stash changes first."
        )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    branch = f"codex/{agent_id}-{timestamp}"
    parent = REPO_ROOT.parent / f".{REPO_ROOT.name}-worktrees"
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / f"{agent_id}-{timestamp}"
    git_output(["worktree", "add", "-b", branch, str(path), "HEAD"])
    return path.resolve(), branch


def validate_workspace_root(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == REPO_ROOT:
        return resolved
    registered = {
        Path(line.removeprefix("worktree ")).resolve()
        for line in git_output(["worktree", "list", "--porcelain"]).splitlines()
        if line.startswith("worktree ")
    }
    if resolved not in registered:
        raise AgentError(f"Workspace is not a registered Git worktree: {resolved}")
    return resolved


def print_worktree_details(path: Path, branch: str) -> None:
    print(f"Worktree: {path}")
    print(f"Branch:   {branch}")
    print("Changes will remain uncommitted for review in that worktree.")
    print(f"When ready: git -C \"{path}\" add -A")
    print(f"Then commit there and merge {branch} from the main checkout.")


def main() -> int:
    help_requested = any(argument in {"-h", "--help"} for argument in sys.argv[1:])
    parser = argparse.ArgumentParser(
        description="Run risk-scoped Codex agents and save validated artifacts.",
        epilog=build_help_epilog(sys.argv[1:]) if help_requested else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--agent",
        "--a",
        help="Agent ID corresponding to agents/<id>.json",
    )
    mode.add_argument("--batch", type=Path, help="JSON file of report-agent jobs")
    mode.add_argument("--batch-worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--agent-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workspace-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--param",
        "--p",
        action="append",
        default=[],
        metavar="VALUE|ID=VALUE",
        help=(
            "Repeatable parameter. Bare values fill the prompt's parameters "
            "in order; ID=VALUE explicitly names one."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Codex model. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--reasoning",
        default=DEFAULT_REASONING,
        help=f"Reasoning effort. Default: {DEFAULT_REASONING}",
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help="Save the full Codex JSONL event stream.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the agent, parameters, and output path without running.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Wait for a read-only agent or batch instead of detaching it.",
    )
    parser.add_argument("--batch-result-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        if args.agent:
            workspace_root = validate_workspace_root(
                args.workspace_root or REPO_ROOT
            )
            validate_model_settings(args.model, args.reasoning)
            config = load_agent(args.agent)
            supplied = parse_param_assignments(
                args.param, config.get("params", {})
            )
            params = validate_params(
                supplied, config.get("params", {}), workspace_root
            )
            prompt = render_template(config["prompt"], params)
            destination = get_output_path(
                config,
                params,
                reserve=False,
                workspace_root=workspace_root,
            )
            if args.validate_only:
                print(f"VALID: {args.agent} -> {destination}")
                return 0

            if args.agent_worker:
                execute_agent(
                    args.agent,
                    supplied,
                    args.model,
                    args.reasoning,
                    args.log,
                    True,
                    workspace_root,
                )
                return 0

            risk = config["risk"]
            is_report = config["output"]["format"] == "markdown"
            use_worktree = False
            worktree_branch: str | None = None

            if risk == "yellow":
                print_warning(
                    "yellow",
                    f"the handler will write repository file {destination}; "
                    "the agent itself remains read-only.",
                )
                use_worktree = ask_yes(
                    "Create an isolated Git worktree? [y/N]: ", default=False
                )
            elif risk == "red":
                write_root = workspace_root / config["write_scope"]
                print_warning(
                    "red",
                    "this agent has genuine write permission. Its writable "
                    f"working root is {write_root}.",
                )
                if not ask_yes("Type Y to continue: ", default=False):
                    print("Cancelled.")
                    return 0
                use_worktree = ask_yes(
                    "Create the recommended isolated Git worktree? [Y/n]: ",
                    default=True,
                )

            if use_worktree:
                workspace_root, worktree_branch = create_agent_worktree(args.agent)

            should_detach = (
                use_worktree
                or risk == "yellow" and not args.foreground
                or is_report and not args.foreground
            )
            if should_detach:
                launch_dir = launch_detached_agent(
                    args.agent,
                    args.param,
                    args.model,
                    args.reasoning,
                    args.log,
                    workspace_root,
                )
                print(f"Started {args.agent} in the background.")
                if is_report:
                    print(f"Reports: {workspace_root / 'reports' / args.agent}")
                elif destination is not None:
                    print(f"Output:  {get_output_path(config, params, workspace_root=workspace_root)}")
                print(f"Log:     {launch_dir / 'manager.log'}")
                if worktree_branch is not None:
                    (launch_dir / "worktree.json").write_text(
                        json.dumps(
                            {
                                "path": str(workspace_root),
                                "branch": worktree_branch,
                                "status": "worker changes remain uncommitted",
                            },
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    print_worktree_details(workspace_root, worktree_branch)
                return 0
            execute_agent(
                args.agent,
                supplied,
                args.model,
                args.reasoning,
                args.log,
                True,
                workspace_root,
            )
            return 0

        batch_path = args.batch_worker or args.batch
        assert batch_path is not None
        if args.agent_worker:
            raise AgentError("--agent-worker requires --agent.")
        if args.param:
            raise AgentError("--param is only valid with --agent.")
        jobs = load_batch_jobs(
            batch_path,
            args.model,
            args.reasoning,
            args.log,
        )
        if args.validate_only:
            print(f"VALID: {len(jobs)} parallel report job(s)")
            return 0

        if args.batch_worker:
            if args.batch_result_dir is None:
                raise AgentError("Detached batch is missing its result directory.")
            return run_batch(jobs, args.batch_result_dir)

        if args.foreground:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            result_dir = BATCHES_DIR / timestamp
            return run_batch(jobs, result_dir)

        result_dir = launch_detached_batch(
            batch_path,
            args.model,
            args.reasoning,
            args.log,
        )
        print(f"Started {len(jobs)} report agent(s) in the background.")
        print(f"Status: {result_dir / 'results.json'}")
        print(f"Log:    {result_dir / 'manager.log'}")
        return 0
    except AgentError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

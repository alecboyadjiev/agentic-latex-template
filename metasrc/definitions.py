from __future__ import annotations

import json
from pathlib import Path
import re
import string

from metasrc.errors import AgentError
from metasrc.paths import REPO_ROOT
from metasrc.validation.inputs import validate_agent_id


VALID_PARAM_TYPES = {
    "agent_id", "choice", "math_target", "paper_name", "repo_file",
    "slug", "string", "tex_label",
}
VALID_OUTPUT_FORMATS = {"direct", "file", "json_agent", "markdown"}
VALID_RISK_LEVELS = {"green", "yellow", "red"}
VALID_AGENT_KEYS = {
    "id", "risk", "description", "params", "prompt", "output", "write_scope",
}
VALID_OUTPUT_KEYS = {"format", "path", "overwrite"}
COMMON_PARAM_SPEC_KEYS = {
    "type", "required", "default", "min_length", "max_length", "pattern",
}
TYPE_SPECIFIC_PARAM_KEYS = {
    "agent_id": {"must_exist"}, "choice": {"choices"},
    "repo_file": {"extensions"}, "math_target": set(), "paper_name": set(),
    "slug": set(), "string": set(), "tex_label": set(),
}
VALID_INPUTS_INSTRUCTION = "All supplied inputs are valid; use them directly."
COMMON_PERMISSION_INSTRUCTION = (
    "You may write inside agent-data/ and are read-only elsewhere."
)
RED_PERMISSION_INSTRUCTION = (
    "You may write inside agent-data/ and the resolved paper directory, and are "
    "read-only elsewhere."
)
LEGACY_READ_ONLY_INSTRUCTION = "You are read-only; do not attempt writes."
COMPLETION_CONTRACT_INSTRUCTION = (
    'End every turn with exactly one JSON object containing only "task" and "message". '
    'Set "task" to "completed" only when all stated completion criteria hold; otherwise '
    'set it to "not completed" and explain the blocker or remaining work in "message". '
    'A completed "message" must be self-contained: include the complete current artifact '
    'for handler-owned outputs, or the concise changed-files, checks, and build summary for '
    'direct-edit outputs.'
)
OBSOLETE_RETURN_PATTERN = re.compile(r"\bReturn ONLY\b", re.IGNORECASE)
PARAM_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def get_template_fields(template: str, location: str) -> set[str]:
    fields: set[str] = set()
    try:
        for _, field_name, format_spec, conversion in string.Formatter().parse(template):
            if field_name is None:
                continue
            if PARAM_NAME_PATTERN.fullmatch(field_name) is None:
                raise AgentError(
                    f"{location} uses invalid placeholder {{{field_name}}}; use a bare name."
                )
            if format_spec or conversion:
                raise AgentError(f"{location} placeholder {{{field_name}}} may not use formatting.")
            fields.add(field_name)
    except ValueError as exc:
        raise AgentError(f"Invalid template syntax in {location}: {exc}") from exc
    return fields


def _validate_param_spec(name: str, spec: object) -> None:
    if PARAM_NAME_PATTERN.fullmatch(name) is None or not isinstance(spec, dict):
        raise AgentError(f"Invalid parameter specification {name!r}.")
    kind = spec.get("type", "string")
    if kind not in VALID_PARAM_TYPES:
        raise AgentError(f"Unsupported parameter type {kind!r} for {name!r}.")
    unknown = set(spec) - (COMMON_PARAM_SPEC_KEYS | TYPE_SPECIFIC_PARAM_KEYS[kind])
    if unknown:
        raise AgentError(f"Parameter {name!r} has unknown field(s): " + ", ".join(sorted(unknown)))
    required = spec.get("required", False)
    if not isinstance(required, bool):
        raise AgentError(f"Parameter {name!r} 'required' must be boolean.")
    if required and "default" in spec:
        raise AgentError(f"Parameter {name!r} cannot be required and have a default.")
    if not required and "default" not in spec and kind != "paper_name":
        raise AgentError(f"Parameter {name!r} must be required or declare a default.")
    if "default" in spec and not isinstance(spec["default"], str):
        raise AgentError(f"Parameter {name!r} default must be a string.")
    if kind in {"paper_name", "math_target"} and "default" in spec:
        raise AgentError(f"Parameter {name!r} of type {kind} may not declare a default.")
    minimum, maximum = spec.get("min_length"), spec.get("max_length")
    for label, value in (("min_length", minimum), ("max_length", maximum)):
        if value is not None and (type(value) is not int or value < 0):
            raise AgentError(f"Parameter {name!r} {label} must be a nonnegative integer.")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise AgentError(f"Parameter {name!r} min_length may not exceed max_length.")
    if "pattern" in spec:
        if not isinstance(spec["pattern"], str):
            raise AgentError(f"Parameter {name!r} pattern must be a string.")
        try:
            re.compile(spec["pattern"])
        except re.error as exc:
            raise AgentError(f"Parameter {name!r} has invalid regex pattern: {exc}") from exc
    if kind == "choice":
        choices = spec.get("choices")
        if not isinstance(choices, list) or not choices or any(
            not isinstance(item, str) or not item for item in choices
        ) or len(set(choices)) != len(choices):
            raise AgentError(f"Parameter {name!r} has invalid choices.")
    if kind == "repo_file" and "extensions" in spec:
        extensions = spec["extensions"]
        if not isinstance(extensions, list) or not extensions or any(
            not isinstance(item, str) or re.fullmatch(r"\.[A-Za-z0-9]+", item) is None
            for item in extensions
        ) or len({item.casefold() for item in extensions}) != len(extensions):
            raise AgentError(f"Parameter {name!r} extensions are invalid.")
    if kind == "agent_id" and not isinstance(spec.get("must_exist", False), bool):
        raise AgentError(f"Parameter {name!r} 'must_exist' must be boolean.")


def validate_agent_config(config: object, expected_id: str | None = None) -> dict:
    if not isinstance(config, dict):
        raise AgentError("Agent definition must be a JSON object.")
    unknown = set(config) - VALID_AGENT_KEYS
    if unknown:
        raise AgentError("Agent JSON has unknown field(s): " + ", ".join(sorted(unknown)))
    agent_id = config.get("id")
    if not isinstance(agent_id, str):
        raise AgentError("Agent JSON must contain a string field 'id'.")
    validate_agent_id(agent_id)
    if expected_id is not None and agent_id != expected_id:
        raise AgentError(f"Agent ID mismatch: expected {expected_id!r}, got {agent_id!r}.")
    if not isinstance(config.get("description"), str) or not config["description"].strip():
        raise AgentError("Agent JSON requires a nonempty 'description'.")
    risk = config.get("risk")
    if risk not in VALID_RISK_LEVELS:
        raise AgentError("Agent JSON has an invalid risk.")
    prompt = config.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AgentError("Agent JSON requires a nonempty string 'prompt'.")
    permission = RED_PERMISSION_INSTRUCTION if risk == "red" else COMMON_PERMISSION_INSTRUCTION
    expected_prefix = VALID_INPUTS_INSTRUCTION + "\n" + permission + "\n\n"
    if not prompt.startswith(expected_prefix):
        raise AgentError("Agent prompt must start with the canonical input and permission lines.")
    if prompt.count(VALID_INPUTS_INSTRUCTION) != 1 or prompt.count(permission) != 1:
        raise AgentError("Agent prompt canonical preamble must occur exactly once.")
    if LEGACY_READ_ONLY_INSTRUCTION in prompt:
        raise AgentError("Agent prompt contains the obsolete blanket read-only line.")
    if prompt.count(COMPLETION_CONTRACT_INSTRUCTION) != 1:
        raise AgentError("Agent prompt must contain the canonical completion contract exactly once.")
    if OBSOLETE_RETURN_PATTERN.search(prompt):
        raise AgentError("Agent prompt contains the obsolete bare-artifact return form.")

    write_scope = config.get("write_scope")
    if risk == "red":
        if write_scope != "papers/{paper}":
            raise AgentError("Red agents must declare write_scope as 'papers/{paper}'.")
    elif write_scope is not None:
        raise AgentError("Only red agents may declare write_scope.")

    specs = config.get("params", {})
    if not isinstance(specs, dict):
        raise AgentError("Agent JSON field 'params' must be an object.")
    for name, spec in specs.items():
        _validate_param_spec(name, spec)
        if "default" in spec:
            from metasrc.validation.inputs import validate_single_param
            validate_single_param(name, spec["default"], spec, REPO_ROOT)
    if risk == "red" and (
        "paper" not in specs or specs["paper"].get("type") != "paper_name"
        or not specs["paper"].get("required", False)
    ):
        raise AgentError("Red agents require a required paper_name parameter named 'paper'.")

    output = config.get("output")
    if not isinstance(output, dict):
        raise AgentError("Agent JSON must contain an 'output' object.")
    unknown_output = set(output) - VALID_OUTPUT_KEYS
    if unknown_output:
        raise AgentError("Agent output has unknown field(s): " + ", ".join(sorted(unknown_output)))
    output_format = output.get("format")
    if output_format not in VALID_OUTPUT_FORMATS:
        raise AgentError("Agent output format is invalid.")
    output_path = output.get("path")
    if output_format in {"markdown", "direct"}:
        if output_path is not None or "overwrite" in output:
            raise AgentError(f"{output_format} agents must omit path and overwrite.")
        output_fields: set[str] = set()
    else:
        if not isinstance(output_path, str) or not output_path.strip():
            raise AgentError("File-like output requires a nonempty path.")
        if not isinstance(output.get("overwrite", False), bool):
            raise AgentError("output.overwrite must be boolean.")
        output_fields = get_template_fields(output_path, "output.path")
    prompt_fields = get_template_fields(prompt, "prompt")
    scope_fields = get_template_fields(write_scope, "write_scope") if write_scope else set()
    fields = prompt_fields | output_fields | scope_fields
    unknown_fields = fields - set(specs)
    if unknown_fields:
        raise AgentError("Template references undefined parameter(s): " + ", ".join(sorted(unknown_fields)))
    unused = {
        name for name in set(specs) - fields
        if specs[name].get("type") != "paper_name"
    }
    if unused:
        raise AgentError("Agent declares unused parameter(s): " + ", ".join(sorted(unused)))
    unsafe = {
        name for name in output_fields
        if specs[name].get("type", "string") not in {"agent_id", "slug", "tex_label", "math_target"}
    }
    if unsafe:
        raise AgentError("Unsafe output-path parameter(s): " + ", ".join(sorted(unsafe)))
    normalized_path = output_path.replace("\\", "/") if isinstance(output_path, str) else ""
    if output_format == "json_agent" and (
        not normalized_path.startswith("agents/") or not normalized_path.endswith(".json")
    ):
        raise AgentError("JSON agent output must be an agents/*.json path.")
    if output_format == "file" and normalized_path != "README.md":
        raise AgentError("File-output agents may target only root README.md.")
    expected_risk = {"markdown": "green", "file": "yellow", "json_agent": "yellow", "direct": "red"}
    if risk != expected_risk[output_format]:
        raise AgentError(f"Output format {output_format!r} requires {expected_risk[output_format]} risk.")
    if "papers/template/" in prompt and any(
        spec.get("type") == "paper_name" for spec in specs.values()
    ):
        raise AgentError("Paper-dependent prompts may not hard-code papers/template/.")
    return config


def load_agent(agent_id: str, workspace_root: Path = REPO_ROOT) -> dict:
    validate_agent_id(agent_id)
    path = workspace_root / "agents" / f"{agent_id}.json"
    if not path.is_file():
        raise AgentError(f"Unknown agent {agent_id!r}. Expected file:\n{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentError(f"Invalid JSON in {path}: {exc}") from exc
    return validate_agent_config(value, expected_id=agent_id)


class StrictFormatDict(dict):
    def __missing__(self, key):
        raise AgentError(f"Template references unknown parameter {{{key}}}.")


def render_template(template: str, params: dict[str, str]) -> str:
    try:
        return template.format_map(StrictFormatDict(params))
    except ValueError as exc:
        raise AgentError(f"Invalid template syntax: {exc}") from exc

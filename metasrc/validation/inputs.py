from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from metasrc.errors import AgentError
from metasrc.validation.labels import (
    PAPER_NAME_PATTERN,
    TEX_LABEL_PATTERN,
    LabelLocation,
    ResolvedPaper,
    resolve_label_target,
    resolve_paper,
)


AGENT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
SLUG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


@dataclass(frozen=True)
class ResolvedTarget:
    kind: str
    value: str
    path: Path
    relative_path: str
    line: int | None
    paper: ResolvedPaper | None
    scope: str | None = None

    def as_metadata(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "path": self.relative_path,
            "line": self.line,
            "paper": self.paper.as_metadata() if self.paper else None,
            **({"scope": self.scope} if self.scope else {}),
        }


@dataclass(frozen=True)
class ValidatedInputs:
    values: dict[str, str]
    paper: ResolvedPaper | None
    target: ResolvedTarget | None


def validate_agent_id(agent_id: str) -> None:
    if AGENT_ID_PATTERN.fullmatch(agent_id) is None:
        raise AgentError(
            "Invalid agent ID. Use letters, digits, underscores, hyphens, or "
            "periods, starting with a letter or digit."
        )


def parse_param_assignments(items: list[str], specs: dict[str, dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        key: str | None = None
        value = item
        if "=" in item:
            candidate, candidate_value = item.split("=", 1)
            candidate = candidate.strip()
            if candidate in specs:
                key, value = candidate, candidate_value
        if key is None:
            key = next((name for name in specs if name not in result), None)
            if key is None:
                expected = ", ".join(specs) or "none"
                raise AgentError(
                    "Too many --param values. This agent's parameters, in order, "
                    f"are: {expected}."
                )
        if key in result:
            raise AgentError(f"Parameter {key!r} was supplied more than once.")
        result[key] = value
    return result


def _validate_repo_file(name: str, value: str, spec: dict, workspace: Path) -> None:
    relative = Path(value)
    if not value.strip() or relative.is_absolute() or relative.drive:
        raise AgentError(f"Parameter {name!r} must be a repository-relative file path.")
    try:
        resolved = (workspace / relative).resolve(strict=True)
        resolved.relative_to(workspace)
    except (OSError, ValueError) as exc:
        raise AgentError(
            f"Parameter {name!r} must resolve to an existing file inside the "
            f"workspace: {value!r}."
        ) from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise AgentError(f"Parameter {name!r} must resolve to a regular file: {value!r}.")
    extensions = spec.get("extensions")
    if extensions and resolved.suffix.lower() not in {item.lower() for item in extensions}:
        raise AgentError(
            f"Parameter {name!r} must use one of these suffixes: " + ", ".join(extensions)
        )
    try:
        with resolved.open("rb") as source:
            source.read(1)
    except OSError as exc:
        raise AgentError(f"Parameter {name!r} is not readable: {value!r}.") from exc


def validate_single_param(
    name: str, value: str, spec: dict, workspace_root: Path
) -> str:
    if not isinstance(value, str):
        raise AgentError(f"Parameter {name!r} must be a string.")
    workspace = workspace_root.resolve(strict=True)
    kind = spec.get("type", "string")
    if kind == "string":
        if not value.strip():
            raise AgentError(f"Parameter {name!r} may not be blank.")
    elif kind == "slug":
        if SLUG_PATTERN.fullmatch(value) is None:
            raise AgentError(f"Parameter {name!r} must be a path-safe slug. Got {value!r}.")
    elif kind == "agent_id":
        validate_agent_id(value)
        path = workspace / "agents" / f"{value}.json"
        if spec.get("must_exist") is True and not path.is_file():
            raise AgentError(f"Parameter {name!r} must name an existing agent: {value!r}.")
        if spec.get("must_exist") is False and path.exists():
            raise AgentError(f"Parameter {name!r} must name a new agent: {value!r} exists.")
    elif kind == "choice":
        if value not in spec["choices"]:
            raise AgentError(
                f"Invalid value for {name!r}: {value!r}. Allowed values: "
                + ", ".join(spec["choices"])
            )
    elif kind == "repo_file":
        _validate_repo_file(name, value, spec, workspace)
    elif kind == "paper_name":
        if PAPER_NAME_PATTERN.fullmatch(value) is None:
            raise AgentError(f"Parameter {name!r} is not a valid paper name: {value!r}.")
    elif kind in {"tex_label", "math_target"}:
        if kind == "tex_label" and TEX_LABEL_PATTERN.fullmatch(value) is None:
            raise AgentError(f"Parameter {name!r} is not a valid TeX label key: {value!r}.")
        if kind == "math_target" and not value.lower().endswith((".md", ".markdown")):
            if TEX_LABEL_PATTERN.fullmatch(value) is None:
                raise AgentError(f"Parameter {name!r} is not a valid TeX label key: {value!r}.")
    else:
        raise AgentError(f"Unsupported parameter type {kind!r} for {name!r}.")
    minimum, maximum = spec.get("min_length"), spec.get("max_length")
    if minimum is not None and len(value) < minimum:
        raise AgentError(f"Parameter {name!r} must have length >= {minimum}.")
    if maximum is not None and len(value) > maximum:
        raise AgentError(f"Parameter {name!r} must have length <= {maximum}.")
    if spec.get("pattern") is not None and re.fullmatch(spec["pattern"], value) is None:
        raise AgentError(f"Parameter {name!r} does not satisfy pattern {spec['pattern']!r}.")
    return value


def _markdown_target(value: str, workspace: Path) -> ResolvedTarget:
    supplied = Path(value)
    if supplied.is_absolute() or supplied.drive or not value.strip():
        raise AgentError("Markdown targets must be repository-relative paths beneath markdown/.")
    markdown_root = workspace / "markdown"
    if markdown_root.is_symlink() or not markdown_root.is_dir():
        raise AgentError(f"Markdown root is missing or invalid: {markdown_root}")
    try:
        resolved_root = markdown_root.resolve(strict=True)
        resolved = (workspace / supplied).resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise AgentError(f"Markdown target must exist beneath markdown/: {value!r}") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise AgentError(f"Markdown target is not a readable regular file: {value!r}")
    try:
        with resolved.open("rb") as source:
            source.read(1)
    except OSError as exc:
        raise AgentError(f"Markdown target is not readable: {value!r}") from exc
    return ResolvedTarget(
        kind="markdown_file",
        value=value,
        path=resolved,
        relative_path=resolved.relative_to(workspace).as_posix(),
        line=None,
        paper=None,
        scope="whole_file",
    )


def validate_inputs(
    supplied: dict[str, str], specs: dict[str, dict], workspace_root: Path
) -> ValidatedInputs:
    workspace = workspace_root.resolve(strict=True)
    if not isinstance(supplied, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in supplied.items()
    ):
        raise AgentError("Agent parameters must map strings to strings.")
    unknown = set(supplied) - set(specs)
    if unknown:
        raise AgentError("Unknown parameter(s): " + ", ".join(sorted(unknown)))
    values: dict[str, str] = {}
    for name, spec in specs.items():
        if name in supplied:
            value = supplied[name]
        elif spec.get("required", False):
            raise AgentError(f"Missing required parameter: {name!r}")
        elif "default" in spec:
            value = spec["default"]
        else:
            continue
        values[name] = validate_single_param(name, value, spec, workspace)

    paper: ResolvedPaper | None = None
    if "paper" in values:
        paper = resolve_paper(values["paper"], workspace)

    target: ResolvedTarget | None = None
    math_names = [name for name, spec in specs.items() if spec.get("type") == "math_target"]
    if math_names:
        name = math_names[0]
        value = values[name]
        if value.lower().endswith((".md", ".markdown")):
            if paper is not None:
                raise AgentError("Parameter 'paper' must be omitted for a Markdown math target.")
            target = _markdown_target(value, workspace)
        else:
            if paper is None:
                raise AgentError("Parameter 'paper' is required for a TeX-label math target.")
            location = resolve_label_target(value, paper, workspace)
            target = ResolvedTarget(
                kind="tex_label",
                value=value,
                path=location.path,
                relative_path=location.relative_path,
                line=location.line,
                paper=paper,
            )
    return ValidatedInputs(values=values, paper=paper, target=target)


def validate_params(
    supplied: dict[str, str], specs: dict[str, dict], workspace_root: Path
) -> dict[str, str]:
    return validate_inputs(supplied, specs, workspace_root).values


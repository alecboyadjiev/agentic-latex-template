from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from metasrc.definitions import load_agent
from metasrc.errors import AgentError
from metasrc.handlers.worktrees import create_agent_worktree, print_worktree_details, validate_workspace_root
from metasrc.maintenance.runner import run_maintenance
from metasrc.orchestration import execute_agent, prepare_invocation
from metasrc.paths import REPO_ROOT
from metasrc.providers.codex_cli import DEFAULT_MODEL, DEFAULT_REASONING, validate_model_settings
from metasrc.validation.inputs import parse_param_assignments


def format_param_details(name: str, spec: dict) -> str:
    details = [spec.get("type", "string")]
    if spec.get("required", False):
        details.append("required")
    elif "default" in spec:
        details.append(f"default={spec['default']!r}")
    else:
        details.append("optional (conditional)")
    if "min_length" in spec:
        details.append(f"min length {spec['min_length']}")
    if "max_length" in spec:
        details.append(f"max length {spec['max_length']}")
    if "choices" in spec:
        details.append("choices: " + ", ".join(spec["choices"]))
    if "extensions" in spec:
        details.append("extensions: " + ", ".join(spec["extensions"]))
    return f"  {name}: " + "; ".join(details)


def format_agent_help(config: dict) -> str:
    agent_id, specs = config["id"], config.get("params", {})
    lines = [f"{agent_id} - {config['description']}", "Parameters (in order):"]
    lines.extend(format_param_details(name, spec) for name, spec in specs.items())
    if not specs:
        lines.append("  (none)")
    positional = f"  python agent.py --agent {agent_id}"
    named = positional
    for name in specs:
        positional += f' --p "<{name}>"'
        named += f' --p "{name}=<{name}>"'
    lines.extend(("Usage:", positional))
    if specs:
        lines.append(named)
    return "\n".join(lines)


def _requested_help_agent(argv: list[str]) -> str | None:
    for index, argument in enumerate(argv):
        if argument in {"--agent", "--a"} and index + 1 < len(argv):
            return argv[index + 1]
        if argument.startswith(("--agent=", "--a=")):
            return argument.split("=", 1)[1]
    return None


def build_help_epilog(argv: list[str]) -> str:
    selected = _requested_help_agent(argv)
    configs = [load_agent(selected)] if selected else [
        load_agent(path.stem) for path in sorted((REPO_ROOT / "agents").glob("*.json"))
    ]
    heading = "Selected prompt:\n" if selected else "Available prompts:\n"
    return heading + "\n\n".join(format_agent_help(config) for config in configs)


def load_batch_jobs(path: Path, default_model: str, default_reasoning: str, default_log: bool,
                    workspace_root: Path = REPO_ROOT) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentError(f"Cannot read batch file {path}: {exc}") from exc
    if isinstance(data, dict):
        unknown = set(data) - {"jobs"}
        if unknown:
            raise AgentError("Batch JSON has unknown field(s): " + ", ".join(sorted(unknown)))
        jobs = data.get("jobs")
    else:
        jobs = data
    if not isinstance(jobs, list) or not jobs:
        raise AgentError("Batch JSON must be a nonempty array or jobs array.")
    normalized: list[dict] = []
    for index, job in enumerate(jobs, 1):
        if not isinstance(job, dict):
            raise AgentError(f"Batch job {index} must be an object.")
        unknown = set(job) - {"agent", "params", "model", "reasoning", "log"}
        if unknown:
            raise AgentError(f"Batch job {index} has unknown field(s): " + ", ".join(sorted(unknown)))
        agent_id, params = job.get("agent"), job.get("params", {})
        if not isinstance(agent_id, str) or not isinstance(params, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in params.items()
        ):
            raise AgentError(f"Batch job {index} requires an agent and string params.")
        model, reasoning = job.get("model", default_model), job.get("reasoning", default_reasoning)
        enable_log = job.get("log", default_log)
        if not isinstance(model, str) or not isinstance(reasoning, str) or not isinstance(enable_log, bool):
            raise AgentError(f"Batch job {index} has invalid model, reasoning, or log.")
        validate_model_settings(model, reasoning)
        config = load_agent(agent_id, workspace_root)
        if config["output"]["format"] != "markdown":
            raise AgentError(f"Batch job {index} uses non-report agent {agent_id!r}.")
        prepare_invocation(agent_id, params, model, reasoning, enable_log, False,
                           workspace_root, reserve_output=False)
        normalized.append({"agent": agent_id, "params": params, "model": model,
                           "reasoning": reasoning, "log": enable_log})
    return normalized


def run_batch(jobs: list[dict], result_dir: Path, workspace_root: Path = REPO_ROOT) -> int:
    result_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {
            executor.submit(execute_agent, job["agent"], job["params"], job["model"],
                            job["reasoning"], job["log"], False, workspace_root): index
            for index, job in enumerate(jobs, 1)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                outcome = future.result()
                outcome["job"] = index
            except Exception as exc:
                outcome = {"job": index, "agent": jobs[index - 1]["agent"],
                           "status": "failed", "error": str(exc)}
            results.append(outcome)
    results.sort(key=lambda item: item["job"])
    (result_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 1 if any(item["status"] == "failed" for item in results) else 0


def _detached_options() -> dict:
    options: dict = {"cwd": REPO_ROOT, "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    return options


def launch_detached_agent(agent_id: str, param_items: list[str], model: str, reasoning: str,
                          enable_log: bool, workspace_root: Path) -> Path:
    launches = REPO_ROOT / ".agent-runs" / "launches"
    launches.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    launch_dir = Path(tempfile.mkdtemp(prefix=f"{timestamp}-{agent_id}-", dir=launches))
    command = [sys.executable, "-u", str(REPO_ROOT / "agent.py"), "--agent", agent_id,
               "--agent-worker", "--workspace-root", str(workspace_root), "--model", model,
               "--reasoning", reasoning]
    for item in param_items:
        command.extend(("--param", item))
    if enable_log:
        command.append("--log")
    with (launch_dir / "manager.log").open("w", encoding="utf-8") as log:
        subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **_detached_options())
    return launch_dir


def launch_detached_batch(path: Path, model: str, reasoning: str, enable_log: bool) -> Path:
    batches = REPO_ROOT / ".agent-runs" / "batches"
    batches.mkdir(parents=True, exist_ok=True)
    result_dir = batches / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    result_dir.mkdir()
    command = [sys.executable, "-u", str(REPO_ROOT / "agent.py"), "--batch-worker",
               str(path.resolve()), "--batch-result-dir", str(result_dir), "--model", model,
               "--reasoning", reasoning]
    if enable_log:
        command.append("--log")
    with (result_dir / "manager.log").open("w", encoding="utf-8") as log:
        subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **_detached_options())
    return result_dir


def ask_yes(prompt: str, default: bool) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    return default if not answer else answer in {"y", "yes"}


def print_warning(level: str, message: str) -> None:
    color = "\033[93m" if level == "yellow" else "\033[91m"
    print(f"{color}{level.upper()} WARNING\033[0m: {message}")


def main() -> int:
    help_requested = any(item in {"-h", "--help"} for item in sys.argv[1:])
    parser = argparse.ArgumentParser(
        description="Run risk-scoped model agents and save validated artifacts.",
        epilog=build_help_epilog(sys.argv[1:]) if help_requested else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--agent", "--a", help="Agent ID corresponding to agents/<id>.json")
    mode.add_argument("--batch", type=Path, help="JSON file of report-agent jobs")
    mode.add_argument("--batch-worker", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--maintenance", action="store_true", help="Run registered maintenance tasks")
    parser.add_argument("--agent-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workspace-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--param", "--p", action="append", default=[], metavar="VALUE|ID=VALUE")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning", default=DEFAULT_REASONING)
    parser.add_argument("--log", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--batch-result-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        workspace = validate_workspace_root(args.workspace_root or REPO_ROOT)
        if args.maintenance:
            if args.param or args.validate_only or args.agent_worker:
                raise AgentError("--maintenance cannot be combined with agent-only options.")
            results = run_maintenance(workspace, log=print)
            print(json.dumps([item.as_dict() for item in results], indent=2))
            return 0
        if args.agent:
            config = load_agent(args.agent, workspace)
            supplied = parse_param_assignments(args.param, config.get("params", {}))
            prepared = prepare_invocation(args.agent, supplied, args.model, args.reasoning,
                                          args.log, True, workspace, reserve_output=False)
            if args.validate_only:
                print(f"VALID: {args.agent} -> {prepared.destination}")
                return 0
            if args.agent_worker:
                execute_agent(args.agent, supplied, args.model, args.reasoning, args.log,
                              True, workspace)
                return 0
            risk = config["risk"]
            is_report = config["output"]["format"] == "markdown"
            use_worktree = False
            branch: str | None = None
            if risk == "yellow":
                print_warning("yellow", f"the model may write only agent-data/; the handler will write {prepared.destination}.")
                use_worktree = ask_yes("Create an isolated Git worktree? [y/N]: ", False)
            elif risk == "red":
                assert prepared.inputs.paper is not None
                print_warning("red", "the model may write agent-data/ and the resolved paper "
                              f"{prepared.inputs.paper.relative_path}.")
                if not ask_yes("Type Y to continue: ", False):
                    print("Cancelled.")
                    return 0
                use_worktree = ask_yes("Create the recommended isolated Git worktree? [Y/n]: ", True)
            if use_worktree:
                workspace, branch = create_agent_worktree(args.agent)
            should_detach = use_worktree or (risk == "yellow" and not args.foreground) or (is_report and not args.foreground)
            if should_detach:
                launch_dir = launch_detached_agent(args.agent, args.param, args.model,
                                                   args.reasoning, args.log, workspace)
                print(f"Started {args.agent} in the background.")
                print(f"Log:     {launch_dir / 'manager.log'}")
                if branch:
                    (launch_dir / "worktree.json").write_text(
                        json.dumps({"path": str(workspace), "branch": branch}, indent=2) + "\n",
                        encoding="utf-8")
                    print_worktree_details(workspace, branch)
                return 0
            execute_agent(args.agent, supplied, args.model, args.reasoning, args.log,
                          True, workspace)
            return 0
        batch_path = args.batch_worker or args.batch
        assert batch_path is not None
        if args.agent_worker or args.param:
            raise AgentError("Agent worker/parameters require --agent.")
        jobs = load_batch_jobs(batch_path, args.model, args.reasoning, args.log, workspace)
        if args.validate_only:
            print(f"VALID: {len(jobs)} parallel report job(s)")
            return 0
        if args.batch_worker:
            if args.batch_result_dir is None:
                raise AgentError("Detached batch is missing its result directory.")
            return run_batch(jobs, args.batch_result_dir, workspace)
        if args.foreground:
            result_dir = REPO_ROOT / ".agent-runs" / "batches" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            return run_batch(jobs, result_dir, workspace)
        result_dir = launch_detached_batch(batch_path, args.model, args.reasoning, args.log)
        print(f"Started {len(jobs)} report agent(s) in the background.")
        print(f"Status: {result_dir / 'results.json'}")
        print(f"Log:    {result_dir / 'manager.log'}")
        return 0
    except AgentError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


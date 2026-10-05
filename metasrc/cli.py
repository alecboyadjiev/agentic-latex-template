from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import uuid

from metasrc.definitions import load_agent
from metasrc.errors import AgentError
from metasrc.handlers.runs import atomic_json
from metasrc.handlers.worktrees import (
    create_agent_worktree, print_worktree_details, validate_workspace_root,
)
from metasrc.maintenance.runner import run_maintenance
from metasrc.orchestration import prepare_invocation
from metasrc.paths import REPO_ROOT
from metasrc.providers.codex_cli import DEFAULT_MODEL, DEFAULT_REASONING, validate_model_settings
from metasrc.sessions.client import SessionClient
from metasrc.sessions.console import render_menu, run_console
from metasrc.sessions.registry import FileLock
from metasrc.sessions.supervisor import serve
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


def load_batch_jobs(
    path: Path, default_model: str, default_reasoning: str, default_log: bool,
    workspace_root: Path = REPO_ROOT,
) -> list[dict]:
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
        prepare_invocation(
            agent_id, params, model, reasoning, enable_log, False,
            workspace_root, reserve_output=False,
        )
        normalized.append({
            "agent": agent_id, "params": params, "model": model,
            "reasoning": reasoning, "log": enable_log,
        })
    return normalized


def _launch(client: SessionClient, job: dict, workspace: Path, *, worktree: dict | None = None,
            session_key: str | None = None, batch: dict | None = None) -> dict:
    return client.request(
        "launch", agent=job["agent"], params=job["params"], model=job["model"],
        reasoning=job["reasoning"], log=job["log"], workspace=str(workspace),
        worktree=worktree, session_key=session_key, batch=batch,
    )


def run_batch(jobs: list[dict], result_dir: Path, workspace_root: Path = REPO_ROOT,
              *, foreground: bool = False, client: SessionClient | None = None) -> int:
    result_dir.mkdir(parents=True, exist_ok=True)
    active_client = client or SessionClient(workspace_root)
    batch_key = str(uuid.uuid4())
    result_path = result_dir / "results.json"
    session_keys = [str(uuid.uuid4()) for _ in jobs]
    results: list[dict] = [
        {"job": index, "agent": job["agent"], "session_key": session_keys[index - 1],
         "status": "launching"}
        for index, job in enumerate(jobs, 1)
    ]
    atomic_json(result_path, results)
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {
            executor.submit(_launch, active_client, job, workspace_root,
                            session_key=session_keys[index - 1],
                            batch={"batch_key": batch_key, "job": index,
                                   "result_path": str(result_path)}): index
            for index, job in enumerate(jobs, 1)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                launched = future.result()
                outcome = {"short_id": launched["short_id"], "status": launched["status"]}
            except Exception as exc:
                outcome = {"status": "failed", "error": str(exc)}
            with FileLock(result_path.with_suffix(result_path.suffix + ".lock")):
                current = json.loads(result_path.read_text(encoding="utf-8"))
                row = current[index - 1]
                if row.get("status") != "success":
                    row.update(outcome)
                atomic_json(result_path, current)
    results = json.loads(result_path.read_text(encoding="utf-8"))
    if foreground:
        while True:
            menu = active_client.request("list")["menu"]["sessions"]
            live_by_key = {row["session_key"]: row for row in menu.values()}
            executing = False
            with FileLock(result_path.with_suffix(result_path.suffix + ".lock")):
                results = json.loads(result_path.read_text(encoding="utf-8"))
                for result in results:
                    session_key = result.get("session_key")
                    if not session_key:
                        continue
                    row = live_by_key.get(session_key)
                    receipt = workspace_root / ".agent-runs" / "receipts" / f"{session_key}.json"
                    if receipt.exists():
                        result["status"] = "success"
                    elif row:
                        result["status"] = row["status"]
                        executing = executing or row["status"] == "executing"
                atomic_json(result_path, results)
            if not executing:
                break
            time.sleep(0.25)
    return 1 if any(item["status"] in {"failed", "incomplete"} for item in results) else 0


def ask_yes(prompt: str, default: bool) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    return default if not answer else answer in {"y", "yes"}


def print_warning(level: str, message: str) -> None:
    color = "\033[93m" if level == "yellow" else "\033[91m"
    reset = "\033[0m"
    if not sys.stdout.isatty():
        color = reset = ""
    print(f"{color}{level.upper()} WARNING{reset}: {message}")


def _positive_id(value: str) -> int:
    if not value.isdecimal() or int(value) < 1:
        raise argparse.ArgumentTypeError("session ID must be a positive decimal integer")
    return int(value)


def _option_present(*names: str) -> bool:
    return any(
        argument in names or any(argument.startswith(name + "=") for name in names)
        for argument in sys.argv[1:]
    )


def _print_client_menu(client: SessionClient) -> None:
    print(render_menu(client.request("list")["menu"]))


def main() -> int:
    help_requested = any(item in {"-h", "--help"} for item in sys.argv[1:])
    parser = argparse.ArgumentParser(
        description="Launch and control persistent risk-scoped model agents.",
        epilog=build_help_epilog(sys.argv[1:]) if help_requested else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--agent", "--a", help="Agent ID corresponding to agents/<id>.json")
    mode.add_argument("--batch", type=Path, help="JSON file of report-agent jobs")
    mode.add_argument("--maintenance", action="store_true", help="Run registered maintenance tasks")
    mode.add_argument("--sessions", action="store_true", help="List living agent sessions")
    mode.add_argument("--session", type=_positive_id, help="Open an interactive agent session")
    mode.add_argument("--interrupt", type=_positive_id, help="Interrupt a live session")
    mode.add_argument("--delete", type=_positive_id, help="Permanently delete a session and chat")
    mode.add_argument("--session-supervisor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workspace-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--param", "--p", action="append", default=[], metavar="VALUE|ID=VALUE")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning", default=DEFAULT_REASONING)
    parser.add_argument("--log", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--force", action="store_true", help="Skip hard-delete confirmation")
    args = parser.parse_args()
    try:
        workspace = validate_workspace_root(args.workspace_root or REPO_ROOT)
        if args.session_supervisor:
            serve(workspace)
            return 0
        if args.maintenance:
            if (
                args.param or args.validate_only or args.foreground or args.force or args.log
                or _option_present("--model", "--reasoning")
            ):
                raise AgentError("--maintenance cannot be combined with agent/session options.")
            results = run_maintenance(workspace, log=print)
            print(json.dumps([item.as_dict() for item in results], indent=2))
            return 0
        if args.sessions or args.session or args.interrupt or args.delete:
            if (
                args.param or args.validate_only or args.foreground or args.log
                or _option_present("--model", "--reasoning")
                or args.force and not args.delete
            ):
                raise AgentError("Session controls cannot be combined with launch-only options.")
            client = SessionClient(workspace)
            if args.sessions:
                _print_client_menu(client)
                return 0
            if args.session:
                return run_console(client, args.session)
            if args.interrupt:
                result = client.request("interrupt", short_id=args.interrupt)
                print(result["message"])
                print(render_menu(result["menu"]))
                return 0
            assert args.delete
            if not args.force:
                if not sys.stdin.isatty():
                    raise AgentError("Non-interactive deletion requires --force.")
                if not ask_yes(f"Permanently delete session {args.delete} and its chat? [y/N]: ", False):
                    print("Cancelled.")
                    _print_client_menu(client)
                    return 0
            result = client.request("delete", short_id=args.delete)
            print(result["message"])
            print(render_menu(result["menu"]))
            return 0
        if args.batch:
            if args.param or args.force:
                raise AgentError("Batch mode does not accept --param or --force.")
            jobs = load_batch_jobs(args.batch, args.model, args.reasoning, args.log, workspace)
            if args.validate_only:
                print(f"VALID: {len(jobs)} parallel report job(s)")
                return 0
            result_dir = workspace / ".agent-runs" / "batches" / datetime.now().strftime(
                "%Y%m%d-%H%M%S-%f"
            )
            client = SessionClient(workspace)
            code = run_batch(jobs, result_dir, workspace, foreground=args.foreground, client=client)
            results = json.loads((result_dir / "results.json").read_text(encoding="utf-8"))
            for item in results:
                if "short_id" in item:
                    print(f"Job {item['job']} ({item['agent']}): session {item['short_id']} [{item['status']}]")
                else:
                    print(f"Job {item['job']} ({item['agent']}): failed: {item.get('error', '')}")
            print(f"Status: {result_dir / 'results.json'}")
            _print_client_menu(client)
            return code
        assert args.agent
        if args.force:
            raise AgentError("--force is valid only with --delete.")
        config = load_agent(args.agent, workspace)
        supplied = parse_param_assignments(args.param, config.get("params", {}))
        prepared = prepare_invocation(
            args.agent, supplied, args.model, args.reasoning, args.log, True,
            workspace, reserve_output=False,
        )
        if args.validate_only:
            print(f"VALID: {args.agent} -> {prepared.destination}")
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
        session_key = str(uuid.uuid4())
        worktree_metadata = None
        if use_worktree:
            print("Creating a full-state worktree snapshot...")
            workspace, branch = create_agent_worktree(
                args.agent, workspace, session_key, progress=print if sys.stdout.isatty() else None
            )
            worktree_metadata = {"path": str(workspace), "branch": branch}
        client = SessionClient(REPO_ROOT)
        result = _launch(
            client,
            {"agent": args.agent, "params": supplied, "model": args.model,
             "reasoning": args.reasoning, "log": args.log},
            workspace, worktree=worktree_metadata, session_key=session_key,
        )
        attach = args.foreground or (risk == "red" and not use_worktree)
        disposition = "attached" if attach else "continuing in the background"
        print(f"Session {result['short_id']} launched ({disposition}).")
        if branch:
            print_worktree_details(workspace, branch)
        if attach:
            return run_console(client, result["short_id"])
        _print_client_menu(client)
        return 0
    except AgentError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

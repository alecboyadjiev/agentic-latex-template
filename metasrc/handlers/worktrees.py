from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess

from metasrc.errors import AgentError
from metasrc.paths import REPO_ROOT


def git_output(arguments: list[str], cwd: Path = REPO_ROOT) -> str:
    process = subprocess.run(
        ["git", *arguments], cwd=cwd, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise AgentError(f"Git command failed: git {' '.join(arguments)}\n{process.stderr.strip()}")
    return process.stdout


def create_agent_worktree(agent_id: str) -> tuple[Path, str]:
    if git_output(["status", "--porcelain"]).strip():
        raise AgentError(
            "Worktree mode requires a clean main checkout. Commit or stash changes first."
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


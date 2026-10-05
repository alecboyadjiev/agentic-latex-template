from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil
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


def _source_guard(source: Path) -> tuple[str, bytes]:
    status_value = git_output(["status", "--porcelain=v1", "--untracked-files=all"], source)
    index = Path(git_output(["rev-parse", "--git-path", "index"], source).strip())
    if not index.is_absolute():
        index = source / index
    return status_value, index.read_bytes() if index.is_file() else b""


def _manifest(root: Path) -> dict[str, tuple[str, int, int, str | None]]:
    result: dict[str, tuple[str, int, int, str | None]] = {}

    def walk(directory: Path, prefix: Path) -> None:
        for entry in os.scandir(directory):
            relative = prefix / entry.name
            if not prefix.parts and entry.name in {".git", ".agent-runs"}:
                continue
            path = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            key = relative.as_posix()
            if entry.is_symlink():
                result[key] = ("symlink", 0, 0, os.readlink(path))
            elif entry.is_dir(follow_symlinks=False):
                result[key] = ("directory", 0, info.st_mtime_ns, None)
                walk(path, relative)
            elif entry.is_file(follow_symlinks=False):
                result[key] = ("file", info.st_size, info.st_mtime_ns, None)
            else:
                raise AgentError(f"Unsupported filesystem entry in worktree snapshot: {path}")

    walk(root, Path())
    return result


def _remove_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _overlay(source: Path, destination: Path, manifest: dict) -> tuple[int, int]:
    destination_manifest = _manifest(destination)
    for relative in sorted(
        set(destination_manifest) - set(manifest),
        key=lambda value: len(Path(value).parts), reverse=True,
    ):
        _remove_entry(destination / Path(relative))
    count = total = 0
    for relative in sorted(manifest, key=lambda value: (len(Path(value).parts), value)):
        kind, size, _, target = manifest[relative]
        src, dst = source / Path(relative), destination / Path(relative)
        existing = destination_manifest.get(relative)
        if existing and existing[0] != kind:
            _remove_entry(dst)
        if kind == "directory":
            dst.mkdir(parents=True, exist_ok=True)
        elif kind == "symlink":
            if not (dst.is_symlink() and os.readlink(dst) == target):
                if dst.exists() or dst.is_symlink():
                    _remove_entry(dst)
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, dst, target_is_directory=src.resolve().is_dir())
                count += 1
            try:
                shutil.copystat(src, dst, follow_symlinks=False)
            except OSError:
                pass
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() or dst.is_symlink():
                _remove_entry(dst)
            shutil.copy2(src, dst, follow_symlinks=False)
            count += 1
            total += size
    for relative in sorted(
        (value for value, data in manifest.items() if data[0] == "directory"),
        key=lambda value: len(Path(value).parts), reverse=True,
    ):
        try:
            shutil.copystat(source / Path(relative), destination / Path(relative), follow_symlinks=False)
        except OSError:
            pass
    return count, total


def create_agent_worktree(
    agent_id: str, source_workspace: Path = REPO_ROOT, session_key: str | None = None,
    progress=None,
) -> tuple[Path, str]:
    source = validate_workspace_root(source_workspace)
    suffix = (session_key or "session").replace("-", "")[:12]
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    branch = f"codex/{agent_id}-{timestamp}-{suffix}"
    registered = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in git_output(["worktree", "list", "--porcelain"], source).splitlines()
        if line.startswith("worktree ")
    ]
    primary = registered[0]
    parent = primary.parent / f".{primary.name}-worktrees"
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / f"{agent_id}-{timestamp}-{suffix}"
    head = git_output(["rev-parse", "HEAD"], source).strip()
    source_guard = _source_guard(source)
    created = False
    try:
        git_output(["worktree", "add", "-b", branch, str(path), head], source)
        created = True
        copied_files = copied_bytes = 0
        for attempt in range(2):
            before = _manifest(source)
            copied_files, copied_bytes = _overlay(source, path, before)
            after = _manifest(source)
            if before == after:
                break
            if attempt:
                raise AgentError("source changed during worktree snapshot")
        if _source_guard(source) != source_guard:
            raise AgentError("source changed during worktree snapshot")
        if _manifest(path) != after:
            raise AgentError("Worktree snapshot verification failed.")
        git_output(["status", "--porcelain=v1", "--untracked-files=all"], path)
        if progress is not None:
            progress(f"Snapshot copied {copied_files} file(s), {copied_bytes} byte(s).")
        return path.resolve(), branch
    except Exception:
        if created:
            try:
                git_output(["worktree", "remove", "--force", str(path)], source)
            finally:
                try:
                    git_output(["branch", "-D", branch], source)
                except AgentError:
                    pass
        raise


def validate_workspace_root(path: Path) -> Path:
    resolved = path.resolve()
    registered = {
        Path(line.removeprefix("worktree ")).resolve()
        for line in git_output(["worktree", "list", "--porcelain"], resolved).splitlines()
        if line.startswith("worktree ")
    }
    if resolved not in registered:
        raise AgentError(f"Workspace is not a registered Git worktree: {resolved}")
    return resolved


def print_worktree_details(path: Path, branch: str) -> None:
    print(f"Worktree: {path}")
    print(f"Branch:   {branch}")
    print("Changes will remain uncommitted for review in that worktree.")

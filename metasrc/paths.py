from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def canonical_workspace(path: Path | None = None) -> Path:
    root = (path or REPO_ROOT).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Workspace root is not a directory: {root}")
    return root


def workspace_paths(root: Path) -> dict[str, Path]:
    root = root.resolve()
    return {
        "root": root,
        "agents": root / "agents",
        "reports": root / "reports",
        "runs": root / ".agent-runs",
        "papers": root / "papers",
        "markdown": root / "markdown",
        "agent_data": root / "agent-data",
    }


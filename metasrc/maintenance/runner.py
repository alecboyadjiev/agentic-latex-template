from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Callable, Iterator, Sequence

from metasrc.errors import AgentError
from metasrc.maintenance.base import MaintenanceResult, MaintenanceTask
from metasrc.maintenance.pdf_cache import MANIFEST_NAME, cache_pdfs


class MaintenanceError(AgentError):
    def __init__(self, message: str, result: MaintenanceResult):
        super().__init__(message)
        self.result = result
        self.completed: tuple[MaintenanceResult, ...] = ()


@contextmanager
def maintenance_lock(cache_dir: Path) -> Iterator[None]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / ".maintenance.lock"
    with lock_path.open("a+b") as stream:
        stream.seek(0)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class PdfCacheTask:
    id = "pdf-cache"

    def run(self, workspace_root: Path) -> MaintenanceResult:
        started = datetime.now(timezone.utc)
        start_clock = time.monotonic()
        root = workspace_root.resolve(strict=True)
        cache_dir = root / ".pdf-cache"
        manifest_path = cache_dir / MANIFEST_NAME
        before = manifest_path.read_bytes() if manifest_path.is_file() else None
        try:
            with maintenance_lock(cache_dir):
                code = cache_pdfs(
                    root=root,
                    cache_dir=cache_dir,
                    verify=False,
                    force=False,
                    prune=True,
                )
            after = manifest_path.read_bytes() if manifest_path.is_file() else None
            if code:
                raise RuntimeError(f"PDF cache reported {code} extraction error status")
            status = "updated" if before != after else "unchanged"
            manifest = json.loads(after.decode("utf-8")) if after else {"documents": {}}
            details = {
                "files": len(manifest.get("documents", {})),
                "errors": sum(
                    item.get("status") == "error"
                    for item in manifest.get("documents", {}).values()
                ),
                "cache_dir": str(cache_dir),
            }
            return MaintenanceResult(
                id=self.id, status=status, started_at=started.isoformat(),
                duration_ms=round((time.monotonic() - start_clock) * 1000), details=details,
            )
        except Exception as exc:
            result = MaintenanceResult(
                id=self.id, status="failed", started_at=started.isoformat(),
                duration_ms=round((time.monotonic() - start_clock) * 1000),
                details={"error_class": type(exc).__name__, "message": str(exc)},
            )
            raise MaintenanceError(f"Maintenance task {self.id!r} failed: {exc}", result) from exc


def run_maintenance(
    workspace_root: Path,
    tasks: Sequence[MaintenanceTask] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[MaintenanceResult]:
    if tasks is None:
        from metasrc.maintenance.base import default_registry
        tasks = default_registry()
    results: list[MaintenanceResult] = []
    for task in tasks:
        if log:
            log(f"MAINTENANCE start task={task.id}")
        try:
            result = task.run(workspace_root)
        except MaintenanceError as exc:
            exc.completed = tuple(results)
            if log:
                log(
                    f"MAINTENANCE failed task={task.id} duration_ms={exc.result.duration_ms} "
                    f"error={exc.result.details.get('error_class')}: "
                    f"{exc.result.details.get('message')}"
                )
            raise
        results.append(result)
        if log:
            log(
                f"MAINTENANCE result task={task.id} status={result.status} "
                f"duration_ms={result.duration_ms}"
            )
    return results

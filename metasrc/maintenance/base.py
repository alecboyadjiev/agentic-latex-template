from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol


@dataclass(frozen=True)
class MaintenanceResult:
    id: str
    status: str
    started_at: str
    duration_ms: int
    details: dict

    def as_dict(self) -> dict:
        return asdict(self)


class MaintenanceTask(Protocol):
    id: str
    def run(self, workspace_root: Path) -> MaintenanceResult: ...


def default_registry() -> tuple[MaintenanceTask, ...]:
    from metasrc.maintenance.runner import PdfCacheTask
    return (PdfCacheTask(),)


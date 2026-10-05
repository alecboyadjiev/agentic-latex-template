from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid
import shutil

from metasrc.errors import AgentError


SCHEMA_VERSION = 1
LIVE_STATUSES = {"executing", "incomplete"}
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        with _PROCESS_LOCKS_GUARD:
            self._thread_lock = _PROCESS_LOCKS.setdefault(str(path.resolve()), threading.RLock())
        self._stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_lock.acquire()
        self._stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._stream.seek(0)
                if self._stream.tell() == self._stream.seek(0, 2):
                    self._stream.write(b"0")
                    self._stream.flush()
                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
        except Exception:
            self._stream.close()
            self._stream = None
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        assert self._stream is not None
        try:
            if os.name == "nt":
                import msvcrt
                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None
            self._thread_lock.release()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class SessionRegistry:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.root = self.workspace_root / ".agent-runs" / "sessions"
        self.index_path = self.root / "index.json"
        self.lock_path = self.root / "registry.lock"

    @contextmanager
    def locked(self):
        with FileLock(self.lock_path):
            yield

    def _read_unlocked(self) -> dict:
        if not self.index_path.exists():
            return {"schema_version": SCHEMA_VERSION, "revision": 0, "sessions": {}}
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentError(f"Cannot read live session registry: {exc}") from exc
        if value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("sessions"), dict):
            raise AgentError("Unsupported or malformed live session registry.")
        self._validate_index(value)
        return value

    def _write_unlocked(self, index: dict) -> None:
        self._validate_index(index)
        index["revision"] = int(index.get("revision", 0)) + 1
        _atomic_json(self.index_path, index)

    def _validate_index(self, index: dict) -> None:
        keys: set[str] = set()
        for short_id, row in index.get("sessions", {}).items():
            if not short_id.isdecimal() or str(int(short_id)) != short_id or int(short_id) < 1:
                raise AgentError(f"Invalid live session ID: {short_id!r}")
            if row.get("status") not in LIVE_STATUSES:
                raise AgentError(f"Invalid live session status for {short_id}.")
            key = row.get("session_key")
            if not isinstance(key, str) or not key or key in keys:
                raise AgentError("Live session registry contains a missing or duplicate UUID.")
            keys.add(key)

    def allocate(
        self, definition_id: str, workspace: Path, *, session_key: str | None = None,
        **state: object,
    ) -> dict:
        with self.locked():
            index = self._read_unlocked()
            used = {int(value) for value in index["sessions"]}
            short = next(value for value in range(1, len(used) + 2) if value not in used)
            session_key = session_key or str(uuid.uuid4())
            try:
                uuid.UUID(session_key)
            except ValueError as exc:
                raise AgentError("Internal session key must be a UUID.") from exc
            if any(row.get("session_key") == session_key for row in index["sessions"].values()):
                raise AgentError("Internal session key is already live.")
            timestamp = now_iso()
            row = {
                "session_key": session_key,
                "definition_id": definition_id,
                "status": "incomplete",
                "detail": "preparing",
                "updated_at": timestamp,
                "workspace": str(workspace.resolve()),
            }
            index["sessions"][str(short)] = row
            directory = self.root / session_key
            directory.mkdir(parents=True, exist_ok=False)
            full = {
                "schema_version": SCHEMA_VERSION,
                "short_id": short,
                "session_key": session_key,
                "definition_id": definition_id,
                "status": "incomplete",
                "detail": "preparing",
                "created_at": timestamp,
                "updated_at": timestamp,
                "active_turn_id": None,
                "provider_thread_id": None,
                "artifact_committed": False,
                "cleanup_phase": None,
                "last_error": None,
                "workspace": str(workspace.resolve()),
                **state,
            }
            self._validate_state(full)
            _atomic_json(directory / "session.json", full)
            self._write_unlocked(index)
            return full

    def _validate_state(self, state: dict) -> None:
        status = state.get("status")
        active = state.get("active_turn_id")
        if status not in LIVE_STATUSES:
            raise AgentError("Session state must be executing or incomplete.")
        if (status == "executing") != bool(active):
            raise AgentError("Executing state must have exactly one active turn; incomplete must have none.")

    def resolve(self, short_id: int | str, session_key: str | None = None) -> dict:
        key = str(int(short_id))
        with self.locked():
            index = self._read_unlocked()
            row = index["sessions"].get(key)
            if row is None:
                raise AgentError(f"Unknown live session ID {key}.")
            if session_key is not None and row["session_key"] != session_key:
                raise AgentError(f"Session ID {key} has been reused; refusing stale request.")
            path = self.root / row["session_key"] / "session.json"
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AgentError(f"Cannot read state for session {key}: {exc}") from exc
            self._validate_state(state)
            return state

    def update(self, short_id: int | str, session_key: str, **updates: object) -> dict:
        key = str(int(short_id))
        with self.locked():
            index = self._read_unlocked()
            row = index["sessions"].get(key)
            if row is None or row.get("session_key") != session_key:
                raise AgentError(f"Session ID {key} no longer identifies {session_key}.")
            path = self.root / session_key / "session.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state.update(updates)
            state["updated_at"] = now_iso()
            self._validate_state(state)
            _atomic_json(path, state)
            for field in ("status", "detail", "updated_at", "workspace"):
                if field in state:
                    row[field] = state[field]
            self._write_unlocked(index)
            return state

    def remove(self, short_id: int | str, session_key: str) -> Path:
        key = str(int(short_id))
        with self.locked():
            index = self._read_unlocked()
            row = index["sessions"].get(key)
            if row is None or row.get("session_key") != session_key:
                raise AgentError(f"Session ID {key} no longer identifies {session_key}.")
            directory = self.root / session_key
            del index["sessions"][key]
            self._write_unlocked(index)
            return directory

    def remove_with_data(self, short_id: int | str, session_key: str) -> None:
        key = str(int(short_id))
        with self.locked():
            index = self._read_unlocked()
            row = index["sessions"].get(key)
            if row is None or row.get("session_key") != session_key:
                raise AgentError(f"Session ID {key} no longer identifies {session_key}.")
            directory = (self.root / session_key).resolve()
            if directory.parent != self.root.resolve():
                raise AgentError("Refusing to remove session data outside the registry.")
            if directory.exists():
                shutil.rmtree(directory)
            del index["sessions"][key]
            self._write_unlocked(index)

    def snapshot(self) -> dict:
        with self.locked():
            return self._read_unlocked()

    def states(self) -> list[dict]:
        index = self.snapshot()
        result: list[dict] = []
        for short_id in sorted(index["sessions"], key=int):
            try:
                result.append(self.resolve(short_id, index["sessions"][short_id]["session_key"]))
            except AgentError:
                row = dict(index["sessions"][short_id])
                row["short_id"] = int(short_id)
                result.append(row)
        return result

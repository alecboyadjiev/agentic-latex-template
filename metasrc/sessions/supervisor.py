from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import socketserver
import threading
import time
import traceback
from typing import Callable

from metasrc.errors import AgentError
from metasrc.handlers.runs import atomic_json
from metasrc.handlers.outputs import get_output_path
from metasrc.maintenance.runner import MaintenanceError, run_maintenance
from metasrc.orchestration import prepare_invocation
from metasrc.providers.base import ProviderEvent, ProviderSettings
from metasrc.providers.codex_app_server import CodexAppServerProvider
from metasrc.sessions.client import PROTOCOL_VERSION, new_control_token
from metasrc.sessions.completion import (
    COMPLETION_SCHEMA, commit_completed_message, parse_completion_envelope,
)
from metasrc.sessions.registry import FileLock, SessionRegistry, now_iso


class SessionSupervisor:
    def __init__(self, workspace_root: Path, provider=None,
                 provider_factory: Callable[[], object] = CodexAppServerProvider,
                 maintenance_tasks=None):
        self.workspace_root = workspace_root.resolve()
        self.registry = SessionRegistry(self.workspace_root)
        self._provider = provider
        self._provider_factory = provider_factory
        self._provider_lock = threading.RLock()
        self._session_locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._event_thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._progress: dict[str, str] = {}
        self._known_threads: set[str] = set()
        self._closures: dict[str, dict] = {}
        self._maintenance_tasks = maintenance_tasks

    def _progress_path(self, state: dict) -> Path:
        return self.registry.root / state["session_key"] / "reasoning-summary.txt"

    def _read_progress(self, state: dict) -> str:
        thread_id = state.get("provider_thread_id")
        if not thread_id:
            return ""
        cached = self._progress.get(thread_id)
        if cached is not None:
            return cached
        path = self._progress_path(state)
        try:
            progress = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError as exc:
            self._log_error(f"reasoning summary read failed for {state['session_key']}: {exc}")
            progress = ""
        self._progress[thread_id] = progress
        return progress

    def _append_progress(self, state: dict, value: str) -> None:
        if not value:
            return
        thread_id = state.get("provider_thread_id")
        if not thread_id:
            return
        current = self._read_progress(state)
        path = self._progress_path(state)
        try:
            with path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            self._log_error(f"reasoning summary write failed for {state['session_key']}: {exc}")
            return
        self._progress[thread_id] = current + value

    def _get_provider(self):
        with self._provider_lock:
            if self._provider is None:
                self._provider = self._provider_factory()
            if self._event_thread is None or not self._event_thread.is_alive():
                self._event_thread = threading.Thread(
                    target=self._event_loop, name="session-provider-events", daemon=True
                )
                self._event_thread.start()
            return self._provider

    def _lock_for(self, session_key: str) -> threading.RLock:
        with self._locks_guard:
            return self._session_locks.setdefault(session_key, threading.RLock())

    @staticmethod
    def _settings(state: dict) -> ProviderSettings:
        return ProviderSettings(
            state["model"], state["reasoning"], Path(state["workspace"]),
            Path(state["working_directory"]), tuple(Path(item) for item in state["writable_roots"]),
        )

    def launch(self, arguments: dict) -> dict:
        workspace = Path(arguments.get("workspace") or self.workspace_root).resolve()
        prepared = prepare_invocation(
            arguments["agent"], arguments.get("params", {}), arguments["model"],
            arguments["reasoning"], bool(arguments.get("log")), False, workspace,
            reserve_output=False,
        )
        state = self.registry.allocate(
            prepared.config["id"], workspace, session_key=arguments.get("session_key"),
            config=prepared.config,
            params=prepared.inputs.values,
            paper=prepared.inputs.paper.as_metadata() if prepared.inputs.paper else None,
            target=prepared.inputs.target.as_metadata() if prepared.inputs.target else None,
            risk=prepared.config["risk"], output_contract=prepared.config["output"],
            model=arguments["model"], reasoning=arguments["reasoning"],
            provider_id="codex-app-server",
            working_directory=str(prepared.request.working_directory),
            writable_roots=[str(path) for path in prepared.request.writable_roots],
            destination=str(prepared.destination) if prepared.destination else None,
            overwrite=prepared.overwrite,
            worktree=arguments.get("worktree"),
            batch=arguments.get("batch"),
        )
        directory = self.registry.root / state["session_key"]
        try:
            (directory / "prompt.txt").write_text(prepared.prompt, encoding="utf-8")
            provider = self._get_provider()
            thread = provider.start_thread(self._settings(state))
            self._known_threads.add(thread.id)
            state = self.registry.update(
                state["short_id"], state["session_key"], provider_thread_id=thread.id,
                detail="starting first turn",
            )
            self._start_turn(state, prepared.prompt)
        except Exception as exc:
            current = self.registry.update(
                state["short_id"], state["session_key"], status="incomplete",
                active_turn_id=None, detail="launch failed", last_error=f"{type(exc).__name__}: {exc}",
            )
            self._update_batch(current, "incomplete", str(exc))
        current = self.registry.resolve(state["short_id"], state["session_key"])
        return {"short_id": current["short_id"], "session_key": current["session_key"],
                "status": current["status"], "detail": current.get("detail")}

    def _start_turn(self, state: dict, message: str) -> dict:
        if state.get("artifact_committed"):
            raise AgentError("Session cleanup is pending; new prompts are disabled.")
        try:
            results = run_maintenance(
                Path(state["workspace"]), self._maintenance_tasks,
                log=lambda _message: None,
            )
            maintenance = [item.as_dict() for item in results]
        except MaintenanceError as exc:
            recorded = [item.as_dict() for item in exc.completed] + [exc.result.as_dict()]
            updated = self.registry.update(
                state["short_id"], state["session_key"], status="incomplete",
                active_turn_id=None, detail="maintenance failed", last_error=str(exc),
                maintenance=recorded,
            )
            self._update_batch(updated, "incomplete", str(exc))
            return updated
        provider = self._get_provider()
        turn = provider.start_turn(
            state["provider_thread_id"], message, self._settings(state), COMPLETION_SCHEMA
        )
        updated = self.registry.update(
            state["short_id"], state["session_key"], status="executing",
            active_turn_id=turn.id, detail="working", last_error=None,
            maintenance=maintenance,
        )
        self._update_batch(updated, "executing")
        return updated

    def _event_loop(self) -> None:
        provider = self._provider
        while not self._stopping.is_set() and provider is not None:
            try:
                event = provider.next_event(timeout=0.5)
            except Exception:
                return
            if event is None:
                continue
            try:
                self.handle_event(event)
            except Exception:
                self._log_error("event handling failed\n" + traceback.format_exc())

    def _find_by_thread(self, thread_id: str) -> dict | None:
        for state in self.registry.states():
            if state.get("provider_thread_id") == thread_id:
                return state
        return None

    def handle_event(self, event: ProviderEvent) -> None:
        if event.kind == "progress":
            state = self._find_by_thread(event.thread_id)
            if state is not None:
                with self._lock_for(state["session_key"]):
                    self._append_progress(state, event.message or "")
            return
        state = self._find_by_thread(event.thread_id)
        if state is None:
            return
        with self._lock_for(state["session_key"]):
            state = self.registry.resolve(state["short_id"], state["session_key"])
            if event.turn_id and state.get("active_turn_id") not in {None, event.turn_id}:
                return
            if event.kind == "turn_started":
                self.registry.update(
                    state["short_id"], state["session_key"], status="executing",
                    active_turn_id=event.turn_id, detail="working",
                )
            elif event.kind == "turn_completed":
                self._complete_turn(state, event)
            elif event.kind == "error":
                updated = self.registry.update(
                    state["short_id"], state["session_key"], status="incomplete",
                    active_turn_id=None, detail="provider error", last_error=event.message,
                )
                self._update_batch(updated, "incomplete", event.message)

    def _complete_turn(self, state: dict, event: ProviderEvent) -> None:
        if event.status != "completed":
            detail = "interrupted" if event.status == "interrupted" else "provider turn failed"
            self.registry.update(
                state["short_id"], state["session_key"], status="incomplete",
                active_turn_id=None, detail=detail, last_error=event.message,
            )
            self._update_batch(state, "incomplete", event.message)
            return
        message = event.message
        if not message:
            conversation = self._get_provider().read_thread(state["provider_thread_id"], True)
            candidates = [item.text for item in conversation.messages if item.role == "agent"]
            message = candidates[-1] if candidates else None
        try:
            envelope = parse_completion_envelope(message or "")
        except AgentError as exc:
            updated = self.registry.update(
                state["short_id"], state["session_key"], status="incomplete",
                active_turn_id=None, detail="invalid completion envelope", last_error=str(exc),
            )
            self._update_batch(updated, "incomplete", str(exc))
            return
        if envelope.task == "not completed":
            updated = self.registry.update(
                state["short_id"], state["session_key"], status="incomplete",
                active_turn_id=None, detail="not completed", last_error=None,
            )
            self._update_batch(updated, "incomplete")
            return
        try:
            existing = Path(state["artifact_path"]) if state.get("artifact_path") else None
            if not state.get("commit_started"):
                output_format = state["config"]["output"]["format"]
                if output_format != "direct":
                    existing = get_output_path(
                        state["config"], state["params"],
                        reserve=output_format == "markdown",
                        workspace_root=Path(state["workspace"]),
                    )
                state = self.registry.update(
                    state["short_id"], state["session_key"], status="incomplete",
                    active_turn_id=None, detail="committing artifact", commit_started=True,
                    artifact_path=str(existing) if existing else None,
                    completion_summary=envelope.message,
                )
            committed = commit_completed_message(
                envelope, state["config"], state["params"], Path(state["workspace"]),
                already_committed=bool(state.get("artifact_committed")),
                existing_artifact=existing,
                destination_override=existing,
            )
            state = self.registry.update(
                state["short_id"], state["session_key"], status="incomplete",
                active_turn_id=None, detail="cleanup pending", artifact_committed=True,
                artifact_path=str(committed.artifact) if committed.artifact else None,
                completion_summary=committed.summary,
                cleanup_phase="provider deletion", last_error=None,
            )
            self._write_receipt(state)
            self._update_batch(state, "success")
            self._delete_provider_thread(state["provider_thread_id"])
            self._known_threads.discard(state["provider_thread_id"])
            self.registry.remove_with_data(state["short_id"], state["session_key"])
            self._remember_closure(state, "completed", envelope.message)
        except Exception as exc:
            updated = self.registry.update(
                state["short_id"], state["session_key"], status="incomplete",
                active_turn_id=None,
                detail="cleanup pending" if state.get("artifact_committed") else "artifact invalid",
                last_error=f"{type(exc).__name__}: {exc}",
            )
            self._update_batch(updated, "incomplete", str(exc))

    def _write_receipt(self, state: dict) -> None:
        receipt = {
            "schema_version": 1,
            "session_key": state["session_key"],
            "definition_id": state["definition_id"],
            "provider": state["provider_id"],
            "created_at": state["created_at"],
            "completed_at": now_iso(),
            "artifact": state.get("artifact_path"),
            "worktree": state.get("worktree"),
        }
        if state["config"]["output"]["format"] == "direct":
            receipt["summary"] = state.get("completion_summary")
        atomic_json(
            self.workspace_root / ".agent-runs" / "receipts" / f"{state['session_key']}.json",
            receipt,
        )

    def _delete_provider_thread(self, thread_id: str) -> None:
        try:
            self._get_provider().delete_thread(thread_id)
        except AgentError as exc:
            lowered = str(exc).casefold()
            absent = ("not found", "does not exist", "unknown thread")
            if not any(marker in lowered for marker in absent):
                raise

    def _update_batch(self, state: dict, status: str, error: str | None = None) -> None:
        batch = state.get("batch")
        if not isinstance(batch, dict) or not batch.get("result_path"):
            return
        result_path = Path(batch["result_path"]).resolve()
        batches_root = (self.workspace_root / ".agent-runs" / "batches").resolve()
        try:
            result_path.relative_to(batches_root)
        except ValueError:
            self._log_error(f"Rejected batch result path outside control root: {result_path}")
            return
        with FileLock(result_path.with_suffix(result_path.suffix + ".lock")):
            if not result_path.is_file():
                return
            try:
                rows = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            for row in rows:
                if row.get("session_key") == state["session_key"]:
                    row["status"] = status
                    row["short_id"] = state["short_id"]
                    if error:
                        row["error"] = error
                    else:
                        row.pop("error", None)
                    break
            atomic_json(result_path, rows)

    def _remember_closure(
        self, state: dict, detail: str, final_message: str | None = None
    ) -> None:
        self._closures[state["session_key"]] = {
            "closed": True, "short_id": state["short_id"],
            "session_key": state["session_key"], "detail": detail,
            "final_message": final_message, "transcript": [], "notes": [],
        }
        while len(self._closures) > 256:
            self._closures.pop(next(iter(self._closures)))

    def open_session(self, short_id: int, session_key: str | None = None) -> dict:
        try:
            state = self.registry.resolve(short_id, session_key)
        except AgentError:
            if session_key and session_key in self._closures:
                return self._closures[session_key]
            return {"closed": True, "transcript": [], "notes": []}
        provider_thread = state.get("provider_thread_id")
        transcript: list[dict] = []
        notes: list[str] = []
        if provider_thread:
            try:
                conversation = self._get_provider().read_thread(provider_thread, True)
                transcript = [
                    {"role": message.role, "text": message.text, "turn_id": message.turn_id}
                    for message in conversation.messages
                ]
            except AgentError as exc:
                notes.append(str(exc))
        if state.get("last_error"):
            notes.append(str(state["last_error"]))
        return {
            "closed": False, "short_id": short_id, "session_key": state["session_key"],
            "status": state["status"], "detail": state.get("detail"),
            "progress": self._read_progress(state),
            "transcript": transcript, "notes": notes,
        }

    def send_message(self, short_id: int, message: str) -> dict:
        if not message.strip():
            return {"message": "Empty input ignored."}
        state = self.registry.resolve(short_id)
        with self._lock_for(state["session_key"]):
            state = self.registry.resolve(short_id, state["session_key"])
            if state.get("artifact_committed"):
                raise AgentError("Session cleanup is pending; new prompts are disabled.")
            if state["status"] == "executing":
                turn_id = state["active_turn_id"]
                try:
                    self._get_provider().steer_turn(
                        state["provider_thread_id"], turn_id, message
                    )
                except AgentError as exc:
                    try:
                        conversation = self._get_provider().read_thread(
                            state["provider_thread_id"], True
                        )
                    except AgentError:
                        conversation = None
                    if conversation is None or conversation.active_turn_id != turn_id:
                        self.registry.update(
                            short_id, state["session_key"], status="incomplete",
                            active_turn_id=None, detail="turn ended before steering",
                            last_error=str(exc),
                        )
                        raise AgentError(
                            f"Steering was rejected; session {short_id} is now incomplete."
                        ) from exc
                    raise
                return {"message": f"Steered active turn {turn_id}."}
            self._start_turn(state, message)
            return {"message": "Started a new turn."}

    def interrupt(self, short_id: int) -> dict:
        state = self.registry.resolve(short_id)
        with self._lock_for(state["session_key"]):
            state = self.registry.resolve(short_id, state["session_key"])
            if state["status"] == "incomplete":
                return {"message": f"Session {short_id} is already incomplete."}
            self._get_provider().interrupt_turn(
                state["provider_thread_id"], state["active_turn_id"]
            )
            self._wait_turn_terminal(state["provider_thread_id"], state["active_turn_id"])
            updated = self.registry.update(
                short_id, state["session_key"], status="incomplete", active_turn_id=None,
                detail="interrupted", last_error=None,
            )
            self._update_batch(updated, "incomplete")
            return {"message": f"Interrupted session {short_id}."}

    def delete(self, short_id: int) -> dict:
        state = self.registry.resolve(short_id)
        with self._lock_for(state["session_key"]):
            state = self.registry.resolve(short_id, state["session_key"])
            try:
                if state["status"] == "executing":
                    self._get_provider().interrupt_turn(
                        state["provider_thread_id"], state["active_turn_id"]
                    )
                    self._wait_turn_terminal(
                        state["provider_thread_id"], state["active_turn_id"]
                    )
                    state = self.registry.update(
                        short_id, state["session_key"], status="incomplete",
                        active_turn_id=None, detail="deleting",
                    )
                if state.get("provider_thread_id"):
                    self._delete_provider_thread(state["provider_thread_id"])
                    self._known_threads.discard(state["provider_thread_id"])
                self._update_batch(state, "incomplete", "hard deleted")
                self.registry.remove_with_data(short_id, state["session_key"])
                self._remember_closure(state, "deleted")
                return {"message": f"Deleted session {short_id} and its provider chat.", "closed": True}
            except Exception as exc:
                self.registry.update(
                    short_id, state["session_key"], status="incomplete",
                    active_turn_id=None, detail="delete pending", last_error=str(exc),
                )
                raise

    def _wait_turn_terminal(self, thread_id: str, turn_id: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            conversation = self._get_provider().read_thread(thread_id, True)
            if conversation.active_turn_id != turn_id:
                return
            time.sleep(0.1)
        raise AgentError(f"Timed out waiting for interrupted turn {turn_id} to stop.")

    def reconcile(self) -> None:
        states = self.registry.states()
        if not states:
            return
        provider = self._get_provider()
        for state in states:
            with self._lock_for(state["session_key"]):
                if state.get("commit_started") and not state.get("artifact_committed"):
                    self._complete_turn(state, ProviderEvent(
                        "turn_completed", state.get("provider_thread_id") or "",
                        state.get("active_turn_id"), "completed",
                        json.dumps({
                            "task": "completed",
                            "message": state.get("completion_summary", ""),
                        }),
                    ))
                    continue
                if state.get("artifact_committed"):
                    try:
                        if state.get("provider_thread_id"):
                            self._delete_provider_thread(state["provider_thread_id"])
                            self._known_threads.discard(state["provider_thread_id"])
                        self.registry.remove_with_data(
                            state["short_id"], state["session_key"]
                        )
                    except Exception as exc:
                        self.registry.update(
                            state["short_id"], state["session_key"], status="incomplete",
                            active_turn_id=None, detail="cleanup pending", last_error=str(exc),
                        )
                    continue
                if not state.get("provider_thread_id"):
                    continue
                try:
                    if state["provider_thread_id"] not in self._known_threads:
                        provider.resume_thread(state["provider_thread_id"], self._settings(state))
                        self._known_threads.add(state["provider_thread_id"])
                    conversation = provider.read_thread(state["provider_thread_id"], True)
                    if conversation.active_turn_id:
                        self.registry.update(
                            state["short_id"], state["session_key"], status="executing",
                            active_turn_id=conversation.active_turn_id, detail="recovered active turn",
                        )
                    elif (
                        state.get("status") == "executing"
                        and conversation.latest_turn_id == state.get("active_turn_id")
                        and conversation.latest_turn_status in {"completed", "interrupted", "failed"}
                    ):
                        self._complete_turn(state, ProviderEvent(
                            "turn_completed", state["provider_thread_id"],
                            conversation.latest_turn_id, conversation.latest_turn_status,
                            conversation.latest_agent_message,
                        ))
                    else:
                        self.registry.update(
                            state["short_id"], state["session_key"], status="incomplete",
                            active_turn_id=None, detail="recovered",
                        )
                except Exception as exc:
                    self.registry.update(
                        state["short_id"], state["session_key"], status="incomplete",
                        active_turn_id=None, detail="provider thread missing", last_error=str(exc),
                    )

    def list_menu(self) -> dict:
        self.reconcile()
        return self.registry.snapshot()

    def dispatch(self, operation: str, arguments: dict) -> dict:
        if operation == "health":
            return {"status": "ok"}
        if operation == "list":
            return {"menu": self.list_menu()}
        if operation == "launch":
            result = self.launch(arguments)
            result["menu"] = self.registry.snapshot()
            return result
        if operation == "open":
            return self.open_session(
                int(arguments["short_id"]), arguments.get("session_key")
            )
        if operation == "message":
            return self.send_message(int(arguments["short_id"]), str(arguments["message"]))
        if operation == "interrupt":
            result = self.interrupt(int(arguments["short_id"]))
            result["menu"] = self.registry.snapshot()
            return result
        if operation == "delete":
            result = self.delete(int(arguments["short_id"]))
            result["menu"] = self.registry.snapshot()
            return result
        raise AgentError(f"Unknown session operation {operation!r}.")

    def _log_error(self, message: str) -> None:
        path = self.workspace_root / ".agent-runs" / "supervisor-errors.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"[{now_iso()}] {message}\n")

    def close(self) -> None:
        self._stopping.set()
        if self._provider is not None:
            self._provider.close()


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server
        raw = self.rfile.readline(4 * 1024 * 1024)
        try:
            request = json.loads(raw)
            if request.get("protocol") != PROTOCOL_VERSION or request.get("token") != server.token:
                raise AgentError("Unauthorized or incompatible supervisor request.")
            result = server.supervisor.dispatch(
                str(request.get("operation", "")), request.get("arguments", {})
            )
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))


class _ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True


def serve(workspace_root: Path) -> None:
    workspace = workspace_root.resolve()
    run_root = workspace / ".agent-runs"
    run_root.mkdir(parents=True, exist_ok=True)
    supervisor = SessionSupervisor(workspace)
    token = new_control_token()
    with _ThreadingServer(("127.0.0.1", 0), _RequestHandler) as server:
        server.supervisor = supervisor
        server.token = token
        host, port = server.server_address
        control = run_root / "control.json"
        atomic_json(control, {
            "protocol": PROTOCOL_VERSION, "host": host, "port": port,
            "token": token, "pid": os.getpid(), "started_at": now_iso(),
        })
        try:
            os.chmod(control, 0o600)
            server.serve_forever(poll_interval=0.5)
        finally:
            supervisor.close()
            try:
                current = json.loads(control.read_text(encoding="utf-8"))
                if current.get("pid") == os.getpid():
                    control.unlink()
            except (OSError, json.JSONDecodeError):
                pass

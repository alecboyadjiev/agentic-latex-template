from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

from metasrc.errors import AgentError
from metasrc.sessions.registry import FileLock


PROTOCOL_VERSION = 1


class SessionClient:
    def __init__(self, workspace_root: Path, *, auto_start: bool = True):
        self.workspace_root = workspace_root.resolve()
        self.run_root = self.workspace_root / ".agent-runs"
        self.control_path = self.run_root / "control.json"
        if auto_start:
            self.ensure_supervisor()

    def _control(self) -> dict:
        try:
            value = json.loads(self.control_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentError("Session supervisor control file is unavailable.") from exc
        required = {"protocol", "host", "port", "token"}
        if not required.issubset(value) or value["protocol"] != PROTOCOL_VERSION:
            raise AgentError("Session supervisor control file is malformed or incompatible.")
        return value

    def request(self, operation: str, **arguments) -> dict:
        control = self._control()
        payload = {
            "protocol": PROTOCOL_VERSION, "token": control["token"],
            "operation": operation, "arguments": arguments,
        }
        try:
            with socket.create_connection((control["host"], int(control["port"])), timeout=10) as stream:
                stream.settimeout(None)
                stream.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
                received = b""
                while b"\n" not in received:
                    part = stream.recv(65536)
                    if not part:
                        break
                    received += part
        except OSError as exc:
            raise AgentError("Cannot connect to the repository session supervisor.") from exc
        try:
            response = json.loads(received.split(b"\n", 1)[0])
        except (json.JSONDecodeError, IndexError) as exc:
            raise AgentError("Session supervisor returned a malformed response.") from exc
        if not response.get("ok"):
            raise AgentError(str(response.get("error", "Session supervisor request failed.")))
        return response.get("result", {})

    def _healthy(self) -> bool:
        try:
            return self.request("health").get("status") == "ok"
        except AgentError:
            return False

    def ensure_supervisor(self) -> None:
        if self.control_path.exists() and self._healthy():
            return
        self.run_root.mkdir(parents=True, exist_ok=True)
        with FileLock(self.run_root / "supervisor.lock"):
            if self.control_path.exists() and self._healthy():
                return
            if self.control_path.exists():
                self.control_path.unlink()
            log_path = self.run_root / "supervisor.log"
            command = [
                sys.executable, "-u", str(self.workspace_root / "agent.py"),
                "--session-supervisor", "--workspace-root", str(self.workspace_root),
            ]
            log_stream = log_path.open("a", encoding="utf-8")
            options: dict = {
                "cwd": self.workspace_root, "stdin": subprocess.DEVNULL,
                "stdout": log_stream,
                "stderr": subprocess.STDOUT, "close_fds": True,
            }
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            else:
                options["start_new_session"] = True
            try:
                subprocess.Popen(command, **options)
            finally:
                log_stream.close()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.control_path.exists() and self._healthy():
                    return
                time.sleep(0.1)
        raise AgentError(f"Session supervisor did not start. See: {self.run_root / 'supervisor.log'}")


def new_control_token() -> str:
    return secrets.token_hex(32)

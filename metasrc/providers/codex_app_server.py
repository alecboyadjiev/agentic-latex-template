from __future__ import annotations

import asyncio
from concurrent.futures import Future
import json
import queue
import subprocess
import threading
from typing import AsyncIterator, Callable

from metasrc.errors import AgentError
from metasrc.providers.base import (
    Conversation, ConversationMessage, ProviderEvent, ProviderSettings,
    ProviderThread, ProviderTurn,
)
from metasrc.providers.codex_cli import locate_codex, validate_model_settings


class CodexAppServerProvider:
    """Small provider-neutral adapter around Codex app-server JSONL stdio."""

    provider_id = "codex-app-server"

    def __init__(
        self,
        command: list[str] | None = None,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        request_timeout: float = 30.0,
    ):
        self.command = command or [locate_codex(), "app-server", "--stdio"]
        self.request_timeout = request_timeout
        self._process = process_factory(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        if self._process.stdin is None or self._process.stdout is None:
            raise AgentError("Codex app-server did not expose JSONL stdio.")
        self._write_lock = threading.Lock()
        self._pending: dict[int, Future] = {}
        self._pending_lock = threading.Lock()
        self._events: queue.Queue[ProviderEvent | None] = queue.Queue()
        self._next_id = 1
        self._closed = False
        self._cleaned = False
        self._reader = threading.Thread(target=self._read_loop, name="codex-app-server-reader", daemon=True)
        self._reader.start()
        self._stderr = threading.Thread(target=self._drain_stderr, name="codex-app-server-stderr", daemon=True)
        self._stderr.start()
        self._request(
            "initialize",
            {"clientInfo": {"name": "agentic-latex-template", "version": "1"},
             "capabilities": {"experimentalApi": True}},
        )
        self._notify("initialized")

    def _send(self, value: dict) -> None:
        if self._closed:
            raise AgentError("Codex app-server connection is closed.")
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        try:
            with self._write_lock:
                assert self._process.stdin is not None
                self._process.stdin.write(encoded + "\n")
                self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AgentError("Codex app-server connection failed.") from exc

    def _notify(self, method: str, params: dict | None = None) -> None:
        value = {"method": method}
        if params is not None:
            value["params"] = params
        self._send(value)

    def _request(self, method: str, params: dict) -> dict:
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            future: Future = Future()
            self._pending[request_id] = future
        try:
            self._send({"id": request_id, "method": method, "params": params})
            response = future.result(timeout=self.request_timeout)
        except TimeoutError as exc:
            raise AgentError(f"Codex app-server timed out during {method}.") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in response:
            error = response["error"]
            detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise AgentError(f"Codex app-server {method} failed: {detail}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise AgentError(f"Codex app-server returned a malformed {method} response.")
        return result

    def _read_loop(self) -> None:
        assert self._process.stdout is not None
        try:
            for raw in self._process.stdout:
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    self._events.put(ProviderEvent("error", "", message="Malformed app-server JSONL"))
                    continue
                if "id" in value and ("result" in value or "error" in value) and "method" not in value:
                    with self._pending_lock:
                        future = self._pending.get(value["id"])
                    if future is not None and not future.done():
                        future.set_result(value)
                    continue
                if "id" in value and "method" in value:
                    self._send({"id": value["id"], "error": {
                        "code": -32601, "message": "Server-initiated requests are unsupported"
                    }})
                    continue
                event = self._convert_event(value)
                if event is not None:
                    self._events.put(event)
        finally:
            self._closed = True
            error = AgentError("Codex app-server exited unexpectedly.")
            with self._pending_lock:
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(error)
            self._events.put(ProviderEvent("error", "", message=str(error)))
            self._events.put(None)

    def _drain_stderr(self) -> None:
        if self._process.stderr is not None:
            for _ in self._process.stderr:
                pass

    @staticmethod
    def _convert_event(value: dict) -> ProviderEvent | None:
        method, params = value.get("method"), value.get("params", {})
        if not isinstance(params, dict):
            return None
        thread_id = str(params.get("threadId", ""))
        if method == "turn/started":
            turn = params.get("turn", {})
            return ProviderEvent("turn_started", thread_id, str(turn.get("id", "")),
                                 str(turn.get("status", "inProgress")), payload=params)
        if method == "turn/completed":
            turn = params.get("turn", {})
            message = CodexAppServerProvider._last_agent_message(turn)
            return ProviderEvent("turn_completed", thread_id, str(turn.get("id", "")),
                                 str(turn.get("status", "failed")), message, params)
        if method == "item/reasoning/summaryTextDelta":
            return ProviderEvent("progress", thread_id, str(params.get("turnId", "")),
                                 message=str(params.get("delta", "")), payload=params)
        if method == "error":
            return ProviderEvent("error", thread_id, message=str(params.get("message", method)),
                                 payload=params)
        return None

    @staticmethod
    def _last_agent_message(turn: dict) -> str | None:
        for item in reversed(turn.get("items", [])):
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = item.get("text")
                return text if isinstance(text, str) else None
        return None

    @staticmethod
    def _thread_params(settings: ProviderSettings) -> dict:
        validate_model_settings(settings.model, settings.reasoning)
        return {
            "model": settings.model,
            "cwd": str(settings.working_directory),
            "approvalPolicy": "never",
            "sandbox": "workspace-write",
            "runtimeWorkspaceRoots": [str(path) for path in settings.writable_roots],
        }

    @staticmethod
    def _turn_params(settings: ProviderSettings) -> dict:
        validate_model_settings(settings.model, settings.reasoning)
        return {
            "model": settings.model,
            "effort": settings.reasoning,
            "summary": "concise",
            "cwd": str(settings.working_directory),
            "approvalPolicy": "never",
            "sandboxPolicy": {
                "type": "workspaceWrite", "networkAccess": False,
                "writableRoots": [str(path) for path in settings.writable_roots],
            },
        }

    def start_thread(self, settings: ProviderSettings) -> ProviderThread:
        params = self._thread_params(settings)
        params["ephemeral"] = False
        result = self._request("thread/start", params)
        thread = result.get("thread", {})
        if not isinstance(thread.get("id"), str):
            raise AgentError("Codex thread/start did not return a thread ID.")
        return ProviderThread(thread["id"], str(thread.get("status", "idle")))

    def resume_thread(self, provider_thread_id: str, settings: ProviderSettings) -> ProviderThread:
        params = self._thread_params(settings)
        params["threadId"] = provider_thread_id
        result = self._request("thread/resume", params)
        thread = result.get("thread", {})
        if not isinstance(thread.get("id"), str):
            raise AgentError("Codex thread/resume did not return a thread ID.")
        return ProviderThread(thread["id"], str(thread.get("status", "idle")))

    def read_thread(self, provider_thread_id: str, include_turns: bool = True) -> Conversation:
        result = self._request(
            "thread/read", {"threadId": provider_thread_id, "includeTurns": include_turns}
        )
        thread = result.get("thread", {})
        messages: list[ConversationMessage] = []
        active: str | None = None
        latest_turn_id: str | None = None
        latest_turn_status: str | None = None
        latest_agent_message: str | None = None
        for turn in thread.get("turns", []):
            turn_id = str(turn.get("id", ""))
            latest_turn_id = turn_id
            latest_turn_status = str(turn.get("status", ""))
            if turn.get("status") == "inProgress":
                active = turn_id
            for item in turn.get("items", []):
                if item.get("type") == "userMessage":
                    text = "".join(
                        part.get("text", "") for part in item.get("content", [])
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                    messages.append(ConversationMessage("user", text, turn_id))
                elif item.get("type") == "agentMessage":
                    latest_agent_message = str(item.get("text", ""))
                    messages.append(ConversationMessage("agent", latest_agent_message, turn_id))
        return Conversation(
            provider_thread_id, tuple(messages), active, latest_turn_id,
            latest_turn_status, latest_agent_message,
        )

    def start_turn(
        self, provider_thread_id: str, message: str, settings: ProviderSettings,
        output_schema: dict,
    ) -> ProviderTurn:
        params = self._turn_params(settings)
        params.update({
            "threadId": provider_thread_id,
            "input": [{"type": "text", "text": message}],
            "outputSchema": output_schema,
        })
        result = self._request("turn/start", params)
        turn = result.get("turn", {})
        if not isinstance(turn.get("id"), str):
            raise AgentError("Codex turn/start did not return a turn ID.")
        return ProviderTurn(turn["id"], str(turn.get("status", "inProgress")))

    def steer_turn(self, provider_thread_id: str, provider_turn_id: str, message: str) -> None:
        self._request("turn/steer", {
            "threadId": provider_thread_id, "expectedTurnId": provider_turn_id,
            "input": [{"type": "text", "text": message}],
        })

    def interrupt_turn(self, provider_thread_id: str, provider_turn_id: str) -> None:
        self._request("turn/interrupt", {"threadId": provider_thread_id, "turnId": provider_turn_id})

    def delete_thread(self, provider_thread_id: str) -> None:
        self._request("thread/delete", {"threadId": provider_thread_id})

    async def events(self) -> AsyncIterator[ProviderEvent]:
        while True:
            event = await asyncio.to_thread(self._events.get)
            if event is None:
                break
            yield event

    def next_event(self, timeout: float | None = None) -> ProviderEvent | None:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._closed = True
        try:
            if self._process.stdin and not self._process.stdin.closed:
                self._process.stdin.close()
        finally:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            self._reader.join(timeout=2)
            self._stderr.join(timeout=2)
            for stream in (self._process.stdout, self._process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

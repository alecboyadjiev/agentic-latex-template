from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Literal, Protocol


@dataclass(frozen=True)
class ModelRequest:
    prompt: str
    model: str
    reasoning: str
    workspace_root: Path
    working_directory: Path
    writable_roots: tuple[Path, ...]
    output_schema: dict | None
    run_directory: Path
    stream_output: bool
    enable_event_log: bool


@dataclass(frozen=True)
class ModelResponse:
    final_text: str
    provider_id: str
    model: str
    exit_code: int
    stdout_path: Path
    stderr_path: Path
    events_path: Path | None


class ModelProvider(Protocol):
    provider_id: str

    def validate(self, request: ModelRequest) -> None: ...
    def invoke(self, request: ModelRequest) -> ModelResponse: ...


@dataclass(frozen=True)
class ProviderSettings:
    model: str
    reasoning: str
    workspace_root: Path
    working_directory: Path
    writable_roots: tuple[Path, ...]


@dataclass(frozen=True)
class ProviderThread:
    id: str
    status: str = "idle"


@dataclass(frozen=True)
class ProviderTurn:
    id: str
    status: str = "inProgress"


@dataclass(frozen=True)
class ConversationMessage:
    role: Literal["user", "agent"]
    text: str
    turn_id: str | None = None


@dataclass(frozen=True)
class Conversation:
    thread_id: str
    messages: tuple[ConversationMessage, ...]
    active_turn_id: str | None = None
    latest_turn_id: str | None = None
    latest_turn_status: str | None = None
    latest_agent_message: str | None = None


@dataclass(frozen=True)
class ProviderEvent:
    kind: Literal["turn_started", "turn_completed", "progress", "error"]
    thread_id: str
    turn_id: str | None = None
    status: str | None = None
    message: str | None = None
    payload: dict | None = None


class InteractiveProvider(Protocol):
    provider_id: str

    def start_thread(self, settings: ProviderSettings) -> ProviderThread: ...
    def resume_thread(self, provider_thread_id: str, settings: ProviderSettings) -> ProviderThread: ...
    def read_thread(self, provider_thread_id: str, include_turns: bool = True) -> Conversation: ...
    def start_turn(
        self, provider_thread_id: str, message: str, settings: ProviderSettings,
        output_schema: dict,
    ) -> ProviderTurn: ...
    def steer_turn(self, provider_thread_id: str, provider_turn_id: str, message: str) -> None: ...
    def interrupt_turn(self, provider_thread_id: str, provider_turn_id: str) -> None: ...
    def delete_thread(self, provider_thread_id: str) -> None: ...
    def events(self) -> AsyncIterator[ProviderEvent]: ...
    def close(self) -> None: ...

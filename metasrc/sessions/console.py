from __future__ import annotations

from datetime import datetime
import os
import shutil
import sys
import time
from typing import Callable, TextIO


def _workspace_label(value: str) -> str:
    path = value.rstrip("\\/")
    name = os.path.basename(path)
    return "main" if name == "agentic-latex-template" else f"worktree: {name}"


def render_menu(snapshot: dict, *, unicode: bool | None = None, width: int | None = None) -> str:
    sessions = snapshot.get("sessions", snapshot)
    if not sessions:
        return "Living agents: none"
    rows = []
    for short_id in sorted(sessions, key=lambda value: int(value)):
        row = sessions[short_id]
        updated = str(row.get("updated_at", ""))
        try:
            updated = datetime.fromisoformat(updated).astimezone().strftime("%H:%M:%S")
        except ValueError:
            updated = updated[-8:] or "-"
        workspace = _workspace_label(str(row.get("workspace", "")))
        rows.append([short_id, str(row.get("definition_id", "")), str(row.get("status", "")), updated, workspace])
    headings = ["ID", "Agent", "Status", "Updated", "Workspace"]
    widths = [max(len(headings[i]), *(len(row[i]) for row in rows)) for i in range(5)]
    terminal_width = width or shutil.get_terminal_size((100, 24)).columns
    fixed = sum(widths[:4]) + 3 * 5 + 1
    widths[4] = max(9, min(widths[4], terminal_width - fixed))
    for row in rows:
        if len(row[4]) > widths[4]:
            row[4] = row[4][:max(1, widths[4] - 3)] + "..."
    if unicode is None:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        try:
            "┌─┐".encode(encoding)
            unicode = True
        except UnicodeEncodeError:
            unicode = False
    chars = ("┌", "┬", "┐", "├", "┼", "┤", "└", "┴", "┘", "─", "│") if unicode else (
        "+", "+", "+", "+", "+", "+", "+", "+", "+", "-", "|"
    )
    tl, tj, tr, ml, mj, mr, bl, bj, br, hz, vt = chars
    border = lambda left, join, right: left + join.join(hz * (size + 2) for size in widths) + right
    line = lambda cells: vt + vt.join(f" {cell:<{widths[i]}} " for i, cell in enumerate(cells)) + vt
    return "\n".join([
        "Living agents", border(tl, tj, tr), line(headings), border(ml, mj, mr),
        *(line(row) for row in rows), border(bl, bj, br),
    ])


def render_transcript(messages: list[dict], stream: TextIO = sys.stdout) -> None:
    for message in messages:
        role = "You" if message.get("role") == "user" else "Agent"
        stream.write(f"\n{role}:\n{message.get('text', '')}\n")
    stream.flush()


def _clear_live_line(stream: TextIO, width: int) -> None:
    if width > 0:
        stream.write("\r" + " " * width + "\r")


def _emit_progress_update(
    stream: TextIO, previous: str, current: str, live_width: int,
) -> tuple[str, int]:
    if current == previous:
        return previous, live_width
    if current.startswith(previous):
        addition = current[len(previous):]
    else:
        addition = current
    _clear_live_line(stream, live_width)
    stream.write(addition)
    if addition and not addition.endswith("\n"):
        stream.write("\n")
    return current, 0


def _read_tty_key() -> str | None:
    if os.name == "nt":
        import msvcrt
        return msvcrt.getwch() if msvcrt.kbhit() else None
    import select
    return sys.stdin.read(1) if select.select([sys.stdin], [], [], 0)[0] else None


def _live_display(status: str, dots: int, chars: list[str]) -> str:
    entered = "".join(chars)
    if status == "executing":
        return "." * dots + " working" + (f" > {entered}" if entered else "")
    return "> " + entered


def _read_tty_line(
    status: Callable[[], dict], stream: TextIO, *,
    key_reader: Callable[[], str | None] | None = None,
    status_interval: float = 0.5, input_interval: float = 0.01,
) -> tuple[str | None, dict]:
    chars: list[str] = []
    dots = 0
    displayed_progress = ""
    live_width = 0
    key_reader = key_reader or _read_tty_key
    latest = status()
    now = time.monotonic()
    next_status = now + status_interval
    next_animation = now
    redraw = True
    while True:
        if latest.get("closed"):
            _clear_live_line(stream, live_width)
            stream.flush()
            return None, latest

        while True:
            character = key_reader()
            if character is None:
                break
            if character in {"\r", "\n"}:
                display = _live_display(str(latest.get("status", "")), dots, chars)
                _clear_live_line(stream, live_width)
                stream.write(display)
                stream.write("\n")
                return "".join(chars), latest
            if character in {"\b", "\x7f"}:
                if chars:
                    chars.pop()
            elif character == "\x03":
                raise KeyboardInterrupt
            else:
                chars.append(character)
            redraw = True

        now = time.monotonic()
        if now >= next_status:
            latest = status()
            next_status = now + status_interval
            redraw = True
            if latest.get("closed"):
                continue

        progress = str(latest.get("progress") or "")
        previous_progress = displayed_progress
        displayed_progress, live_width = _emit_progress_update(
            stream, displayed_progress, progress, live_width,
        )
        if displayed_progress != previous_progress:
            redraw = True

        if latest.get("status") == "executing" and now >= next_animation:
            dots = dots % 3 + 1
            next_animation = now + status_interval
            redraw = True

        if redraw:
            display = _live_display(str(latest.get("status", "")), dots, chars)
            _clear_live_line(stream, live_width)
            stream.write(display)
            live_width = len(display)
            stream.flush()
            redraw = False

        time.sleep(input_interval)


def run_console(client, short_id: int, *, input_stream: TextIO = sys.stdin,
                output_stream: TextIO = sys.stdout) -> int:
    opened = client.request("open", short_id=short_id)
    session_key = opened.get("session_key")
    render_transcript(opened.get("transcript", []), output_stream)
    for note in opened.get("notes", []):
        output_stream.write(f"\nHandler: {note}\n")
    output_stream.flush()

    def status() -> dict:
        try:
            return client.request("open", short_id=short_id, session_key=session_key)
        except Exception:
            return {"closed": True}

    interactive = input_stream.isatty() and output_stream.isatty()
    while True:
        current = status()
        if current.get("closed"):
            if current.get("final_message"):
                output_stream.write(f"\nAgent:\n{current['final_message']}\n")
            output_stream.write("\nSession closed automatically.\n")
            break
        if interactive:
            line, current = _read_tty_line(status, output_stream)
            if line is None:
                break
        else:
            line = input_stream.readline()
            if line == "":
                break
            line = line.rstrip("\r\n")
        if not line.strip():
            continue
        command = line.strip()
        if command == "/exit":
            break
        if command == "/interrupt":
            result = client.request("interrupt", short_id=short_id)
        elif command == "/delete":
            if interactive:
                answer = input("Permanently delete this session and chat? [y/N]: ").strip().lower()
            else:
                output_stream.write("Permanently delete this session and chat? [y/N]: ")
                output_stream.flush()
                answer = input_stream.readline().strip().lower()
            if answer not in {"y", "yes"}:
                continue
            result = client.request("delete", short_id=short_id)
        else:
            result = client.request("message", short_id=short_id, message=line)
        if result.get("message"):
            output_stream.write(str(result["message"]) + "\n")
            output_stream.flush()
        if result.get("closed"):
            break
    output_stream.write(render_menu(client.request("list")["menu"]) + "\n")
    output_stream.flush()
    return 0

"""Qt bridge for the bundled Rust terminal emulator.

The helper owns the PTY and VT state.  Qt deliberately remains responsible for
the application-facing parts of the terminal: tab UI, text selection,
clipboard, drag/drop, and terminal output references.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

from aichs_native import binary_path
from config import MAX_STORED_TERMINAL_OUTPUT_CHARS
from services.terminal_refs import retain_terminal_output_tail, selection_capture_key
from services.tools import _shell_env, _strip_ansi


_ROOT = Path(__file__).resolve().parents[1]
MAX_INTEGRATED_TERMINAL_LINES = 200_000


def native_terminal_path() -> Path | None:
    """Locate the packaged helper, or the release binary during development."""

    executable = "aichs-terminal.exe" if sys.platform == "win32" else "aichs-terminal"
    platform = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    roots = []
    if getattr(sys, "_MEIPASS", ""):
        roots.append(Path(sys._MEIPASS))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(_ROOT)
    relative_paths = (
        Path("bin") / executable,
        Path("tools") / "vendor" / "aichs-terminal" / platform / executable,
        Path("rust") / "aichs-terminal" / "target" / "release" / executable,
    )
    for root in roots:
        for relative in relative_paths:
            candidate = root / relative
            if candidate.is_file():
                return candidate
    return binary_path(executable)


class NativeTerminalSession(QObject):
    """One interactive shell backed by the native terminal helper."""

    output = pyqtSignal(str)
    status = pyqtSignal(str)
    frame = pyqtSignal(object)
    started = pyqtSignal()
    finished = pyqtSignal(object)

    def __init__(self, cwd: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd or os.getcwd()
        self.terminal_id = uuid.uuid4().hex[:8]
        self.program = ""
        self.args: list[str] = []
        self.label = "terminal"
        self._process: QProcess | None = None
        self._stdout_buffer = b""
        self._started_at = 0.0
        self._output_text = ""
        self._line_count = 0
        self._truncated = False
        self._finished = False
        self._columns = 120
        self._lines = 30
        self._selection_captures: dict[str, str] = {}
        self._display_offset = 0
        self._scroll_supported = True

    @staticmethod
    def available() -> bool:
        return native_terminal_path() is not None

    def set_size(self, columns: int, lines: int) -> None:
        self._columns = max(1, int(columns))
        self._lines = max(1, int(lines))

    def start(self) -> None:
        if self._process is not None:
            return
        binary = native_terminal_path()
        if binary is None:
            self._emit_error("native terminal helper is unavailable")
            return
        self.program, self.args, self.label = interactive_shell_command()
        self._started_at = time.monotonic()
        process = QProcess(self)
        process.setWorkingDirectory(self.cwd)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        for key, value in integrated_terminal_env().items():
            env.insert(str(key), str(value))
        process.setProcessEnvironment(env)
        process.started.connect(self._start_helper)
        process.readyReadStandardOutput.connect(self._read_ready_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        process.start(str(binary), [])

    def _start_helper(self) -> None:
        self._send(
            {
                "type": "start",
                "cwd": self.cwd,
                "program": self.program,
                "args": self.args,
                "columns": self._columns,
                "lines": self._lines,
            }
        )

    def scroll(self, delta: int) -> None:
        amount = int(delta)
        if amount == 0 or not self._scroll_supported:
            return
        self._send({"type": "scroll", "delta": amount})

    def scroll_to_bottom(self) -> None:
        if not self._scroll_supported or self._display_offset <= 0:
            return
        self._send({"type": "scroll", "to_bottom": True})

    def write(self, text: str) -> None:
        self.scroll_to_bottom()
        self._send({"type": "input", "data": _encode(str(text or ""))})

    def resize(self, columns: int, lines: int) -> None:
        self.set_size(columns, lines)
        self._send({"type": "resize", "columns": self._columns, "lines": self._lines})

    def terminate(self) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._send({"type": "shutdown"})
        if process.waitForFinished(200):
            return
        process.terminate()
        if process.waitForFinished(200):
            return
        process.kill()
        process.waitForFinished(200)

    def output_text(self) -> str:
        return self._output_text.rstrip()

    def remember_selection(self, start: int, end: int, text: str) -> None:
        """Snapshot screen lines for a `#term[id:start:end]` created from the UI."""
        body = "\n".join(line.rstrip() for line in str(text or "").splitlines())
        if not body.strip():
            return
        start = max(1, int(start))
        end = max(start, int(end))
        self._selection_captures[selection_capture_key(start, end)] = body

    def result(self, exit_code: int | None = None) -> dict:
        output = self.output_text()
        payload = {
            "terminal_id": self.terminal_id,
            "command": self.label,
            "cwd": self.cwd,
            "exit_code": int(exit_code if exit_code is not None else 0),
            "duration_s": max(0.0, time.monotonic() - self._started_at) if self._started_at else 0.0,
            "line_count": self._line_count,
            "stored_line_count": len(output.splitlines()),
            "truncated": self._truncated,
            "output": output,
        }
        if self._selection_captures:
            payload["selection_captures"] = dict(self._selection_captures)
        return payload

    def _send(self, command: dict) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        data = (json.dumps(command, separators=(",", ":")) + "\n").encode("utf-8")
        process.write(data)

    def _read_ready_output(self) -> None:
        process = self._process
        if process is None:
            return
        self._stdout_buffer += bytes(process.readAllStandardOutput())
        while b"\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split(b"\n", 1)
            if not line:
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._emit_error("native terminal emitted invalid output")
                continue
            self._handle_event(event)

    def _handle_event(self, event: dict) -> None:
        kind = str(event.get("type") or "")
        if kind == "ready":
            self.started.emit()
        elif kind == "output":
            try:
                text = base64.b64decode(str(event.get("data") or ""), validate=True).decode("utf-8", errors="replace")
            except ValueError:
                self._emit_error("native terminal emitted invalid output data")
                return
            self._append_output(_strip_ansi(text))
            self.output.emit(text)
        elif kind == "frame" and isinstance(event.get("frame"), dict):
            frame = event["frame"]
            self._display_offset = max(0, int(frame.get("display_offset") or 0))
            self.frame.emit(frame)
        elif kind == "exit":
            self._finish(int(event.get("code") or 0))
        elif kind == "error":
            message = str(event.get("message") or "native terminal failed")
            if self._is_unsupported_scroll_error(message):
                self._scroll_supported = False
                return
            self._emit_error(message)

    @staticmethod
    def _is_unsupported_scroll_error(message: str) -> bool:
        text = str(message or "").casefold()
        return "unknown variant" in text and "scroll" in text

    def _append_output(self, text: str) -> None:
        self._line_count += text.count("\n")
        if text and not text.endswith("\n"):
            self._line_count += 1
        controlled = apply_terminal_output_controls(self._output_text, text)
        self._output_text, dropped = retain_terminal_output_tail("", controlled, MAX_STORED_TERMINAL_OUTPUT_CHARS)
        if dropped or len(self.output_text().splitlines()) > MAX_INTEGRATED_TERMINAL_LINES:
            self._truncated = True

    def _emit_error(self, message: str) -> None:
        text = f"[terminal error] {message}\n"
        self._append_output(text)
        self.status.emit(text.rstrip("\n"))
        self.output.emit(text)
        self._finish(1)

    def _on_error(self, _error) -> None:
        process = self._process
        if process is None or self._finished or process.state() != QProcess.ProcessState.NotRunning:
            return
        self._emit_error(process.errorString())

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        self._read_ready_output()
        self._finish(int(exit_code))

    def _finish(self, exit_code: int) -> None:
        if self._finished:
            return
        self._finished = True
        self.finished.emit(self.result(exit_code))


def _encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8", errors="replace")).decode("ascii")


def apply_terminal_output_controls(current: str, chunk: str) -> str:
    chars = list(str(current or ""))
    normalized = str(chunk or "").replace("\r\n", "\n").replace("\r", "\n")
    for char in normalized:
        if char == "\b":
            if chars:
                chars.pop()
            continue
        chars.append(char)
    return "".join(chars)


def interactive_shell_command() -> tuple[str, list[str], str]:
    if sys.platform == "win32":
        pwsh = shutil.which("pwsh")
        if pwsh:
            return pwsh, ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass"], "pwsh"
        powershell = shutil.which("powershell")
        if powershell:
            return powershell, ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass"], "powershell"
        cmd = os.environ.get("COMSPEC") or "cmd.exe"
        return cmd, ["/Q"], "cmd"
    shell = os.environ.get("SHELL") or "/bin/sh"
    name = os.path.basename(shell) or "sh"
    return (shell, ["-i"], name) if name in {"bash", "zsh", "fish", "sh"} else (shell, [], name)


def integrated_terminal_env() -> dict:
    env = _shell_env()
    for key in ("NO_COLOR", "FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE"):
        env.pop(key, None)
    if sys.platform == "darwin":
        env["TERM"] = "xterm-256color"
    return env

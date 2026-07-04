from __future__ import annotations

import locale
import os
import shutil
import sys
import time

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

from config import MAX_STORED_TERMINAL_OUTPUT_CHARS
from services.terminal_refs import retain_terminal_output_tail
from services.tools import _shell_env, _strip_ansi

MAX_INTEGRATED_TERMINAL_CHARS = MAX_STORED_TERMINAL_OUTPUT_CHARS
MAX_INTEGRATED_TERMINAL_LINES = 200_000


class TerminalSession(QObject):
    output = pyqtSignal(str)
    started = pyqtSignal()
    finished = pyqtSignal(object)

    def __init__(self, cwd: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd or os.getcwd()
        self._process: QProcess | None = None
        self._started_at = 0.0
        self._output_text = ""
        self._stored_chars = 0
        self._line_count = 0
        self._truncated = False
        self._finished = False
        self.program = ""
        self.args: list[str] = []
        self.label = "terminal"

    def start(self) -> None:
        if self._process is not None:
            return
        self.program, self.args, self.label = interactive_shell_command()
        self._started_at = time.monotonic()
        process = QProcess(self)
        process.setWorkingDirectory(self.cwd)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        for key, value in _shell_env().items():
            env.insert(str(key), str(value))
        process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self._read_ready_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        process.start(self.program, self.args)
        self.started.emit()

    def write(self, text: str) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        process.write(str(text or "").encode(_terminal_encoding(), errors="replace"))

    def terminate(self) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._request_shell_exit(process)
        if process.waitForFinished(1500):
            return
        process.terminate()
        if process.waitForFinished(1500):
            return
        process.kill()
        process.waitForFinished(500)

    def _request_shell_exit(self, process: QProcess) -> None:
        newline = "\r\n" if sys.platform == "win32" else "\n"
        try:
            process.write(f"exit{newline}".encode(_terminal_encoding(), errors="replace"))
            process.closeWriteChannel()
        except RuntimeError:
            pass

    def output_text(self) -> str:
        return self._output_text.rstrip()

    def result(self, exit_code: int | None = None) -> dict:
        output = self.output_text()
        return {
            "command": self.label,
            "cwd": self.cwd,
            "exit_code": int(exit_code if exit_code is not None else 0),
            "duration_s": max(0.0, time.monotonic() - self._started_at) if self._started_at else 0.0,
            "line_count": self._line_count,
            "stored_line_count": len(output.splitlines()),
            "truncated": self._truncated,
            "output": output,
        }

    def _read_ready_output(self) -> None:
        process = self._process
        if process is None:
            return
        data = bytes(process.readAllStandardOutput())
        if not data:
            return
        text = _strip_ansi(data.decode(_terminal_encoding(), errors="replace"))
        self._append_output(text)
        self.output.emit(text)

    def _append_output(self, text: str) -> None:
        self._line_count += text.count("\n")
        if text and not text.endswith("\n"):
            self._line_count += 1
        controlled = apply_terminal_output_controls(self._output_text, text)
        self._output_text, dropped = retain_terminal_output_tail(
            "",
            controlled,
            MAX_INTEGRATED_TERMINAL_CHARS,
        )
        self._stored_chars = len(self._output_text)
        if dropped:
            self._truncated = True
        if len(self.output_text().splitlines()) > MAX_INTEGRATED_TERMINAL_LINES:
            self._truncated = True

    def _on_error(self, error) -> None:
        process = self._process
        if process is None or self._finished:
            return
        if process.state() != QProcess.ProcessState.NotRunning:
            return
        message = f"[terminal error] {process.errorString()}\n"
        self._append_output(message)
        self.output.emit(message)
        self._finish(1)

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        self._read_ready_output()
        self._finish(int(exit_code))

    def _finish(self, exit_code: int) -> None:
        if self._finished:
            return
        self._finished = True
        self.finished.emit(self.result(exit_code))


def apply_terminal_output_controls(current: str, chunk: str) -> str:
    chars = list(str(current or ""))
    normalized = str(chunk or "").replace("\r\n", "\n").replace("\r", "\n")
    for ch in normalized:
        if ch == "\b":
            if chars:
                chars.pop()
            continue
        chars.append(ch)
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
    if name in {"bash", "zsh", "fish", "sh"}:
        return shell, ["-i"], name
    return shell, [], name


def _terminal_encoding() -> str:
    return locale.getpreferredencoding(False) or "utf-8"

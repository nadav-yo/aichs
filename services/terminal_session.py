from __future__ import annotations

import locale
import os
import shutil
import signal
import subprocess
import sys
import time

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QSocketNotifier, QTimer, pyqtSignal

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
        self._pty_process: subprocess.Popen | None = None
        self._pty_master_fd: int | None = None
        self._pty_notifier: QSocketNotifier | None = None
        self._pty_poll_timer: QTimer | None = None
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
        if self._process is not None or self._pty_process is not None:
            return
        self.program, self.args, self.label = interactive_shell_command()
        self._started_at = time.monotonic()
        if _uses_macos_pty():
            self._start_macos_pty()
            return
        process = QProcess(self)
        process.setWorkingDirectory(self.cwd)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        for key, value in integrated_terminal_env().items():
            env.insert(str(key), str(value))
        process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self._read_ready_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        process.start(self.program, self.args)
        self.started.emit()

    def write(self, text: str) -> None:
        pty_process = self._pty_process
        if pty_process is not None:
            if pty_process.poll() is None and self._pty_master_fd is not None:
                try:
                    # A PTY translates carriage return to newline.  Sending an
                    # additional LF would submit a second, empty command.
                    os.write(self._pty_master_fd, str(text or "").replace("\r\n", "\r").encode(_terminal_encoding(), errors="replace"))
                except OSError:
                    pass
            return
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        process.write(str(text or "").encode(_terminal_encoding(), errors="replace"))

    def terminate(self) -> None:
        if self._pty_process is not None:
            self._terminate_macos_pty()
            return
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._request_shell_exit(process)
        # This is called from the GUI shutdown path.  Do not make closing the
        # app wait multiple seconds for a shell that is blocked in a command.
        if process.waitForFinished(200):
            return
        process.terminate()
        if process.waitForFinished(200):
            return
        process.kill()
        process.waitForFinished(200)

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

    def _start_macos_pty(self) -> None:
        """Run macOS shells on a PTY so zsh has a real terminal to use."""
        master_fd = slave_fd = None
        try:
            import pty

            master_fd, slave_fd = pty.openpty()
            os.set_blocking(master_fd, False)
            self._pty_process = subprocess.Popen(
                [self.program, *self.args],
                cwd=self.cwd,
                env=integrated_terminal_env(),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
            os.close(slave_fd)
            slave_fd = None
            self._pty_master_fd = master_fd
            master_fd = None
            self._pty_notifier = QSocketNotifier(self._pty_master_fd, QSocketNotifier.Type.Read, self)
            self._pty_notifier.activated.connect(self._read_pty_output)
            self._pty_poll_timer = QTimer(self)
            self._pty_poll_timer.setInterval(100)
            self._pty_poll_timer.timeout.connect(self._poll_pty_process)
            self._pty_poll_timer.start()
            self.started.emit()
        except OSError as exc:
            if slave_fd is not None:
                os.close(slave_fd)
            if master_fd is not None:
                os.close(master_fd)
            message = f"[terminal error] {exc}\n"
            self._append_output(message)
            self.output.emit(message)
            self._finish(1)

    def _read_pty_output(self, *_args) -> None:
        if self._pty_master_fd is None:
            return
        chunks = []
        while True:
            try:
                data = os.read(self._pty_master_fd, 8192)
            except BlockingIOError:
                break
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            if len(data) < 8192:
                break
        if not chunks:
            return
        text = _strip_ansi(b"".join(chunks).decode(_terminal_encoding(), errors="replace"))
        self._append_output(text)
        self.output.emit(text)

    def _poll_pty_process(self) -> None:
        process = self._pty_process
        if process is None or process.poll() is None:
            return
        self._read_pty_output()
        exit_code = int(process.returncode or 0)
        self._cleanup_pty()
        self._finish(exit_code)

    def _terminate_macos_pty(self) -> None:
        process = self._pty_process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=0.2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=0.2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._read_pty_output()
        exit_code = int(process.returncode if process.returncode is not None else 1)
        self._cleanup_pty()
        self._finish(exit_code)

    def _cleanup_pty(self) -> None:
        if self._pty_poll_timer is not None:
            self._pty_poll_timer.stop()
            self._pty_poll_timer.deleteLater()
            self._pty_poll_timer = None
        if self._pty_notifier is not None:
            self._pty_notifier.setEnabled(False)
            self._pty_notifier.deleteLater()
            self._pty_notifier = None
        if self._pty_master_fd is not None:
            try:
                os.close(self._pty_master_fd)
            except OSError:
                pass
            self._pty_master_fd = None
        self._pty_process = None

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


def integrated_terminal_env() -> dict:
    """Environment for the lightweight terminal renderer.

    It renders plain text rather than emulating a complete VT terminal.  The
    dumb terminal declaration keeps zsh and command-line tools from entering
    alternate-screen/cursor modes that cannot be represented in the widget.
    """
    env = _shell_env()
    if sys.platform == "darwin":
        env["TERM"] = "dumb"
    return env


def _uses_macos_pty() -> bool:
    return sys.platform == "darwin"


def _terminal_encoding() -> str:
    return locale.getpreferredencoding(False) or "utf-8"

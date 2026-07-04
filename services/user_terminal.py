from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from config import MAX_STORED_TERMINAL_OUTPUT_CHARS
from services.subprocess_utils import popen_no_window
from services.terminal_refs import build_terminal_summary, retain_terminal_output_tail
from services.tools import _shell_command_args, _shell_env, _strip_ansi

MAX_USER_TERMINAL_OUTPUT_CHARS = MAX_STORED_TERMINAL_OUTPUT_CHARS
MAX_USER_TERMINAL_OUTPUT_LINES = 200_000


class UserTerminalThread(QThread):
    line = pyqtSignal(str)
    done = pyqtSignal(object)

    def __init__(self, command: str, cwd: str, parent=None):
        super().__init__(parent)
        self.command = command
        self.cwd = cwd
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        result = run_user_terminal_command(
            self.command,
            self.cwd,
            on_line=self.line.emit,
            cancel=self._cancel,
        )
        result["summary"] = build_terminal_summary(result)
        self.done.emit(result)


def run_user_terminal_command(
    command: str,
    cwd: str,
    *,
    on_line: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    started = time.monotonic()
    output = ""
    line_count = 0
    truncated = False
    proc = None
    exit_code = 1

    try:
        proc = popen_no_window(
            _shell_command_args(command),
            cwd=cwd,
            env=_shell_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        seen_output = False
        for raw_line in proc.stdout:
            line = _strip_ansi(raw_line)
            if cancel and cancel.is_set():
                proc.kill()
                break
            if not seen_output and not line.strip():
                continue
            seen_output = True
            line_count += 1
            if on_line:
                on_line(line.rstrip("\n"))
            output, dropped = retain_terminal_output_tail(
                output,
                line,
                MAX_USER_TERMINAL_OUTPUT_CHARS,
            )
            if dropped:
                truncated = True
            if len(output.splitlines()) > MAX_USER_TERMINAL_OUTPUT_LINES:
                kept = output.splitlines(True)[-MAX_USER_TERMINAL_OUTPUT_LINES:]
                output = "".join(kept)
                truncated = True
        proc.wait(timeout=5)
        exit_code = int(proc.returncode or 0)
    except Exception as exc:
        message = f"[terminal error] {exc}\n"
        output, dropped = retain_terminal_output_tail(output, message, MAX_USER_TERMINAL_OUTPUT_CHARS)
        truncated = truncated or dropped
        line_count += 1
        if on_line:
            on_line(message.rstrip("\n"))
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()

    output = output.rstrip()
    if cancel and cancel.is_set():
        output = (output + "\n[cancelled]").strip()
    return {
        "command": command,
        "cwd": cwd,
        "exit_code": exit_code,
        "duration_s": time.monotonic() - started,
        "line_count": line_count,
        "stored_line_count": len(output.splitlines()),
        "truncated": truncated,
        "output": output,
    }

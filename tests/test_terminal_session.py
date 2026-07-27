import sys
import types

from PyQt6.QtCore import QProcess

import services.terminal_session as terminal_session
from services.terminal_session import (
    TerminalSession,
    apply_terminal_output_controls,
    integrated_terminal_env,
    interactive_shell_command,
)


class _FakeProcess:
    def __init__(self, waits):
        self._waits = list(waits)
        self.writes = []
        self.closed = False
        self.terminated = False
        self.killed = False

    def state(self):
        return QProcess.ProcessState.Running

    def write(self, data):
        self.writes.append(bytes(data))

    def closeWriteChannel(self):
        self.closed = True

    def waitForFinished(self, _ms):
        return self._waits.pop(0) if self._waits else False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_terminal_session_terminate_sends_exit_before_terminate(qapp):
    session = TerminalSession("C:/repo")
    process = _FakeProcess([True])
    session._process = process

    session.terminate()

    assert process.writes
    assert process.writes[0].startswith(b"exit")
    assert process.closed is True
    assert process.terminated is False
    assert process.killed is False


def test_terminal_session_terminate_falls_back_to_terminate_then_kill(qapp):
    session = TerminalSession("C:/repo")
    process = _FakeProcess([False, False])
    session._process = process

    session.terminate()

    assert process.closed is True
    assert process.terminated is True
    assert process.killed is True


def test_terminal_session_exit_sequence_matches_platform(qapp):
    session = TerminalSession("C:/repo")
    process = _FakeProcess([True])
    session._process = process

    session.terminate()

    expected = b"exit\r\n" if sys.platform == "win32" else b"exit\n"
    assert process.writes[0] == expected


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class _PtyNotifier:
    class Type:
        Read = "read"

    def __init__(self, fd, mode, parent):
        self.fd = fd
        self.mode = mode
        self.parent = parent
        self.activated = _FakeSignal()
        self.enabled = True
        self.deleted = False

    def setEnabled(self, enabled):
        self.enabled = enabled

    def deleteLater(self):
        self.deleted = True


class _PtyTimer:
    def __init__(self, parent):
        self.parent = parent
        self.timeout = _FakeSignal()
        self.interval = None
        self.running = False
        self.deleted = False

    def setInterval(self, interval):
        self.interval = interval

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def deleteLater(self):
        self.deleted = True


class _PtyProcess:
    def __init__(self, _args, **kwargs):
        self.kwargs = kwargs
        self.pid = 1234
        self.returncode = None
        self.running = True
        self.wait_results = []

    def poll(self):
        return None if self.running else self.returncode

    def wait(self, timeout=None):
        result = self.wait_results.pop(0) if self.wait_results else self.returncode
        if isinstance(result, Exception):
            raise result
        self.returncode = result
        self.running = False
        return result


class _StartProcess:
    class ProcessChannelMode:
        MergedChannels = "merged"

    instances = []

    def __init__(self, parent=None):
        self.parent = parent
        self.readyReadStandardOutput = _FakeSignal()
        self.finished = _FakeSignal()
        self.errorOccurred = _FakeSignal()
        self.cwd = ""
        self.channel_mode = None
        self.env = None
        self.started = None
        _StartProcess.instances.append(self)

    def setWorkingDirectory(self, cwd):
        self.cwd = cwd

    def setProcessChannelMode(self, mode):
        self.channel_mode = mode

    def setProcessEnvironment(self, env):
        self.env = env

    def start(self, program, args):
        self.started = (program, list(args))


class _OutputProcess:
    def __init__(self, chunks=None, state=QProcess.ProcessState.Running, error="boom"):
        self.chunks = list(chunks or [])
        self._state = state
        self._error = error

    def state(self):
        return self._state

    def readAllStandardOutput(self):
        return self.chunks.pop(0) if self.chunks else b""

    def errorString(self):
        return self._error


def test_terminal_session_start_wires_process(monkeypatch, qapp):
    _StartProcess.instances = []
    monkeypatch.setattr(terminal_session, "QProcess", _StartProcess)
    monkeypatch.setattr(terminal_session, "interactive_shell_command", lambda: ("shell.exe", ["-i"], "fake"))
    monkeypatch.setattr(terminal_session, "_shell_env", lambda: {"AICHS_TEST_ENV": "1"})
    session = TerminalSession("C:/repo")
    started = []
    session.started.connect(lambda: started.append(True))

    session.start()
    session.start()

    process = _StartProcess.instances[0]
    assert len(_StartProcess.instances) == 1
    assert process.cwd == "C:/repo"
    assert process.channel_mode == _StartProcess.ProcessChannelMode.MergedChannels
    assert process.started == ("shell.exe", ["-i"])
    assert process.readyReadStandardOutput.callbacks == [session._read_ready_output]
    assert process.finished.callbacks == [session._on_finished]
    assert process.errorOccurred.callbacks == [session._on_error]
    assert session.program == "shell.exe"
    assert session.args == ["-i"]
    assert session.label == "fake"
    assert started == [True]


def test_terminal_session_macos_pty_echoes_input_and_drains_output(monkeypatch, qapp):
    monkeypatch.setattr(terminal_session, "_uses_macos_pty", lambda: True)
    monkeypatch.setattr(terminal_session, "interactive_shell_command", lambda: ("/bin/zsh", ["-i"], "zsh"))
    monkeypatch.setattr(terminal_session, "QSocketNotifier", _PtyNotifier)
    monkeypatch.setattr(terminal_session, "QTimer", _PtyTimer)
    monkeypatch.setattr(terminal_session.subprocess, "Popen", _PtyProcess)
    monkeypatch.setattr(terminal_session.os, "set_blocking", lambda *_args: None, raising=False)
    closed = []
    written = []
    monkeypatch.setattr(terminal_session.os, "close", closed.append)
    monkeypatch.setattr(terminal_session.os, "write", lambda fd, data: written.append((fd, data)))
    reads = iter([b"\x1b[?1h\x1b=ready\r\n", BlockingIOError()])

    def fake_read(*_args):
        item = next(reads)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(terminal_session.os, "read", fake_read)
    monkeypatch.setitem(sys.modules, "pty", types.SimpleNamespace(openpty=lambda: (41, 42)))
    session = TerminalSession("/repo")
    started = []
    emitted = []
    session.started.connect(lambda: started.append(True))
    session.output.connect(emitted.append)

    session.start()
    session.write("echo ok\r\n")
    session._read_pty_output()

    assert started == [True]
    assert session._pty_process.kwargs["stdin"] == 42
    assert session._pty_process.kwargs["start_new_session"] is True
    assert written == [(41, b"echo ok\r")]
    assert emitted == ["ready\r\n"]
    assert 42 in closed
    process = session._pty_process
    process.returncode = 0
    process.running = False
    session._poll_pty_process()
    assert session._pty_process is None
    assert 41 in closed


def test_terminal_session_macos_pty_terminate_kills_the_process_group(monkeypatch, qapp):
    session = TerminalSession("/repo")
    process = _PtyProcess([], cwd="/repo")
    process.wait_results = [terminal_session.subprocess.TimeoutExpired("zsh", 0.2), -9]
    session._pty_process = process
    session._pty_master_fd = 41
    signals = []
    monkeypatch.setattr(terminal_session.signal, "SIGTERM", 15, raising=False)
    monkeypatch.setattr(terminal_session.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(terminal_session.os, "killpg", lambda pid, sig: signals.append((pid, sig)), raising=False)
    monkeypatch.setattr(terminal_session.os, "read", lambda *_args: (_ for _ in ()).throw(BlockingIOError()))
    monkeypatch.setattr(terminal_session.os, "close", lambda _fd: None)

    session.terminate()

    assert signals == [
        (1234, terminal_session.signal.SIGTERM),
        (1234, terminal_session.signal.SIGKILL),
    ]
    assert session._pty_process is None


def test_terminal_session_write_encodes_running_process(qapp):
    session = TerminalSession("C:/repo")
    process = _FakeProcess([True])
    session._process = process

    session.write("abc")
    session.write("")

    assert process.writes == [b"abc", b""]


def test_terminal_session_write_ignores_missing_or_stopped_process(qapp):
    session = TerminalSession("C:/repo")
    session.write("ignored")
    process = _OutputProcess(state=QProcess.ProcessState.NotRunning)
    process.write = lambda data: (_ for _ in ()).throw(AssertionError("should not write"))
    session._process = process

    session.write("ignored")


def test_terminal_session_reads_strips_and_emits_output(qapp):
    session = TerminalSession("C:/repo")
    session._process = _OutputProcess([b"\x1b[31mred\x1b[0m\n"])
    emitted = []
    session.output.connect(emitted.append)

    session._read_ready_output()

    assert emitted == ["red\n"]
    assert session.output_text() == "red"
    assert session.result()["line_count"] == 1


def test_terminal_session_read_ignores_missing_or_empty_process(qapp):
    session = TerminalSession("C:/repo")
    session._read_ready_output()
    session._process = _OutputProcess([b""])
    session._read_ready_output()

    assert session.output_text() == ""


def test_terminal_session_result_tracks_duration_and_lines(monkeypatch, qapp):
    session = TerminalSession("C:/repo")
    session.label = "pwsh"
    session._started_at = 2.0
    monkeypatch.setattr(terminal_session.time, "monotonic", lambda: 5.25)

    session._append_output("one\ntwo\n")
    result = session.result(7)

    assert result["command"] == "pwsh"
    assert result["cwd"] == "C:/repo"
    assert result["exit_code"] == 7
    assert result["duration_s"] == 3.25
    assert result["line_count"] == 2
    assert result["stored_line_count"] == 2
    assert result["output"] == "one\ntwo"


def test_terminal_session_append_output_truncates_by_chars(monkeypatch, qapp):
    monkeypatch.setattr(terminal_session, "MAX_INTEGRATED_TERMINAL_CHARS", 10)
    session = TerminalSession("C:/repo")

    session._append_output("1234567890abc\nnext")
    session._append_output("ignored")

    assert session.output_text() == "extignored"
    assert session.result()["line_count"] == 3
    assert session.result()["truncated"] is True


def test_terminal_session_append_output_marks_line_truncation(monkeypatch, qapp):
    monkeypatch.setattr(terminal_session, "MAX_INTEGRATED_TERMINAL_LINES", 1)
    session = TerminalSession("C:/repo")

    session._append_output("one\ntwo\n")

    assert session.result()["truncated"] is True


def test_terminal_session_error_finishes_once(qapp):
    session = TerminalSession("C:/repo")
    session._process = _OutputProcess(state=QProcess.ProcessState.NotRunning, error="broken")
    emitted_output = []
    emitted_finished = []
    session.output.connect(emitted_output.append)
    session.finished.connect(emitted_finished.append)

    session._on_error(None)
    session._on_error(None)

    assert emitted_output == ["[terminal error] broken\n"]
    assert len(emitted_finished) == 1
    assert emitted_finished[0]["exit_code"] == 1
    assert "broken" in emitted_finished[0]["output"]


def test_terminal_session_error_ignores_running_process(qapp):
    session = TerminalSession("C:/repo")
    session._process = _OutputProcess(state=QProcess.ProcessState.Running, error="broken")
    emitted_finished = []
    session.finished.connect(emitted_finished.append)

    session._on_error(None)

    assert emitted_finished == []
    assert session.output_text() == ""


def test_terminal_session_finish_drains_output_and_is_idempotent(qapp):
    session = TerminalSession("C:/repo")
    session._process = _OutputProcess([b"done\n"])
    emitted_finished = []
    session.finished.connect(emitted_finished.append)

    session._on_finished(4, None)
    session._finish(9)

    assert len(emitted_finished) == 1
    assert emitted_finished[0]["exit_code"] == 4
    assert emitted_finished[0]["output"] == "done"


def test_interactive_shell_command_prefers_windows_pwsh(monkeypatch):
    monkeypatch.setattr(terminal_session.sys, "platform", "win32")
    monkeypatch.setattr(terminal_session.shutil, "which", lambda name: f"C:/{name}.exe" if name == "pwsh" else None)

    assert interactive_shell_command() == ("C:/pwsh.exe", ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass"], "pwsh")


def test_interactive_shell_command_falls_back_to_windows_powershell(monkeypatch):
    monkeypatch.setattr(terminal_session.sys, "platform", "win32")
    monkeypatch.setattr(terminal_session.shutil, "which", lambda name: f"C:/{name}.exe" if name == "powershell" else None)

    assert interactive_shell_command() == ("C:/powershell.exe", ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass"], "powershell")


def test_interactive_shell_command_falls_back_to_cmd(monkeypatch):
    monkeypatch.setattr(terminal_session.sys, "platform", "win32")
    monkeypatch.setattr(terminal_session.shutil, "which", lambda _name: None)
    monkeypatch.setenv("COMSPEC", "C:/Windows/System32/cmd.exe")

    assert interactive_shell_command() == ("C:/Windows/System32/cmd.exe", ["/Q"], "cmd")


def test_interactive_shell_command_uses_posix_interactive_shell(monkeypatch):
    monkeypatch.setattr(terminal_session.sys, "platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/bash")

    assert interactive_shell_command() == ("/bin/bash", ["-i"], "bash")


def test_interactive_shell_command_uses_posix_custom_shell(monkeypatch):
    monkeypatch.setattr(terminal_session.sys, "platform", "linux")
    monkeypatch.setenv("SHELL", "/opt/shells/custom")

    assert interactive_shell_command() == ("/opt/shells/custom", [], "custom")


def test_integrated_terminal_env_uses_dumb_terminal_on_macos(monkeypatch):
    monkeypatch.setattr(terminal_session.sys, "platform", "darwin")
    monkeypatch.setattr(terminal_session, "_shell_env", lambda: {"TERM": "xterm-256color", "A": "1"})

    assert integrated_terminal_env() == {"TERM": "dumb", "A": "1"}


def test_integrated_terminal_env_preserves_other_platform_term(monkeypatch):
    monkeypatch.setattr(terminal_session.sys, "platform", "linux")
    monkeypatch.setattr(terminal_session, "_shell_env", lambda: {"TERM": "xterm-256color"})

    assert integrated_terminal_env() == {"TERM": "xterm-256color"}


def test_apply_terminal_output_controls_handles_backspace():
    assert apply_terminal_output_controls("ab", "\bc") == "ac"
    assert apply_terminal_output_controls("ab", "\b \bc") == "ac"


def test_terminal_session_output_text_applies_backspace_controls(qapp):
    session = TerminalSession("C:/repo")

    session._append_output("Write-Output ab\bc\n")

    assert session.output_text() == "Write-Output ac"


def test_apply_terminal_output_controls_normalizes_crlf():
    assert apply_terminal_output_controls("", "one\r\ntwo\r\n") == "one\ntwo\n"

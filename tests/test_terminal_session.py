import sys

from PyQt6.QtCore import QProcess

import services.terminal_session as terminal_session
from services.terminal_session import TerminalSession, apply_terminal_output_controls, interactive_shell_command


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


def test_apply_terminal_output_controls_handles_backspace():
    assert apply_terminal_output_controls("ab", "\bc") == "ac"
    assert apply_terminal_output_controls("ab", "\b \bc") == "ac"


def test_terminal_session_output_text_applies_backspace_controls(qapp):
    session = TerminalSession("C:/repo")

    session._append_output("Write-Output ab\bc\n")

    assert session.output_text() == "Write-Output ac"


def test_apply_terminal_output_controls_normalizes_crlf():
    assert apply_terminal_output_controls("", "one\r\ntwo\r\n") == "one\ntwo\n"

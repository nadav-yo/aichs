import base64
import json
from pathlib import Path

from PyQt6.QtCore import QProcess

import services.native_terminal as native_terminal
from services.native_terminal import (
    NativeTerminalSession,
    _encode,
    apply_terminal_output_controls,
    integrated_terminal_env,
    interactive_shell_command,
)


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class _Process:
    class ProcessChannelMode:
        MergedChannels = "merged"

    ProcessState = QProcess.ProcessState

    instances = []

    def __init__(self, parent=None):
        self.parent = parent
        self.started = _Signal()
        self.readyReadStandardOutput = _Signal()
        self.finished = _Signal()
        self.errorOccurred = _Signal()
        self.writes = []
        self.chunks = []
        self._state = QProcess.ProcessState.Running
        self.waits = []
        self.terminated = False
        self.killed = False
        self.error = "broken"
        _Process.instances.append(self)

    def setWorkingDirectory(self, cwd):
        self.cwd = cwd

    def setProcessChannelMode(self, mode):
        self.channel_mode = mode

    def setProcessEnvironment(self, env):
        self.env = env

    def start(self, program, args):
        self.command = (program, list(args))

    def write(self, data):
        self.writes.append(bytes(data))

    def readAllStandardOutput(self):
        return self.chunks.pop(0) if self.chunks else b""

    def state(self):
        return self._state

    def waitForFinished(self, _milliseconds):
        return self.waits.pop(0) if self.waits else False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def errorString(self):
        return self.error


def _session(monkeypatch):
    _Process.instances = []
    monkeypatch.setattr(native_terminal, "QProcess", _Process)
    monkeypatch.setattr(native_terminal, "native_terminal_path", lambda: Path("C:/tools/aichs-terminal.exe"))
    monkeypatch.setattr(native_terminal, "interactive_shell_command", lambda: ("shell.exe", ["-i"], "shell"))
    monkeypatch.setattr(native_terminal, "integrated_terminal_env", lambda: {"TERM": "xterm-256color"})
    session = NativeTerminalSession("C:/repo")
    session.start()
    process = _Process.instances[0]
    process.started.emit()
    return session, process


def _command(process, index=-1):
    return json.loads(process.writes[index].decode("utf-8"))


def test_native_terminal_starts_helper_and_sends_shell_request(monkeypatch, qapp):
    session, process = _session(monkeypatch)

    assert Path(process.command[0]) == Path("C:/tools/aichs-terminal.exe")
    assert process.command[1] == []
    assert process.cwd == "C:/repo"
    assert process.channel_mode == _Process.ProcessChannelMode.MergedChannels
    assert _command(process) == {
        "type": "start", "cwd": "C:/repo", "program": "shell.exe",
        "args": ["-i"], "columns": 120, "lines": 30,
    }
    assert session.label == "shell"


def test_native_terminal_handles_protocol_output_frame_and_exit(monkeypatch, qapp):
    session, process = _session(monkeypatch)
    started, output, frames, finished = [], [], [], []
    session.started.connect(lambda: started.append(True))
    session.output.connect(output.append)
    session.frame.connect(frames.append)
    session.finished.connect(finished.append)
    payload = base64.b64encode(b"\x1b[31mred\x1b[0m\r\n").decode("ascii")
    events = [
        {"type": "ready"},
        {"type": "output", "data": payload},
        {"type": "frame", "frame": {"columns": 1, "lines": 1, "text": "r", "spans": [], "cursor_row": 0, "cursor_column": 0}},
        {"type": "exit", "code": 7},
    ]
    process.chunks = [("\n".join(json.dumps(event) for event in events) + "\n").encode("utf-8")]

    session._read_ready_output()

    assert started == [True]
    assert output == ["\x1b[31mred\x1b[0m\r\n"]
    assert frames == [{"columns": 1, "lines": 1, "text": "r", "spans": [], "cursor_row": 0, "cursor_column": 0}]
    assert finished[0]["exit_code"] == 7
    assert finished[0]["output"] == "red"
    assert len(finished[0]["terminal_id"]) == 8


def test_native_terminal_remembers_selection_captures(monkeypatch, qapp):
    session, _process = _session(monkeypatch)

    session.remember_selection(2, 3, "screen line\nnext line  ")
    session.remember_selection(2, 3, "")
    result = session.result(0)

    assert result["selection_captures"] == {"2:3": "screen line\nnext line"}


def test_native_terminal_buffers_protocol_lines_and_resizes(monkeypatch, qapp):
    session, process = _session(monkeypatch)
    started = []
    session.started.connect(lambda: started.append(True))
    process.chunks = [b'{"type":"rea', b'dy"}\n']

    session._read_ready_output()
    session._read_ready_output()
    session.resize(80, 24)

    assert started == [True]
    assert _command(process) == {"type": "resize", "columns": 80, "lines": 24}


def test_native_terminal_writes_base64_input_and_terminates(monkeypatch, qapp):
    session, process = _session(monkeypatch)
    process.writes.clear()

    session.write("echo hello\r\n")
    process.waits = [False, False]
    session.terminate()

    assert base64.b64decode(_command(process, -2)["data"]) == b"echo hello\r\n"
    assert _command(process)["type"] == "shutdown"
    assert process.terminated is True
    assert process.killed is True


def test_native_terminal_scroll_commands(monkeypatch, qapp):
    session, process = _session(monkeypatch)
    process.writes.clear()

    session.scroll(0)
    session._display_offset = 4
    session.scroll(3)
    session.scroll_to_bottom()

    assert [json.loads(item.decode("utf-8")) for item in process.writes] == [
        {"type": "scroll", "delta": 3},
        {"type": "scroll", "to_bottom": True},
    ]


def test_native_terminal_write_scrolls_only_when_view_is_scrolled_up(monkeypatch, qapp):
    session, process = _session(monkeypatch)
    process.writes.clear()

    session.write("a")
    session._display_offset = 2
    session.write("b")

    commands = [json.loads(item.decode("utf-8")) for item in process.writes]
    assert commands[0]["type"] == "input"
    assert commands[1] == {"type": "scroll", "to_bottom": True}
    assert commands[2]["type"] == "input"


def test_native_terminal_ignores_unsupported_scroll_errors(monkeypatch, qapp):
    session, process = _session(monkeypatch)
    finished, statuses = [], []
    session.finished.connect(finished.append)
    session.status.connect(statuses.append)
    session._display_offset = 3

    session._handle_event({
        "type": "error",
        "message": "invalid terminal command: unknown variant `scroll`, expected one of `start`, `input`, `resize`, `shutdown`",
    })
    process.writes.clear()
    session.scroll(2)
    session.scroll_to_bottom()

    assert finished == []
    assert statuses == []
    assert session._scroll_supported is False
    assert process.writes == []


def test_native_terminal_reports_protocol_and_process_errors(monkeypatch, qapp):
    session, process = _session(monkeypatch)
    finished, statuses = [], []
    session.finished.connect(finished.append)
    session.status.connect(statuses.append)

    session._handle_event({"type": "error", "message": "no pty"})
    session._on_error(None)

    assert finished[0]["exit_code"] == 1
    assert "no pty" in finished[0]["output"]
    assert statuses[0] == "[terminal error] no pty"


def test_native_terminal_ignores_invalid_data_and_finishes_process(monkeypatch, qapp):
    session, process = _session(monkeypatch)
    finished = []
    session.finished.connect(finished.append)
    session._handle_event({"type": "output", "data": "%%%"})
    process.chunks = [b'{"type":"ready"}\n']

    session._on_finished(0, None)

    assert finished[0]["exit_code"] == 1


def test_native_terminal_path_and_encoder(monkeypatch, tmp_path):
    helper = tmp_path / "rust" / "aichs-terminal" / "target" / "release" / "aichs-terminal.exe"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"helper")
    monkeypatch.setattr(native_terminal, "_ROOT", tmp_path)
    monkeypatch.setattr(native_terminal.sys, "platform", "win32")
    monkeypatch.setattr(native_terminal.sys, "frozen", False, raising=False)

    assert native_terminal.native_terminal_path() == helper
    assert base64.b64decode(_encode("hello")) == b"hello"


def test_native_terminal_shell_helpers(monkeypatch):
    monkeypatch.setattr(native_terminal.sys, "platform", "win32")
    monkeypatch.setattr(native_terminal.shutil, "which", lambda name: "C:/pwsh.exe" if name == "pwsh" else None)
    monkeypatch.setattr(native_terminal, "_shell_env", lambda: {"NO_COLOR": "1", "A": "1"})

    assert interactive_shell_command() == ("C:/pwsh.exe", ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass"], "pwsh")
    assert integrated_terminal_env() == {"A": "1"}
    assert apply_terminal_output_controls("ab", "\b \bc") == "ac"

import threading

from services.user_terminal import UserTerminalThread, run_user_terminal_command


class _Proc:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_run_user_terminal_command_collects_output(monkeypatch, workspace):
    proc = _Proc(["hello\n", "bye\n"], returncode=0)
    monkeypatch.setattr("services.user_terminal.subprocess.Popen", lambda *a, **k: proc)
    monkeypatch.setattr("services.user_terminal._shell_command_args", lambda command: ["shell", command])
    seen = []

    result = run_user_terminal_command("echo hi", str(workspace), on_line=seen.append)

    assert result["exit_code"] == 0
    assert result["output"] == "hello\nbye"
    assert result["line_count"] == 2
    assert result["stored_line_count"] == 2
    assert seen == ["hello", "bye"]


def test_run_user_terminal_command_skips_leading_blank_lines(monkeypatch, workspace):
    proc = _Proc(["\n", "pytest.ini\n", "README.md\n"], returncode=0)
    monkeypatch.setattr("services.user_terminal.subprocess.Popen", lambda *a, **k: proc)
    seen = []

    result = run_user_terminal_command("dir", str(workspace), on_line=seen.append)

    assert result["output"] == "pytest.ini\nREADME.md"
    assert result["line_count"] == 2
    assert seen == ["pytest.ini", "README.md"]


def test_run_user_terminal_command_truncates(monkeypatch, workspace):
    monkeypatch.setattr("services.user_terminal.MAX_USER_TERMINAL_OUTPUT_CHARS", 5)
    monkeypatch.setattr("services.user_terminal.MAX_USER_TERMINAL_OUTPUT_LINES", 10)
    monkeypatch.setattr(
        "services.user_terminal.subprocess.Popen",
        lambda *a, **k: _Proc(["abcdef\n", "more\n"], returncode=0),
    )

    result = run_user_terminal_command("noisy", str(workspace))

    assert result["output"] == "abcde"
    assert result["line_count"] == 2
    assert result["truncated"] is True


def test_run_user_terminal_command_can_cancel(monkeypatch, workspace):
    proc = _Proc(["first\n", "second\n"], returncode=0)
    monkeypatch.setattr("services.user_terminal.subprocess.Popen", lambda *a, **k: proc)
    cancel = threading.Event()

    def on_line(_line):
        cancel.set()

    result = run_user_terminal_command("stop", str(workspace), on_line=on_line, cancel=cancel)

    assert proc.killed is True
    assert result["output"] == "first\n[cancelled]"


def test_run_user_terminal_command_reports_start_error(monkeypatch, workspace):
    def fail(*_args, **_kwargs):
        raise OSError("no shell")

    monkeypatch.setattr("services.user_terminal.subprocess.Popen", fail)

    result = run_user_terminal_command("bad", str(workspace))

    assert result["exit_code"] == 1
    assert "[terminal error] no shell" in result["output"]


def test_user_terminal_thread_run_emits_summary(monkeypatch, qapp):
    def fake_run(command, cwd, *, on_line=None, cancel=None):
        if on_line:
            on_line("ok")
        return {
            "command": command,
            "cwd": cwd,
            "exit_code": 0,
            "duration_s": 0,
            "line_count": 1,
            "stored_line_count": 1,
            "truncated": False,
            "output": "ok",
        }

    monkeypatch.setattr("services.user_terminal.run_user_terminal_command", fake_run)
    thread = UserTerminalThread("echo ok", "cwd")
    lines = []
    done = []
    thread.line.connect(lines.append)
    thread.done.connect(done.append)

    thread.cancel()
    thread.run()

    assert thread._cancel.is_set()
    assert lines == ["ok"]
    assert done[0]["summary"].startswith("Terminal")

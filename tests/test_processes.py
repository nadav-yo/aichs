import sys
import time
from itertools import count

import pytest

from services.processes import ManagedProcessError, ProcessInfo, ProcessManager, RuntimeProcessApi


class _FakeProc:
    _pids = count(1000)

    def __init__(self, *args, **kwargs):
        self.pid = next(self._pids)
        self.returncode = None
        self.stdin = None
        self.stdout = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.returncode = -9

    def terminate(self):
        self.returncode = 0


def _use_fake_processes(monkeypatch):
    monkeypatch.setattr("services.processes.popen_no_window", _FakeProc)
    monkeypatch.setattr("services.processes._terminate_process", lambda proc, force: proc.terminate())
    monkeypatch.setattr(ProcessManager, "_pump_output", lambda self, key: None)


def test_runtime_process_api_start_tail_write_and_stop(workspace):
    manager = ProcessManager()
    api = RuntimeProcessApi(manager, workspace=str(workspace), extension_id="test")
    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys\n"
            "print('ready', flush=True)\n"
            "for line in sys.stdin:\n"
            "    print('echo:' + line.strip(), flush=True)\n"
        ),
    ]

    info = api.start("repl", command, allow_stdin=True)
    assert info.running is True

    _wait_for(lambda: "ready" in api.tail("repl"))
    api.write("repl", "hello\n")
    _wait_for(lambda: "echo:hello" in api.tail("repl"))

    stopped = api.stop("repl")
    assert stopped.name == "repl"
    assert manager.status(workspace=str(workspace)) == []


def test_process_manager_blocks_cwd_outside_workspace(workspace, tmp_path):
    manager = ProcessManager()

    with pytest.raises(ManagedProcessError, match="inside workspace"):
        manager.start(
            "bad",
            [sys.executable, "-c", "print('x')"],
            cwd=str(tmp_path),
            workspace=str(workspace),
        )


def test_process_manager_rejects_invalid_name_and_command(workspace):
    manager = ProcessManager()

    with pytest.raises(ManagedProcessError, match="name is required"):
        manager.start("", [sys.executable, "-c", "print('x')"], cwd=str(workspace))
    with pytest.raises(ManagedProcessError, match="cannot be empty"):
        manager.start("empty", [], cwd=str(workspace))
    with pytest.raises(ManagedProcessError, match="string or list"):
        manager.start("bad", [sys.executable, 1], cwd=str(workspace))


def test_process_info_as_dict():
    info = ProcessInfo(
        name="demo",
        command="echo hi",
        cwd="cwd",
        workspace="workspace",
        extension_id="ext",
        pid=123,
        running=True,
        line_count=7,
    )

    data = info.as_dict()

    assert data["name"] == "demo"
    assert data["pid"] == 123
    assert data["line_count"] == 7


def test_process_manager_duplicate_restart_and_stop_all(workspace, monkeypatch):
    _use_fake_processes(monkeypatch)
    manager = ProcessManager()
    api = RuntimeProcessApi(manager, workspace=str(workspace))
    command = [sys.executable, "-u", "-c", "import time; print('up', flush=True); time.sleep(30)"]
    first = api.start("server", command)

    with pytest.raises(ManagedProcessError, match="already running"):
        api.start("server", command)

    second = api.start("server", command, restart=True)
    assert second.pid != first.pid
    assert api.status("server")[0].running is True

    stopped = manager.stop_all()
    assert [info.name for info in stopped] == ["server"]
    assert api.status() == []


def test_process_manager_stop_workspace_only_stops_matching_workspace(workspace, tmp_path, monkeypatch):
    _use_fake_processes(monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    manager = ProcessManager()
    cmd = [sys.executable, "-u", "-c", "import time; time.sleep(30)"]
    manager.start("one", cmd, cwd=str(workspace), workspace=str(workspace))
    manager.start("two", cmd, cwd=str(other), workspace=str(other))

    stopped = manager.stop_workspace(str(workspace))

    assert [info.name for info in stopped] == ["one"]
    assert [info.name for info in manager.status()] == ["two"]
    manager.stop_all()


def test_process_manager_natural_exit_and_stop_missing(workspace):
    manager = ProcessManager()
    api = RuntimeProcessApi(manager, workspace=str(workspace))
    api.start("quick", [sys.executable, "-u", "-c", "print('done')"])

    _wait_for(lambda: api.status("quick") and api.status("quick")[0].running is False)
    assert "done" in api.tail("quick", 1)

    with pytest.raises(ManagedProcessError, match="not found"):
        api.stop("missing")


def test_process_write_requires_stdin(workspace, monkeypatch):
    _use_fake_processes(monkeypatch)
    manager = ProcessManager()
    api = RuntimeProcessApi(manager, workspace=str(workspace))
    api.start("no_stdin", [sys.executable, "-u", "-c", "import time; time.sleep(30)"])

    with pytest.raises(ManagedProcessError, match="does not accept stdin"):
        api.write("no_stdin", "hello\n")

    api.stop("no_stdin")


def test_runtime_process_api_approval_can_deny(workspace):
    manager = ProcessManager()
    api = RuntimeProcessApi(
        manager,
        workspace=str(workspace),
        approve_start=lambda request: False,
    )

    with pytest.raises(ManagedProcessError, match="denied"):
        api.start("denied", [sys.executable, "-c", "print('x')"])


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from services.subprocess_utils import popen_no_window, run_no_window
from services.tools import _shell_command_args, _shell_env, _strip_ansi


MAX_PROCESS_BUFFER_LINES = 1000
MAX_PROCESS_BUFFER_CHARS = 256 * 1024


@dataclass(frozen=True)
class ProcessStartRequest:
    name: str
    command: str | list[str]
    cwd: str
    workspace: str
    extension_id: str = ""
    allow_stdin: bool = False
    restart: bool = False


@dataclass(frozen=True)
class ProcessInfo:
    name: str
    command: str | list[str]
    cwd: str
    workspace: str
    extension_id: str
    pid: int | None = None
    running: bool = False
    exit_code: int | None = None
    started_at: float | None = None
    ended_at: float | None = None
    line_count: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "command": self.command,
            "cwd": self.cwd,
            "workspace": self.workspace,
            "extension_id": self.extension_id,
            "pid": self.pid,
            "running": self.running,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "line_count": self.line_count,
        }


@dataclass
class _ManagedProcess:
    name: str
    command: str | list[str]
    cwd: str
    workspace: str
    extension_id: str = ""
    allow_stdin: bool = False
    proc: subprocess.Popen | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    line_count: int = 0
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_PROCESS_BUFFER_LINES))
    char_count: int = 0
    exit_emitted: bool = False

    def info(self) -> ProcessInfo:
        proc = self.proc
        running = bool(proc and proc.poll() is None)
        exit_code = None if running or proc is None else proc.returncode
        return ProcessInfo(
            name=self.name,
            command=self.command,
            cwd=self.cwd,
            workspace=self.workspace,
            extension_id=self.extension_id,
            pid=proc.pid if proc else None,
            running=running,
            exit_code=exit_code,
            started_at=self.started_at,
            ended_at=self.ended_at,
            line_count=self.line_count,
        )

    def append_line(self, line: str) -> None:
        self.line_count += 1
        self.lines.append(line)
        self.char_count += len(line)
        while self.lines and self.char_count > MAX_PROCESS_BUFFER_CHARS:
            self.char_count -= len(self.lines.popleft())


class ManagedProcessError(RuntimeError):
    pass


class ProcessManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._processes: dict[tuple[str, str], _ManagedProcess] = {}

    def start(
        self,
        name: str,
        command: str | list[str],
        *,
        cwd: str,
        workspace: str | None = None,
        extension_id: str = "",
        allow_stdin: bool = False,
        restart: bool = False,
    ) -> ProcessInfo:
        safe_name = _safe_name(name)
        if not safe_name:
            raise ManagedProcessError("process name is required")
        workspace_path = Path(workspace or cwd).resolve()
        cwd_path = Path(cwd).resolve()
        _ensure_inside_workspace(cwd_path, workspace_path)
        key = (str(workspace_path), safe_name)

        with self._lock:
            existing = self._processes.get(key)
            if existing and existing.proc and existing.proc.poll() is None:
                if not restart:
                    raise ManagedProcessError(f"process already running: {safe_name}")
                self._stop_locked(key, force=True)

            stdin = subprocess.PIPE if allow_stdin else subprocess.DEVNULL
            proc = popen_no_window(
                _process_args(command),
                cwd=str(cwd_path),
                env=_shell_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=stdin,
                text=True,
                bufsize=1,
                process_group=True,
                preexec_fn=_preexec_fn(),
            )
            managed = _ManagedProcess(
                name=safe_name,
                command=command,
                cwd=str(cwd_path),
                workspace=str(workspace_path),
                extension_id=extension_id,
                allow_stdin=allow_stdin,
                proc=proc,
            )
            self._processes[key] = managed
            info = managed.info()
            thread = threading.Thread(
                target=self._pump_output,
                args=(key,),
                name=f"aichs-process-{safe_name}",
                daemon=True,
            )
            thread.start()

        self._emit_lifecycle("process_started", info)
        return info

    def stop(self, name: str, *, workspace: str, force: bool = True) -> ProcessInfo:
        key = (str(Path(workspace).resolve()), _safe_name(name))
        with self._lock:
            info = self._stop_locked(key, force=force)
        if info:
            self._emit_lifecycle("process_exited", info)
            return info
        raise ManagedProcessError(f"process not found: {name}")

    def write(self, name: str, text: str, *, workspace: str) -> None:
        proc = self._require_running(name, workspace)
        if proc.proc is None or proc.proc.stdin is None:
            raise ManagedProcessError(f"process does not accept stdin: {name}")
        proc.proc.stdin.write(text)
        proc.proc.stdin.flush()

    def status(self, name: str = "", *, workspace: str | None = None) -> list[ProcessInfo]:
        with self._lock:
            self._refresh_exits_locked()
            items = list(self._processes.items())
            if workspace:
                root = str(Path(workspace).resolve())
                items = [(key, proc) for key, proc in items if key[0] == root]
            if name:
                safe_name = _safe_name(name)
                items = [(key, proc) for key, proc in items if key[1] == safe_name]
            return [proc.info() for _key, proc in sorted(items)]

    def tail(self, name: str, *, workspace: str, lines: int = 80) -> str:
        key = (str(Path(workspace).resolve()), _safe_name(name))
        with self._lock:
            proc = self._processes.get(key)
            if proc is None:
                raise ManagedProcessError(f"process not found: {name}")
            count = max(1, min(int(lines or 80), MAX_PROCESS_BUFFER_LINES))
            return "\n".join(list(proc.lines)[-count:])

    def stop_workspace(self, workspace: str, *, force: bool = True) -> list[ProcessInfo]:
        root = str(Path(workspace).resolve())
        stopped: list[ProcessInfo] = []
        with self._lock:
            keys = [key for key in self._processes if key[0] == root]
            for key in keys:
                info = self._stop_locked(key, force=force)
                if info:
                    stopped.append(info)
        for info in stopped:
            self._emit_lifecycle("process_exited", info)
        return stopped

    def stop_all(self, *, force: bool = True) -> list[ProcessInfo]:
        stopped: list[ProcessInfo] = []
        with self._lock:
            for key in list(self._processes):
                info = self._stop_locked(key, force=force)
                if info:
                    stopped.append(info)
        for info in stopped:
            self._emit_lifecycle("process_exited", info)
        return stopped

    def _require_running(self, name: str, workspace: str) -> _ManagedProcess:
        key = (str(Path(workspace).resolve()), _safe_name(name))
        with self._lock:
            proc = self._processes.get(key)
            if proc is None or proc.proc is None or proc.proc.poll() is not None:
                raise ManagedProcessError(f"process not running: {name}")
            return proc

    def _pump_output(self, key: tuple[str, str]) -> None:
        with self._lock:
            managed = self._processes.get(key)
            proc = managed.proc if managed else None
        if managed is None or proc is None or proc.stdout is None:
            return

        try:
            for raw_line in proc.stdout:
                line = _strip_ansi(raw_line).rstrip("\n")
                with self._lock:
                    current = self._processes.get(key)
                    if current is not managed:
                        return
                    current.append_line(line)
            proc.wait(timeout=5)
        finally:
            with self._lock:
                managed.ended_at = time.time()
                info = managed.info()
                should_emit = not managed.exit_emitted
                managed.exit_emitted = True
            if should_emit:
                self._emit_lifecycle("process_exited", info)

    def _stop_locked(self, key: tuple[str, str], *, force: bool) -> ProcessInfo | None:
        managed = self._processes.get(key)
        if managed is None:
            return None
        proc = managed.proc
        if proc and proc.poll() is None:
            _terminate_process(proc, force=force)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        managed.ended_at = time.time()
        info = managed.info()
        managed.exit_emitted = True
        self._processes.pop(key, None)
        return info

    def _refresh_exits_locked(self) -> None:
        for managed in self._processes.values():
            if managed.proc and managed.proc.poll() is not None and managed.ended_at is None:
                managed.ended_at = time.time()

    def _emit_lifecycle(self, event: str, info: ProcessInfo) -> None:
        try:
            from services.tool_registry import HookContext, run_extension_hooks

            ctx = HookContext(
                event=event,
                cwd=info.workspace,
                process=info.as_dict(),
                status="ok" if info.exit_code in (None, 0) else "error",
            )
            run_extension_hooks(info.workspace, event, ctx)
        except Exception:
            pass


class RuntimeProcessApi:
    def __init__(
        self,
        manager: ProcessManager,
        *,
        workspace: str,
        extension_id: str = "",
        approve_start: Callable[[ProcessStartRequest], bool] | None = None,
    ):
        self._manager = manager
        self._workspace = str(Path(workspace).resolve())
        self._extension_id = extension_id
        self._approve_start = approve_start

    def start(
        self,
        name: str,
        command: str | list[str],
        *,
        cwd: str | None = None,
        allow_stdin: bool = False,
        restart: bool = False,
    ) -> ProcessInfo:
        request = ProcessStartRequest(
            name=name,
            command=command,
            cwd=str(Path(cwd or self._workspace).resolve()),
            workspace=self._workspace,
            extension_id=self._extension_id,
            allow_stdin=allow_stdin,
            restart=restart,
        )
        if self._approve_start and not self._approve_start(request):
            raise ManagedProcessError("process start denied by user")
        return self._manager.start(
            request.name,
            request.command,
            cwd=request.cwd,
            workspace=request.workspace,
            extension_id=request.extension_id,
            allow_stdin=request.allow_stdin,
            restart=request.restart,
        )

    def stop(self, name: str, *, force: bool = True) -> ProcessInfo:
        return self._manager.stop(name, workspace=self._workspace, force=force)

    def restart(self, name: str, command: str | list[str], *, cwd: str | None = None) -> ProcessInfo:
        return self.start(name, command, cwd=cwd, restart=True)

    def status(self, name: str = "") -> list[ProcessInfo]:
        return self._manager.status(name, workspace=self._workspace)

    def tail(self, name: str, lines: int = 80) -> str:
        return self._manager.tail(name, workspace=self._workspace, lines=lines)

    def write(self, name: str, text: str) -> None:
        self._manager.write(name, text, workspace=self._workspace)

    def stop_workspace(self, *, force: bool = True) -> list[ProcessInfo]:
        return self._manager.stop_workspace(self._workspace, force=force)


_PROCESS_MANAGER = ProcessManager()


def get_process_manager() -> ProcessManager:
    return _PROCESS_MANAGER


def _process_args(command: str | list[str]) -> list[str]:
    if isinstance(command, str):
        return _shell_command_args(command)
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        if not command:
            raise ManagedProcessError("process command cannot be empty")
        return command
    raise ManagedProcessError("process command must be a string or list of strings")


def _ensure_inside_workspace(cwd: Path, workspace: Path) -> None:
    try:
        cwd.relative_to(workspace)
    except ValueError as exc:
        raise ManagedProcessError(f"process cwd must stay inside workspace: {cwd}") from exc


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(value or ""))
    return safe.strip("._-")


def _preexec_fn():
    if os.name == "nt":
        return None
    return os.setsid


def _terminate_process(proc: subprocess.Popen, *, force: bool) -> None:
    if os.name == "nt":
        args = ["taskkill", "/PID", str(proc.pid), "/T"]
        if force:
            args.append("/F")
        run_no_window(
            args,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            process_group=False,
        )
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        proc.terminate()

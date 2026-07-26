"""Client for the bundled Rust filename-search helper."""

from __future__ import annotations

import base64
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from config import IGNORED
from services.subprocess_utils import popen_no_window


_ROOT = Path(__file__).resolve().parents[1]
_SESSIONS: dict[str, "_IndexerSession"] = {}
_SESSIONS_LOCK = threading.Lock()


@dataclass(frozen=True)
class FilenameMatch:
    rel_path: str
    name: str
    score: int
    indices: tuple[int, ...]


def prepare_filename_index(root: str | Path) -> bool:
    return _session_for(Path(root).resolve()) is not None


def query_filename_index(
    root: str | Path,
    query: str,
    *,
    limit: int,
) -> list[FilenameMatch] | None:
    session = _session_for(Path(root).resolve())
    return session.query(query, limit) if session is not None else None


def clear_filename_index(root: str | Path | None = None) -> None:
    with _SESSIONS_LOCK:
        keys = list(_SESSIONS) if root is None else [str(Path(root).resolve())]
        sessions = [_SESSIONS.pop(key, None) for key in keys]
    for session in sessions:
        if session is not None:
            session.close()


def _session_for(root: Path) -> "_IndexerSession | None":
    binary = _indexer_path()
    if binary is None:
        return None
    key = str(root)
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(key)
        if session is not None and session.running:
            return session
        if session is not None:
            _SESSIONS.pop(key, None)
        try:
            session = _IndexerSession.start(binary, root)
        except OSError:
            return None
        _SESSIONS[key] = session
        return session


def _indexer_path() -> Path | None:
    executable = "aichs-indexer.exe" if sys.platform == "win32" else "aichs-indexer"
    platform = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    roots = [
        Path(getattr(sys, "_MEIPASS", "")),
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(),
        _ROOT,
    ]
    relative_paths = (
        Path("bin") / executable,
        Path("tools") / "vendor" / "aichs-indexer" / platform / executable,
    )
    for root in roots:
        if not str(root):
            continue
        for relative in relative_paths:
            candidate = root / relative
            if candidate.is_file():
                return candidate
    return None


class _IndexerSession:
    def __init__(self, process: subprocess.Popen):
        self._process = process
        self._lock = threading.Lock()

    @classmethod
    def start(cls, binary: Path, root: Path) -> "_IndexerSession":
        process = popen_no_window(
            [str(binary), str(root), ",".join(sorted(IGNORED))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        session = cls(process)
        line = session._readline()
        if not line.startswith("READY\t"):
            session.close()
            raise OSError("filename indexer did not start")
        return session

    @property
    def running(self) -> bool:
        return self._process.poll() is None

    def query(self, query: str, limit: int) -> list[FilenameMatch] | None:
        with self._lock:
            if not self.running or self._process.stdin is None:
                return None
            encoded_query = base64.b64encode(query.encode("utf-8")).decode("ascii")
            try:
                self._process.stdin.write(f"Q\t{encoded_query}\t{max(1, limit)}\n")
                self._process.stdin.flush()
            except OSError:
                return None
            matches: list[FilenameMatch] = []
            while True:
                line = self._readline()
                if line == "D":
                    return matches
                if not line or line.startswith("E\t"):
                    return None
                parts = line.split("\t")
                if len(parts) != 5 or parts[0] != "M":
                    return None
                try:
                    rel_path = base64.b64decode(parts[2]).decode("utf-8")
                    name = base64.b64decode(parts[3]).decode("utf-8")
                    indices = tuple(int(value) for value in parts[4].split(",") if value)
                    matches.append(FilenameMatch(rel_path, name, int(parts[1]), indices))
                except (UnicodeDecodeError, ValueError):
                    return None

    def close(self) -> None:
        if self._process.poll() is not None:
            return
        try:
            if self._process.stdin is not None:
                self._process.stdin.write("X\n")
                self._process.stdin.flush()
            self._process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            self._process.kill()
            self._process.wait(timeout=1)

    def _readline(self) -> str:
        if self._process.stdout is None:
            return ""
        return self._process.stdout.readline().rstrip("\r\n")

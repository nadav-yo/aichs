from __future__ import annotations

import base64
from io import StringIO
from pathlib import Path

import services.filename_indexer as indexer


class _Input(StringIO):
    def flush(self):
        pass


class _Process:
    def __init__(self, output: str):
        self.stdin = _Input()
        self.stdout = StringIO(output)
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_session_reads_structured_filename_matches():
    rel = base64.b64encode(b"src/main.py").decode()
    name = base64.b64encode(b"main.py").decode()
    process = _Process(f"M\t123\t{rel}\t{name}\t0,2\nD\n")
    session = indexer._IndexerSession(process)

    matches = session.query("mn", 80)

    assert process.stdin.getvalue() == "Q\tbW4=\t80\n"
    assert matches == [indexer.FilenameMatch("src/main.py", "main.py", 123, (0, 2))]


def test_prepare_returns_false_without_a_bundled_indexer(tmp_path, monkeypatch):
    indexer.clear_filename_index()
    monkeypatch.setattr(indexer, "_indexer_path", lambda: None)

    assert not indexer.prepare_filename_index(tmp_path)
    assert indexer.query_filename_index(tmp_path, "main", limit=80) is None


def test_clear_filename_index_stops_workspace_session(tmp_path, monkeypatch):
    indexer.clear_filename_index()
    process = _Process("READY\t0\n")
    monkeypatch.setattr(indexer, "_indexer_path", lambda: Path("indexer.exe"))
    monkeypatch.setattr(indexer, "popen_no_window", lambda *_args, **_kwargs: process)

    assert indexer.prepare_filename_index(tmp_path)
    indexer.clear_filename_index(tmp_path)

    assert process.stdin.getvalue() == "X\n"

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.tools import execute, tools_anthropic, tools_openai


def test_tools_openai_shape(workspace):
    tools = tools_openai(str(workspace))
    assert tools[0]["type"] == "function"
    assert "read_file" in {t["function"]["name"] for t in tools}


def test_search_invalid_directory(cwd, workspace):
    out = execute("search_files", {"pattern": "x", "directory": "nope"}, cwd)
    assert "Directory not found" in out


def test_search_invalid_regex(cwd, workspace, monkeypatch):
    monkeypatch.setattr("services.tools._search_files_with_rg", lambda *a, **k: None)
    out = execute("search_files", {"pattern": "[", "directory": "."}, cwd)
    assert "Invalid search pattern" in out


def test_read_file_missing(cwd, workspace):
    out = execute("read_file", {"path": "missing.txt"}, cwd)
    assert "[tool error]" in out


def test_bash_mocked(cwd, workspace, monkeypatch):
    proc = MagicMock()
    proc.stdout = iter(["hello\n"])
    proc.wait.return_value = 0
    proc.returncode = 0
    monkeypatch.setattr("services.tools.subprocess.Popen", lambda *a, **k: proc)
    out = execute("bash", {"command": "echo hi"}, cwd)
    assert "hello" in out


def test_edit_overlapping_edits(cwd, workspace):
    (workspace / "dup.txt").write_text("aaa bbb aaa", encoding="utf-8")
    out = execute(
        "edit_file",
        {
            "path": "dup.txt",
            "edits": [
                {"oldText": "aaa", "newText": "x"},
                {"oldText": "aaa", "newText": "y"},
            ],
        },
        cwd,
    )
    assert "overlap" in out or "found 2" in out

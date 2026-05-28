"""Additional coverage for services.tools — security-critical tool execution."""

import json
import subprocess
import sys
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from config import MAX_TOOL_OUTPUT_CHARS, MAX_TOOL_OUTPUT_LINES, MAX_TOOL_READ_BYTES
from services.tool_registry import ToolContext
from services.shell_tool import shell_tool_name
from services.tools import (
    _shell_tool_description,
    _display_path,
    _edit_file,
    _iter_list_paths,
    _iter_search_paths,
    _list_files,
    _read_text_limited,
    _read_project_chat,
    _run_shell_command,
    _search_project_chats,
    _search_files,
    _search_files_with_rg,
    execute,
    registry_for,
    tool_approval,
)


@pytest.fixture
def cwd(workspace):
    return str(workspace)


class TestBashDescription:
    def test_unix_shell_in_description(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert "/bin/sh" in _shell_tool_description()


class TestReadFile:
    def test_truncated_large_file(self, cwd, workspace):
        path = workspace / "big.txt"
        path.write_bytes(b"x" * (MAX_TOOL_READ_BYTES + 500))
        out = execute("read_file", {"path": "big.txt"}, cwd)
        assert "[truncated:" in out

    def test_execute_surfaces_exception(self, cwd, monkeypatch):
        def boom(_ctx, _inputs):
            raise OSError("read failed")

        monkeypatch.setattr("services.tools._execute_read_file", boom)
        out = execute("read_file", {"path": "src/main.py"}, cwd)
        assert "[tool error] read failed" in out


class TestEditFileErrors:
    def test_content_must_be_string(self, cwd):
        assert "content must be a string" in _edit_file({"path": "n.txt", "content": 1}, cwd)

    def test_append_must_be_string(self, cwd):
        assert "append must be a string" in _edit_file({"path": "src/main.py", "append": 99}, cwd)

    def test_append_literal_newline(self, cwd):
        out = execute("edit_file", {"path": "src/main.py", "append": "a\\nb"}, cwd)
        assert "literal '\\n'" in out

    def test_append_missing_file(self, cwd):
        out = execute("edit_file", {"path": "ghost.txt", "append": "x"}, cwd)
        assert "does not exist" in out

    def test_append_empty_string(self, cwd):
        out = execute("edit_file", {"path": "src/main.py", "append": ""}, cwd)
        assert "No changes made" in out

    def test_append_to_directory(self, cwd, workspace):
        (workspace / "folder").mkdir()
        out = execute("edit_file", {"path": "folder", "append": "x"}, cwd)
        assert "Not a file" in out

    def test_empty_edits_array(self, cwd):
        out = execute("edit_file", {"path": "src/main.py", "edits": []}, cwd)
        assert "non-empty edits" in out

    def test_edits_missing_file(self, cwd):
        out = execute("edit_file", {"path": "nope.py", "edits": [{"oldText": "a", "newText": "b"}]}, cwd)
        assert "does not exist" in out

    def test_edits_on_directory(self, cwd, workspace):
        (workspace / "dir2").mkdir()
        out = execute(
            "edit_file",
            {"path": "dir2", "edits": [{"oldText": "a", "newText": "b"}]},
            cwd,
        )
        assert "Not a file" in out

    def test_edit_item_not_object(self, cwd):
        out = execute("edit_file", {"path": "src/main.py", "edits": ["bad"]}, cwd)
        assert "must be an object" in out

    def test_old_text_empty(self, cwd):
        out = execute(
            "edit_file",
            {"path": "src/main.py", "edits": [{"oldText": "", "newText": "x"}]},
            cwd,
        )
        assert "oldText must be a non-empty string" in out

    def test_new_text_not_string(self, cwd):
        out = execute(
            "edit_file",
            {"path": "src/main.py", "edits": [{"oldText": "print", "newText": 1}]},
            cwd,
        )
        assert "newText must be a string" in out

    def test_new_text_literal_newline(self, cwd):
        out = execute(
            "edit_file",
            {
                "path": "src/main.py",
                "edits": [{"oldText": "print('hi')", "newText": "a\\nb"}],
            },
            cwd,
        )
        assert "literal '\\n'" in out

    def test_overlapping_edits(self, cwd, workspace):
        (workspace / "overlap.txt").write_text("abcdef", encoding="utf-8")
        out = execute(
            "edit_file",
            {
                "path": "overlap.txt",
                "edits": [
                    {"oldText": "ab", "newText": "1"},
                    {"oldText": "bc", "newText": "2"},
                ],
            },
            cwd,
        )
        assert "overlap" in out

    def test_no_op_edit(self, cwd):
        out = execute(
            "edit_file",
            {
                "path": "src/main.py",
                "edits": [{"oldText": "print('hi')", "newText": "print('hi')"}],
            },
            cwd,
        )
        assert "No changes made" in out


class TestBashExecution:
    def _mock_proc(self, lines, returncode=0):
        proc = MagicMock()
        proc.stdout = iter(lines)
        proc.wait.return_value = None
        proc.returncode = returncode
        proc.kill = MagicMock()
        return proc

    def test_on_line_callback(self, cwd, monkeypatch):
        proc = self._mock_proc(["line\n"])
        monkeypatch.setattr("services.tools.subprocess.Popen", lambda *a, **k: proc)
        seen = []
        _run_shell_command("echo", cwd, on_line=lambda ln: seen.append(ln))
        assert seen == ["line"]

    def test_cancel_kills_process(self, cwd, monkeypatch):
        cancel = Event()
        proc = MagicMock()
        proc.wait.return_value = None
        proc.returncode = 0
        proc.kill = MagicMock()

        def lines():
            yield "a\n"
            cancel.set()
            yield "b\n"

        proc.stdout = lines()
        monkeypatch.setattr("services.tools.subprocess.Popen", lambda *a, **k: proc)
        out = _run_shell_command("cmd", cwd, cancel=cancel)
        proc.kill.assert_called_once()
        assert "a" in out

    def test_truncates_long_lines_and_output(self, cwd, monkeypatch):
        long_line = "z" * (MAX_TOOL_OUTPUT_CHARS + 50) + "\n"
        proc = self._mock_proc([long_line, "tail\n"])
        monkeypatch.setattr("services.tools.subprocess.Popen", lambda *a, **k: proc)
        out = _run_shell_command("cmd", cwd)
        assert "[output truncated]" in out

    def test_truncates_too_many_output_lines(self, cwd, monkeypatch):
        proc = self._mock_proc(["x\n"] * (MAX_TOOL_OUTPUT_LINES + 5))
        monkeypatch.setattr("services.tools.subprocess.Popen", lambda *a, **k: proc)
        out = _run_shell_command("cmd", cwd)
        assert "[output truncated]" in out

    def test_nonzero_exit_code(self, cwd, monkeypatch):
        proc = self._mock_proc(["err\n"], returncode=2)
        monkeypatch.setattr("services.tools.subprocess.Popen", lambda *a, **k: proc)
        out = _run_shell_command("false", cwd)
        assert "[exit 2]" in out

    def test_no_output_message(self, cwd, monkeypatch):
        proc = self._mock_proc([])
        monkeypatch.setattr("services.tools.subprocess.Popen", lambda *a, **k: proc)
        assert _run_shell_command("cmd", cwd) == "(no output)"


class TestSearchFiles:
    def test_list_files_maps_workspace(self, cwd, workspace):
        docs = workspace / "docs"
        docs.mkdir()
        (docs / "intro.md").write_text("# Intro\n", encoding="utf-8")
        (docs / "api.md").write_text("# API\n", encoding="utf-8")

        out = execute("list_files", {"directory": "docs", "glob": "*.md"}, cwd)
        assert "docs" in out
        assert "intro.md" in out
        assert "api.md" in out

    def test_list_files_recursive_skips_ignored_dirs(self, workspace):
        cache = workspace / "__pycache__"
        cache.mkdir()
        (cache / "junk.py").write_text("secret", encoding="utf-8")
        (workspace / "visible.py").write_text("secret", encoding="utf-8")

        paths = list(_iter_list_paths(workspace, "*.py", recursive=True))
        names = {p.name for p in paths}
        assert "visible.py" in names
        assert "junk.py" not in names

    def test_list_files_limit_reports_omitted(self, cwd, workspace):
        for idx in range(3):
            (workspace / f"file{idx}.txt").write_text("x", encoding="utf-8")

        out = _list_files(workspace, "*.txt", recursive=False, limit=2, cwd=cwd)
        assert "[truncated: showing first 2 entries; 1 omitted]" in out

    def test_not_a_directory(self, cwd, workspace):
        out = execute(
            "search_files",
            {"pattern": "x", "directory": "src/main.py"},
            cwd,
        )
        assert "Not a directory" in out

    def test_fallback_walk_finds_match(self, cwd, workspace, monkeypatch):
        monkeypatch.setattr("services.tools.shutil.which", lambda _: None)
        (workspace / "findme.py").write_text("needle here\n", encoding="utf-8")
        out = execute("search_files", {"pattern": "needle", "glob": "*.py"}, cwd)
        assert "findme.py" in out
        assert "needle" in out

    def test_fallback_read_error(self, cwd, workspace, monkeypatch):
        monkeypatch.setattr("services.tools.shutil.which", lambda _: None)
        (workspace / "locked.py").write_text("needle", encoding="utf-8")
        orig_open = Path.open

        def selective_open(self, *args, **kwargs):
            if self.name == "locked.py":
                raise OSError("permission denied")
            return orig_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", selective_open)
        out = execute("search_files", {"pattern": "needle", "glob": "*.py"}, cwd)
        assert "[read error:" in out

    def test_rg_success(self, cwd, workspace, monkeypatch):
        monkeypatch.setattr("services.tools.shutil.which", lambda cmd: "/usr/bin/rg" if cmd == "rg" else None)
        mock = MagicMock(returncode=0, stdout="src/main.py:1:print\n", stderr="")
        monkeypatch.setattr("services.tools.subprocess.run", lambda *a, **k: mock)
        out = _search_files_with_rg(workspace, "*.py", "print", cwd)
        assert "main.py" in out

    def test_rg_no_matches(self, cwd, workspace, monkeypatch):
        monkeypatch.setattr("services.tools.shutil.which", lambda cmd: "rg")
        mock = MagicMock(returncode=1, stdout="", stderr="")
        monkeypatch.setattr("services.tools.subprocess.run", lambda *a, **k: mock)
        assert _search_files_with_rg(workspace, "*", "zzz", cwd) == "(no matches)"

    def test_rg_failure_exit_code(self, cwd, workspace, monkeypatch):
        monkeypatch.setattr("services.tools.shutil.which", lambda cmd: "rg")
        mock = MagicMock(returncode=2, stdout="", stderr="bad pattern")
        monkeypatch.setattr("services.tools.subprocess.run", lambda *a, **k: mock)
        out = _search_files_with_rg(workspace, "*", "[", cwd)
        assert "bad pattern" in out or "rg failed" in out

    def test_rg_not_found_returns_none(self, cwd, workspace, monkeypatch):
        monkeypatch.setattr("services.tools.shutil.which", lambda _: None)
        assert _search_files_with_rg(workspace, "*", "x", cwd) is None

    def test_rg_timeout(self, cwd, workspace, monkeypatch):
        monkeypatch.setattr("services.tools.shutil.which", lambda cmd: "rg")

        def timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="rg", timeout=30)

        monkeypatch.setattr("services.tools.subprocess.run", timeout)
        assert _search_files_with_rg(workspace, "*", "x", cwd) == "Search timed out after 30 seconds"

    def test_rg_file_not_found(self, cwd, workspace, monkeypatch):
        monkeypatch.setattr("services.tools.shutil.which", lambda cmd: "rg")

        def missing(*a, **k):
            raise FileNotFoundError

        monkeypatch.setattr("services.tools.subprocess.run", missing)
        assert _search_files_with_rg(workspace, "*", "x", cwd) is None

    def test_iter_search_skips_ignored_dirs(self, workspace):
        cache = workspace / "__pycache__"
        cache.mkdir()
        (cache / "junk.py").write_text("secret", encoding="utf-8")
        (workspace / "visible.py").write_text("secret", encoding="utf-8")
        paths = list(_iter_search_paths(workspace, "*.py"))
        names = {p.name for p in paths}
        assert "visible.py" in names
        assert "junk.py" not in names


class TestSearchProjectChats:
    def _write_chat(self, conv_dir: Path, conv_id: str, **overrides):
        data = {
            "id": conv_id,
            "title": "Project notes",
            "created_at": "2026-01-01T10:00:00",
            "updated_at": "2026-01-01T11:00:00",
            "messages": [{"role": "user", "content": "we discussed playwright testing"}],
        }
        data.update(overrides)
        (conv_dir / f"{conv_id}.json").write_text(json.dumps(data), encoding="utf-8")

    def test_search_project_chats_finds_project_and_legacy(self, cwd, workspace, tmp_path, monkeypatch):
        conv_dir = tmp_path / "conversations"
        conv_dir.mkdir()
        monkeypatch.setattr("services.tools.config.CONV_DIR", conv_dir)
        self._write_chat(conv_dir, "project", cwd=str(workspace), title="Playwright plan")
        self._write_chat(conv_dir, "legacy", title="Legacy note")
        self._write_chat(
            conv_dir,
            "other",
            cwd=str(tmp_path / "other"),
            title="Other project",
            messages=[{"role": "user", "content": "playwright elsewhere"}],
        )

        out = execute("search_project_chats", {"query": "have we discussed using playwright", "limit": 5}, cwd)
        assert "Playwright plan" in out
        assert "Legacy note" in out
        assert "Other project" not in out
        assert "legacy unscoped" in out

    def test_search_project_chats_empty_and_missing(self, cwd, tmp_path, monkeypatch):
        monkeypatch.setattr("services.tools.config.CONV_DIR", tmp_path / "missing")
        assert "(no saved conversations)" in _search_project_chats("playwright", cwd)
        assert "requires a query" in execute("search_project_chats", {"query": ""}, cwd)

    def test_read_project_chat_exact_reference(self, cwd, workspace, tmp_path, monkeypatch):
        conv_dir = tmp_path / "conversations"
        conv_dir.mkdir()
        monkeypatch.setattr("services.tools.config.CONV_DIR", conv_dir)
        self._write_chat(
            conv_dir,
            "exact",
            cwd=str(workspace),
            title="Exact notes",
            messages=[
                {"role": "user", "content": "old setup"},
                {"role": "user", "content": "first decision"},
                {"role": "assistant", "content": "second answer"},
            ],
        )

        out = execute("read_project_chat", {"conversation_id": "exact", "max_messages": 2}, cwd)

        assert "Conversation: Exact notes" in out
        assert "ID: exact" in out
        assert "old setup" not in out
        assert "user: first decision" in out
        assert "assistant: second answer" in out

    def test_read_project_chat_guards_missing_and_other_workspace(self, cwd, workspace, tmp_path, monkeypatch):
        conv_dir = tmp_path / "conversations"
        conv_dir.mkdir()
        monkeypatch.setattr("services.tools.config.CONV_DIR", conv_dir)
        self._write_chat(conv_dir, "other", cwd=str(tmp_path / "other"))

        assert "requires a conversation_id" in execute("read_project_chat", {"conversation_id": ""}, cwd)
        assert "belongs to another workspace" in _read_project_chat("other", cwd)
        assert "Conversation not found" in _read_project_chat("missing", cwd)


class TestRegistryApi:
    def test_tool_schema_exports(self, cwd):
        from services.tools import tools_anthropic, tools_openai

        anthropic = tools_anthropic(cwd)
        openai = tools_openai(cwd)
        assert anthropic[0]["name"] == "read_file"
        assert openai[0]["function"]["name"] == "read_file"

    def test_is_parallel_safe_unknown(self, cwd):
        from services.tools import is_parallel_safe, tool_names

        assert not is_parallel_safe("nope", cwd)
        assert "read_file" in tool_names(cwd)

    def test_execute_unknown_and_path_error(self, cwd, tmp_path):
        assert "Unknown tool" in execute("nope", {}, cwd)
        outside = tmp_path / "x.txt"
        outside.write_text("s", encoding="utf-8")
        out = execute("read_file", {"path": str(outside)}, cwd)
        assert "[tool error]" in out

    def test_edit_file_requires_mode(self, cwd):
        assert "exactly one" in _edit_file({"path": "only.txt"}, cwd)

    def test_edits_multiple_matches(self, cwd, workspace):
        (workspace / "multi.txt").write_text("aa aa", encoding="utf-8")
        out = execute(
            "edit_file",
            {"path": "multi.txt", "edits": [{"oldText": "aa", "newText": "b"}]},
            cwd,
        )
        assert "found 2" in out

    def test_create_and_append_via_edit_file(self, cwd, workspace):
        out = _edit_file({"path": "direct.txt", "content": "new"}, cwd)
        assert out.startswith("Created direct.txt")
        out2 = _edit_file({"path": "direct.txt", "append": "!" }, cwd)
        assert out2.startswith("Appended")

    def test_search_directory_not_found(self, cwd):
        missing = Path(cwd) / "missing_dir"
        assert "Directory not found" in _search_files(missing, "*", "x", cwd)

    def test_search_delegates_to_rg(self, cwd, workspace, monkeypatch):
        monkeypatch.setattr(
            "services.tools._search_files_with_rg",
            lambda *a, **k: "rg: src/main.py:1:hit",
        )
        out = _search_files(workspace, "*", "hit", cwd)
        assert "rg:" in out

    def test_unix_shell_args_used(self, cwd, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = None
        proc.returncode = 0
        captured = {}

        def popen(args, **kwargs):
            captured["args"] = args
            return proc

        monkeypatch.setattr("services.tools.subprocess.Popen", popen)
        _run_shell_command("echo hi", cwd)
        assert captured["args"][:2] == ["/bin/sh", "-c"]

    def test_execute_shell_via_execute(self, cwd, monkeypatch):
        proc = MagicMock()
        proc.stdout = iter(["ok\n"])
        proc.wait.return_value = None
        proc.returncode = 0
        monkeypatch.setattr("services.tools.subprocess.Popen", lambda *a, **k: proc)
        assert "ok" in execute(shell_tool_name(), {"command": "echo ok"}, cwd)


class TestHelpers:
    def test_display_path_outside_cwd(self, tmp_path):
        outside = tmp_path / "other" / "file.txt"
        outside.parent.mkdir(parents=True)
        assert _display_path(outside.resolve(), str(tmp_path / "proj")) == str(outside.resolve())

    def test_tool_approval_unknown(self, cwd):
        assert tool_approval("missing_tool", cwd) is None

    def test_read_text_limited_direct(self, workspace):
        p = workspace / "tiny.txt"
        p.write_text("hi", encoding="utf-8")
        assert _read_text_limited(p, 100) == "hi"

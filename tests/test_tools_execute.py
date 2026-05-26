from pathlib import Path

import pytest

from services.tools import execute, is_parallel_safe, registry_for, tool_names
from tests.conftest import write_extension


@pytest.fixture
def cwd(workspace):
    return str(workspace)


class TestRegistryFor:
    def test_builtin_tool_names(self, cwd):
        names = set(tool_names(cwd))
        from services.shell_tool import shell_tool_name

        assert {shell_tool_name(), "read_file", "edit_file", "list_files", "search_files"} <= set(names)

    def test_extension_tool_merged(self, workspace_with_tool, cwd):
        cwd = str(workspace_with_tool)
        names = tool_names(cwd)
        assert "ping" in names
        assert is_parallel_safe("ping", cwd)

    def test_unknown_tool_message(self, cwd):
        out = execute("nonexistent_tool", {}, cwd)
        assert "[tool error] Unknown tool" in out
        assert "read_file" in out


class TestReadFile:
    def test_reads_workspace_file(self, cwd, workspace):
        out = execute("read_file", {"path": "src/main.py"}, cwd)
        assert "print('hi')" in out

    def test_reads_line_range(self, cwd, workspace):
        path = workspace / "lines.txt"
        path.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
        out = execute("read_file", {"path": "lines.txt", "offset": 2, "limit": 2}, cwd)
        assert out.startswith("line2\nline3\n")
        assert "[read: lines 2-3 of 5]" in out
        assert "more lines follow" in out

    def test_offset_past_eof(self, cwd, workspace):
        path = workspace / "short.txt"
        path.write_text("only\n", encoding="utf-8")
        out = execute("read_file", {"path": "short.txt", "offset": 9}, cwd)
        assert "past end of file" in out

    def test_blocks_path_outside_workspace(self, cwd, tmp_path):
        outside = tmp_path / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        out = execute("read_file", {"path": str(outside)}, cwd)
        assert "[tool error]" in out
        assert "must stay inside" in out


class TestEditFile:
    def test_create_file(self, cwd, workspace):
        out = execute(
            "edit_file",
            {"path": "new.txt", "content": "hello\n"},
            cwd,
        )
        assert out.startswith("Created new.txt")
        assert (workspace / "new.txt").read_text(encoding="utf-8") == "hello\n"

    def test_create_rejects_existing(self, cwd):
        out = execute(
            "edit_file",
            {"path": "src/main.py", "content": "x"},
            cwd,
        )
        assert "File already exists" in out

    def test_append(self, cwd, workspace):
        out = execute("edit_file", {"path": "src/main.py", "append": "\n# tail"}, cwd)
        assert "Appended" in out
        assert "# tail" in (workspace / "src" / "main.py").read_text(encoding="utf-8")

    def test_replace_via_edits(self, cwd, workspace):
        out = execute(
            "edit_file",
            {
                "path": "src/main.py",
                "edits": [{"oldText": "print('hi')", "newText": "print('bye')"}],
            },
            cwd,
        )
        assert "Edited" in out
        assert "print('bye')" in (workspace / "src" / "main.py").read_text(encoding="utf-8")

    def test_rejects_multiple_modes(self, cwd):
        out = execute(
            "edit_file",
            {"path": "x.txt", "content": "a", "append": "b"},
            cwd,
        )
        assert "exactly one of content, append, or edits" in out

    def test_rejects_literal_backslash_n_in_content(self, cwd):
        out = execute("edit_file", {"path": "bad.txt", "content": "a\\nb"}, cwd)
        assert "literal '\\n'" in out

    def test_edits_old_text_must_match_once(self, cwd):
        out = execute(
            "edit_file",
            {
                "path": "src/main.py",
                "edits": [{"oldText": "print('hi')", "newText": "x"}],
            },
            cwd,
        )
        assert "Edited" in out
        out2 = execute(
            "edit_file",
            {
                "path": "src/main.py",
                "edits": [{"oldText": "print('hi')", "newText": "y"}],
            },
            cwd,
        )
        assert "found 0" in out2


class TestExtensionToolExecute:
    def test_custom_tool_runs(self, workspace_with_tool):
        cwd = str(workspace_with_tool)
        assert execute("ping", {}, cwd) == "pong"


class TestSearchFiles:
    def test_finds_pattern_in_workspace(self, cwd):
        out = execute("search_files", {"pattern": "print"}, cwd)
        assert "main.py" in out or "print" in out

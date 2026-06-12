

from services.tool_policy import (
    path_in_repo,
    repo_root,
    resolve_path,
    validate_tool_paths,
)


class TestResolvePath:
    def test_relative_inside_workspace(self, workspace):
        resolved = resolve_path("src/main.py", str(workspace))
        assert resolved == (workspace / "src" / "main.py").resolve()

    def test_absolute_inside_workspace(self, workspace):
        target = workspace / "src" / "main.py"
        resolved = resolve_path(str(target), str(workspace))
        assert resolved == target.resolve()


class TestPathInRepo:
    def test_file_inside_repo(self, workspace):
        inside = workspace / "src" / "main.py"
        assert path_in_repo(inside, str(workspace))

    def test_sibling_outside_repo(self, workspace, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        assert not path_in_repo(outside.resolve(), str(workspace))

    def test_traversal_outside_repo(self, workspace, tmp_path):
        escape = (workspace / ".." / "escape.txt").resolve()
        escape.write_text("nope", encoding="utf-8")
        assert escape.parent == tmp_path
        assert not path_in_repo(escape, str(workspace))


class TestValidateToolPaths:
    def test_read_file_ok(self, workspace):
        assert validate_tool_paths("read_file", {"path": "src/main.py"}, str(workspace)) is None

    def test_edit_file_ok(self, workspace):
        assert validate_tool_paths("edit_file", {"path": "src/main.py"}, str(workspace)) is None

    def test_missing_path(self, workspace):
        err = validate_tool_paths("read_file", {}, str(workspace))
        assert err == "Missing read_file path."

    def test_wrong_argument_name_hint(self, workspace):
        err = validate_tool_paths("edit_file", {"file_path": "src/main.py"}, str(workspace))
        assert "Missing edit_file path." in err
        assert "'path', not file_path" in err

    def test_escape_via_dot_dot(self, workspace):
        err = validate_tool_paths("read_file", {"path": "../escape.txt"}, str(workspace))
        assert err is not None
        assert "must stay inside the workspace" in err

    def test_absolute_outside_workspace(self, workspace, tmp_path):
        outside = tmp_path / "other.py"
        outside.write_text("x", encoding="utf-8")
        err = validate_tool_paths("read_file", {"path": str(outside)}, str(workspace))
        assert err is not None
        assert str(repo_root(str(workspace))) in err

    def test_search_files_default_directory(self, workspace):
        assert validate_tool_paths("search_files", {}, str(workspace)) is None

    def test_list_files_default_directory(self, workspace):
        assert validate_tool_paths("list_files", {}, str(workspace)) is None

    def test_list_files_outside_directory(self, workspace, tmp_path):
        err = validate_tool_paths(
            "list_files",
            {"directory": str(tmp_path)},
            str(workspace),
        )
        assert err is not None
        assert "list directory" in err

    def test_search_files_outside_directory(self, workspace, tmp_path):
        err = validate_tool_paths(
            "search_files",
            {"directory": str(tmp_path)},
            str(workspace),
        )
        assert err is not None
        assert "search directory" in err

    def test_unknown_tool_skipped(self, workspace):
        assert validate_tool_paths("execute", {"command": "echo hi"}, str(workspace)) is None

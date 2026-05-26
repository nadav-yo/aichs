import sys

import pytest

from config import MAX_TOOL_OUTPUT_CHARS, MAX_TOOL_OUTPUT_LINES
from services.shell_tool import SHELL_TOOL_NAME, is_shell_tool, shell_tool_name
from services.tools import (
    _literal_newline_error,
    _shell_command_args,
    _strip_ansi,
    _trim_output,
)


class TestLiteralNewlineError:
    def test_detects_literal_backslash_n(self):
        err = _literal_newline_error("old_string", "line1\\nline2")
        assert err is not None
        assert "literal '\\n'" in err

    def test_allows_real_newlines(self):
        assert _literal_newline_error("new_string", "line1\nline2") is None

    def test_allows_plain_text(self):
        assert _literal_newline_error("old_string", "hello") is None


class TestShellCommandArgs:
    def test_windows_pwsh_disables_ansi_output(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("services.tools.shutil.which", lambda name: r"C:\pwsh\pwsh.exe" if name == "pwsh" else None)
        args = _shell_command_args("Get-ChildItem")
        assert args[0] == r"C:\pwsh\pwsh.exe"
        assert args[-1].startswith("$PSStyle.OutputRendering = 'PlainText'; ")
        assert args[-1].endswith("Get-ChildItem")

    def test_windows_legacy_powershell_unchanged_command(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("services.tools.shutil.which", lambda name: None)
        args = _shell_command_args("Get-ChildItem")
        assert args[-2:] == ["-Command", "Get-ChildItem"]

    def test_unix_uses_sh(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert _shell_command_args("echo hi") == ["/bin/sh", "-c", "echo hi"]


class TestShellToolName:
    def test_shell_tool_is_execute(self):
        assert shell_tool_name() == "execute"
        assert SHELL_TOOL_NAME == "execute"
        assert is_shell_tool("execute")
        assert not is_shell_tool("bash")


class TestStripAnsi:
    def test_strips_standard_sgr(self):
        assert _strip_ansi("\x1b[31;1mGet-Content: error\x1b[0m") == "Get-Content: error"

    def test_strips_orphan_sgr_prefix(self):
        assert _strip_ansi("[31;1mGet-Content: error") == "Get-Content: error"


class TestTrimOutput:
    def test_short_text_unchanged(self):
        text = "hello"
        assert _trim_output(text) == text

    def test_long_text_truncated(self):
        text = "x" * (MAX_TOOL_OUTPUT_CHARS + 100)
        out = _trim_output(text)
        assert len(out) == MAX_TOOL_OUTPUT_CHARS + len("\n\n[output truncated]")
        assert out.endswith("[output truncated]")

    def test_many_lines_truncated(self):
        text = "x\n" * (MAX_TOOL_OUTPUT_LINES + 2)
        out = _trim_output(text)
        assert out.endswith("[output truncated]")

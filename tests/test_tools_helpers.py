import sys

import pytest

from config import MAX_TOOL_OUTPUT_CHARS
from services.tools import _literal_newline_error, _shell_command_args, _trim_output


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
    def test_windows_uses_powershell_wrapper(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        args = _shell_command_args("Get-ChildItem")
        assert args[-2:] == ["-Command", "Get-ChildItem"]
        assert args[0].lower().endswith(("pwsh.exe", "powershell.exe", "pwsh", "powershell"))

    def test_unix_uses_sh(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert _shell_command_args("echo hi") == ["/bin/sh", "-c", "echo hi"]


class TestTrimOutput:
    def test_short_text_unchanged(self):
        text = "hello"
        assert _trim_output(text) == text

    def test_long_text_truncated(self):
        text = "x" * (MAX_TOOL_OUTPUT_CHARS + 100)
        out = _trim_output(text)
        assert len(out) == MAX_TOOL_OUTPUT_CHARS + len("\n\n[output truncated]")
        assert out.endswith("[output truncated]")

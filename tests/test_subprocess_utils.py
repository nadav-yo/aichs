import subprocess

from services import subprocess_utils


def test_no_window_creationflags_includes_windows_flags(monkeypatch):
    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)

    assert subprocess_utils.no_window_creationflags() == 0x08000000
    assert subprocess_utils.no_window_creationflags(process_group=True) == 0x08000200


def test_no_window_creationflags_non_windows(monkeypatch):
    monkeypatch.setattr(subprocess_utils.os, "name", "posix")

    assert subprocess_utils.no_window_creationflags(process_group=True) == 0
    assert subprocess_utils.no_window_startupinfo() is None


def test_text_mode_defaults_to_utf8_replace(monkeypatch):
    captured = {}

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(subprocess_utils.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess_utils, "no_window_creationflags", lambda process_group=False: 0)
    monkeypatch.setattr(subprocess_utils, "no_window_startupinfo", lambda: None)

    subprocess_utils.popen_no_window(["echo"], text=True, stdout=subprocess.PIPE)

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_text_mode_preserves_explicit_encoding(monkeypatch):
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(subprocess_utils.subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess_utils, "no_window_creationflags", lambda process_group=False: 0)
    monkeypatch.setattr(subprocess_utils, "no_window_startupinfo", lambda: None)

    subprocess_utils.run_no_window(["echo"], text=True, encoding="cp437", errors="strict")

    assert captured["encoding"] == "cp437"
    assert captured["errors"] == "strict"

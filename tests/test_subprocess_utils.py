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

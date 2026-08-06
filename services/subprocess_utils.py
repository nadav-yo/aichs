import os
import subprocess


def no_window_creationflags(*, process_group: bool = False) -> int:
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if process_group:
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def no_window_startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def _apply_text_defaults(kwargs: dict) -> dict:
    """Decode subprocess text pipes as UTF-8 instead of the Windows ANSI code page.

    ``text=True`` without an encoding uses locale encoding (often cp1252). Tool
    output is commonly UTF-8, and undecodable bytes crash the internal
    ``_readerthread`` with ``UnicodeDecodeError``.
    """
    text = kwargs.get("text")
    if text is None:
        text = kwargs.get("universal_newlines")
    if not text:
        return kwargs
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return kwargs


def run_no_window(*args, process_group: bool = False, **kwargs):
    kwargs.setdefault("creationflags", no_window_creationflags(process_group=process_group))
    kwargs.setdefault("startupinfo", no_window_startupinfo())
    return subprocess.run(*args, **_apply_text_defaults(kwargs))


def popen_no_window(*args, process_group: bool = False, **kwargs):
    kwargs.setdefault("creationflags", no_window_creationflags(process_group=process_group))
    kwargs.setdefault("startupinfo", no_window_startupinfo())
    return subprocess.Popen(*args, **_apply_text_defaults(kwargs))

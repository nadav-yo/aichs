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


def run_no_window(*args, process_group: bool = False, **kwargs):
    kwargs.setdefault("creationflags", no_window_creationflags(process_group=process_group))
    kwargs.setdefault("startupinfo", no_window_startupinfo())
    return subprocess.run(*args, **kwargs)


def popen_no_window(*args, process_group: bool = False, **kwargs):
    kwargs.setdefault("creationflags", no_window_creationflags(process_group=process_group))
    kwargs.setdefault("startupinfo", no_window_startupinfo())
    return subprocess.Popen(*args, **kwargs)

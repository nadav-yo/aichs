"""Parse `git status --short` for UI panels and workspace context."""

from __future__ import annotations

import os
from dataclasses import dataclass

from services.subprocess_utils import run_no_window


@dataclass(frozen=True)
class GitFileChange:
    code: str
    label: str
    rel_path: str
    abs_path: str


def run_git(cmd: list[str], cwd: str, timeout: float = 5) -> str:
    try:
        r = run_no_window(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def is_git_repo(repo_path: str) -> bool:
    return os.path.isdir(os.path.join(repo_path, ".git"))


def parse_status_line(line: str) -> tuple[str, str, str]:
    line = line.rstrip("\r")
    if len(line) < 2:
        return "", "", line
    code = line[:2]
    path = line[2:].lstrip()
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip().strip('"')
    label = {"??": "?", " M": "M", "M ": "M", "A ": "A", " D": "D", "D ": "D"}.get(
        code, code.strip() or "·",
    )
    return code, label, path


def list_file_changes(repo_path: str) -> list[GitFileChange]:
    """Uncommitted file changes (same filters as the Git tab)."""
    status = run_git(["git", "status", "--short", "-uall"], repo_path)
    if not status:
        return []

    changes: list[GitFileChange] = []
    for line in status.splitlines():
        code, label, path = parse_status_line(line)
        if path.endswith("/"):
            continue
        abs_path = path if os.path.isabs(path) else os.path.join(repo_path, path)
        abs_path = os.path.normpath(os.path.abspath(abs_path))
        if os.path.isdir(abs_path):
            continue
        changes.append(GitFileChange(code=code, label=label, rel_path=path, abs_path=abs_path))
    return changes

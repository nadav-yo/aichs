"""Parse `git status --short` for UI panels and workspace context."""

from __future__ import annotations

import os
from dataclasses import dataclass

from services.subprocess_utils import run_no_window


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


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


def run_git_command(cmd: list[str], cwd: str, timeout: float = 60) -> GitCommandResult:
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
        return GitCommandResult(
            returncode=r.returncode,
            stdout=(r.stdout or "").strip(),
            stderr=(r.stderr or "").strip(),
        )
    except Exception as e:
        return GitCommandResult(returncode=1, stdout="", stderr=str(e))


def count_commits_to_push(repo_path: str) -> int:
    """Return local commits ahead of the configured upstream branch."""
    if not is_git_repo(repo_path):
        return 0
    upstream = run_git(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], repo_path)
    if not upstream:
        return 0
    raw = run_git(["git", "rev-list", "--count", "@{u}..HEAD"], repo_path)
    try:
        return max(0, int(raw.strip()))
    except (TypeError, ValueError):
        return 0


def count_commits_to_pull(repo_path: str) -> int:
    """Return upstream commits not yet in HEAD, using local tracking info."""
    if not is_git_repo(repo_path):
        return 0
    upstream = run_git(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], repo_path)
    if not upstream:
        return 0
    raw = run_git(["git", "rev-list", "--count", "HEAD..@{u}"], repo_path)
    try:
        return max(0, int(raw.strip()))
    except (TypeError, ValueError):
        return 0


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

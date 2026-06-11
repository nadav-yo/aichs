"""Parse `git status --short` for UI panels and workspace context."""

from __future__ import annotations

import os
from dataclasses import dataclass

from services.performance import time_operation
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
    staged: bool = False
    unstaged: bool = True
    staged_label: str = ""
    unstaged_label: str = ""


def run_git(cmd: list[str], cwd: str, timeout: float = 5) -> str:
    with time_operation("git.run", detail=" ".join(cmd)):
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
            return (r.stdout or "").rstrip()
        except Exception:
            return ""


def run_git_command(cmd: list[str], cwd: str, timeout: float = 60) -> GitCommandResult:
    with time_operation("git.command", detail=" ".join(cmd)):
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


def status_stage_flags(code: str) -> tuple[bool, bool, str, str]:
    """Return staged/unstaged flags and per-section labels for a short-status code."""
    code = (code or "  ")[:2].ljust(2)
    if code == "??":
        return False, True, "", "?"
    index, worktree = code[0], code[1]
    staged = index not in (" ", "?")
    unstaged = worktree not in (" ", "?")
    return staged, unstaged, _status_char_label(index), _status_char_label(worktree)


def _status_char_label(ch: str) -> str:
    return "" if ch == " " else (ch or "").strip()


def _change_from_status_line(repo_path: str, line: str) -> GitFileChange | None:
    code, label, path = parse_status_line(line)
    if path.endswith("/"):
        return None
    abs_path = path if os.path.isabs(path) else os.path.join(repo_path, path)
    abs_path = os.path.normpath(os.path.abspath(abs_path))
    if os.path.isdir(abs_path):
        return None
    staged, unstaged, staged_label, unstaged_label = status_stage_flags(code)
    return GitFileChange(
        code=code,
        label=label,
        rel_path=path,
        abs_path=abs_path,
        staged=staged,
        unstaged=unstaged,
        staged_label=staged_label,
        unstaged_label=unstaged_label,
    )


def list_file_changes(repo_path: str) -> list[GitFileChange]:
    """Uncommitted file changes (same filters as the Git tab)."""
    if not is_git_repo(repo_path):
        return []

    status = run_git(["git", "status", "--short", "-uall"], repo_path)
    if not status:
        return []

    changes: list[GitFileChange] = []
    for line in status.splitlines():
        change = _change_from_status_line(repo_path, line)
        if change:
            changes.append(change)
    return changes


def stage_files(repo_path: str, rel_paths: list[str]) -> GitCommandResult:
    paths = repo_relative_paths(repo_path, rel_paths)
    if not paths:
        return GitCommandResult(returncode=1, stdout="", stderr="No files selected.")
    return run_git_command(["git", "add", "--", *paths], repo_path)


def unstage_files(repo_path: str, rel_paths: list[str]) -> GitCommandResult:
    paths = repo_relative_paths(repo_path, rel_paths)
    if not paths:
        return GitCommandResult(returncode=1, stdout="", stderr="No files selected.")
    return run_git_command(["git", "restore", "--staged", "--", *paths], repo_path)


def discard_files(repo_path: str, rel_paths: list[str], *, staged: bool = False) -> GitCommandResult:
    paths = repo_relative_paths(repo_path, rel_paths)
    if not paths:
        return GitCommandResult(returncode=1, stdout="", stderr="No files selected.")

    if staged:
        in_head = _paths_in_head(repo_path, paths)
        tracked_paths = [path for path in paths if path in in_head]
        added_paths = [path for path in paths if path not in in_head]
        results: list[GitCommandResult] = []
        if tracked_paths:
            results.append(
                run_git_command(
                    ["git", "restore", "--staged", "--worktree", "--", *tracked_paths],
                    repo_path,
                )
            )
        if added_paths:
            results.append(run_git_command(["git", "rm", "-f", "--cached", "--", *added_paths], repo_path))
            if results[-1].ok:
                results.append(run_git_command(["git", "clean", "-f", "--", *added_paths], repo_path))
        return _combined_git_result(results)

    tracked_paths = _tracked_paths(repo_path, paths)
    results: list[GitCommandResult] = []
    if tracked_paths:
        results.append(run_git_command(["git", "restore", "--worktree", "--", *tracked_paths], repo_path))
    results.append(run_git_command(["git", "clean", "-f", "--", *paths], repo_path))
    return _combined_git_result(results)


def stash_files(repo_path: str, rel_paths: list[str], message: str) -> GitCommandResult:
    paths = repo_relative_paths(repo_path, rel_paths)
    if not paths:
        return GitCommandResult(returncode=1, stdout="", stderr="No files selected.")
    msg = str(message or "").strip() or "AICHS stash"
    return run_git_command(["git", "stash", "push", "-u", "-m", msg, "--", *paths], repo_path)


def commit_staged(repo_path: str, summary: str, body: str = "") -> GitCommandResult:
    title = str(summary or "").strip()
    if not title:
        return GitCommandResult(returncode=1, stdout="", stderr="Commit summary is required.")
    cmd = ["git", "commit", "-m", title]
    detail = str(body or "").strip()
    if detail:
        cmd += ["-m", detail]
    return run_git_command(cmd, repo_path, timeout=120)


def repo_relative_paths(repo_path: str, rel_paths: list[str]) -> list[str]:
    root = os.path.normpath(os.path.abspath(repo_path))
    safe: list[str] = []
    seen: set[str] = set()
    for raw in rel_paths:
        path = str(raw or "").strip().replace("\\", "/")
        if not path:
            continue
        abs_path = path if os.path.isabs(path) else os.path.join(root, path)
        abs_path = os.path.normpath(os.path.abspath(abs_path))
        try:
            common = os.path.commonpath([root, abs_path])
        except ValueError:
            continue
        if common != root:
            continue
        rel = os.path.relpath(abs_path, root).replace("\\", "/")
        if rel.startswith("../") or rel == ".." or rel in seen:
            continue
        seen.add(rel)
        safe.append(rel)
    return safe


def _tracked_paths(repo_path: str, paths: list[str]) -> set[str]:
    if not paths:
        return set()
    output = run_git(["git", "ls-files", "--", *paths], repo_path)
    return {line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()}


def _paths_in_head(repo_path: str, paths: list[str]) -> set[str]:
    if not paths:
        return set()
    output = run_git(["git", "ls-tree", "-r", "--name-only", "HEAD", "--", *paths], repo_path)
    return {line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()}


def _combined_git_result(results: list[GitCommandResult]) -> GitCommandResult:
    if not results:
        return GitCommandResult(returncode=0, stdout="", stderr="")
    failed = next((result for result in results if not result.ok), None)
    returncode = failed.returncode if failed else 0
    return GitCommandResult(
        returncode=returncode,
        stdout="\n".join(result.stdout for result in results if result.stdout).strip(),
        stderr="\n".join(result.stderr for result in results if result.stderr).strip(),
    )

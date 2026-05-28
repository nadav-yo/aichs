"""Build unified diffs of working-tree files against HEAD."""

from __future__ import annotations

import difflib
import os

from config import MAX_FILE_PREVIEW_BYTES
from services.git_status import GitFileChange, is_git_repo, list_file_changes, run_git
from services.subprocess_utils import run_no_window


def _run_git(cmd: list[str], cwd: str) -> tuple[int, str]:
    try:
        r = run_no_window(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        return r.returncode, r.stdout or ""
    except Exception:
        return 1, ""


def change_for_file(repo_path: str, abs_path: str) -> GitFileChange | None:
    target = os.path.normpath(os.path.abspath(abs_path))
    for ch in list_file_changes(repo_path):
        if os.path.normpath(ch.abs_path) == target:
            return ch
    return None


def can_diff_against_head(repo_path: str, abs_path: str) -> bool:
    return change_for_file(repo_path, abs_path) is not None


def _read_text(path: str) -> tuple[str, bool]:
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", False
    with open(path, "rb") as f:
        raw = f.read(MAX_FILE_PREVIEW_BYTES + 1)
    truncated = len(raw) > MAX_FILE_PREVIEW_BYTES
    text = raw[:MAX_FILE_PREVIEW_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[Diff truncated: showing {MAX_FILE_PREVIEW_BYTES} of {size} bytes]"
    return text, truncated


def _head_text(repo_path: str, rel_path: str) -> str | None:
    code, out = _run_git(["git", "show", f"HEAD:{rel_path}"], repo_path)
    if code != 0:
        return None
    if len(out.encode("utf-8")) > MAX_FILE_PREVIEW_BYTES:
        out = out.encode("utf-8")[:MAX_FILE_PREVIEW_BYTES].decode("utf-8", errors="replace")
        out += f"\n\n[Diff truncated at {MAX_FILE_PREVIEW_BYTES} bytes]"
    return out


def _unified(old: str, new: str, rel_path: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    if old and not old_lines:
        old_lines = [""]
    if new and not new_lines:
        new_lines = [""]
    rel = rel_path.replace("\\", "/")
    lines = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        lineterm="",
    )
    return "\n".join(lines)


def diff_against_head(repo_path: str, abs_path: str) -> str | None:
    """
    Unified diff (working tree vs HEAD) for a changed file.
    Returns None if not a git repo, file is clean, or diff is empty.
    """
    if not is_git_repo(repo_path):
        return None

    ch = change_for_file(repo_path, abs_path)
    if not ch:
        return None

    rel = ch.rel_path.replace("\\", "/")

    if ch.code == "??":
        new, _ = _read_text(abs_path)
        text = _unified("", new, rel)
        return text or None

    if ch.code in ("A ", "A"):
        new, _ = _read_text(abs_path)
        text = _unified("", new, rel)
        return text or None

    if "D" in ch.code:
        old = _head_text(repo_path, rel)
        if old is None:
            return None
        text = _unified(old, "", rel)
        return text or None

    diff = run_git(["git", "diff", "HEAD", "--", rel], repo_path)
    if diff:
        return diff

    try:
        new, _ = _read_text(abs_path)
    except OSError:
        return None
    old = _head_text(repo_path, rel)
    if old is None:
        text = _unified("", new, rel)
    else:
        text = _unified(old, new, rel)
    return text or None

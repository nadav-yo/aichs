"""Build unified diffs of working-tree files against HEAD."""

from __future__ import annotations

import difflib
import os
import shlex
from dataclasses import dataclass

from config import MAX_FILE_PREVIEW_BYTES
from services.git_status import GitFileChange, is_git_repo, list_file_changes, run_git
from services.subprocess_utils import run_no_window


@dataclass(frozen=True)
class FileDiff:
    path: str
    diff: str
    added: int
    removed: int


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


def commit_diff(repo_path: str, commit_hash: str) -> str | None:
    """
    Unified diff for a committed change.
    Returns None if the repo or commit cannot be read.
    """
    sha = str(commit_hash or "").strip()
    if not sha or not is_git_repo(repo_path):
        return None

    code, _ = _run_git(["git", "show", "--no-patch", "--format=%H", sha], repo_path)
    if code != 0:
        return None

    code, patch = _run_git(
        ["git", "show", "--format=", "--no-color", "--patch", sha],
        repo_path,
    )
    if code != 0:
        return None
    return patch


def split_diff_by_file(unified_diff: str) -> list[FileDiff]:
    """Split a git unified diff into per-file chunks."""
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in str(unified_diff or "").splitlines():
        if line.startswith("diff --git ") and current:
            chunks.append(current)
            current = []
        current.append(line)
    if current:
        chunks.append(current)

    files: list[FileDiff] = []
    for chunk in chunks:
        text = "\n".join(chunk)
        path = _diff_chunk_path(chunk)
        added = sum(1 for line in chunk if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in chunk if line.startswith("-") and not line.startswith("---"))
        files.append(FileDiff(path=path, diff=text, added=added, removed=removed))
    return files


def _diff_chunk_path(lines: list[str]) -> str:
    for prefix in ("+++ ", "--- "):
        for line in lines:
            if line.startswith(prefix):
                path = line[len(prefix):].strip()
                if path != "/dev/null":
                    return _strip_diff_path_prefix(path)

    first = lines[0] if lines else ""
    try:
        parts = shlex.split(first)
    except ValueError:
        parts = first.split()
    if len(parts) >= 4 and parts[0] == "diff" and parts[1] == "--git":
        return _strip_diff_path_prefix(parts[3])
    return "(unknown file)"


def _strip_diff_path_prefix(path: str) -> str:
    path = path.strip().strip('"')
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path

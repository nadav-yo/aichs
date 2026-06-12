from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from services.git_status import (
    GitFileChange,
    is_git_repo,
    read_git_status_snapshot,
    run_git,
)
from services.performance import time_operation

_GIT_SNAPSHOT_CACHE_TTL_S = 2.0
_GIT_SNAPSHOT_CACHE: dict[str, tuple[float, "GitSnapshot"]] = {}
_GIT_LOG_CACHE: dict[str, tuple[str, tuple[str, ...]]] = {}
_GIT_SNAPSHOT_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class GitSnapshot:
    repo_path: str
    is_repo: bool
    changes: tuple[GitFileChange, ...] = ()
    log_lines: tuple[str, ...] = ()
    branch: str = ""
    ahead: int = 0
    behind: int = 0


def build_git_snapshot(repo_path: str) -> GitSnapshot:
    repo_key = _repo_cache_key(repo_path)
    now = time.monotonic()
    with _GIT_SNAPSHOT_CACHE_LOCK:
        cached = _GIT_SNAPSHOT_CACHE.get(repo_key)
        if cached is not None:
            created_at, snapshot = cached
            if now - created_at <= _GIT_SNAPSHOT_CACHE_TTL_S:
                return snapshot

    with time_operation("git.snapshot", detail=repo_key):
        if not is_git_repo(repo_key):
            snapshot = GitSnapshot(repo_path=repo_key, is_repo=False)
        else:
            status = read_git_status_snapshot(repo_key, check_repo=False)
            snapshot = GitSnapshot(
                repo_path=repo_key,
                is_repo=True,
                changes=status.changes,
                log_lines=tuple(_commit_log_lines(repo_key)),
                branch=status.branch,
                ahead=status.ahead,
                behind=status.behind,
            )
    with _GIT_SNAPSHOT_CACHE_LOCK:
        _GIT_SNAPSHOT_CACHE[repo_key] = (now, snapshot)
    return snapshot


def clear_git_snapshot_cache(repo_path: str | Path | None = None) -> None:
    with _GIT_SNAPSHOT_CACHE_LOCK:
        if repo_path is None:
            _GIT_SNAPSHOT_CACHE.clear()
            _GIT_LOG_CACHE.clear()
            return
        repo_key = _repo_cache_key(repo_path)
        _GIT_SNAPSHOT_CACHE.pop(repo_key, None)
        _GIT_LOG_CACHE.pop(repo_key, None)


def _repo_cache_key(repo_path: str | Path) -> str:
    return str(Path(repo_path).resolve())


def _commit_log_lines(repo_path: str) -> list[str]:
    head_oid = _head_oid(repo_path)
    if head_oid:
        with _GIT_SNAPSHOT_CACHE_LOCK:
            cached = _GIT_LOG_CACHE.get(repo_path)
            if cached is not None and cached[0] == head_oid:
                return list(cached[1])

    raw = run_git(
        ["git", "log", "--decorate=short", "--format=%H%x1f%h%x1f%D%x1f%s", "-40"],
        repo_path,
    )
    lines = tuple(line for line in raw.splitlines() if line.strip())
    if head_oid:
        with _GIT_SNAPSHOT_CACHE_LOCK:
            _GIT_LOG_CACHE[repo_path] = (head_oid, lines)
    return list(lines)


def _head_oid(repo_path: str) -> str:
    return run_git(["git", "rev-parse", "HEAD"], repo_path).strip()

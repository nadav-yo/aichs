from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from services.git_status import (
    GitFileChange,
    count_commits_to_pull,
    count_commits_to_push,
    is_git_repo,
    list_file_changes,
    run_git,
)
from services.performance import time_operation

_GIT_SNAPSHOT_CACHE_TTL_S = 2.0
_GIT_SNAPSHOT_CACHE: dict[str, tuple[float, "GitSnapshot"]] = {}
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
            snapshot = GitSnapshot(
                repo_path=repo_key,
                is_repo=True,
                changes=tuple(list_file_changes(repo_key)),
                log_lines=tuple(_commit_log_lines(repo_key)),
                branch=_branch_name(repo_key),
                ahead=count_commits_to_push(repo_key),
                behind=count_commits_to_pull(repo_key),
            )
    with _GIT_SNAPSHOT_CACHE_LOCK:
        _GIT_SNAPSHOT_CACHE[repo_key] = (now, snapshot)
    return snapshot


def clear_git_snapshot_cache(repo_path: str | Path | None = None) -> None:
    with _GIT_SNAPSHOT_CACHE_LOCK:
        if repo_path is None:
            _GIT_SNAPSHOT_CACHE.clear()
            return
        _GIT_SNAPSHOT_CACHE.pop(_repo_cache_key(repo_path), None)


def _repo_cache_key(repo_path: str | Path) -> str:
    return str(Path(repo_path).resolve())


def _commit_log_lines(repo_path: str) -> list[str]:
    raw = run_git(
        ["git", "log", "--decorate=short", "--format=%H%x1f%h%x1f%D%x1f%s", "-40"],
        repo_path,
    )
    return [line for line in raw.splitlines() if line.strip()]


def _branch_name(repo_path: str) -> str:
    return run_git(["git", "branch", "--show-current"], repo_path).strip()

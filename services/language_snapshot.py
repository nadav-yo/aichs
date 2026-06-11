from __future__ import annotations

import fnmatch
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from services.language_features import LanguageFeatureStatus, language_status
from services.performance import time_operation
from services.tool_registry import extension_cache_signature


_LANGUAGE_STATUS_CACHE_LOCK = threading.Lock()
_LANGUAGE_STATUS_CACHE: dict[
    tuple[str, tuple],
    tuple[tuple[LanguageFeatureStatus, ...], tuple[str, ...]],
] = {}


@dataclass(frozen=True)
class LanguageStatusSnapshot:
    path: str
    repo_root: str
    is_text: bool
    statuses: tuple[LanguageFeatureStatus, ...] = ()
    errors: tuple[str, ...] = ()


def build_language_status_snapshot(context: dict) -> LanguageStatusSnapshot:
    path = str(context.get("path") or "")
    repo_root = str(context.get("repo_root") or "")
    is_text = bool(context.get("is_text"))
    if not path or not repo_root or not is_text:
        return LanguageStatusSnapshot(path=path, repo_root=repo_root, is_text=is_text)

    statuses, errors = _cached_language_status(repo_root)

    rel = _relative_path(repo_root, path)
    name = os.path.basename(path)
    matches = tuple(
        status for status in statuses
        if any(
            fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern)
            for pattern in status.file_patterns
        )
    )
    return LanguageStatusSnapshot(
        path=path,
        repo_root=repo_root,
        is_text=is_text,
        statuses=matches,
        errors=tuple(errors),
    )


def clear_language_status_cache(repo_root: str | Path | None = None) -> None:
    with _LANGUAGE_STATUS_CACHE_LOCK:
        if repo_root is None:
            _LANGUAGE_STATUS_CACHE.clear()
            return
        root_key = str(Path(repo_root).resolve())
        for key in list(_LANGUAGE_STATUS_CACHE):
            if key[0] == root_key:
                _LANGUAGE_STATUS_CACHE.pop(key, None)


def _cached_language_status(repo_root: str) -> tuple[tuple[LanguageFeatureStatus, ...], tuple[str, ...]]:
    root_key = str(Path(repo_root).resolve())
    signature = extension_cache_signature(root_key)
    key = (root_key, signature)
    with _LANGUAGE_STATUS_CACHE_LOCK:
        cached = _LANGUAGE_STATUS_CACHE.get(key)
        if cached is not None:
            return cached

    with time_operation("language.status", detail=f"repo={root_key}"):
        statuses, errors = language_status(root_key)
    snapshot = (tuple(statuses), tuple(errors))
    with _LANGUAGE_STATUS_CACHE_LOCK:
        _LANGUAGE_STATUS_CACHE[key] = snapshot
    return snapshot


def _relative_path(repo_root: str, path: str) -> str:
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except (OSError, ValueError):
        return Path(path).name

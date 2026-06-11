import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config import IGNORED
from services.performance import time_operation

_WORKSPACE_FILE_CACHE_TTL_S = 2.0
_WORKSPACE_FILE_CACHE: dict[tuple[str, int], tuple[tuple[int, int, int], float, tuple[str, ...]]] = {}
_WORKSPACE_FILE_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class FileSearchMatch:
    path: str
    rel_path: str
    name: str
    score: int
    indices: tuple[int, ...]


@dataclass(frozen=True)
class FileSearchEntry:
    path: str
    rel_path: str
    name: str


@dataclass(frozen=True)
class FileSearchIndex:
    entries: tuple[FileSearchEntry, ...]

    @classmethod
    def from_root(cls, root: str | Path, *, scan_limit: int = 1200) -> "FileSearchIndex":
        root_path = Path(root).resolve()
        key = (str(root_path), int(scan_limit), tuple(sorted(IGNORED)))
        signature = _workspace_root_signature(root_path)
        now = time.monotonic()
        with _FILE_SEARCH_INDEX_CACHE_LOCK:
            cached = _FILE_SEARCH_INDEX_CACHE.get(key)
            if cached is not None:
                cached_signature, created_at, index = cached
                if cached_signature == signature and now - created_at <= _WORKSPACE_FILE_CACHE_TTL_S:
                    return index

        with time_operation(
            "file_search.index",
            detail=f"root={root_path} limit={scan_limit}",
        ):
            entries = []
            for path in list_workspace_files(root_path, limit=scan_limit):
                entries.append(
                    FileSearchEntry(
                        path=path,
                        rel_path=os.path.relpath(path, root_path),
                        name=os.path.basename(path),
                    )
                )
            index = cls(tuple(entries))
            with _FILE_SEARCH_INDEX_CACHE_LOCK:
                _FILE_SEARCH_INDEX_CACHE[key] = (signature, now, index)
            return index

    def search(
        self,
        query: str,
        *,
        limit: int = 80,
        entries: Iterable[FileSearchEntry] | None = None,
    ) -> list[FileSearchMatch]:
        matches, _candidates = self.search_with_candidates(
            query,
            limit=limit,
            entries=entries,
        )
        return matches

    def search_with_candidates(
        self,
        query: str,
        *,
        limit: int = 80,
        entries: Iterable[FileSearchEntry] | None = None,
    ) -> tuple[list[FileSearchMatch], tuple[FileSearchEntry, ...]]:
        source = self.entries if entries is None else tuple(entries)
        with time_operation(
            "file_search.query",
            detail=f"query_len={len(query.strip())} entries={len(source)} limit={limit}",
        ):
            matches: list[FileSearchMatch] = []
            candidates: list[FileSearchEntry] = []
            for entry in source:
                name_score, name_indices = match_file_name(query, entry.name)
                rel_score, _rel_indices = match_file_name(query, entry.rel_path)
                if name_score <= 0 and rel_score <= 0:
                    continue
                candidates.append(entry)
                score = max(name_score * 2, rel_score)
                matches.append(
                    FileSearchMatch(
                        entry.path,
                        entry.rel_path,
                        entry.name,
                        score,
                        name_indices,
                    )
                )
            matches.sort(key=lambda item: (-item.score, item.rel_path.casefold()))
            return matches[:limit], tuple(candidates)


_FILE_SEARCH_INDEX_CACHE: dict[
    tuple[str, int, tuple[str, ...]],
    tuple[tuple[int, int, int], float, FileSearchIndex],
] = {}
_FILE_SEARCH_INDEX_CACHE_LOCK = threading.Lock()


def match_file_name(query: str, text: str) -> tuple[int, tuple[int, ...]]:
    q = query.strip()
    if not q:
        return 1, ()

    folded_text = text.casefold()
    folded_query = q.casefold()
    start = folded_text.find(folded_query)
    if start >= 0:
        indices = tuple(range(start, start + len(q)))
        score = 3000 - start * 4 + _boundary_bonus(text, start) + len(q)
        return score, indices

    return _subsequence_match(q, text)


def search_file_names(
    root: str | Path,
    query: str,
    *,
    limit: int = 80,
    scan_limit: int = 1200,
) -> list[FileSearchMatch]:
    return FileSearchIndex.from_root(root, scan_limit=scan_limit).search(query, limit=limit)


def list_workspace_files(root: str | Path, *, limit: int = 1200) -> list[str]:
    root_path = Path(root).resolve()
    key = (str(root_path), int(limit))
    signature = _workspace_root_signature(root_path)
    now = time.monotonic()
    with _WORKSPACE_FILE_CACHE_LOCK:
        cached = _WORKSPACE_FILE_CACHE.get(key)
        if cached is not None:
            cached_signature, created_at, files = cached
            if cached_signature == signature and now - created_at <= _WORKSPACE_FILE_CACHE_TTL_S:
                return list(files)

    with time_operation("workspace.files", detail=f"root={root_path} limit={limit}"):
        found: list[str] = []
        if root_path.is_dir():
            _walk_files(root_path, found, limit)
        with _WORKSPACE_FILE_CACHE_LOCK:
            _WORKSPACE_FILE_CACHE[key] = (signature, now, tuple(found))
        return found


def clear_workspace_file_cache(root: str | Path | None = None) -> None:
    with _WORKSPACE_FILE_CACHE_LOCK:
        if root is None:
            _WORKSPACE_FILE_CACHE.clear()
        else:
            root_key = str(Path(root).resolve())
            for key in list(_WORKSPACE_FILE_CACHE):
                if key[0] == root_key:
                    _WORKSPACE_FILE_CACHE.pop(key, None)
    with _FILE_SEARCH_INDEX_CACHE_LOCK:
        if root is None:
            _FILE_SEARCH_INDEX_CACHE.clear()
            return
        root_key = str(Path(root).resolve())
        for key in list(_FILE_SEARCH_INDEX_CACHE):
            if key[0] == root_key:
                _FILE_SEARCH_INDEX_CACHE.pop(key, None)


def _workspace_root_signature(root: Path) -> tuple[int, int, int]:
    try:
        stat = root.stat()
    except OSError:
        return (0, 0, 0)
    return (int(stat.st_mtime_ns), int(stat.st_ctime_ns), int(stat.st_size))


def _walk_files(dir_path: Path, found: list[str], limit: int) -> None:
    if len(found) >= limit:
        return
    try:
        entries = sorted(dir_path.iterdir(), key=lambda e: e.name.lower())
    except PermissionError:
        return
    for entry in entries:
        if len(found) >= limit:
            return
        if entry.name in IGNORED or entry.name.startswith("."):
            continue
        if entry.is_file():
            found.append(str(entry))
        elif entry.is_dir():
            _walk_files(entry, found, limit)


def _subsequence_match(query: str, text: str) -> tuple[int, tuple[int, ...]]:
    states: list[tuple[int, tuple[int, ...]]] = [(-10**9, ()) for _ in query]
    folded_query = query.casefold()
    for index, char in enumerate(text):
        folded_char = char.casefold()
        for qi in range(len(query) - 1, -1, -1):
            if folded_query[qi] != folded_char:
                continue
            if qi == 0:
                prev_score = 0
                prev_indices: tuple[int, ...] = ()
            else:
                prev_score, prev_indices = states[qi - 1]
                if prev_score < 0:
                    continue
            states[qi] = max(
                states[qi],
                (
                    prev_score + _char_score(text, index, prev_indices),
                    (*prev_indices, index),
                ),
                key=lambda state: (state[0], -sum(state[1])),
            )
    score, indices = states[-1]
    if score < 0:
        return 0, ()
    return score, indices


def _char_score(text: str, index: int, prev_indices: Iterable[int]) -> int:
    indices = tuple(prev_indices)
    score = 30 + _boundary_bonus(text, index)
    if indices and index == indices[-1] + 1:
        score += 35
    elif indices:
        score -= min(18, index - indices[-1] - 1)
    return score


def _boundary_bonus(text: str, index: int) -> int:
    if index <= 0:
        return 55
    current = text[index]
    previous = text[index - 1]
    if previous in "_-. /\\:":
        return 45
    if previous.islower() and current.isupper():
        return 45
    if previous.isalpha() != current.isalpha():
        return 25
    return 0

import os
from dataclasses import dataclass
from pathlib import Path

from config import IGNORED, MAX_TREE_ENTRIES_PER_DIR
from services.git_snapshot import GitSnapshot
from services.git_status import list_file_changes
from services.performance import time_operation


@dataclass(frozen=True)
class FileTreeEntry:
    name: str
    abs_path: str
    is_dir: bool
    display_name: str = ""


@dataclass(frozen=True)
class FileTreeGitStatus:
    abs_path: str
    code: str
    label: str


@dataclass(frozen=True)
class FileTreeSnapshot:
    root_path: str
    filter_text: str
    entries: tuple[FileTreeEntry, ...] = ()
    omitted: int = 0
    git_status: tuple[FileTreeGitStatus, ...] = ()


def build_file_tree_snapshot(
    root_path: str,
    *,
    filter_text: str = "",
    git_snapshot: GitSnapshot | None = None,
    git_changes=None,
    load_git_status: bool = True,
    cancelled=None,
) -> FileTreeSnapshot:
    with time_operation(
        "file_tree.populate",
        detail=f"root={root_path} filtered={bool(filter_text)}",
    ):
        if _cancelled(cancelled):
            entries, omitted = [], 0
            git_status = ()
        else:
            entries, omitted = (
                _filtered_entries(root_path, filter_text, cancelled=cancelled)
                if filter_text
                else _directory_entries(root_path)
            )
            git_status = () if _cancelled(cancelled) else _git_status(
                root_path,
                git_snapshot,
                git_changes,
                load_git_status,
            )
        return FileTreeSnapshot(
            root_path=root_path,
            filter_text=filter_text,
            entries=tuple(entries),
            omitted=omitted,
            git_status=git_status,
        )


def build_directory_snapshot(path: str) -> FileTreeSnapshot:
    with time_operation("file_tree.children", detail=f"path={path}"):
        entries, omitted = _directory_entries(path)
        return FileTreeSnapshot(root_path=path, filter_text="", entries=tuple(entries), omitted=omitted)


def _directory_entries(path: str) -> tuple[list[FileTreeEntry], int]:
    try:
        with os.scandir(path) as scanned:
            entries = []
            for entry in scanned:
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                entries.append((entry.name, entry.path, is_dir))
    except (OSError, PermissionError):
        return [], 0
    entries.sort(key=lambda entry: (not entry[2], entry[0].lower()))
    visible = [
        entry for entry in entries
        if _is_visible_tree_entry(entry[0], entry[2])
    ]
    items = [
        FileTreeEntry(name, abs_path, is_dir)
        for name, abs_path, is_dir in visible[:MAX_TREE_ENTRIES_PER_DIR]
    ]
    return items, max(0, len(visible) - MAX_TREE_ENTRIES_PER_DIR)


def _filtered_entries(root_path: str, filter_text: str, *, cancelled=None) -> tuple[list[FileTreeEntry], int]:
    root = Path(root_path)
    try:
        resolved_root = root.resolve()
    except OSError:
        resolved_root = root.absolute()
    terms = [term for term in filter_text.split(" ") if term]
    if not terms:
        return [], 0
    matches: list[FileTreeEntry] = []
    omitted = 0
    for dirpath, dirnames, filenames in os.walk(root_path, onerror=lambda _e: None):
        if _cancelled(cancelled):
            break
        dirnames[:] = sorted(
            [name for name in dirnames if _is_visible_tree_entry(name, True)],
            key=str.lower,
        )
        entries = [(name, True) for name in dirnames] + [(name, False) for name in filenames]
        for name, is_dir in sorted(entries, key=lambda entry: (not entry[1], entry[0].lower())):
            if _cancelled(cancelled):
                break
            if not _is_visible_tree_entry(name, is_dir):
                continue
            path = os.path.join(dirpath, name)
            try:
                rel = Path(path).resolve(strict=False).relative_to(resolved_root).as_posix()
            except (OSError, ValueError):
                continue
            if not all(term in rel.casefold() for term in terms):
                continue
            if len(matches) >= MAX_TREE_ENTRIES_PER_DIR:
                omitted += 1
                continue
            matches.append(FileTreeEntry(name, path, is_dir, display_name=rel))
    return matches, omitted


def _git_status(
    root_path: str,
    git_snapshot: GitSnapshot | None,
    git_changes,
    load_git_status: bool,
) -> tuple[FileTreeGitStatus, ...]:
    if not load_git_status:
        return ()
    if git_snapshot is not None:
        changes = git_snapshot.changes if git_snapshot.is_repo else ()
    else:
        changes = list_file_changes(root_path) if git_changes is None else git_changes
    return tuple(
        FileTreeGitStatus(ch.abs_path, ch.code, ch.label)
        for ch in changes
    )


def _is_visible_tree_entry(name: str, is_dir: bool) -> bool:
    if name in IGNORED:
        return False
    return True


def _cancelled(cancelled) -> bool:
    return bool(cancelled and cancelled())

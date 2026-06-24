from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config
from services.git_snapshot import GitSnapshot, build_git_snapshot
from services.git_status import GitFileChange
from services.performance import time_operation
from storage.repository import ConversationStore, list_workspaces


PREVIEW_LIMIT = 18_000
README_NAMES = ("README.md", "README.markdown", "README.txt", "README")


@dataclass(frozen=True)
class RecentChat:
    path: str
    title: str
    updated_at: str
    message_count: int


@dataclass(frozen=True)
class RecentWorkspace:
    path: str
    name: str
    updated_at: str
    exists: bool


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    name: str
    agents_exists: bool
    agents_text: str
    skills_count: int
    extensions_count: int
    git_repo: bool
    changed_count: int
    branch: str
    readme_exists: bool
    readme_text: str
    recent_chats: tuple[RecentChat, ...]
    recent_workspaces: tuple[RecentWorkspace, ...]


def build_workspace_snapshot(
    root: str,
    *,
    git_snapshot: GitSnapshot | None = None,
    git_changes: list[GitFileChange] | None = None,
) -> WorkspaceSnapshot:
    root_path = Path(root).resolve()
    with time_operation("workspace.snapshot", detail=str(root_path)):
        agents_path = root_path / "AGENTS.md"
        readme_path = _first_existing(root_path, README_NAMES)
        if git_snapshot is None:
            if git_changes is None:
                git_snapshot = build_git_snapshot(str(root_path))
            else:
                git_snapshot = GitSnapshot(
                    repo_path=str(root_path),
                    is_repo=True,
                    changes=tuple(git_changes),
                )
        return WorkspaceSnapshot(
            root=str(root_path),
            name=root_path.name or str(root_path),
            agents_exists=agents_path.is_file(),
            agents_text=_read_text(agents_path) if agents_path.is_file() else "",
            skills_count=_skill_count(root_path),
            extensions_count=_extension_count(root_path),
            git_repo=git_snapshot.is_repo,
            changed_count=len(git_snapshot.changes),
            branch=git_snapshot.branch,
            readme_exists=readme_path is not None,
            readme_text=_read_text(readme_path) if readme_path else "",
            recent_chats=_recent_chats(root_path),
            recent_workspaces=_recent_workspaces(root_path),
        )


def display_updated_at(value: str) -> str:
    if not value:
        return "Recent"
    dt = datetime.fromisoformat(value)
    return dt.strftime("Last opened %b %d, %Y %H:%M")


def display_chat_time(value: str) -> str:
    if not value:
        return "Recent"
    dt = datetime.fromisoformat(value)
    return dt.strftime("%b %d, %Y %H:%M")


def _recent_chats(root: Path) -> tuple[RecentChat, ...]:
    rows = []
    for path, summary in ConversationStore(str(root)).list_all()[:5]:
        rows.append(
            RecentChat(
                path=str(path),
                title=str(summary.get("title") or "Untitled"),
                updated_at=str(summary.get("updated_at") or ""),
                message_count=int(summary.get("message_count") or 0),
            )
        )
    return tuple(rows)


def _recent_workspaces(root: Path) -> tuple[RecentWorkspace, ...]:
    current_key = os.path.normcase(os.path.abspath(str(root)))
    rows = []
    for row in list_workspaces():
        path = str(row.get("path") or "")
        if not path:
            continue
        path_key = os.path.normcase(os.path.abspath(path))
        if path_key == current_key:
            continue
        rows.append(
            RecentWorkspace(
                path=path,
                name=str(row.get("name") or Path(path).name or path),
                updated_at=str(row.get("updated_at") or ""),
                exists=bool(row.get("exists")),
            )
        )
    return tuple(rows)


def _first_existing(root: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        path = root / name
        if path.is_file():
            return path
    return None


def _read_text(path: Path, limit: int = PREVIEW_LIMIT) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(limit)


def _extension_count(root: Path) -> int:
    ext_dir = root / config.PROJECT_AICHS_DIR / "extensions"
    if not ext_dir.is_dir():
        return 0
    count = 0
    for child in ext_dir.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_file() and child.suffix == ".py":
            count += 1
        elif child.is_dir() and (child / "extension.py").is_file():
            count += 1
    return count


def _skill_count(root: Path) -> int:
    skills_dir = root / config.PROJECT_AGENTS_DIR / "skills"
    if not skills_dir.is_dir():
        return 0
    count = 0
    for child in skills_dir.iterdir():
        if child.name.startswith("."):
            continue
        if child.suffix == ".md" and child.is_file():
            count += 1
    return count

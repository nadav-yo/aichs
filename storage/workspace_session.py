"""Per-workspace UI session snapshots (last chat, open files, layout)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config import AICHS_HOME
from storage.repository import workspace_id

SESSION_VERSION = 1


def session_path(workspace: str | Path) -> Path:
    return AICHS_HOME / "sessions" / f"{workspace_id(workspace)}.json"


def _normalize_open_files(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    files: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        try:
            line = max(1, int(item.get("line") or 1))
        except (TypeError, ValueError):
            line = 1
        files.append({
            "path": path,
            "line": line,
            "active": bool(item.get("active")),
        })
    if files and not any(item["active"] for item in files):
        files[0]["active"] = True
    elif files:
        active_seen = False
        for item in files:
            if item["active"]:
                if active_seen:
                    item["active"] = False
                else:
                    active_seen = True
        if not active_seen:
            files[0]["active"] = True
    return files


def normalize_session(data: dict | None) -> dict:
    data = data if isinstance(data, dict) else {}
    conversation_id = str(data.get("conversation_id") or "").strip()
    open_files = _normalize_open_files(data.get("open_files"))
    workbench_sizes = data.get("workbench_sizes")
    sizes: list[int] | None = None
    if isinstance(workbench_sizes, list) and len(workbench_sizes) == 2:
        try:
            sizes = [max(1, int(workbench_sizes[0])), max(1, int(workbench_sizes[1]))]
        except (TypeError, ValueError):
            sizes = None
    panel = str(data.get("context_panel") or "run_log").strip().lower()
    if panel not in {"run_log", "language"}:
        panel = "run_log"
    return {
        "version": SESSION_VERSION,
        "conversation_id": conversation_id,
        "open_files": open_files,
        "viewer_visible": bool(data.get("viewer_visible")),
        "workbench_sizes": sizes,
        "context_panel": panel,
        "context_collapsed": bool(data.get("context_collapsed", True)),
        "updated_at": str(data.get("updated_at") or ""),
    }


def session_has_restorable_state(data: dict | None) -> bool:
    session = normalize_session(data)
    return bool(session["conversation_id"] or session["open_files"])


def load_workspace_session(workspace: str | Path) -> dict:
    path = session_path(workspace)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return normalize_session({})
    return normalize_session(raw if isinstance(raw, dict) else {})


def save_workspace_session(workspace: str | Path, data: dict) -> None:
    session = normalize_session(data)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = session_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(session, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def resolve_open_file_path(path: str, repo_root: str | Path) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(repo_root) / candidate
    return str(candidate.resolve())

import json
import os
import re
import hashlib
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import config
from config import AICHS_HOME, CONV_DIR, WORKSPACES_PATH
from services.content import is_visible_message
from services.performance import time_operation
from storage.settings import SettingsStore, trash_retention_days


_last_workspace_updated_at: datetime | None = None
_CONVERSATION_INDEX_NAME = "conversation_index.v1"
_IMPORTED_AICHS_HOME = AICHS_HOME
_IMPORTED_CONV_DIR = CONV_DIR
_IMPORTED_WORKSPACES_PATH = WORKSPACES_PATH


class ConversationStore:
    def __init__(self, workspace: str | None = None):
        self.workspace = _resolve_workspace(workspace)
        self.workspace_id = workspace_id(self.workspace) if self.workspace else ""
        self.conv_dir = (
            workspace_conversations_dir(self.workspace)
            if self.workspace
            else _conv_dir()
        )
        self.trash_dir = (
            workspace_trash_dir(self.workspace)
            if self.workspace
            else _aichs_home() / "trash"
        )
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        if self.workspace:
            _register_workspace(self.workspace, self.workspace_id)
        self.prune_trash()
        _prune_leaked_test_conversations()

    def list_all(self) -> list[tuple[Path, dict]]:
        by_id: dict[str, tuple[Path, dict]] = {}
        with time_operation("conversation.list", detail=f"dir={self.conv_dir}"):
            records = self._index_records()
        for p, summary in records:
            conv_id = summary.get("id") or p.stem
            summary["id"] = conv_id
            prev = by_id.get(conv_id)
            if prev is None or _is_newer(summary, prev[1], p, prev[0]):
                by_id[conv_id] = (p, summary)
        convs = list(by_id.values())
        pinned = sorted(
            [c for c in convs if c[1].get("pinned")],
            key=lambda x: x[1].get("updated_at", ""),
            reverse=True,
        )
        rest = sorted(
            [c for c in convs if not c[1].get("pinned")],
            key=lambda x: x[1].get("updated_at", ""),
            reverse=True,
        )
        return pinned + rest

    def load(self, path: str) -> dict:
        with time_operation("conversation.load", detail=f"path={path}"):
            return json.loads(Path(path).read_text(encoding="utf-8"))

    def delete(self, path: str) -> None:
        source = Path(path)
        if not source.exists():
            return
        data = self.load(str(source))
        conv_id = str(data.get("id") or source.stem)
        data["id"] = conv_id
        data["deleted_at"] = datetime.now().isoformat()
        data["deleted_from"] = str(source)
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        target = _available_path(self.trash_dir / f"{conv_id}.json")
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        source.unlink(missing_ok=True)
        self._remove_index_path(source)

    def list_trash(self) -> list[tuple[Path, dict]]:
        self.prune_trash()
        records: list[tuple[Path, dict]] = []
        for path in sorted(self.trash_dir.glob("*.json")):
            data = _load_json(path)
            if data is None:
                continue
            summary = _summary(data)
            summary["id"] = summary.get("id") or path.stem
            summary["deleted_at"] = data.get("deleted_at", "")
            records.append((path, summary))
        return sorted(
            records,
            key=lambda item: item[1].get("deleted_at") or item[1].get("updated_at", ""),
            reverse=True,
        )

    def restore(self, path: str) -> Path:
        source = Path(path)
        data = self.load(str(source))
        conv_id = str(data.get("id") or source.stem)
        data.pop("deleted_at", None)
        data.pop("deleted_from", None)
        restored = self.save(conv_id, data)
        source.unlink(missing_ok=True)
        return restored

    def prune_trash(self, retention_days: int | None = None) -> int:
        if retention_days is None:
            retention_days = trash_retention_days(SettingsStore().load())
        cutoff = datetime.now() - timedelta(days=trash_retention_days({
            "trash_retention_days": retention_days,
        }))
        removed = 0
        for path in list(self.trash_dir.glob("*.json")):
            data = _load_json(path)
            deleted_at = _parse_datetime((data or {}).get("deleted_at"))
            if deleted_at is None:
                continue
            if deleted_at <= cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def save(self, conv_id: str, data: dict) -> Path:
        data["id"] = conv_id
        if self.workspace:
            data["workspace_id"] = self.workspace_id
            data.setdefault("cwd", str(self.workspace))
        path = self.conv_dir / f"{conv_id}.json"
        with time_operation("conversation.save", detail=f"id={conv_id}"):
            _write_json_atomic(path, data)
            self._upsert_index_path(path, _summary(data))
            self._drop_duplicate_files(conv_id, keep=path)
        return path

    def load_by_id(self, conv_id: str) -> dict:
        wanted = str(conv_id or "").strip()
        with time_operation("conversation.load_by_id", detail=f"id={wanted}"):
            return self.load(str(self.path_for_id(wanted)))

    def path_for_id(self, conv_id: str) -> Path:
        wanted = str(conv_id or "").strip()
        for path, summary in self._index_records():
            if str(summary.get("id") or path.stem) == wanted or path.stem == wanted:
                return path
        raise FileNotFoundError(wanted)

    def rename(self, path: str, title: str) -> str:
        data = self.load(path)
        data["title"] = title.strip() or "Untitled"
        data["title_auto"] = False
        conv_id = data.get("id") or Path(path).stem
        self.save(conv_id, data)
        return conv_id

    def _drop_duplicate_files(self, conv_id: str, *, keep: Path) -> None:
        keep_resolved = keep.resolve()
        removed: list[Path] = []
        for p, summary in self._index_records():
            if p.resolve() == keep_resolved:
                continue
            other_id = summary.get("id") or p.stem
            if other_id == conv_id:
                p.unlink(missing_ok=True)
                removed.append(p)
        for p in removed:
            self._remove_index_path(p)

    def toggle_pin(self, path: str) -> bool:
        data = self.load(path)
        data["pinned"] = not data.get("pinned", False)
        self.save(data["id"], data)
        return data["pinned"]

    @staticmethod
    def new_id() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def make_title(first_message: str) -> str:
        return first_message[:50] + ("…" if len(first_message) > 50 else "")

    def matches_search(self, path: Path, summary: dict, query: str) -> bool:
        q = query.casefold()
        if q in summary.get("title", "").casefold():
            return True
        search_text = summary.get("search_text", "")
        return isinstance(search_text, str) and q in search_text.casefold()

    def iter_records(self) -> list[tuple[Path, dict]]:
        records: list[tuple[Path, dict]] = []
        for path in sorted(self.conv_dir.glob("*.json")):
            data = _load_json(path)
            if data is None:
                continue
            records.append((path, data))
        return records

    def _index_path(self) -> Path:
        return self.conv_dir / _CONVERSATION_INDEX_NAME

    def _index_records(self) -> list[tuple[Path, dict]]:
        index = self._read_index()
        rows = index.get("rows") if isinstance(index, dict) else None
        if not isinstance(rows, dict):
            rows = {}
        changed = not rows
        next_rows: dict[str, dict] = {}

        for path in sorted(self.conv_dir.glob("*.json")):
            try:
                stat = path.stat()
            except OSError:
                changed = True
                continue
            key = path.name
            row = rows.get(key)
            if (
                isinstance(row, dict)
                and row.get("mtime_ns") == stat.st_mtime_ns
                and row.get("size") == stat.st_size
                and isinstance(row.get("summary"), dict)
                and "workspace_id" in row["summary"]
                and isinstance(row["summary"].get("search_text"), str)
                and isinstance(row["summary"].get("search_messages"), list)
            ):
                next_rows[key] = row
                continue
            data = _load_json(path)
            changed = True
            if data is None:
                continue
            summary = _summary(data)
            summary["id"] = summary.get("id") or path.stem
            next_rows[key] = _index_row(path, summary, stat)

        if set(rows) != set(next_rows):
            changed = True
        if changed:
            self._write_index(next_rows)

        return [
            (self.conv_dir / name, dict(row["summary"]))
            for name, row in next_rows.items()
            if isinstance(row.get("summary"), dict)
        ]

    def _read_index(self) -> dict:
        data = _load_json(self._index_path())
        if isinstance(data, dict) and data.get("version") == 1:
            return data
        return {"version": 1, "rows": {}}

    def _write_index(self, rows: dict[str, dict]) -> None:
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self._index_path(), {"version": 1, "rows": rows})

    def _upsert_index_path(self, path: Path, summary: dict) -> None:
        index = self._read_index()
        rows = index.get("rows")
        if not isinstance(rows, dict):
            rows = {}
        try:
            stat = path.stat()
        except OSError:
            return
        summary = dict(summary)
        summary["id"] = summary.get("id") or path.stem
        rows[path.name] = _index_row(path, summary, stat)
        self._write_index(rows)

    def _remove_index_path(self, path: Path) -> None:
        index = self._read_index()
        rows = index.get("rows")
        if not isinstance(rows, dict) or path.name not in rows:
            return
        rows.pop(path.name, None)
        self._write_index(rows)

def workspace_id(workspace: str | Path) -> str:
    path = Path(workspace).expanduser().resolve()
    name = path.name or "workspace"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip(".-").lower() or "workspace"
    slug = slug[:48].rstrip(".-") or "workspace"
    digest = hashlib.sha1(os.path.normcase(str(path)).encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def workspace_data_dir(workspace: str | Path) -> Path:
    return _aichs_home() / workspace_id(workspace)


def workspace_conversations_dir(workspace: str | Path) -> Path:
    return workspace_data_dir(workspace) / "conversations"


def workspace_trash_dir(workspace: str | Path) -> Path:
    return workspace_data_dir(workspace) / "trash"


def register_workspace(workspace: str | Path) -> dict:
    path = Path(workspace).expanduser().resolve()
    wid = workspace_id(path)
    updated_at = _workspace_updated_at()
    _register_workspace(path, wid, updated_at=updated_at)
    return {
        "id": wid,
        "path": str(path),
        "name": path.name or str(path),
        "updated_at": updated_at,
        "exists": path.is_dir(),
    }


def list_workspaces(limit: int | None = None) -> list[dict]:
    data = _load_json(_workspaces_path())
    if not isinstance(data, dict):
        return []
    raw_workspaces = data.get("workspaces")
    if not isinstance(raw_workspaces, dict):
        return []

    rows = []
    for wid, raw in raw_workspaces.items():
        if not isinstance(raw, dict):
            continue
        if _is_leaked_test_workspace(str(wid), raw):
            continue
        path = str(raw.get("path") or "").strip()
        if not path:
            continue
        name = str(raw.get("name") or "").strip()
        rows.append({
            "id": str(raw.get("id") or wid),
            "path": path,
            "name": name or Path(path).name or path,
            "updated_at": str(raw.get("updated_at") or ""),
            "exists": Path(path).expanduser().is_dir(),
        })

    rows.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    if limit is not None:
        return rows[:max(0, int(limit))]
    return rows


def remove_workspace(workspace: str | Path) -> bool:
    raw_path = str(workspace or "").strip()
    if not raw_path:
        return False
    path = Path(raw_path).expanduser().resolve()
    target_id = workspace_id(path)
    target_path = os.path.normcase(str(path))
    registry_path = _workspaces_path()
    data = _load_json(registry_path)
    if not isinstance(data, dict):
        return False
    workspaces = data.get("workspaces")
    if not isinstance(workspaces, dict):
        return False

    removed = False
    kept = {}
    for key, value in workspaces.items():
        if not isinstance(value, dict):
            kept[key] = value
            continue
        row_id = str(value.get("id") or key)
        row_path_raw = str(value.get("path") or "").strip()
        try:
            row_path = os.path.normcase(str(Path(row_path_raw).expanduser().resolve()))
        except OSError:
            row_path = os.path.normcase(row_path_raw)
        if row_id == target_id or row_path == target_path:
            removed = True
            continue
        kept[key] = value

    if not removed:
        return False
    data["version"] = 1
    data["workspaces"] = kept
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return True


def project_conversation_records(cwd: str) -> list[tuple[Path, dict]]:
    return ConversationStore(cwd).iter_records()


def project_conversation_summaries(cwd: str) -> list[tuple[Path, dict]]:
    return ConversationStore(cwd).list_all()


def _message_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    parts.append("[image]")
                elif "content" in block:
                    parts.append(_message_text(block["content"]))
        return " ".join(parts)
    return str(content)


def _search_text(data: dict) -> str:
    return "\n".join(message["text"] for message in _search_messages(data))


def _search_messages(data: dict) -> list[dict]:
    parts = []
    for msg in data.get("messages", []):
        if not is_visible_message(msg):
            continue
        text = _message_text(msg.get("content", "")).strip()
        if text:
            parts.append({
                "role": str(msg.get("role") or "message"),
                "text": text,
            })
    return parts


def _resolve_workspace(workspace: str | None) -> Path | None:
    if not workspace:
        return None
    return Path(workspace).expanduser().resolve()


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _aichs_home() -> Path:
    current = Path(AICHS_HOME)
    if current != Path(_IMPORTED_AICHS_HOME):
        return current
    return Path(config.AICHS_HOME)


def _conv_dir() -> Path:
    current = Path(CONV_DIR)
    if current != Path(_IMPORTED_CONV_DIR):
        return current
    return Path(config.CONV_DIR)


def _workspaces_path() -> Path:
    current = Path(WORKSPACES_PATH)
    if current != Path(_IMPORTED_WORKSPACES_PATH):
        return current
    return Path(config.WORKSPACES_PATH)


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
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


def _index_row(path: Path, summary: dict, stat_result: os.stat_result) -> dict:
    return {
        "mtime_ns": stat_result.st_mtime_ns,
        "size": stat_result.st_size,
        "summary": dict(summary),
    }


def _available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for idx in range(1, 10_000):
        candidate = path.with_name(f"{stem}-{idx}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}-{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{suffix}")


def _parse_datetime(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _register_workspace(workspace: Path, wid: str, *, updated_at: str | None = None) -> None:
    if _skip_workspace_registration(workspace):
        return
    registry_path = _workspaces_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_json(registry_path)
    if not isinstance(data, dict):
        data = {}
    workspaces = data.get("workspaces")
    if not isinstance(workspaces, dict):
        workspaces = {}
    workspaces = {
        key: value
        for key, value in workspaces.items()
        if not _is_leaked_test_workspace(str(key), value)
    }
    workspaces[wid] = {
        "id": wid,
        "path": str(workspace),
        "name": workspace.name or str(workspace),
        "updated_at": updated_at or _workspace_updated_at(),
    }
    data["version"] = 1
    data["workspaces"] = workspaces
    registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _workspace_updated_at() -> str:
    global _last_workspace_updated_at
    now = datetime.now()
    if _last_workspace_updated_at is not None and now <= _last_workspace_updated_at:
        now = _last_workspace_updated_at + timedelta(microseconds=1)
    _last_workspace_updated_at = now
    return now.isoformat()


def _prune_leaked_test_conversations() -> None:
    """Remove c1/First fixture files if pytest ever wrote them to the real ~/.aichs dir."""
    for p in list(_conv_dir().glob("*.json")):
        data = _load_json(p)
        if data is None:
            continue
        if (
            data.get("id") == "c1"
            and data.get("title") == "First"
            and not data.get("messages")
            and data.get("updated_at") == "2026-01-01T12:00:00"
        ):
            p.unlink(missing_ok=True)


def _skip_workspace_registration(workspace: Path) -> bool:
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if _is_inside_path(workspace, Path(tempfile.gettempdir())):
        return not _is_inside_path(_workspaces_path(), Path(tempfile.gettempdir()))
    return False


def _is_leaked_test_workspace(wid: str, raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    name = str(raw.get("name") or "")
    path = str(raw.get("path") or "")
    if name != "proj" or not str(wid).startswith("proj-"):
        return False
    try:
        workspace = Path(path).expanduser().resolve()
    except OSError:
        return False
    if not _is_inside_path(workspace, Path(tempfile.gettempdir())):
        return False
    parent = workspace.parent.name.lower()
    return parent.startswith("tmp") or parent.startswith("pytest-")


def _is_inside_path(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _is_newer(summary: dict, prev_summary: dict, path: Path, prev_path: Path) -> bool:
    cur = summary.get("updated_at", "")
    old = prev_summary.get("updated_at", "")
    if cur != old:
        return cur > old
    conv_id = summary.get("id") or path.stem
    return path.name == f"{conv_id}.json" and prev_path.name != f"{conv_id}.json"


def _summary(data: dict) -> dict:
    search_messages = _search_messages(data)
    return {
        "id": data.get("id", ""),
        "title": data.get("title", "Untitled"),
        "title_auto": data.get("title_auto", False),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
        "model": data.get("model", ""),
        "workspace_id": data.get("workspace_id", ""),
        "cwd": data.get("cwd", ""),
        "pinned": data.get("pinned", False),
        "message_count": len(data.get("messages", [])),
        "search_text": "\n".join(message["text"] for message in search_messages),
        "search_messages": search_messages,
    }

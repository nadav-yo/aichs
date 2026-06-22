"""Workspace-local agent canvas persistence."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

CANVAS_FORMAT = "aichs-agent-canvas/v1"
CANVAS_STORAGE_VERSION = 2
DEFAULT_CANVAS_ID = "default"
GRAPH_CHAT_LIMIT = 200
RUN_HISTORY_LIMIT = 20
RUN_TOOL_LIMIT = 50
RUN_CONTENT_LIMIT = 20_000
RUN_PROMPT_LIMIT = 8_000
RUN_FIELD_LIMIT = 500
RUN_TOOL_DETAIL_LIMIT = 6_000


class CanvasSaveRefused(RuntimeError):
    """Raised when saving would likely destroy the only persisted graph."""


def canvas_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / ".aichs" / "canvas" / "agent_canvas.json"


def canvas_storage_dir(workspace: str | Path, canvas_id: str = DEFAULT_CANVAS_ID) -> Path:
    return Path(workspace).expanduser().resolve() / ".aichs" / "canvas" / canvas_id


def canvas_artifacts_dir(workspace: str | Path, canvas_id: str = DEFAULT_CANVAS_ID) -> Path:
    return Path(workspace).expanduser().resolve() / ".aichs" / "canvas" / canvas_id / "artifacts"


def load_agent_canvas(workspace: str | Path) -> tuple[dict | None, str]:
    path = canvas_path(workspace)
    if not path.exists():
        return None, ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"Could not read saved canvas: {exc}"
    if not isinstance(raw, dict):
        return None, "Saved canvas is not an object."
    _hydrate_split_canvas(path.parent, raw)
    return raw, ""


def save_agent_canvas(workspace: str | Path, data: dict, *, allow_empty_overwrite: bool = False) -> None:
    if (
        not allow_empty_overwrite
        and not _graph_has_nodes(data)
        and _existing_saved_graph_has_nodes(workspace)
    ):
        raise CanvasSaveRefused("Refusing to overwrite a non-empty saved canvas with an empty graph.")
    path = canvas_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = _split_canvas_payload(path.parent, workspace, data)
    payload = json.dumps(manifest, indent=2)
    _backup_existing_canvas(path)
    _write_split_sidecars(path.parent, data, manifest)
    _atomic_write_text(path, payload)


def _graph_has_nodes(data: object) -> bool:
    return isinstance(data, dict) and bool(data.get("nodes"))


def _existing_saved_graph_has_nodes(workspace: str | Path) -> bool:
    path = canvas_path(workspace)
    raw = _read_json(path)
    if isinstance(raw, dict):
        _hydrate_split_canvas(path.parent, raw)
        return _graph_has_nodes(raw)
    return False


def _backup_existing_canvas(path: Path) -> None:
    if not path.exists():
        return
    backup_dir = path.parent / "canvas_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    shutil.copy2(path, backup_dir / f"agent_canvas_{stamp}.json")


def _atomic_write_text(path: Path, payload: str) -> None:
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


def _split_canvas_payload(root: Path, workspace: str | Path, data: dict) -> dict:
    manifest = json.loads(json.dumps(data if isinstance(data, dict) else {}))
    storage = {
        "version": CANVAS_STORAGE_VERSION,
        "mode": "split",
        "canvas_id": DEFAULT_CANVAS_ID,
        "graph_ref": _relative_storage_ref(root, _graph_state_path(root)),
        "graph_chat_ref": _relative_storage_ref(root, _graph_chat_path(root)),
        "runs_dir": _relative_storage_ref(root, _runs_dir(root)),
        "artifacts_dir": _relative_storage_ref(root, canvas_artifacts_dir(workspace, DEFAULT_CANVAS_ID)),
    }
    manifest["storage"] = storage
    manifest.pop("graph_chat", None)
    for node in manifest.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        history = node.pop("run_history", None)
        if history:
            node["run_history_ref"] = _relative_storage_ref(root, _run_history_path(root, node.get("id")))
    return manifest


def _write_split_sidecars(root: Path, data: dict, manifest: dict) -> None:
    _write_json(_graph_state_path(root), _graph_payload(manifest))
    graph_chat = _trim_graph_chat((data or {}).get("graph_chat"))
    _write_jsonl(_graph_chat_path(root), graph_chat)

    active_node_ids: set[str] = set()
    nodes = data.get("nodes") if isinstance(data, dict) else []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        history = _trim_run_history(node.get("run_history"))
        if not history:
            continue
        active_node_ids.add(node_id)
        _write_jsonl(_run_history_path(root, node_id), history)

    # Keep the manifest authoritative for refs; old unreferenced files are harmless and
    # can be pruned by a maintenance command later.
    _ = manifest


def _hydrate_split_canvas(root: Path, data: dict) -> None:
    storage = data.get("storage")
    if not isinstance(storage, dict) or storage.get("mode") != "split":
        return
    graph = _read_json(_resolve_storage_ref(root, storage.get("graph_ref")))
    if isinstance(graph, dict) and not data.get("nodes") and graph.get("nodes"):
        for key, value in graph.items():
            if key != "storage":
                data[key] = value
    graph_chat = _read_jsonl(_resolve_storage_ref(root, storage.get("graph_chat_ref")))
    data["graph_chat"] = graph_chat
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ref = node.get("run_history_ref")
        if ref:
            node["run_history"] = _read_jsonl(_resolve_storage_ref(root, ref))


def _graph_payload(manifest: dict) -> dict:
    payload = json.loads(json.dumps(manifest if isinstance(manifest, dict) else {}))
    payload.pop("storage", None)
    return payload


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data if isinstance(data, dict) else {}, indent=2)
    _atomic_write_text(path, payload)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    _atomic_write_text(path, payload)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def _trim_graph_chat(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    rows: list[dict] = []
    for raw in value[-GRAPH_CHAT_LIMIT:]:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "role": str(raw.get("role") or "Message")[:40],
                "text": text[:RUN_CONTENT_LIMIT],
            }
        )
    return rows


def _trim_run_history(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    rows: list[dict] = []
    for raw in value[-RUN_HISTORY_LIMIT:]:
        if not isinstance(raw, dict):
            continue
        tools = []
        raw_tools = raw.get("tools")
        if isinstance(raw_tools, list):
            for tool in raw_tools[-RUN_TOOL_LIMIT:]:
                if not isinstance(tool, dict):
                    continue
                tools.append(
                    {
                        "name": str(tool.get("name") or "tool")[:80],
                        "status": str(tool.get("status") or "called")[:40],
                        "summary": str(tool.get("summary") or "")[:RUN_FIELD_LIMIT],
                        "inputs": str(tool.get("inputs") or "")[:RUN_TOOL_DETAIL_LIMIT],
                        "output": str(tool.get("output") or "")[:RUN_TOOL_DETAIL_LIMIT],
                    }
                )
        touched = raw.get("touched_files")
        touched_files = [str(path)[:RUN_FIELD_LIMIT] for path in touched[-RUN_TOOL_LIMIT:]] if isinstance(touched, list) else []
        rows.append(
            {
                "id": str(raw.get("id") or "")[:80],
                "kind": str(raw.get("kind") or "operation")[:40],
                "role": str(raw.get("role") or "Agent")[:80],
                "status": str(raw.get("status") or "done")[:40],
                "started_at": str(raw.get("started_at") or "")[:40],
                "prompt": str(raw.get("prompt") or "")[:RUN_PROMPT_LIMIT],
                "content": str(raw.get("content") or "")[:RUN_CONTENT_LIMIT],
                "artifact_ref": str(raw.get("artifact_ref") or "")[:RUN_FIELD_LIMIT],
                "artifact_title": str(raw.get("artifact_title") or "")[:RUN_FIELD_LIMIT],
                "tools": tools,
                "touched_files": touched_files,
            }
        )
    return rows


def _graph_chat_path(root: Path) -> Path:
    return _canvas_storage_dir_from_manifest_root(root) / "graph_chat.jsonl"


def _runs_dir(root: Path) -> Path:
    return _canvas_storage_dir_from_manifest_root(root) / "runs"


def _run_history_path(root: Path, node_id: object) -> Path:
    text = str(node_id or "").strip()
    safe = "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_"}) or "node"
    return _runs_dir(root) / f"node_{safe}.jsonl"


def _graph_state_path(root: Path) -> Path:
    return _canvas_storage_dir_from_manifest_root(root) / "graph.json"


def _canvas_storage_dir_from_manifest_root(root: Path) -> Path:
    return root / DEFAULT_CANVAS_ID


def _relative_storage_ref(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_storage_ref(root: Path, ref: object) -> Path:
    text = str(ref or "").strip()
    if not text:
        return root / "__missing__"
    path = Path(text)
    if path.is_absolute():
        return path
    return root / path

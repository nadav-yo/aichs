import json
from datetime import datetime
from pathlib import Path

from config import CONV_DIR
from services.content import is_visible_message


class ConversationStore:
    def __init__(self):
        CONV_DIR.mkdir(parents=True, exist_ok=True)
        _prune_leaked_test_conversations()

    def list_all(self) -> list[tuple[Path, dict]]:
        by_id: dict[str, tuple[Path, dict]] = {}
        for p in CONV_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            summary = _summary(data)
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
        return json.loads(Path(path).read_text())

    def delete(self, path: str) -> None:
        Path(path).unlink(missing_ok=True)

    def save(self, conv_id: str, data: dict) -> Path:
        data["id"] = conv_id
        path = CONV_DIR / f"{conv_id}.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._drop_duplicate_files(conv_id, keep=path)
        return path

    def rename(self, path: str, title: str) -> str:
        data = self.load(path)
        data["title"] = title.strip() or "Untitled"
        data["title_auto"] = False
        conv_id = data.get("id") or Path(path).stem
        self.save(conv_id, data)
        return conv_id

    def _drop_duplicate_files(self, conv_id: str, *, keep: Path) -> None:
        keep_resolved = keep.resolve()
        for p in CONV_DIR.glob("*.json"):
            if p.resolve() == keep_resolved:
                continue
            try:
                other = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            other_id = other.get("id") or p.stem
            if other_id == conv_id:
                p.unlink(missing_ok=True)

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
        try:
            data = self.load(str(path))
        except Exception:
            return False
        for msg in data.get("messages", []):
            if not is_visible_message(msg):
                continue
            if q in _message_text(msg.get("content", "")).casefold():
                return True
        return False


def _message_text(content) -> str:
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


def _prune_leaked_test_conversations() -> None:
    """Remove c1/First fixture files if pytest ever wrote them to the real ~/.aichs dir."""
    for p in list(CONV_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (
            data.get("id") == "c1"
            and data.get("title") == "First"
            and not data.get("messages")
            and data.get("updated_at") == "2026-01-01T12:00:00"
        ):
            p.unlink(missing_ok=True)


def _is_newer(summary: dict, prev_summary: dict, path: Path, prev_path: Path) -> bool:
    cur = summary.get("updated_at", "")
    old = prev_summary.get("updated_at", "")
    if cur != old:
        return cur > old
    conv_id = summary.get("id") or path.stem
    return path.name == f"{conv_id}.json" and prev_path.name != f"{conv_id}.json"


def _summary(data: dict) -> dict:
    return {
        "id": data.get("id", ""),
        "title": data.get("title", "Untitled"),
        "title_auto": data.get("title_auto", False),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
        "model": data.get("model", ""),
        "cwd": data.get("cwd", ""),
        "pinned": data.get("pinned", False),
        "message_count": len(data.get("messages", [])),
    }

import json
from datetime import datetime
from pathlib import Path

from config import CONV_DIR


class ConversationStore:
    def __init__(self):
        CONV_DIR.mkdir(parents=True, exist_ok=True)

    def list_all(self) -> list[tuple[Path, dict]]:
        convs = []
        for p in CONV_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                convs.append((p, _summary(data)))
            except Exception:
                pass
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
        path = CONV_DIR / f"{conv_id}.json"
        path.write_text(json.dumps(data, indent=2))
        return path

    def rename(self, path: str, title: str) -> str:
        data = self.load(path)
        data["title"] = title.strip() or "Untitled"
        data["title_auto"] = False
        self.save(data["id"], data)
        return data["id"]

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


def _summary(data: dict) -> dict:
    return {
        "id": data.get("id", ""),
        "title": data.get("title", "Untitled"),
        "title_auto": data.get("title_auto", False),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
        "model": data.get("model", ""),
        "pinned": data.get("pinned", False),
        "message_count": len(data.get("messages", [])),
    }

import json
import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QWidget

from services.content import content_text
from services.crew import crew_name_from_metadata


def default_export_name(data: dict) -> str:
    title = data.get("title", "conversation")
    safe = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "-")
    return f"{safe or 'conversation'}.md"


def conversation_to_markdown(data: dict) -> str:
    title = data.get("title", "Untitled")
    lines = [f"# {title}", ""]

    meta: list[str] = []
    if model := data.get("model"):
        meta.append(f"**Model:** {model}")
    if created := data.get("created_at"):
        meta.append(f"**Created:** {_fmt_ts(created)}")
    if updated := data.get("updated_at"):
        meta.append(f"**Updated:** {_fmt_ts(updated)}")
    if meta:
        lines.extend(meta)
        lines.append("")

    lines.extend(["---", ""])

    for msg in data.get("messages", []):
        role = msg.get("role")
        content = msg.get("content", "")
        ts = msg.get("created_at", "")

        if role == "tool":
            continue
        if role == "user":
            if _skip_user_message(content):
                continue
            lines.extend(_user_blocks(content, ts))
        elif role == "assistant":
            lines.extend(_assistant_blocks(content, ts, crew_name_from_metadata(msg.get("crew"))))

    return "\n".join(lines).rstrip() + "\n"


def export_conversation_dialog(data: dict, parent: QWidget | None = None) -> bool:
    default = default_export_name(data)
    path, _ = QFileDialog.getSaveFileName(
        parent, "Export conversation", default, "Markdown (*.md)",
    )
    if not path:
        return False
    if not path.lower().endswith(".md"):
        path += ".md"
    Path(path).write_text(conversation_to_markdown(data), encoding="utf-8")
    return True


def export_conversation_file(conv_path: str, parent: QWidget | None = None) -> bool:
    data = json.loads(Path(conv_path).read_text(encoding="utf-8"))
    return export_conversation_dialog(data, parent)


def _fmt_ts(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def _skip_user_message(content) -> bool:
    if isinstance(content, list):
        if not content:
            return True
        return all(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in content
        )
    return False


def _user_blocks(content, ts: str) -> list[str]:
    lines = ["## You", ""]
    if ts:
        lines.extend([f"*{_fmt_ts(ts)}*", ""])
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                lines.append(block["text"])
                lines.append("")
            elif block.get("type") == "image":
                lines.append("*[Image attached]*")
                lines.append("")
    elif content:
        lines.append(str(content))
        lines.append("")
    lines.extend(["---", ""])
    return lines


def _assistant_blocks(content, ts: str, speaker: str = "") -> list[str]:
    lines = [f"## {speaker or 'Agent'}", ""]
    if ts:
        lines.extend([f"*{_fmt_ts(ts)}*", ""])
    text = content if isinstance(content, str) else content_text(content)
    if text:
        lines.append(text)
        lines.append("")
    lines.extend(["---", ""])
    return lines

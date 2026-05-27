import base64
import copy

from services.crew import crew_name_from_metadata

_HIDDEN_SYNTHETIC_MESSAGES = {"tool_results", "active_task", "extension", "extension_resume"}
_TRANSIENT_SYNTHETIC_MESSAGES = {"active_task", "extension", "extension_resume"}
_ACTIVE_TASK_PREFIX = "Continue the active user task."


def build_user_content(text: str, images: list[dict], files: list[dict] | None = None) -> str | list:
    files = files or []
    if not images and not files:
        return text

    blocks = []
    if text:
        blocks.append({"type": "text", "text": text})
    for file in files:
        blocks.append({
            "type": "file",
            "path": file["path"],
            "content": file["content"],
            "truncated": file.get("truncated", False),
            "size": file.get("size", 0),
            "ephemeral": True,
        })
    for img in images:
        blocks.append({
            "type": "image",
            "media_type": img["media_type"],
            "data": img["data"],
            "ephemeral": True,
        })
    return blocks


def compact_ephemeral_attachments(messages: list[dict]) -> list[dict]:
    """Return history with old attachment payloads replaced by small markers."""
    compacted = copy.deepcopy(messages)
    for msg in compacted:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        msg["content"] = _compact_ephemeral_blocks(content)
    return compacted


def prepare_for_storage(messages: list[dict]) -> list[dict]:
    """Return persisted history without runtime-only instruction messages."""
    prepared = []
    for msg in copy.deepcopy(messages):
        if _is_transient_runtime_message(msg):
            continue
        content = msg.get("content")
        if msg.get("synthetic") == "tool_results" and isinstance(content, list):
            msg["content"] = [
                block for block in content
                if not _is_internal_text_block(block)
            ]
        prepared.append(msg)
    return compact_ephemeral_attachments(prepared)


def is_visible_message(msg: dict) -> bool:
    if msg.get("role") == "tool":
        return False
    return str(msg.get("synthetic") or "") not in _HIDDEN_SYNTHETIC_MESSAGES


def content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def content_length(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                total += len(block.get("text", ""))
            elif block.get("type") == "file":
                total += len(block.get("path", "")) + len(block.get("content", ""))
            elif block.get("type") == "image":
                total += len(block.get("data", ""))
            elif "content" in block:
                total += content_length(block["content"])
        return total
    return len(str(content))


def content_preview(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "file":
                parts.append(f"[file: {block.get('path', 'file')}]")
            elif block.get("type") == "image":
                parts.append("[image]")
            elif block.get("type") == "tool_result":
                parts.append(content_preview(block.get("content", "")))
        return " ".join(p for p in parts if p)
    return str(content)


def _compact_ephemeral_blocks(blocks: list) -> list:
    out = []
    for block in blocks:
        if not isinstance(block, dict):
            out.append(block)
            continue
        kind = block.get("type")
        if kind == "file" and block.get("ephemeral") and block.get("content"):
            updated = dict(block)
            updated["content"] = ""
            updated["omitted_after_turn"] = True
            out.append(updated)
        elif kind == "image" and block.get("ephemeral") and block.get("data"):
            media_type = block.get("media_type", "image")
            size = len(str(block.get("data") or ""))
            out.append({
                "type": "text",
                "text": f"[Image attachment omitted after original turn: {media_type}, {size} base64 chars]",
            })
        elif "content" in block and isinstance(block["content"], list):
            updated = dict(block)
            updated["content"] = _compact_ephemeral_blocks(block["content"])
            out.append(updated)
        else:
            out.append(block)
    return out


def _is_transient_runtime_message(msg: dict) -> bool:
    return str(msg.get("synthetic") or "") in _TRANSIENT_SYNTHETIC_MESSAGES


def _is_internal_text_block(block: object) -> bool:
    if not isinstance(block, dict) or block.get("type") != "text":
        return False
    if block.get("internal") or block.get("synthetic"):
        return True
    text = str(block.get("text") or "")
    return text.startswith(_ACTIVE_TASK_PREFIX)


def image_blocks(content) -> list[dict]:
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "image"]


def file_blocks(content) -> list[dict]:
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "file"]


def prepare_for_anthropic(messages: list[dict]) -> list[dict]:
    out = []
    for msg in _model_context_messages(messages):
        role = msg["role"]
        content = _content_for_model(msg)
        if role == "user" and isinstance(content, list) and _has_tool_result(content):
            out.append({"role": "user", "content": content})
        elif role == "user" and isinstance(content, list) and _is_multimodal(content):
            out.append({"role": "user", "content": _to_anthropic_blocks(content)})
        else:
            out.append({"role": role, "content": content})
    return out


def prepare_for_openai(messages: list[dict]) -> list[dict]:
    out = []
    for msg in _model_context_messages(messages):
        role = msg["role"]
        content = _content_for_model(msg)
        if role == "user" and isinstance(content, list) and _is_multimodal(content):
            out.append({"role": "user", "content": _to_openai_blocks(content)})
        elif role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id", ""),
                "content": content,
            })
        elif role == "assistant" and msg.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": content,
                "tool_calls": msg.get("tool_calls", []),
            })
        else:
            out.append({"role": role, "content": content})
    return out


def _content_for_model(msg: dict):
    content = msg.get("content", "")
    speaker = crew_name_from_metadata(msg.get("crew"))
    if not speaker or msg.get("role") != "assistant":
        return content
    if isinstance(content, str):
        return f"{speaker}: {content}"
    if isinstance(content, list):
        out = []
        prefixed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and not prefixed:
                updated = dict(block)
                updated["text"] = f"{speaker}: {updated.get('text', '')}"
                out.append(updated)
                prefixed = True
            else:
                out.append(block)
        if prefixed:
            return out
    return content


def _model_context_messages(messages: list[dict]) -> list[dict]:
    return [
        msg for idx, msg in enumerate(messages)
        if not _is_synthesized_crew_bubble(messages, idx)
    ]


def _is_synthesized_crew_bubble(messages: list[dict], idx: int) -> bool:
    msg = messages[idx]
    if msg.get("role") != "assistant" or not isinstance(msg.get("crew"), dict):
        return False
    for later in messages[idx + 1:]:
        if later.get("role") == "user":
            return False
        if later.get("role") == "assistant" and not isinstance(later.get("crew"), dict):
            return True
    return False


def _is_multimodal(content: list) -> bool:
    return any(isinstance(b, dict) and b.get("type") in ("text", "image", "file") for b in content)


def _has_tool_result(content: list) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _to_anthropic_blocks(blocks: list[dict]) -> list[dict]:
    out = []
    for block in blocks:
        if block.get("type") == "text":
            out.append(block)
        elif block.get("type") == "file":
            out.append({"type": "text", "text": _file_block_text(block)})
        elif block.get("type") == "image":
            out.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block["media_type"],
                    "data": block["data"],
                },
            })
    return out


def _to_openai_blocks(blocks: list[dict]) -> list[dict]:
    out = []
    for block in blocks:
        if block.get("type") == "text":
            out.append(block)
        elif block.get("type") == "file":
            out.append({"type": "text", "text": _file_block_text(block)})
        elif block.get("type") == "image":
            out.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{block['media_type']};base64,{block['data']}",
                },
            })
    return out


def _file_block_text(block: dict) -> str:
    path = block.get("path", "file")
    if block.get("omitted_after_turn"):
        size = block.get("size", 0)
        return (
            f"Attached file: {path}\n\n"
            f"[Attachment content omitted after the original turn; size: {size} bytes.]"
        )
    suffix = ""
    if block.get("truncated"):
        suffix = f"\n[Preview truncated: showing part of {block.get('size', 0)} bytes]"
    return f'Attached file: {path}\n\n```text\n{block.get("content", "")}{suffix}\n```'


def encode_image(image) -> tuple[str, str, bytes]:
    from PyQt6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    raw = bytes(buffer.data())
    return "image/png", base64.b64encode(raw).decode("ascii"), raw

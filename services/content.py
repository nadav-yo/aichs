import base64


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
        })
    for img in images:
        blocks.append({
            "type": "image",
            "media_type": img["media_type"],
            "data": img["data"],
        })
    return blocks


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
    for msg in messages:
        content = msg["content"]
        if msg["role"] == "user" and isinstance(content, list) and _is_multimodal(content):
            out.append({"role": "user", "content": _to_anthropic_blocks(content)})
        else:
            out.append(msg)
    return out


def prepare_for_openai(messages: list[dict]) -> list[dict]:
    out = []
    for msg in messages:
        content = msg["content"]
        if msg["role"] == "user" and isinstance(content, list) and _is_multimodal(content):
            out.append({"role": "user", "content": _to_openai_blocks(content)})
        else:
            out.append(msg)
    return out


def _is_multimodal(content: list) -> bool:
    return any(isinstance(b, dict) and b.get("type") in ("text", "image", "file") for b in content)


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

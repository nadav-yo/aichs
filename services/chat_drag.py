from __future__ import annotations

import json

AICHS_FILE_DROP_MIME = "application/x-aichs-file-drop"
AICHS_COMMIT_DROP_MIME = "application/x-aichs-commit-drop"
AICHS_CHAT_DROP_MIME = "application/x-aichs-chat-drop"


def file_drop_payload(refs: list[str]) -> bytes:
    return _payload("aichs-file-drop", {"refs": _clean_list(refs)})


def commit_drop_payload(commits: list[dict]) -> bytes:
    cleaned = []
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        sha = _clean(str(commit.get("hash") or ""))
        if not sha:
            continue
        cleaned.append(
            {
                "hash": sha,
                "subject": " ".join(str(commit.get("subject") or "").split()),
            }
        )
    return _payload("aichs-commit-drop", {"commits": cleaned})


def chat_drop_payload(chats: list[dict]) -> bytes:
    cleaned = []
    for chat in chats:
        if not isinstance(chat, dict):
            continue
        conv_id = _clean(str(chat.get("id") or ""))
        if not conv_id:
            continue
        cleaned.append(
            {
                "id": conv_id,
                "title": " ".join(str(chat.get("title") or "Untitled").split())
                or "Untitled",
            }
        )
    return _payload("aichs-chat-drop", {"chats": cleaned})


def parse_file_drop(raw: bytes | bytearray | memoryview) -> list[str]:
    data = _parse(raw, "aichs-file-drop")
    return _clean_list(data.get("refs", [])) if data else []


def parse_commit_drop(raw: bytes | bytearray | memoryview) -> list[dict]:
    data = _parse(raw, "aichs-commit-drop")
    if not data:
        return []
    out = []
    for commit in data.get("commits", []):
        if not isinstance(commit, dict):
            continue
        sha = _clean(str(commit.get("hash") or ""))
        if not sha:
            continue
        out.append(
            {
                "hash": sha,
                "subject": " ".join(str(commit.get("subject") or "").split()),
            }
        )
    return out


def parse_chat_drop(raw: bytes | bytearray | memoryview) -> list[dict]:
    data = _parse(raw, "aichs-chat-drop")
    if not data:
        return []
    out = []
    for chat in data.get("chats", []):
        if not isinstance(chat, dict):
            continue
        conv_id = _clean(str(chat.get("id") or ""))
        if not conv_id:
            continue
        out.append(
            {
                "id": conv_id,
                "title": " ".join(str(chat.get("title") or "Untitled").split())
                or "Untitled",
            }
        )
    return out


def file_drop_text(refs: list[str]) -> str:
    return " ".join(_file_token(ref) for ref in _clean_list(refs))


def commit_drop_text(commits: list[dict]) -> str:
    lines = []
    for commit in parse_commit_drop(commit_drop_payload(commits)):
        sha = commit["hash"]
        subject = commit.get("subject") or ""
        lines.append(f"commit {sha} ({subject})" if subject else f"commit {sha}")
    return "\n".join(lines)


def chat_drop_text(chats: list[dict]) -> str:
    parsed = parse_chat_drop(chat_drop_payload(chats))
    if not parsed:
        return ""
    titles = ", ".join(f'"{_escape_title(chat["title"])}"' for chat in parsed)
    noun = "chat" if len(parsed) == 1 else "chats"
    return f"@Archivist using {noun} {titles}, "


def _payload(kind: str, body: dict) -> bytes:
    data = {"kind": kind, **body}
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def _parse(raw: bytes | bytearray | memoryview, kind: str) -> dict:
    try:
        data = json.loads(bytes(raw).decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict) or data.get("kind") != kind:
        return {}
    return data


def _file_token(ref: str) -> str:
    ref = _clean(ref)
    return f'@"{ref}"' if any(ch.isspace() for ch in ref) else f"@{ref}"


def _clean_list(refs) -> list[str]:
    out = []
    seen = set()
    for ref in refs if isinstance(refs, list) else []:
        cleaned = _clean(str(ref or ""))
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def _clean(value: str) -> str:
    return str(value or "").strip().strip("\r\n")


def _escape_title(title: str) -> str:
    return str(title or "Untitled").replace('"', "'")

"""Local code completion providers for the file editor."""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


_WORD_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{1,}\b")
_LOCAL_COMPLETION_SCAN_CHARS = 160_000

_LANGUAGE_KEYWORDS = {
    ".js": {
        "async",
        "await",
        "break",
        "case",
        "catch",
        "class",
        "const",
        "continue",
        "default",
        "else",
        "export",
        "extends",
        "finally",
        "for",
        "from",
        "function",
        "if",
        "import",
        "let",
        "return",
        "switch",
        "throw",
        "try",
        "while",
        "yield",
    },
    ".jsx": set(),
    ".ts": set(),
    ".tsx": set(),
    ".py": set(keyword.kwlist) | {"False", "None", "True", "self"},
    ".rs": {
        "async",
        "await",
        "break",
        "const",
        "continue",
        "crate",
        "else",
        "enum",
        "fn",
        "for",
        "if",
        "impl",
        "let",
        "loop",
        "match",
        "mod",
        "mut",
        "pub",
        "return",
        "self",
        "struct",
        "trait",
        "use",
        "where",
        "while",
    },
}
_LANGUAGE_KEYWORDS[".jsx"] = _LANGUAGE_KEYWORDS[".js"]
_LANGUAGE_KEYWORDS[".ts"] = _LANGUAGE_KEYWORDS[".js"] | {
    "interface",
    "namespace",
    "private",
    "protected",
    "public",
    "type",
}
_LANGUAGE_KEYWORDS[".tsx"] = _LANGUAGE_KEYWORDS[".ts"]


@dataclass(frozen=True)
class CompletionItem:
    label: str
    insert_text: str
    detail: str = ""


class CompletionProvider(Protocol):
    def complete(
        self, *, path: str, content: str, position: int, prefix: str
    ) -> list[CompletionItem]: ...


class LocalCompletionProvider:
    """Complete from visible document symbols and conservative language keywords."""

    def complete(
        self, *, path: str, content: str, position: int, prefix: str
    ) -> list[CompletionItem]:
        prefix = prefix.strip()
        if not prefix:
            return []
        content = _completion_scan_window(content, position)

        candidates: dict[str, CompletionItem] = {}
        for word in _language_keywords(path):
            if _matches_prefix(word, prefix):
                candidates[word] = CompletionItem(word, word, "keyword")

        for word in _WORD_RE.findall(content):
            if word == prefix or not _matches_prefix(word, prefix):
                continue
            candidates.setdefault(word, CompletionItem(word, word, "document"))

        return sorted(
            candidates.values(), key=lambda item: (item.label.lower(), item.label)
        )[:80]


def prefix_at(content: str, position: int) -> str:
    position = max(0, min(position, len(content)))
    start = position
    while start > 0 and _is_word_char(content[start - 1]):
        start -= 1
    return content[start:position]


def _completion_scan_window(content: str, position: int) -> str:
    if len(content) <= _LOCAL_COMPLETION_SCAN_CHARS:
        return content
    position = max(0, min(position, len(content)))
    before = _LOCAL_COMPLETION_SCAN_CHARS // 2
    start = max(0, position - before)
    end = min(len(content), start + _LOCAL_COMPLETION_SCAN_CHARS)
    start = max(0, end - _LOCAL_COMPLETION_SCAN_CHARS)
    return content[start:end]


def _language_keywords(path: str) -> set[str]:
    return _LANGUAGE_KEYWORDS.get(Path(path).suffix.lower(), set())


def _matches_prefix(word: str, prefix: str) -> bool:
    return len(word) > len(prefix) and word.lower().startswith(prefix.lower())


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"

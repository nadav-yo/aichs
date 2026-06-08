"""Language feature routing for extension-provided editor intelligence."""

from __future__ import annotations

import fnmatch
import os
import traceback
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Iterable

from services.code_completion import CompletionItem, CompletionProvider, LocalCompletionProvider
from services.tool_registry import ExtensionStorage, LanguageContribution, extension_languages


_SEVERITIES = {"error", "warning", "info", "hint"}


@dataclass(frozen=True)
class LanguageContext:
    cwd: str
    path: str
    content: str
    position: int = 0
    prefix: str = ""
    extension_id: str = "extension"

    @property
    def storage(self) -> ExtensionStorage:
        return ExtensionStorage(self.cwd, self.extension_id)


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int
    column: int
    message: str
    severity: str = "info"
    source: str = ""
    code: str = ""
    end_line: int | None = None
    end_column: int | None = None


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    kind: str
    line: int
    column: int = 0
    end_line: int | None = None
    end_column: int | None = None


class LanguageCompletionProvider:
    def __init__(self, cwd: str, fallback: CompletionProvider | None = None):
        self.cwd = cwd
        self.fallback = fallback or LocalCompletionProvider()

    def complete(self, *, path: str, content: str, position: int, prefix: str) -> list[CompletionItem]:
        items, _errors = completions(self.cwd, path, content, position, prefix)
        fallback_items = self.fallback.complete(
            path=path,
            content=content,
            position=position,
            prefix=prefix,
        )
        return _dedupe_completion_items(items + fallback_items)


def diagnostics(cwd: str, path: str, content: str) -> tuple[list[Diagnostic], list[str]]:
    results: list[Diagnostic] = []
    languages, errors = _matching_languages(cwd, path)
    for language in languages:
        if language.diagnostics is None:
            continue
        ctx = _context(cwd, path, content, language)
        try:
            results.extend(_normalize_diagnostics(language.diagnostics(ctx), path))
        except Exception:
            errors.append(f"language diagnostics {language.name}:\n{traceback.format_exc().rstrip()}")
    return sorted(results, key=lambda item: (item.line, item.column, item.message)), errors


def symbols(cwd: str, path: str, content: str) -> tuple[list[Symbol], list[str]]:
    results: list[Symbol] = []
    languages, errors = _matching_languages(cwd, path)
    for language in languages:
        if language.symbols is None:
            continue
        ctx = _context(cwd, path, content, language)
        try:
            results.extend(_normalize_symbols(language.symbols(ctx), path))
        except Exception:
            errors.append(f"language symbols {language.name}:\n{traceback.format_exc().rstrip()}")
    return sorted(results, key=lambda item: (item.line, item.column, item.name)), errors


def completions(
    cwd: str,
    path: str,
    content: str,
    position: int,
    prefix: str,
) -> tuple[list[CompletionItem], list[str]]:
    results: list[CompletionItem] = []
    languages, errors = _matching_languages(cwd, path)
    for language in languages:
        if language.completion is None:
            continue
        ctx = _context(cwd, path, content, language, position=position, prefix=prefix)
        try:
            results.extend(_normalize_completions(language.completion(ctx)))
        except Exception:
            errors.append(f"language completion {language.name}:\n{traceback.format_exc().rstrip()}")
    return _dedupe_completion_items(results), errors


def _matching_languages(cwd: str, path: str) -> tuple[list[LanguageContribution], list[str]]:
    languages, errors = extension_languages(cwd)
    rel = _relative_path(cwd, path)
    name = os.path.basename(path)
    return [
        language
        for language in languages
        if any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern) for pattern in language.file_patterns)
    ], list(errors)


def _context(
    cwd: str,
    path: str,
    content: str,
    language: LanguageContribution,
    *,
    position: int = 0,
    prefix: str = "",
) -> LanguageContext:
    return LanguageContext(
        cwd=cwd,
        path=path,
        content=content,
        position=max(0, min(position, len(content))),
        prefix=prefix,
        extension_id=language.extension_id,
    )


def _normalize_diagnostics(raw, path: str) -> list[Diagnostic]:
    items = []
    for data in _iter_raw_items(raw):
        if isinstance(data, Diagnostic):
            items.append(data)
            continue
        if not isinstance(data, dict):
            continue
        message = str(data.get("message") or "").strip()
        if not message:
            continue
        severity = str(data.get("severity") or "info").lower()
        if severity not in _SEVERITIES:
            severity = "info"
        line = _positive_int(data.get("line"), 1)
        column = _nonnegative_int(data.get("column"), 0)
        items.append(Diagnostic(
            path=str(data.get("path") or path),
            line=line,
            column=column,
            end_line=_optional_positive_int(data.get("end_line")),
            end_column=_optional_nonnegative_int(data.get("end_column")),
            severity=severity,
            message=message,
            source=str(data.get("source") or ""),
            code=str(data.get("code") or ""),
        ))
    return items


def _normalize_symbols(raw, path: str) -> list[Symbol]:
    items = []
    for data in _iter_raw_items(raw):
        if isinstance(data, Symbol):
            items.append(data)
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        items.append(Symbol(
            path=str(data.get("path") or path),
            name=name,
            kind=str(data.get("kind") or "symbol"),
            line=_positive_int(data.get("line"), 1),
            column=_nonnegative_int(data.get("column"), 0),
            end_line=_optional_positive_int(data.get("end_line")),
            end_column=_optional_nonnegative_int(data.get("end_column")),
        ))
    return items


def _normalize_completions(raw) -> list[CompletionItem]:
    items = []
    for data in _iter_raw_items(raw):
        if isinstance(data, CompletionItem):
            items.append(data)
            continue
        if isinstance(data, str):
            label = data.strip()
            if label:
                items.append(CompletionItem(label=label, insert_text=label))
            continue
        if not isinstance(data, dict):
            continue
        label = str(data.get("label") or "").strip()
        if not label:
            continue
        items.append(CompletionItem(
            label=label,
            insert_text=str(data.get("insert_text") or label),
            detail=str(data.get("detail") or ""),
        ))
    return items


def _iter_raw_items(raw) -> Iterable[object]:
    if raw is None:
        return []
    if is_dataclass(raw) and not isinstance(raw, type):
        raw = asdict(raw)
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        raw = raw["items"]
    if isinstance(raw, (list, tuple)):
        return [_raw_dict(item) for item in raw]
    return [_raw_dict(raw)]


def _raw_dict(item):
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    return item


def _dedupe_completion_items(items: list[CompletionItem]) -> list[CompletionItem]:
    seen = set()
    deduped = []
    for item in items:
        key = item.label.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:80]


def _relative_path(cwd: str, path: str) -> str:
    try:
        return Path(path).resolve().relative_to(Path(cwd).resolve()).as_posix()
    except (OSError, ValueError):
        return Path(path).name


def _positive_int(value, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _nonnegative_int(value, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _optional_positive_int(value) -> int | None:
    if value is None:
        return None
    return _positive_int(value, 1)


def _optional_nonnegative_int(value) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, 0)

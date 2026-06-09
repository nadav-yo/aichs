"""Language feature routing for extension-provided editor intelligence."""

from __future__ import annotations

import fnmatch
import os
import traceback
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Iterable

from services.code_completion import CompletionItem, CompletionProvider, LocalCompletionProvider
from services.tool_policy import path_in_repo, resolve_path
from services.tool_registry import (
    ExtensionStorage,
    LanguageContribution,
    extension_languages,
    extension_overview,
)


_SEVERITIES = {"error", "warning", "info", "hint"}
_FIX_SAFETIES = {"safe", "unsafe"}


@dataclass(frozen=True)
class LanguageContext:
    cwd: str
    path: str
    content: str
    position: int = 0
    prefix: str = ""
    action_id: str = ""
    diagnostics: tuple[object, ...] = ()
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
    fix_available: bool = False
    fix_safety: str = ""
    data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    kind: str
    line: int
    column: int = 0
    end_line: int | None = None
    end_column: int | None = None


@dataclass(frozen=True)
class CodeAction:
    id: str
    title: str
    kind: str = "quickfix"
    source: str = ""
    diagnostic_code: str = ""
    safe: bool = True
    safety: str = "safe"
    data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeActionResult:
    content: str | None = None
    message: str = ""


@dataclass(frozen=True)
class LanguageFeatureStatus:
    extension_id: str
    language: str
    file_patterns: tuple[str, ...]
    features: tuple[str, ...]
    requirements: dict[str, tuple[str, ...]] = field(default_factory=dict)
    missing_requirements: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.missing_requirements


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


class LanguageService:
    def __init__(self, cwd: str):
        self.cwd = cwd

    def diagnostics(self, path: str, content: str) -> tuple[list[Diagnostic], list[str]]:
        results: list[Diagnostic] = []
        languages, errors = self._matching_languages(path)
        for language in languages:
            if language.diagnostics is None:
                continue
            ctx = self._context(path, content, language)
            try:
                results.extend(_normalize_diagnostics(language.diagnostics(ctx), path))
            except Exception:
                errors.append(f"language diagnostics {language.name}:\n{traceback.format_exc().rstrip()}")
        return sorted(results, key=lambda item: (item.line, item.column, item.message)), errors

    def symbols(self, path: str, content: str) -> tuple[list[Symbol], list[str]]:
        results: list[Symbol] = []
        languages, errors = self._matching_languages(path)
        for language in languages:
            if language.symbols is None:
                continue
            ctx = self._context(path, content, language)
            try:
                results.extend(_normalize_symbols(language.symbols(ctx), path))
            except Exception:
                errors.append(f"language symbols {language.name}:\n{traceback.format_exc().rstrip()}")
        return sorted(results, key=lambda item: (item.line, item.column, item.name)), errors

    def completions(
        self,
        path: str,
        content: str,
        position: int,
        prefix: str,
    ) -> tuple[list[CompletionItem], list[str]]:
        results: list[CompletionItem] = []
        languages, errors = self._matching_languages(path)
        for language in languages:
            if language.completion is None:
                continue
            ctx = self._context(path, content, language, position=position, prefix=prefix)
            try:
                results.extend(_normalize_completions(language.completion(ctx)))
            except Exception:
                errors.append(f"language completion {language.name}:\n{traceback.format_exc().rstrip()}")
        return _dedupe_completion_items(results), errors

    def code_actions(
        self,
        path: str,
        content: str,
        diagnostics: list[Diagnostic] | None = None,
    ) -> tuple[list[CodeAction], list[str]]:
        results: list[CodeAction] = []
        languages, errors = self._matching_languages(path)
        for language in languages:
            if language.code_actions is None:
                continue
            ctx = self._context(path, content, language, diagnostics=diagnostics)
            try:
                results.extend(_normalize_code_actions(language.code_actions(ctx)))
            except Exception:
                errors.append(f"language code_actions {language.name}:\n{traceback.format_exc().rstrip()}")
        return _dedupe_code_actions(results), errors

    def apply_code_action(
        self,
        path: str,
        content: str,
        action_id: str,
        diagnostics: list[Diagnostic] | None = None,
    ) -> tuple[CodeActionResult, list[str]]:
        action_id = str(action_id or "").strip()
        if not action_id:
            return CodeActionResult(message="Missing code action id."), ["missing code action id"]
        languages, errors = self._matching_languages(path)
        for language in languages:
            provider = language.apply_code_action or language.code_actions
            if provider is None:
                continue
            ctx = self._context(
                path,
                content,
                language,
                action_id=action_id,
                diagnostics=diagnostics,
            )
            try:
                result = _normalize_code_action_result(provider(ctx))
            except Exception:
                errors.append(f"language code_action {language.name} {action_id}:\n{traceback.format_exc().rstrip()}")
                continue
            if result is not None:
                return result, errors
        errors.append(f"code action not found: {action_id}")
        return CodeActionResult(message=f"Code action not found: {action_id}"), errors

    def format_document(self, path: str, content: str) -> tuple[CodeActionResult, list[str]]:
        languages, errors = self._matching_languages(path)
        for language in languages:
            if language.format_document is None:
                continue
            ctx = self._context(path, content, language)
            try:
                result = _normalize_code_action_result(language.format_document(ctx))
            except Exception:
                errors.append(f"language format_document {language.name}:\n{traceback.format_exc().rstrip()}")
                continue
            if result is not None:
                return result, errors
        return CodeActionResult(message="No formatter available."), errors

    def format_file(self, path: str, content: str | None = None) -> tuple[CodeActionResult, list[str]]:
        resolved = resolve_path(path, self.cwd)
        if not path_in_repo(resolved, self.cwd):
            message = f"Format blocked: path must stay inside the workspace. Got: {resolved}"
            return CodeActionResult(message=message), [message]
        if content is None:
            try:
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                message = f"Format failed: could not read file: {exc}"
                return CodeActionResult(message=message), [message]
        return self.format_document(str(resolved), content)

    def status(self) -> tuple[list[LanguageFeatureStatus], list[str]]:
        overview = extension_overview(self.cwd)
        statuses: list[LanguageFeatureStatus] = []
        errors: list[str] = []
        for file in overview.files:
            errors.extend(file.errors)
            requirements = {
                "executables": tuple(file.requirements.executables),
                "python": tuple(file.requirements.python),
            }
            for language in file.languages:
                statuses.append(LanguageFeatureStatus(
                    extension_id=language.extension_id,
                    language=language.name,
                    file_patterns=tuple(language.file_patterns),
                    features=_language_feature_names(language),
                    requirements=requirements,
                    missing_requirements=tuple(file.missing_requirements),
                ))
        return statuses, errors

    def _matching_languages(self, path: str) -> tuple[list[LanguageContribution], list[str]]:
        languages, errors = extension_languages(self.cwd)
        rel = _relative_path(self.cwd, path)
        name = os.path.basename(path)
        return [
            language
            for language in languages
            if any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern) for pattern in language.file_patterns)
        ], list(errors)

    def _context(
        self,
        path: str,
        content: str,
        language: LanguageContribution,
        *,
        position: int = 0,
        prefix: str = "",
        action_id: str = "",
        diagnostics: list[Diagnostic] | None = None,
    ) -> LanguageContext:
        return LanguageContext(
            cwd=self.cwd,
            path=path,
            content=content,
            position=max(0, min(position, len(content))),
            prefix=prefix,
            action_id=action_id,
            diagnostics=tuple(diagnostics or ()),
            extension_id=language.extension_id,
        )


def diagnostics(cwd: str, path: str, content: str) -> tuple[list[Diagnostic], list[str]]:
    return LanguageService(cwd).diagnostics(path, content)


def symbols(cwd: str, path: str, content: str) -> tuple[list[Symbol], list[str]]:
    return LanguageService(cwd).symbols(path, content)


def completions(
    cwd: str,
    path: str,
    content: str,
    position: int,
    prefix: str,
) -> tuple[list[CompletionItem], list[str]]:
    return LanguageService(cwd).completions(path, content, position, prefix)


def code_actions(
    cwd: str,
    path: str,
    content: str,
    diagnostics: list[Diagnostic] | None = None,
) -> tuple[list[CodeAction], list[str]]:
    return LanguageService(cwd).code_actions(path, content, diagnostics)


def apply_code_action(
    cwd: str,
    path: str,
    content: str,
    action_id: str,
    diagnostics: list[Diagnostic] | None = None,
) -> tuple[CodeActionResult, list[str]]:
    return LanguageService(cwd).apply_code_action(path, content, action_id, diagnostics)


def format_document(cwd: str, path: str, content: str) -> tuple[CodeActionResult, list[str]]:
    return LanguageService(cwd).format_document(path, content)


def format_file(cwd: str, path: str, content: str | None = None) -> tuple[CodeActionResult, list[str]]:
    return LanguageService(cwd).format_file(path, content)


def language_status(cwd: str) -> tuple[list[LanguageFeatureStatus], list[str]]:
    return LanguageService(cwd).status()


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
        fix_safety = str(data.get("fix_safety") or data.get("fixSafety") or "").lower()
        if fix_safety not in _FIX_SAFETIES:
            fix_safety = ""
        fix_available = bool(data.get("fix_available", data.get("fixAvailable", bool(fix_safety))))
        metadata = data.get("data") if isinstance(data.get("data"), dict) else data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
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
            fix_available=fix_available,
            fix_safety=fix_safety,
            data=dict(metadata),
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


def _normalize_code_actions(raw) -> list[CodeAction]:
    items = []
    for data in _iter_raw_items(raw):
        if isinstance(data, CodeAction):
            items.append(data)
            continue
        if not isinstance(data, dict):
            continue
        action_id = str(data.get("id") or data.get("name") or "").strip()
        title = str(data.get("title") or data.get("label") or "").strip()
        if not action_id or not title:
            continue
        safety = str(data.get("safety") or "").lower()
        safe = bool(data.get("safe", True))
        if safety not in _FIX_SAFETIES:
            safety = "safe" if safe else "unsafe"
        safe = safety != "unsafe"
        metadata = data.get("data") if isinstance(data.get("data"), dict) else data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        items.append(CodeAction(
            id=action_id,
            title=title,
            kind=str(data.get("kind") or "quickfix"),
            source=str(data.get("source") or ""),
            diagnostic_code=str(data.get("diagnostic_code") or data.get("code") or ""),
            safe=safe,
            safety=safety,
            data=dict(metadata),
        ))
    return items


def _normalize_code_action_result(raw) -> CodeActionResult | None:
    if raw is None:
        return None
    if isinstance(raw, CodeActionResult):
        return raw
    if is_dataclass(raw) and not isinstance(raw, type):
        raw = asdict(raw)
    if isinstance(raw, str):
        return CodeActionResult(content=raw)
    if not isinstance(raw, dict):
        return None
    if raw.get("applied") is False:
        return None
    if "content" not in raw and "message" not in raw:
        return None
    return CodeActionResult(
        content=str(raw["content"]) if raw.get("content") is not None else None,
        message=str(raw.get("message") or ""),
    )


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


def _dedupe_code_actions(items: list[CodeAction]) -> list[CodeAction]:
    seen = set()
    deduped = []
    for item in items:
        key = item.id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:20]


def _language_feature_names(language: LanguageContribution) -> tuple[str, ...]:
    features = []
    if language.diagnostics is not None:
        features.append("diagnostics")
    if language.symbols is not None:
        features.append("symbols")
    if language.completion is not None:
        features.append("completion")
    if language.code_actions is not None:
        features.append("code_actions")
    if language.apply_code_action is not None:
        features.append("apply_code_action")
    if language.format_document is not None:
        features.append("format_document")
    return tuple(features)


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

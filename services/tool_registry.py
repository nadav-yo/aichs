from __future__ import annotations

import importlib.util
import ast
import hashlib
import json
import shutil
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

import config
from services.performance import time_operation


ToolExecute = Callable[["ToolContext", dict], str]
CommandExecute = Callable[["CommandContext", str], object]
ContextProvider = Callable[["ExtensionContext"], str]
HookHandler = Callable[["HookContext"], None]
UiProvider = Callable[["ExtensionContext"], object]
LanguageProvider = Callable[[object], object]


PERMISSION_KEYS = (
    "tools",
    "commands",
    "context",
    "hooks",
    "ui",
    "language",
    "processes",
    "network",
    "workspace_read",
    "workspace_write",
    "extension_storage",
)

_ENFORCED_PERMISSIONS = {
    "tools",
    "commands",
    "context",
    "hooks",
    "ui",
    "language",
    "processes",
}
_PROCESS_CAPABILITY_HINTS = ("process", "shell")


@dataclass(frozen=True)
class ToolContext:
    cwd: str
    on_line: Callable[[str], None] | None = None
    cancel: object | None = None
    extension_id: str = "builtin"

    def is_cancelled(self) -> bool:
        return bool(self.cancel and getattr(self.cancel, "is_set", lambda: False)())

    @property
    def storage(self) -> "ExtensionStorage":
        return ExtensionStorage(self.cwd, self.extension_id)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    execute: ToolExecute
    parallel_safe: bool = False
    approval: str | None = None
    source: str = "builtin"
    extension_id: str = "builtin"


@dataclass(frozen=True)
class ExtensionCommand:
    name: str
    description: str
    prompt: str
    tools: list[str] | None = None
    source: str = "extension"
    execute: CommandExecute | None = None
    capabilities: list[str] = field(default_factory=list)
    extension_id: str = "extension"

    @property
    def executable(self) -> bool:
        return self.execute is not None


class ExtensionStorage:
    def __init__(self, cwd: str, extension_id: str, conversation_id: str = ""):
        self.cwd = cwd
        self.extension_id = _safe_extension_id(extension_id)
        self.conversation_id = conversation_id

    def load_config(self, scope: str = "project") -> dict:
        return _read_json_object(self._config_path(scope))

    def save_config(self, data: dict, scope: str = "project") -> None:
        _write_json_object(self._config_path(scope), data)

    def load_state(self, name: str = "state") -> dict:
        return _read_json_object(self._state_path(name))

    def save_state(self, data: dict, name: str = "state") -> None:
        _write_json_object(self._state_path(name), data)

    def artifact_path(self, name: str) -> Path:
        """Return a project-scoped path for extension-owned text artifacts."""
        return self._artifact_path(name)

    def save_artifact(self, name: str, content: str) -> str:
        path = self._artifact_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")
        return str(path)

    def load_artifact(self, name: str, max_chars: int | None = None) -> str:
        path = self._artifact_path(name)
        text = path.read_text(encoding="utf-8", errors="replace")
        if max_chars is None:
            return text
        try:
            limit = int(max_chars)
        except (TypeError, ValueError):
            return text
        if limit <= 0 or len(text) <= limit:
            return text
        return text[:limit]

    def _config_path(self, scope: str) -> Path:
        if scope == "global":
            return config.AICHS_HOME / "extensions" / f"{self.extension_id}.json"
        if scope != "project":
            raise ValueError("scope must be 'project' or 'global'")
        return Path(self.cwd) / ".aichs" / "extensions" / f"{self.extension_id}.json"

    def _state_path(self, name: str) -> Path:
        state_name = _safe_extension_id(name)
        if self.conversation_id:
            state_name = f"{_safe_extension_id(self.conversation_id)}-{state_name}"
        return Path(self.cwd) / ".aichs" / "state" / self.extension_id / f"{state_name}.json"

    def _artifact_path(self, name: str) -> Path:
        return (
            Path(self.cwd)
            / ".aichs"
            / "state"
            / self.extension_id
            / "artifacts"
            / _safe_artifact_name(name)
        )


@dataclass
class RuntimeDirective:
    action: str
    params: dict = field(default_factory=dict)


@dataclass
class RuntimeCommandApi:
    """UI-owned runtime actions exposed to executable extension commands."""

    show_notice: Callable[[str], None] | None = None
    send_message: Callable[[str], None] | None = None
    enqueue_message: Callable[[str], None] | None = None
    compact_now: Callable[[bool], None] | None = None
    compact_and_resume: Callable[[str, bool], None] | None = None
    processes: object | None = None
    process_factory: Callable[[str], object] | None = None

    def notice(self, text: str) -> None:
        if self.show_notice:
            self.show_notice(text)

    def send(self, text: str) -> None:
        if self.send_message:
            self.send_message(text)

    def enqueue(self, text: str) -> None:
        if self.enqueue_message:
            self.enqueue_message(text)

    def compact(self, *, force: bool = True) -> None:
        if self.compact_now:
            self.compact_now(force)

    def continue_after_compact(self, resume_prompt: str = "", *, force: bool = True) -> None:
        if self.compact_and_resume:
            self.compact_and_resume(resume_prompt, force)

    def bind_extension(self, extension_id: str) -> "RuntimeCommandApi":
        processes = self.processes
        if self.process_factory:
            processes = self.process_factory(extension_id)
        return RuntimeCommandApi(
            show_notice=self.show_notice,
            send_message=self.send_message,
            enqueue_message=self.enqueue_message,
            compact_now=self.compact_now,
            compact_and_resume=self.compact_and_resume,
            processes=processes,
            process_factory=self.process_factory,
        )


@dataclass
class CommandContext:
    cwd: str
    model: str = ""
    history: list[dict] = field(default_factory=list)
    conversation_id: str = ""
    command: str = ""
    extension_id: str = "extension"
    runtime: RuntimeCommandApi = field(default_factory=RuntimeCommandApi)

    @property
    def storage(self) -> ExtensionStorage:
        return ExtensionStorage(self.cwd, self.extension_id, self.conversation_id)


@dataclass(frozen=True)
class StatusBadge:
    name: str
    provider: UiProvider
    source: str = "extension"


@dataclass(frozen=True)
class ExtensionPanel:
    name: str
    title: str
    provider: UiProvider
    source: str = "extension"


@dataclass(frozen=True)
class LanguageContribution:
    name: str
    file_patterns: list[str]
    diagnostics: LanguageProvider | None = None
    symbols: LanguageProvider | None = None
    completion: LanguageProvider | None = None
    code_actions: LanguageProvider | None = None
    apply_code_action: LanguageProvider | None = None
    format_document: LanguageProvider | None = None
    source: str = "extension"
    extension_id: str = "extension"


@dataclass(frozen=True)
class ExtensionRequirements:
    executables: list[str] = field(default_factory=list)
    python: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExtensionPermissions:
    declared: bool = False
    tools: bool = False
    commands: bool = False
    context: bool = False
    hooks: bool = False
    ui: bool = False
    language: bool = False
    processes: bool = False
    network: bool = False
    workspace_read: bool = False
    workspace_write: bool = False
    extension_storage: bool = False

    def allows(self, name: str) -> bool:
        if not self.declared:
            return True
        return bool(getattr(self, name, False))

    def enabled_names(self) -> list[str]:
        return [name for name in PERMISSION_KEYS if bool(getattr(self, name, False))]


@dataclass(frozen=True)
class ExtensionFileSummary:
    path: str
    status: str
    tools: list[ToolDefinition]
    commands: list[ExtensionCommand]
    contexts: list[str]
    hooks: list[str]
    badges: list[StatusBadge]
    panels: list[ExtensionPanel]
    errors: list[str]
    description: str = ""
    display_name: str = ""
    languages: list[LanguageContribution] = field(default_factory=list)
    requirements: ExtensionRequirements = field(default_factory=ExtensionRequirements)
    missing_requirements: list[str] = field(default_factory=list)
    permissions: ExtensionPermissions = field(default_factory=ExtensionPermissions)
    permission_violations: list[str] = field(default_factory=list)
    content_hash: str = ""
    reviewed: bool = False
    review_required: bool = False
    risk_messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExtensionOverview:
    files: list[ExtensionFileSummary]

    @property
    def error_count(self) -> int:
        return sum(len(file.errors) for file in self.files)


@dataclass(frozen=True)
class _RegistrySnapshot:
    tools: tuple[ToolDefinition, ...]
    commands: tuple[ExtensionCommand, ...]
    context_providers: tuple[tuple[str, ContextProvider, str], ...]
    hooks: tuple[tuple[str, tuple[tuple[HookHandler, str], ...]], ...]
    badges: tuple[StatusBadge, ...]
    panels: tuple[ExtensionPanel, ...]
    languages: tuple[LanguageContribution, ...]
    errors: tuple[str, ...]
    permission_violations: tuple[str, ...]


@dataclass(frozen=True)
class _RegistryCacheEntry:
    signature: tuple
    snapshot: _RegistrySnapshot


@dataclass(frozen=True)
class _OverviewCacheEntry:
    signature: tuple
    overview: ExtensionOverview


@dataclass(frozen=True)
class _ExtensionTreeFile:
    rel_path: str
    path: Path
    signature: tuple


@dataclass(frozen=True)
class _ExtensionFileMetadata:
    entrypoint: Path
    normalized: str
    files: tuple[_ExtensionTreeFile, ...]
    folder_entrypoint: bool = False
    manifest_path: Path | None = None


_EXTENSION_CACHE_LOCK = threading.RLock()
_REGISTRY_CACHE: dict[tuple[str, str, str], _RegistryCacheEntry] = {}
_OVERVIEW_CACHE: dict[tuple[str, str, str], _OverviewCacheEntry] = {}
_EXTENSION_CACHE_EPOCHS: dict[tuple[str, str, str], int] = {}
_CONTENT_HASH_CACHE: dict[tuple, str] = {}
_CONTENT_HASH_CACHE_LIMIT = 256
_IGNORED_EXTENSION_TREE_NAMES = {".git", "__pycache__"}
_CONTENT_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ExtensionContext:
    cwd: str
    model: str = ""
    history: list[dict] = field(default_factory=list)
    processes: object | None = None
    extension_id: str = "extension"

    @property
    def storage(self) -> ExtensionStorage:
        return ExtensionStorage(self.cwd, self.extension_id)


@dataclass
class HookContext:
    event: str
    cwd: str
    model: str = ""
    system: str = ""
    history: list[dict] = field(default_factory=list)
    tool_name: str = ""
    inputs: dict = field(default_factory=dict)
    output: str = ""
    status: Literal["ok", "error", "cancelled"] = "ok"
    error: str = ""
    process: dict = field(default_factory=dict)
    directives: list[RuntimeDirective] = field(default_factory=list)
    extension_id: str = "extension"
    conversation_id: str = ""

    @property
    def storage(self) -> ExtensionStorage:
        return ExtensionStorage(self.cwd, self.extension_id, self.conversation_id)

    def directive(self, action: str, **params) -> RuntimeDirective:
        item = RuntimeDirective(action=action, params=dict(params))
        self.directives.append(item)
        return item

    def show_notice(self, text: str) -> RuntimeDirective:
        return self.directive("show_notice", text=text)

    def enqueue_message(self, text: str) -> RuntimeDirective:
        return self.directive("enqueue_message", text=text)

    def compact_now(self, *, force: bool = False, ledger: bool = False, reason: str = "") -> RuntimeDirective:
        return self.directive("compact_now", force=force, ledger=ledger, reason=reason)

    def compact_and_resume(
        self,
        *,
        resume_prompt: str = "",
        force: bool = False,
        ledger: bool = False,
        reason: str = "",
    ) -> RuntimeDirective:
        return self.directive(
            "compact_and_resume",
            resume_prompt=resume_prompt,
            force=force,
            ledger=ledger,
            reason=reason,
        )


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._commands: dict[str, ExtensionCommand] = {}
        self._context_providers: list[tuple[str, ContextProvider, str]] = []
        self._hooks: dict[str, list[tuple[HookHandler, str]]] = {}
        self._badges: dict[str, StatusBadge] = {}
        self._panels: dict[str, ExtensionPanel] = {}
        self._languages: dict[str, LanguageContribution] = {}
        self._current_extension_id = "extension"
        self._current_permissions = ExtensionPermissions()
        self._description = ""
        self.errors: list[str] = []
        self.permission_violations: list[str] = []

    def metadata(self, *, description: str = "") -> None:
        self._description = _clean_description(description)

    def tool(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict,
        execute: ToolExecute,
        parallel_safe: bool = False,
        approval: str | None = None,
        source: str = "extension",
    ) -> None:
        if not self._allow_contribution(source, "tools", f"tool {name}"):
            return
        if not name or not name.replace("_", "").isalnum():
            raise ValueError(f"invalid tool name: {name!r}")
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            execute=execute,
            parallel_safe=parallel_safe,
            approval=approval,
            source=source,
            extension_id="builtin" if source == "builtin" else self._current_extension_id,
        )

    def command(
        self,
        *,
        name: str,
        description: str,
        prompt: str = "",
        tools: list[str] | None = None,
        execute: CommandExecute | None = None,
        capabilities: list[str] | None = None,
        source: str = "extension",
    ) -> None:
        if not self._allow_contribution(source, "commands", f"command {name}"):
            return
        if _command_uses_processes(capabilities or []) and not self._allow_contribution(
            source,
            "processes",
            f"process command {name}",
        ):
            return
        if not name or not name.replace("_", "").isalnum():
            raise ValueError(f"invalid command name: {name!r}")
        if name in self._commands:
            raise ValueError(f"command already registered: {name}")
        self._commands[name] = ExtensionCommand(
            name=name,
            description=description,
            prompt=prompt,
            tools=tools,
            source=source,
            execute=execute,
            capabilities=list(capabilities or []),
            extension_id=self._current_extension_id,
        )

    def context(self, name: str, provider: ContextProvider) -> None:
        if not self._allow_contribution("extension", "context", f"context {name}"):
            return
        if not name:
            raise ValueError("context name is required")
        self._context_providers.append((name, provider, self._current_extension_id))

    def hook(self, event: str, handler: HookHandler) -> None:
        if not self._allow_contribution("extension", "hooks", f"hook {event}"):
            return
        if not event:
            raise ValueError("hook event is required")
        self._hooks.setdefault(event, []).append((handler, self._current_extension_id))

    def status_badge(
        self,
        *,
        name: str,
        provider: UiProvider,
        source: str = "extension",
    ) -> None:
        if not self._allow_contribution(source, "ui", f"status badge {name}"):
            return
        if not name or not name.replace("_", "").isalnum():
            raise ValueError(f"invalid status badge name: {name!r}")
        if name in self._badges:
            raise ValueError(f"status badge already registered: {name}")
        self._badges[name] = StatusBadge(name=name, provider=provider, source=source)

    def panel(
        self,
        *,
        name: str,
        title: str,
        provider: UiProvider,
        source: str = "extension",
    ) -> None:
        if not self._allow_contribution(source, "ui", f"panel {name}"):
            return
        if not name or not name.replace("_", "").isalnum():
            raise ValueError(f"invalid panel name: {name!r}")
        if name in self._panels:
            raise ValueError(f"panel already registered: {name}")
        self._panels[name] = ExtensionPanel(
            name=name,
            title=title,
            provider=provider,
            source=source,
        )

    def language(
        self,
        *,
        name: str,
        file_patterns: list[str],
        diagnostics: LanguageProvider | None = None,
        symbols: LanguageProvider | None = None,
        completion: LanguageProvider | None = None,
        code_actions: LanguageProvider | None = None,
        apply_code_action: LanguageProvider | None = None,
        format_document: LanguageProvider | None = None,
        source: str = "extension",
    ) -> None:
        if not self._allow_contribution(source, "language", f"language {name}"):
            return
        if not name or not name.replace("_", "").isalnum():
            raise ValueError(f"invalid language name: {name!r}")
        if name in self._languages:
            raise ValueError(f"language already registered: {name}")
        patterns = [str(pattern).strip() for pattern in file_patterns if str(pattern).strip()]
        if not patterns:
            raise ValueError("language file_patterns are required")
        if (
            diagnostics is None
            and symbols is None
            and completion is None
            and code_actions is None
            and apply_code_action is None
            and format_document is None
        ):
            raise ValueError(
                "language must provide diagnostics, symbols, or completion "
                "(or code_actions, apply_code_action, or format_document)"
            )
        self._languages[name] = LanguageContribution(
            name=name,
            file_patterns=patterns,
            diagnostics=diagnostics,
            symbols=symbols,
            completion=completion,
            code_actions=code_actions,
            apply_code_action=apply_code_action,
            format_document=format_document,
            source=source,
            extension_id="builtin" if source == "builtin" else self._current_extension_id,
        )

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def commands(self) -> list[ExtensionCommand]:
        return list(self._commands.values())

    def command_by_name(self, name: str) -> ExtensionCommand | None:
        return self._commands.get(name)

    def context_snippets(self, ctx: ExtensionContext) -> list[tuple[str, str]]:
        snippets: list[tuple[str, str]] = []
        for name, provider, extension_id in self._context_providers:
            scoped_ctx = ExtensionContext(
                cwd=ctx.cwd,
                model=ctx.model,
                history=ctx.history,
                processes=ctx.processes,
                extension_id=extension_id,
            )
            try:
                with time_operation(
                    "extension.context",
                    detail=f"extension={extension_id} name={name}",
                ):
                    text = provider(scoped_ctx)
            except Exception:
                self.errors.append(f"context {name}:\n{traceback.format_exc().rstrip()}")
                continue
            if text:
                snippets.append((name, str(text).strip()))
        return snippets

    def run_hooks(self, event: str, ctx: HookContext) -> None:
        previous_extension_id = ctx.extension_id
        for handler, extension_id in self._hooks.get(event, []):
            ctx.extension_id = extension_id
            try:
                with time_operation(
                    "extension.hook",
                    detail=f"extension={extension_id} event={event}",
                ):
                    _collect_directives(ctx, handler(ctx))
            except Exception:
                self.errors.append(f"hook {event}:\n{traceback.format_exc().rstrip()}")
            finally:
                ctx.extension_id = previous_extension_id

    def status_badges(self, ctx: ExtensionContext) -> list[tuple[StatusBadge, object]]:
        badges: list[tuple[StatusBadge, object]] = []
        for badge in self._badges.values():
            try:
                with time_operation(
                    "extension.status_badge",
                    detail=f"name={badge.name}",
                ):
                    data = badge.provider(ctx)
            except Exception:
                self.errors.append(f"status badge {badge.name}:\n{traceback.format_exc().rstrip()}")
                continue
            if data:
                badges.append((badge, data))
        return badges

    def panels(self) -> list[ExtensionPanel]:
        return list(self._panels.values())

    def panel_by_name(self, name: str) -> ExtensionPanel | None:
        return self._panels.get(name)

    def languages(self) -> list[LanguageContribution]:
        return list(self._languages.values())

    def _allow_contribution(self, source: str, permission: str, label: str) -> bool:
        if source == "builtin" or permission not in _ENFORCED_PERMISSIONS:
            return True
        if self._current_permissions.allows(permission):
            return True
        message = (
            f"Blocked undeclared extension contribution: {label} "
            f"requires permission '{permission}'."
        )
        self.permission_violations.append(message)
        self.errors.append(message)
        return False

def load_extensions(registry: ToolRegistry, cwd: str | None = None) -> None:
    _apply_registry_snapshot(registry, _cached_registry_snapshot(cwd))


def extension_commands(cwd: str | None = None) -> list[ExtensionCommand]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    return registry.commands()


def extension_command(name: str, cwd: str | None = None) -> ExtensionCommand | None:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    return registry.command_by_name(name)


def run_extension_command(
    cwd: str,
    name: str,
    args: str = "",
    *,
    model: str = "",
    history: list[dict] | None = None,
    conversation_id: str = "",
    runtime: RuntimeCommandApi | None = None,
) -> tuple[object, list[str]]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    command = registry.command_by_name(name)
    if command is None:
        return None, [f"command not found: {name}"]
    if command.execute is None:
        return None, [f"command is prompt-only: {name}"]
    runtime_api = (runtime or RuntimeCommandApi()).bind_extension(command.extension_id)
    ctx = CommandContext(
        cwd=cwd,
        model=model,
        history=history or [],
        conversation_id=conversation_id,
        command=name,
        extension_id=command.extension_id,
        runtime=runtime_api,
    )
    try:
        with time_operation(
            "extension.command",
            detail=f"extension={command.extension_id} name={name}",
        ):
            result = command.execute(ctx, args)
        return result, list(registry.errors)
    except Exception:
        registry.errors.append(f"command {name}:\n{traceback.format_exc().rstrip()}")
        return None, list(registry.errors)


def extension_context_snippets(
    cwd: str,
    *,
    model: str = "",
    history: list[dict] | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    ctx = ExtensionContext(cwd=cwd, model=model, history=history or [], processes=_process_api(cwd))
    return registry.context_snippets(ctx), list(registry.errors)


def run_extension_hooks(cwd: str, event: str, ctx: HookContext) -> list[str]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    registry.run_hooks(event, ctx)
    return list(registry.errors)


def extension_errors(cwd: str | None = None) -> list[str]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    return list(registry.errors)


def extension_overview(cwd: str | None = None) -> ExtensionOverview:
    return _cached_extension_overview(cwd)


def extension_static_summary(path: str | Path, cwd: str | None = None) -> ExtensionFileSummary:
    return _extension_file_summary(Path(path), cwd, load_code=False)


def is_extension_disabled(path: str | Path, cwd: str | None = None) -> bool:
    return _normalized_extension_path(path) in _disabled_extension_paths(cwd)


def set_extension_enabled(path: str | Path, enabled: bool, cwd: str | None = None) -> None:
    disabled = set(_disabled_extension_paths(cwd))
    normalized = _normalized_extension_path(path)
    if enabled:
        disabled.discard(normalized)
        mark_extension_reviewed(path, cwd)
    else:
        disabled.add(normalized)
    _write_disabled_extension_paths(cwd, sorted(disabled))
    clear_extension_cache(cwd)


def extension_content_hash(path: str | Path) -> str:
    return _extension_content_hash_from_metadata(_extension_file_metadata(Path(path)))


def _extension_content_hash_from_metadata(metadata: _ExtensionFileMetadata) -> str:
    signature = _extension_file_signature_from_metadata(metadata)
    with _EXTENSION_CACHE_LOCK:
        cached = _CONTENT_HASH_CACHE.get(signature)
    if cached is not None:
        return cached

    with time_operation(
        "extension.content_hash",
        detail=_extension_content_hash_detail(metadata),
        slow_ms=50,
    ):
        entrypoint = metadata.entrypoint
        hasher = hashlib.sha256()
        if metadata.folder_entrypoint and metadata.manifest_path is not None:
            for item in metadata.files:
                hasher.update(item.rel_path.encode("utf-8"))
                hasher.update(b"\0")
                _hash_file_into(hasher, item.path)
                hasher.update(b"\0")
        elif entrypoint.exists():
            hasher.update(entrypoint.name.encode("utf-8"))
            hasher.update(b"\0")
            _hash_file_into(hasher, entrypoint)
        digest = hasher.hexdigest()
    with _EXTENSION_CACHE_LOCK:
        if signature not in _CONTENT_HASH_CACHE and len(_CONTENT_HASH_CACHE) >= _CONTENT_HASH_CACHE_LIMIT:
            _CONTENT_HASH_CACHE.pop(next(iter(_CONTENT_HASH_CACHE)))
        _CONTENT_HASH_CACHE[signature] = digest
    return digest


def _extension_content_hash_detail(metadata: _ExtensionFileMetadata) -> str:
    size = 0
    for item in metadata.files:
        try:
            item_size = int(item.signature[2])
        except (IndexError, TypeError, ValueError):
            continue
        if item_size > 0:
            size += item_size
    return f"path={metadata.entrypoint} files={len(metadata.files)} bytes={size}"


def _hash_file_into(hasher, path: Path) -> None:
    with path.open("rb") as handle:
        while chunk := handle.read(_CONTENT_HASH_CHUNK_SIZE):
            hasher.update(chunk)


def is_extension_reviewed(path: str | Path, cwd: str | None = None) -> bool:
    return _extension_reviewed(path, cwd, extension_content_hash(path))


def is_extension_seen(path: str | Path, cwd: str | None = None) -> bool:
    return _extension_seen(path, cwd, extension_content_hash(path))


def _extension_reviewed(path: str | Path, cwd: str | None, content_hash: str) -> bool:
    normalized = _normalized_extension_path(path)
    reviewed = _reviewed_extensions(cwd).get(normalized)
    return (
        isinstance(reviewed, dict)
        and reviewed.get("hash") == content_hash
        and bool(reviewed.get("trusted"))
    )


def _extension_seen(path: str | Path, cwd: str | None, content_hash: str) -> bool:
    normalized = _normalized_extension_path(path)
    reviewed = _reviewed_extensions(cwd).get(normalized)
    return isinstance(reviewed, dict) and reviewed.get("hash") == content_hash


def mark_extension_reviewed(path: str | Path, cwd: str | None = None) -> None:
    _write_extension_review_state(path, cwd, trusted=True)
    clear_extension_cache(cwd)


def mark_extension_seen(path: str | Path, cwd: str | None = None) -> None:
    _write_extension_review_state(path, cwd, trusted=False)
    clear_extension_cache(cwd)


def _write_extension_review_state(path: str | Path, cwd: str | None, *, trusted: bool) -> None:
    data = _read_json_object(_reviewed_extensions_path(cwd))
    reviewed = data.get("reviewed")
    if not isinstance(reviewed, dict):
        reviewed = {}
    reviewed[_normalized_extension_path(path)] = {
        "hash": extension_content_hash(path),
        "trusted": bool(trusted),
    }
    _write_json_object(_reviewed_extensions_path(cwd), {"reviewed": reviewed})


def unreviewed_extension_summaries(cwd: str | None = None) -> list[ExtensionFileSummary]:
    return [
        extension_static_summary(path, cwd)
        for path in _extension_files(cwd)
        if not is_extension_seen(path, cwd)
    ]


def disable_unreviewed_extensions(cwd: str | None = None) -> list[ExtensionFileSummary]:
    summaries = unreviewed_extension_summaries(cwd)
    for summary in summaries:
        set_extension_enabled(summary.path, False, cwd)
        mark_extension_seen(summary.path, cwd)
    return summaries


def extension_status_badges(
    cwd: str,
    *,
    model: str = "",
    history: list[dict] | None = None,
) -> tuple[list[tuple[StatusBadge, object]], list[str]]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    ctx = ExtensionContext(cwd=cwd, model=model, history=history or [], processes=_process_api(cwd))
    return registry.status_badges(ctx), list(registry.errors)


def extension_panels(cwd: str | None = None) -> list[ExtensionPanel]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    return registry.panels()


def extension_panel_data(
    cwd: str,
    name: str,
    *,
    model: str = "",
    history: list[dict] | None = None,
) -> tuple[str, object, list[str]]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    panel = registry.panel_by_name(name)
    if panel is None:
        return name, {"title": name, "body": "Panel not found."}, list(registry.errors)
    ctx = ExtensionContext(cwd=cwd, model=model, history=history or [], processes=_process_api(cwd))
    try:
        with time_operation(
            "extension.panel",
            detail=f"name={name}",
        ):
            data = panel.provider(ctx)
    except Exception:
        registry.errors.append(f"panel {name}:\n{traceback.format_exc().rstrip()}")
        data = {"title": panel.title, "body": "Panel failed to load."}
    return panel.title, data, list(registry.errors)


def extension_languages(cwd: str | None = None) -> tuple[list[LanguageContribution], list[str]]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    return registry.languages(), list(registry.errors)


def clear_extension_cache(cwd: str | None = None) -> None:
    scope = _extension_cache_scope(cwd)
    with _EXTENSION_CACHE_LOCK:
        _CONTENT_HASH_CACHE.clear()
        _REGISTRY_CACHE.pop(scope, None)
        _OVERVIEW_CACHE.pop(scope, None)
        _EXTENSION_CACHE_EPOCHS[scope] = _EXTENSION_CACHE_EPOCHS.get(scope, 0) + 1


def clear_all_extension_caches() -> None:
    with _EXTENSION_CACHE_LOCK:
        _CONTENT_HASH_CACHE.clear()
        _REGISTRY_CACHE.clear()
        _OVERVIEW_CACHE.clear()
        _EXTENSION_CACHE_EPOCHS.clear()


def extension_cache_signature(cwd: str | None = None) -> tuple:
    scope = _extension_cache_scope(cwd)
    signature = _extension_cache_signature(cwd)
    with _EXTENSION_CACHE_LOCK:
        epoch = _EXTENSION_CACHE_EPOCHS.get(scope, 0)
    return (epoch, signature)


def _cached_registry_snapshot(cwd: str | None) -> _RegistrySnapshot:
    scope = _extension_cache_scope(cwd)
    extension_files = _extension_files(cwd)
    metadata = tuple(_extension_file_metadata(path) for path in extension_files)
    signature = _extension_cache_signature_for_metadata(cwd, metadata)
    with _EXTENSION_CACHE_LOCK:
        entry = _REGISTRY_CACHE.get(scope)
        if entry is not None and entry.signature == signature:
            return entry.snapshot

    registry = ToolRegistry()
    with time_operation("extension.load", detail=f"cwd={cwd or ''}"):
        _load_extensions_uncached(registry, cwd, extension_files=extension_files)
    snapshot = _registry_snapshot(registry)

    with _EXTENSION_CACHE_LOCK:
        _REGISTRY_CACHE[scope] = _RegistryCacheEntry(signature=signature, snapshot=snapshot)
    return snapshot


def _cached_extension_overview(cwd: str | None) -> ExtensionOverview:
    scope = _extension_cache_scope(cwd)
    extension_files = _extension_files(cwd)
    metadata = tuple(_extension_file_metadata(path) for path in extension_files)
    signature = _extension_cache_signature_for_metadata(cwd, metadata)
    with _EXTENSION_CACHE_LOCK:
        entry = _OVERVIEW_CACHE.get(scope)
        if entry is not None and entry.signature == signature:
            return ExtensionOverview(files=list(entry.overview.files))

    with time_operation("extension.overview", detail=f"cwd={cwd or ''}"):
        overview = ExtensionOverview(
            files=[
                _extension_file_summary(item.entrypoint, cwd, metadata=item)
                for item in metadata
            ]
        )

    with _EXTENSION_CACHE_LOCK:
        _OVERVIEW_CACHE[scope] = _OverviewCacheEntry(signature=signature, overview=overview)
    return ExtensionOverview(files=list(overview.files))


def _load_extensions_uncached(
    registry: ToolRegistry,
    cwd: str | None = None,
    *,
    extension_files: list[Path] | None = None,
) -> None:
    for path in extension_files if extension_files is not None else _extension_files(cwd):
        if is_extension_disabled(path, cwd):
            continue
        _load_extension_file(registry, path)


def _registry_snapshot(registry: ToolRegistry) -> _RegistrySnapshot:
    return _RegistrySnapshot(
        tools=tuple(tool for tool in registry.all() if tool.source != "builtin"),
        commands=tuple(registry.commands()),
        context_providers=tuple(registry._context_providers),
        hooks=tuple(
            (event, tuple(handlers))
            for event, handlers in sorted(registry._hooks.items())
        ),
        badges=tuple(registry._badges.values()),
        panels=tuple(registry.panels()),
        languages=tuple(registry.languages()),
        errors=tuple(registry.errors),
        permission_violations=tuple(registry.permission_violations),
    )


def _apply_registry_snapshot(registry: ToolRegistry, snapshot: _RegistrySnapshot) -> None:
    for tool in snapshot.tools:
        if tool.name in registry._tools:
            registry.errors.append(f"cached extension tool {tool.name}: tool already registered")
            continue
        registry._tools[tool.name] = tool
    for command in snapshot.commands:
        if command.name in registry._commands:
            registry.errors.append(f"cached extension command {command.name}: command already registered")
            continue
        registry._commands[command.name] = command
    registry._context_providers.extend(snapshot.context_providers)
    for event, handlers in snapshot.hooks:
        registry._hooks.setdefault(event, []).extend(handlers)
    for badge in snapshot.badges:
        if badge.name not in registry._badges:
            registry._badges[badge.name] = badge
    for panel in snapshot.panels:
        if panel.name not in registry._panels:
            registry._panels[panel.name] = panel
    for language in snapshot.languages:
        if language.name not in registry._languages:
            registry._languages[language.name] = language
    registry.errors.extend(snapshot.errors)
    registry.permission_violations.extend(snapshot.permission_violations)


def _extension_cache_scope(cwd: str | None) -> tuple[str, str, str]:
    root = ""
    if cwd:
        try:
            root = str(Path(cwd).resolve())
        except OSError:
            root = str(Path(cwd))
    return (root, str(config.AICHS_HOME))


def _extension_cache_signature(cwd: str | None) -> tuple:
    return _extension_cache_signature_for_files(cwd, _extension_files(cwd))


def _extension_cache_signature_for_files(cwd: str | None, extension_files: list[Path]) -> tuple:
    return _extension_cache_signature_for_metadata(
        cwd,
        tuple(_extension_file_metadata(path) for path in extension_files),
    )


def _extension_cache_signature_for_metadata(
    cwd: str | None,
    extension_metadata: tuple[_ExtensionFileMetadata, ...],
) -> tuple:
    return (
        tuple(_extension_file_signature_from_metadata(item) for item in extension_metadata),
        _path_signature(_disabled_extensions_path(cwd)),
        _path_signature(_reviewed_extensions_path(cwd)),
    )


def _extension_file_signature(path: Path) -> tuple:
    return _extension_file_signature_from_metadata(_extension_file_metadata(path))


def _extension_file_signature_from_metadata(metadata: _ExtensionFileMetadata) -> tuple:
    if not metadata.folder_entrypoint:
        return (metadata.normalized, tuple(item.signature for item in metadata.files))
    return (
        metadata.normalized,
        tuple((item.rel_path, item.signature) for item in metadata.files),
    )


def _extension_file_metadata(path: Path) -> _ExtensionFileMetadata:
    entrypoint = _extension_entrypoint_path(path)
    try:
        normalized = str(entrypoint.resolve())
    except OSError:
        normalized = str(entrypoint)
    if entrypoint.name != "extension.py":
        return _ExtensionFileMetadata(
            entrypoint=entrypoint,
            normalized=normalized,
            files=(_ExtensionTreeFile(entrypoint.name, entrypoint, _path_signature(entrypoint)),),
        )

    root = entrypoint.parent
    files: list[_ExtensionTreeFile] = []
    for item in _iter_extension_tree_files(root):
        try:
            rel = item.relative_to(root).as_posix()
        except ValueError:
            rel = item.name
        files.append(_ExtensionTreeFile(rel, item, _path_signature(item)))
    manifest_path = root / "aichs-extension.json"
    return _ExtensionFileMetadata(
        entrypoint=entrypoint,
        normalized=normalized,
        files=tuple(files),
        folder_entrypoint=True,
        manifest_path=manifest_path if manifest_path.exists() else None,
    )


def _iter_extension_tree_files(root: Path):
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        dirs: list[Path] = []
        for child in children:
            if child.name in _IGNORED_EXTENSION_TREE_NAMES:
                continue
            if child.is_dir():
                dirs.append(child)
            elif child.is_file():
                yield child
        stack.extend(reversed(dirs))


def _path_signature(path: Path) -> tuple:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), -1, -1)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _extension_files(cwd: str | None) -> list[Path]:
    roots = [config.AICHS_HOME / "extensions"]
    if cwd:
        roots.append(Path(cwd) / ".aichs" / "extensions")

    files: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in _iter_extension_entrypoints(root):
            resolved = path.resolve()
            if resolved not in seen and path.name != "__init__.py":
                files.append(path)
                seen.add(resolved)
    return files


def _iter_extension_entrypoints(root: Path) -> list[Path]:
    candidates: list[Path] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return candidates
    for child in children:
        if child.name == "__init__.py":
            continue
        if child.is_file() and child.suffix == ".py":
            candidates.append(child)
            continue
        if child.is_dir():
            entrypoint = child / "extension.py"
            if entrypoint.is_file():
                candidates.append(entrypoint)
    return sorted(candidates)


def _load_extension_file(registry: ToolRegistry, path: Path) -> None:
    module_name = f"_aichs_ext_{abs(hash(str(path.resolve())))}"
    previous_extension_id = registry._current_extension_id
    previous_permissions = registry._current_permissions
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not create import spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if not callable(register):
            registry.errors.append(f"{path}: missing register(registry)")
            return
        registry._current_extension_id = _extension_id_for_path(path)
        registry._current_permissions = _extension_manifest_permissions(
            _extension_manifest_metadata(path)
        )
        register(registry)
    except BaseException:
        registry.errors.append(f"{path}:\n{traceback.format_exc().rstrip()}")
    finally:
        registry._current_extension_id = previous_extension_id
        registry._current_permissions = previous_permissions
        sys.modules.pop(module_name, None)


def _extension_file_summary(
    path: Path,
    cwd: str | None = None,
    *,
    load_code: bool = True,
    metadata: _ExtensionFileMetadata | None = None,
) -> ExtensionFileSummary:
    metadata = metadata or _extension_file_metadata(path)
    path = metadata.entrypoint
    manifest = _extension_manifest_metadata(path)
    display_name = _clean_description(str(manifest.get("name") or ""))
    requirements = _extension_manifest_requirements(manifest)
    missing_requirements = _missing_extension_requirements(requirements)
    permissions = _extension_manifest_permissions(manifest)
    content_hash = _extension_content_hash_from_metadata(metadata)
    reviewed = _extension_reviewed(path, cwd, content_hash)
    review_required = not reviewed
    risk_messages = _extension_risk_messages(permissions)
    if is_extension_disabled(path, cwd) or not load_code:
        static_description = _static_extension_description(path, manifest=manifest)
        static = _static_extension_contributions(path)
        return ExtensionFileSummary(
            path=str(path),
            status="Disabled",
            tools=static["tools"],
            commands=static["commands"],
            contexts=static["contexts"],
            hooks=static["hooks"],
            badges=static["badges"],
            panels=static["panels"],
            errors=[],
            description=static_description,
            display_name=display_name,
            languages=static["languages"],
            requirements=requirements,
            missing_requirements=missing_requirements,
            permissions=permissions,
            content_hash=content_hash,
            reviewed=reviewed,
            review_required=review_required,
            risk_messages=risk_messages,
        )
    registry = ToolRegistry()
    before = registry.errors[:]
    before_violations = registry.permission_violations[:]
    _load_extension_file(registry, path)
    errors = registry.errors[len(before):]
    violations = registry.permission_violations[len(before_violations):]
    return ExtensionFileSummary(
        path=str(path),
        status="Failed" if errors else "Loaded",
        tools=[tool for tool in registry.all() if tool.source != "builtin"],
        commands=registry.commands(),
        contexts=[name for name, _provider, _extension_id in registry._context_providers],
        hooks=sorted(registry._hooks.keys()),
        badges=list(registry._badges.values()),
        panels=registry.panels(),
        errors=errors,
        description=registry._description or _static_extension_description(path, manifest=manifest),
        display_name=display_name,
        languages=registry.languages(),
        requirements=requirements,
        missing_requirements=missing_requirements,
        permissions=permissions,
        permission_violations=violations,
        content_hash=content_hash,
        reviewed=reviewed,
        review_required=review_required,
        risk_messages=risk_messages,
    )


def _disabled_extension_paths(cwd: str | None) -> list[str]:
    data = _read_json_object(_disabled_extensions_path(cwd))
    paths = data.get("disabled", [])
    if not isinstance(paths, list):
        return []
    return [str(path) for path in paths if str(path).strip()]


def _write_disabled_extension_paths(cwd: str | None, paths: list[str]) -> None:
    _write_json_object(_disabled_extensions_path(cwd), {"disabled": paths})


def _disabled_extensions_path(cwd: str | None) -> Path:
    return config.AICHS_HOME / "project" / "extensions.disabled.json"


def _reviewed_extensions(cwd: str | None) -> dict:
    data = _read_json_object(_reviewed_extensions_path(cwd))
    reviewed = data.get("reviewed")
    return reviewed if isinstance(reviewed, dict) else {}


def _reviewed_extensions_path(cwd: str | None) -> Path:
    return config.AICHS_HOME / "project" / "extensions.reviewed.json"


def _normalized_extension_path(path: str | Path) -> str:
    path = _extension_entrypoint_path(Path(path))
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path))


def _extension_entrypoint_path(path: Path) -> Path:
    if path.is_dir() and (path / "extension.py").exists():
        return path / "extension.py"
    return path


def _static_extension_description(path: Path, *, manifest: dict | None = None) -> str:
    manifest_description = _clean_description(
        str((manifest if manifest is not None else _extension_manifest_metadata(path)).get("description") or "")
    )
    if manifest_description:
        return manifest_description
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ""
    explicit = _module_string_constant(module, "EXTENSION_DESCRIPTION")
    if explicit:
        return explicit
    metadata = _module_dict_string(module, "EXTENSION", "description")
    if metadata:
        return metadata
    return _clean_description(ast.get_docstring(module) or "")


def _static_extension_contributions(path: Path) -> dict:
    empty = {
        "tools": [],
        "commands": [],
        "contexts": [],
        "hooks": [],
        "badges": [],
        "panels": [],
        "languages": [],
    }
    try:
        module = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return empty

    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        name = _registry_call_name(node)
        if not name:
            continue
        if name == "tool":
            tool_name = _call_string_arg(node, "name")
            if tool_name:
                empty["tools"].append(
                    ToolDefinition(
                        name=tool_name,
                        description=_call_string_arg(node, "description"),
                        input_schema={},
                        execute=lambda _ctx, _inputs: "",
                    )
                )
        elif name == "command":
            command_name = _call_string_arg(node, "name")
            if command_name:
                capabilities = _call_string_list_arg(node, "capabilities")
                empty["commands"].append(
                    ExtensionCommand(
                        name=command_name,
                        description=_call_string_arg(node, "description"),
                        prompt=_call_string_arg(node, "prompt"),
                        execute=(lambda _ctx, _args: "") if _call_has_keyword(node, "execute") else None,
                        capabilities=capabilities,
                    )
                )
        elif name == "context":
            context_name = _call_pos_string_arg(node, 0) or _call_string_arg(node, "name")
            if context_name:
                empty["contexts"].append(context_name)
        elif name == "hook":
            hook_name = _call_pos_string_arg(node, 0) or _call_string_arg(node, "event")
            if hook_name:
                empty["hooks"].append(hook_name)
        elif name == "status_badge":
            badge_name = _call_string_arg(node, "name")
            if badge_name:
                empty["badges"].append(StatusBadge(name=badge_name, provider=lambda _ctx: {}))
        elif name == "panel":
            panel_name = _call_string_arg(node, "name")
            if panel_name:
                empty["panels"].append(
                    ExtensionPanel(
                        name=panel_name,
                        title=_call_string_arg(node, "title") or panel_name,
                        provider=lambda _ctx: {},
                    )
                )
        elif name == "language":
            language_name = _call_string_arg(node, "name")
            if language_name:
                patterns = _call_string_list_arg(node, "file_patterns")
                empty["languages"].append(
                    LanguageContribution(
                        name=language_name,
                        file_patterns=patterns,
                        diagnostics=(lambda _ctx: []) if _call_has_keyword(node, "diagnostics") else None,
                        symbols=(lambda _ctx: []) if _call_has_keyword(node, "symbols") else None,
                        completion=(lambda _ctx: []) if _call_has_keyword(node, "completion") else None,
                        code_actions=(lambda _ctx: []) if _call_has_keyword(node, "code_actions") else None,
                        apply_code_action=(lambda _ctx: {}) if _call_has_keyword(node, "apply_code_action") else None,
                        format_document=(lambda _ctx: {}) if _call_has_keyword(node, "format_document") else None,
                    )
                )
    empty["hooks"] = sorted(dict.fromkeys(empty["hooks"]))
    empty["contexts"] = list(dict.fromkeys(empty["contexts"]))
    return empty


def _registry_call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "registry":
        return func.attr
    return ""


def _call_has_keyword(node: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in node.keywords)


def _call_string_arg(node: ast.Call, name: str) -> str:
    for keyword in node.keywords:
        if keyword.arg == name:
            return _literal_string(keyword.value)
    return ""


def _call_pos_string_arg(node: ast.Call, index: int) -> str:
    if index < len(node.args):
        return _literal_string(node.args[index])
    return ""


def _call_string_list_arg(node: ast.Call, name: str) -> list[str]:
    for keyword in node.keywords:
        if keyword.arg != name:
            continue
        value = keyword.value
        if not isinstance(value, (ast.List, ast.Tuple)):
            return []
        items = [_literal_string(item) for item in value.elts]
        return [item for item in items if item]
    return []


def _extension_manifest_metadata(path: Path) -> dict:
    manifest_path = _extension_manifest_path(path)
    if manifest_path is None:
        return {}
    try:
        data = _read_json_object(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data


def _extension_manifest_requirements(manifest: dict) -> ExtensionRequirements:
    requires = manifest.get("requires")
    if not isinstance(requires, dict):
        return ExtensionRequirements()
    return ExtensionRequirements(
        executables=_manifest_string_list(requires.get("executables")),
        python=_manifest_string_list(requires.get("python")),
    )


def _extension_manifest_permissions(manifest: dict) -> ExtensionPermissions:
    raw = manifest.get("permissions")
    if not isinstance(raw, dict):
        return ExtensionPermissions(declared=False)
    values = {
        key: bool(raw.get(key, False))
        for key in PERMISSION_KEYS
    }
    return ExtensionPermissions(declared=True, **values)


def _extension_risk_messages(permissions: ExtensionPermissions) -> list[str]:
    messages = [
        "Enabled extensions run local Python code in the AICHS process.",
        "Manifest permissions enforce AICHS contribution surfaces, not an OS sandbox.",
    ]
    if not permissions.declared:
        messages.append("This extension does not disclose manifest permissions.")
    if permissions.workspace_read or permissions.workspace_write:
        messages.append("Workspace access is disclosed but not sandbox-enforced in v1.")
    if permissions.network:
        messages.append("Network access is disclosed but not sandbox-enforced in v1.")
    return messages


def _manifest_string_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    seen = set()
    items = []
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _missing_extension_requirements(requirements: ExtensionRequirements) -> list[str]:
    missing = []
    for executable in requirements.executables:
        if shutil.which(executable) is None:
            missing.append(f"executable:{executable}")
    for module in requirements.python:
        if not _python_requirement_available(module):
            missing.append(f"python:{module}")
    return missing


def _python_requirement_available(name: str) -> bool:
    candidates = [name, name.replace("-", "_")]
    for candidate in candidates:
        try:
            if importlib.util.find_spec(candidate) is not None:
                return True
        except (ImportError, AttributeError, ValueError):
            continue
    return False


def _extension_manifest_path(path: Path) -> Path | None:
    if path.name == "extension.py":
        candidate = path.parent / "aichs-extension.json"
        return candidate if candidate.exists() else None
    return None


def _command_uses_processes(capabilities: list[str]) -> bool:
    text = " ".join(str(capability).lower() for capability in capabilities)
    return any(hint in text for hint in _PROCESS_CAPABILITY_HINTS)


def _extension_id_for_path(path: Path) -> str:
    if path.name == "extension.py" and path.parent.name:
        return _safe_extension_id(path.parent.name)
    return _safe_extension_id(path.stem)


def _module_string_constant(module: ast.Module, name: str) -> str:
    for node in module.body:
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return _literal_string(value)
    return ""


def _module_dict_string(module: ast.Module, name: str, key: str) -> str:
    for node in module.body:
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        if not isinstance(value, ast.Dict):
            continue
        for dict_key, dict_value in zip(value.keys, value.values):
            if _literal_string(dict_key) == key:
                return _literal_string(dict_value)
    return ""


def _literal_string(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _clean_description(node.value)
    return ""


def _clean_description(value: str) -> str:
    return " ".join(str(value or "").split())


def _collect_directives(ctx: HookContext, result) -> None:
    if result is None:
        return
    if isinstance(result, RuntimeDirective):
        ctx.directives.append(result)
        return
    if isinstance(result, dict):
        action = str(result.get("action") or "").strip()
        if action:
            params = {k: v for k, v in result.items() if k != "action"}
            ctx.directives.append(RuntimeDirective(action=action, params=params))
        return
    if isinstance(result, (list, tuple)):
        for item in result:
            _collect_directives(ctx, item)


def _process_api(cwd: str) -> object:
    try:
        from services.processes import RuntimeProcessApi, get_process_manager

        return RuntimeProcessApi(get_process_manager(), workspace=cwd)
    except Exception:
        return None


def _safe_extension_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(value or "extension"))
    return safe.strip("._-") or "extension"


def _safe_artifact_name(value: str) -> str:
    basename = str(value or "artifact").replace("\\", "/").split("/")[-1]
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in basename)
    return safe.strip(" ._-") or "artifact"


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json_object(path: Path, data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("extension config/state must be a JSON object")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

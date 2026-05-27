from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal


ToolExecute = Callable[["ToolContext", dict], str]
ContextProvider = Callable[["ExtensionContext"], str]
HookHandler = Callable[["HookContext"], None]
UiProvider = Callable[["ExtensionContext"], object]


@dataclass(frozen=True)
class ToolContext:
    cwd: str
    on_line: Callable[[str], None] | None = None
    cancel: object | None = None

    def is_cancelled(self) -> bool:
        return bool(self.cancel and getattr(self.cancel, "is_set", lambda: False)())


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    execute: ToolExecute
    parallel_safe: bool = False
    approval: str | None = None
    source: str = "builtin"


@dataclass(frozen=True)
class ExtensionCommand:
    name: str
    description: str
    prompt: str
    tools: list[str] | None = None
    source: str = "extension"


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


@dataclass(frozen=True)
class ExtensionOverview:
    files: list[ExtensionFileSummary]

    @property
    def error_count(self) -> int:
        return sum(len(file.errors) for file in self.files)


@dataclass(frozen=True)
class ExtensionContext:
    cwd: str
    model: str = ""
    history: list[dict] = field(default_factory=list)


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


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._commands: dict[str, ExtensionCommand] = {}
        self._context_providers: list[tuple[str, ContextProvider]] = []
        self._hooks: dict[str, list[HookHandler]] = {}
        self._badges: dict[str, StatusBadge] = {}
        self._panels: dict[str, ExtensionPanel] = {}
        self.errors: list[str] = []

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
        )

    def command(
        self,
        *,
        name: str,
        description: str,
        prompt: str,
        tools: list[str] | None = None,
        source: str = "extension",
    ) -> None:
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
        )

    def context(self, name: str, provider: ContextProvider) -> None:
        if not name:
            raise ValueError("context name is required")
        self._context_providers.append((name, provider))

    def hook(self, event: str, handler: HookHandler) -> None:
        if not event:
            raise ValueError("hook event is required")
        self._hooks.setdefault(event, []).append(handler)

    def status_badge(
        self,
        *,
        name: str,
        provider: UiProvider,
        source: str = "extension",
    ) -> None:
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
        for name, provider in self._context_providers:
            try:
                text = provider(ctx)
            except Exception:
                self.errors.append(f"context {name}:\n{traceback.format_exc().rstrip()}")
                continue
            if text:
                snippets.append((name, str(text).strip()))
        return snippets

    def run_hooks(self, event: str, ctx: HookContext) -> None:
        for handler in self._hooks.get(event, []):
            try:
                handler(ctx)
            except Exception:
                self.errors.append(f"hook {event}:\n{traceback.format_exc().rstrip()}")

    def status_badges(self, ctx: ExtensionContext) -> list[tuple[StatusBadge, object]]:
        badges: list[tuple[StatusBadge, object]] = []
        for badge in self._badges.values():
            try:
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

def load_extensions(registry: ToolRegistry, cwd: str | None = None) -> None:
    for path in _extension_files(cwd):
        _load_extension_file(registry, path)


def extension_commands(cwd: str | None = None) -> list[ExtensionCommand]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    return registry.commands()


def extension_command(name: str, cwd: str | None = None) -> ExtensionCommand | None:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    return registry.command_by_name(name)


def extension_context_snippets(
    cwd: str,
    *,
    model: str = "",
    history: list[dict] | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    ctx = ExtensionContext(cwd=cwd, model=model, history=history or [])
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
    summaries = [_extension_file_summary(path) for path in _extension_files(cwd)]
    return ExtensionOverview(files=summaries)


def extension_status_badges(
    cwd: str,
    *,
    model: str = "",
    history: list[dict] | None = None,
) -> tuple[list[tuple[StatusBadge, object]], list[str]]:
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    ctx = ExtensionContext(cwd=cwd, model=model, history=history or [])
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
    ctx = ExtensionContext(cwd=cwd, model=model, history=history or [])
    try:
        data = panel.provider(ctx)
    except Exception:
        registry.errors.append(f"panel {name}:\n{traceback.format_exc().rstrip()}")
        data = {"title": panel.title, "body": "Panel failed to load."}
    return panel.title, data, list(registry.errors)


def _extension_files(cwd: str | None) -> list[Path]:
    roots = [Path.home() / ".aichs" / "extensions"]
    if cwd:
        roots.append(Path(cwd) / ".aichs" / "extensions")

    files: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.py")):
            resolved = path.resolve()
            if resolved not in seen and path.name != "__init__.py":
                files.append(path)
                seen.add(resolved)
    return files


def _load_extension_file(registry: ToolRegistry, path: Path) -> None:
    module_name = f"_aichs_ext_{abs(hash(str(path.resolve())))}"
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
        register(registry)
    except Exception:
        registry.errors.append(f"{path}:\n{traceback.format_exc().rstrip()}")
    finally:
        sys.modules.pop(module_name, None)


def _extension_file_summary(path: Path) -> ExtensionFileSummary:
    registry = ToolRegistry()
    before = registry.errors[:]
    _load_extension_file(registry, path)
    errors = registry.errors[len(before):]
    return ExtensionFileSummary(
        path=str(path),
        status="Failed" if errors else "Loaded",
        tools=[tool for tool in registry.all() if tool.source != "builtin"],
        commands=registry.commands(),
        contexts=[name for name, _provider in registry._context_providers],
        hooks=sorted(registry._hooks.keys()),
        badges=list(registry._badges.values()),
        panels=registry.panels(),
        errors=errors,
    )

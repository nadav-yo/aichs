from __future__ import annotations

import importlib.util
import ast
import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal


ToolExecute = Callable[["ToolContext", dict], str]
CommandExecute = Callable[["CommandContext", str], object]
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

    def _config_path(self, scope: str) -> Path:
        if scope == "global":
            return Path.home() / ".aichs" / "extensions" / f"{self.extension_id}.json"
        if scope != "project":
            raise ValueError("scope must be 'project' or 'global'")
        return Path(self.cwd) / ".aichs" / "extensions" / f"{self.extension_id}.json"

    def _state_path(self, name: str) -> Path:
        state_name = _safe_extension_id(name)
        if self.conversation_id:
            state_name = f"{_safe_extension_id(self.conversation_id)}-{state_name}"
        return Path(self.cwd) / ".aichs" / "state" / self.extension_id / f"{state_name}.json"


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
    directives: list[RuntimeDirective] = field(default_factory=list)

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
        self._context_providers: list[tuple[str, ContextProvider]] = []
        self._hooks: dict[str, list[HookHandler]] = {}
        self._badges: dict[str, StatusBadge] = {}
        self._panels: dict[str, ExtensionPanel] = {}
        self._current_extension_id = "extension"
        self._description = ""
        self.errors: list[str] = []

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
        prompt: str = "",
        tools: list[str] | None = None,
        execute: CommandExecute | None = None,
        capabilities: list[str] | None = None,
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
            execute=execute,
            capabilities=list(capabilities or []),
            extension_id=self._current_extension_id,
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
                _collect_directives(ctx, handler(ctx))
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
        if is_extension_disabled(path, cwd):
            continue
        _load_extension_file(registry, path)


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
    ctx = CommandContext(
        cwd=cwd,
        model=model,
        history=history or [],
        conversation_id=conversation_id,
        command=name,
        extension_id=command.extension_id,
        runtime=runtime or RuntimeCommandApi(),
    )
    try:
        return command.execute(ctx, args), list(registry.errors)
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
    summaries = [_extension_file_summary(path, cwd) for path in _extension_files(cwd)]
    return ExtensionOverview(files=summaries)


def is_extension_disabled(path: str | Path, cwd: str | None = None) -> bool:
    return _normalized_extension_path(path) in _disabled_extension_paths(cwd)


def set_extension_enabled(path: str | Path, enabled: bool, cwd: str | None = None) -> None:
    disabled = set(_disabled_extension_paths(cwd))
    normalized = _normalized_extension_path(path)
    if enabled:
        disabled.discard(normalized)
    else:
        disabled.add(normalized)
    _write_disabled_extension_paths(cwd, sorted(disabled))


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
    previous_extension_id = registry._current_extension_id
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
        registry._current_extension_id = path.stem
        register(registry)
    except Exception:
        registry.errors.append(f"{path}:\n{traceback.format_exc().rstrip()}")
    finally:
        registry._current_extension_id = previous_extension_id
        sys.modules.pop(module_name, None)


def _extension_file_summary(path: Path, cwd: str | None = None) -> ExtensionFileSummary:
    static_description = _static_extension_description(path)
    if is_extension_disabled(path, cwd):
        return ExtensionFileSummary(
            path=str(path),
            status="Disabled",
            tools=[],
            commands=[],
            contexts=[],
            hooks=[],
            badges=[],
            panels=[],
            errors=[],
            description=static_description,
        )
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
        description=registry._description or static_description,
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
    base = Path(cwd) if cwd else Path.home()
    return base / ".aichs" / "extensions.disabled.json"


def _normalized_extension_path(path: str | Path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path))


def _static_extension_description(path: Path) -> str:
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


def _safe_extension_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(value or "extension"))
    return safe.strip("._-") or "extension"


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

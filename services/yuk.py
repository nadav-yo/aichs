from __future__ import annotations

import json
import os
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import config
from storage.settings import (
    ARCHIVIST_PROMPT_KEY,
    AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY,
    COMPACT_RESUME_PROMPT_KEY,
    COMPACTION_SUMMARY_GUIDANCE_KEY,
    COMMIT_MESSAGE_PROMPT_ADDITION_KEY,
    DEFAULT_ARCHIVIST_PROMPT,
    DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS,
    DEFAULT_COMPACT_RESUME_PROMPT,
    DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE,
    DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
    DEFAULT_GIT_FIX_PROMPT_TEMPLATE,
    DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY,
    FILE_REVIEW_PROMPT_TEMPLATE_KEY,
    GIT_FIX_PROMPT_TEMPLATE_KEY,
    SettingsStore,
)
from services.tool_registry import (
    is_extension_disabled,
    set_extension_enabled,
    extension_static_summary,
)


YUK_FORMAT = "aichs-yuk/v1"

_PROMPT_SETTING_KEYS = [
    "system_prompt",
    FILE_REVIEW_PROMPT_TEMPLATE_KEY,
    DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY,
    GIT_FIX_PROMPT_TEMPLATE_KEY,
    COMPACT_RESUME_PROMPT_KEY,
    AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY,
    COMPACTION_SUMMARY_GUIDANCE_KEY,
    ARCHIVIST_PROMPT_KEY,
    COMMIT_MESSAGE_PROMPT_ADDITION_KEY,
]
_PROMPT_SETTING_DEFAULTS = {
    "system_prompt": config.SYSTEM_PROMPT,
    FILE_REVIEW_PROMPT_TEMPLATE_KEY: DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
    DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY: DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE,
    GIT_FIX_PROMPT_TEMPLATE_KEY: DEFAULT_GIT_FIX_PROMPT_TEMPLATE,
    COMPACT_RESUME_PROMPT_KEY: DEFAULT_COMPACT_RESUME_PROMPT,
    AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY: DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS,
    COMPACTION_SUMMARY_GUIDANCE_KEY: "",
    ARCHIVIST_PROMPT_KEY: DEFAULT_ARCHIVIST_PROMPT,
    COMMIT_MESSAGE_PROMPT_ADDITION_KEY: "",
}
_CREW_SETTING_KEYS = ["crew", "crew_models", "avatar_human", "avatar_agent"]
_SETTING_KEYS = _PROMPT_SETTING_KEYS + _CREW_SETTING_KEYS
_SECRET_AND_MODEL_KEYS = {
    "anthropic_api_key",
    "openai_api_key",
    "provider_api_keys",
    "default_models",
    "provider_order",
    "models",
    "providers",
}
_IGNORED_EXTENSION_NAMES = {".git", "__pycache__"}


@dataclass(frozen=True)
class YukExportItem:
    id: str
    section: str
    label: str
    scope: str = ""
    path: str = ""
    kind: str = ""
    selected: bool = True
    enabled: bool | None = None
    note: str = ""


@dataclass
class YukExportSelection:
    selected_item_ids: set[str] | None = None

    def includes(self, item_id: str) -> bool:
        return self.selected_item_ids is None or item_id in self.selected_item_ids


@dataclass(frozen=True)
class YukConflict:
    item_id: str
    kind: str
    target: str
    reason: str


@dataclass(frozen=True)
class YukInspection:
    path: Path
    manifest: dict
    items: list[YukExportItem]
    conflicts: list[YukConflict]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class YukImportResult:
    settings_applied: list[str] = field(default_factory=list)
    skills_installed: list[str] = field(default_factory=list)
    extensions_installed: list[str] = field(default_factory=list)
    avatars_installed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def discover_export_items(cwd: str | None = None, settings: dict | None = None) -> list[YukExportItem]:
    data = settings if isinstance(settings, dict) else SettingsStore().load()
    items: list[YukExportItem] = []

    for key in _PROMPT_SETTING_KEYS:
        if _is_custom_prompt_setting(data, key):
            items.append(
                YukExportItem(
                    id=f"setting:{key}",
                    section="Personality & Prompts",
                    label=_setting_label(key),
                    kind="setting",
                )
            )

    for key in _CREW_SETTING_KEYS:
        if key in data:
            items.append(
                YukExportItem(
                    id=f"setting:{key}",
                    section="Crew",
                    label=_setting_label(key),
                    kind="setting",
                )
            )

    for scope, root in _skill_roots(cwd):
        for path in sorted(root.glob("*.md")) if root.exists() else []:
            items.append(
                YukExportItem(
                    id=f"skill:{scope}:{path.name}",
                    section="Skills",
                    label=path.name,
                    scope=scope,
                    path=str(path),
                    kind="skill",
                )
            )

    for scope, root in _extension_roots(cwd):
        for source, entrypoint, kind, name in _extension_sources(root):
            enabled = not is_extension_disabled(entrypoint, _extension_state_cwd(cwd))
            summary = extension_static_summary(entrypoint, cwd)
            items.append(
                YukExportItem(
                    id=f"extension:{scope}:{name}",
                    section="Extensions",
                    label=name,
                    scope=scope,
                    path=str(source),
                    kind=kind,
                    enabled=enabled,
                    note=_extension_item_note(enabled, summary.permissions),
                )
            )

    for avatar in _avatar_items(data):
        items.append(avatar)
    return items


def export_yuk(
    path: str | Path,
    cwd: str | None = None,
    selection: YukExportSelection | None = None,
    *,
    name: str = "AICHS User Kit",
) -> dict:
    selection = selection or YukExportSelection()
    out_path = Path(path)
    if out_path.suffix.lower() != ".yuk":
        out_path = out_path.with_suffix(".yuk")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    settings = SettingsStore().load()
    items = discover_export_items(cwd, settings)
    selected = {item.id: item for item in items if selection.includes(item.id)}
    manifest = {
        "format": YUK_FORMAT,
        "name": name,
        "settings": _selected_settings(settings, selected),
        "avatar_refs": {},
        "items": [],
    }

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in selected.values():
            if item.kind == "setting":
                manifest["items"].append(_manifest_item(item))
            elif item.kind == "skill":
                archive_path = f"skills/{item.scope}/{Path(item.path).name}"
                zf.write(item.path, archive_path)
                entry = _manifest_item(item)
                entry["archive_path"] = archive_path
                entry["name"] = Path(item.path).name
                manifest["items"].append(entry)
            elif item.kind in ("extension_file", "extension_folder"):
                entry = _manifest_item(item)
                entry["name"] = Path(item.path).name
                entry["enabled"] = bool(item.enabled)
                _add_extension_disclosure(entry, Path(item.path))
                if item.kind == "extension_file":
                    archive_path = f"extensions/{item.scope}/{Path(item.path).name}"
                    zf.write(item.path, archive_path)
                    entry["archive_path"] = archive_path
                    entry["files"] = [archive_path]
                else:
                    archive_root = f"extensions/{item.scope}/{Path(item.path).name}"
                    entry["archive_path"] = archive_root
                    entry["files"] = _write_tree(zf, Path(item.path), archive_root)
                manifest["items"].append(entry)
            elif item.kind == "avatar":
                archive_path = f"avatars/{_safe_name(Path(item.path).name)}"
                zf.write(item.path, archive_path)
                entry = _manifest_item(item)
                entry["archive_path"] = archive_path
                entry["name"] = Path(item.path).name
                entry["setting_path"] = _avatar_setting_path(item.id)
                manifest["avatar_refs"][entry["setting_path"]] = item.id
                manifest["items"].append(entry)

        _rewrite_unselected_avatar_settings(manifest["settings"], selected, manifest["avatar_refs"])
        zf.writestr("yuk.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def inspect_yuk(path: str | Path, cwd: str | None = None) -> YukInspection:
    yuk_path = Path(path)
    with zipfile.ZipFile(yuk_path, "r") as zf:
        _validate_zip(zf)
        try:
            manifest = json.loads(zf.read("yuk.json").decode("utf-8"))
        except KeyError as exc:
            raise ValueError("YUK package is missing yuk.json") from exc
    warnings = _validate_manifest(manifest)
    warnings.extend(_unknown_item_warnings(manifest))
    items = [_item_from_manifest(entry) for entry in manifest.get("items", []) if isinstance(entry, dict)]
    conflicts = _conflicts(manifest, cwd)
    return YukInspection(path=yuk_path, manifest=manifest, items=items, conflicts=conflicts, warnings=warnings)


def apply_yuk(
    path: str | Path,
    cwd: str | None = None,
    choices: dict[str, str] | None = None,
) -> YukImportResult:
    inspection = inspect_yuk(path, cwd)
    choices = choices or {}
    result = YukImportResult()
    settings = SettingsStore().load()
    manifest = inspection.manifest
    installed_avatars: dict[str, str] = {}

    with zipfile.ZipFile(inspection.path, "r") as zf:
        for entry in _manifest_items(manifest, "avatar"):
            action = choices.get(entry["id"], "overwrite")
            if action == "skip":
                result.skipped.append(entry["id"])
                continue
            archive_path = str(entry.get("archive_path") or "")
            target = _avatar_target(str(entry.get("name") or Path(archive_path).name))
            _copy_zip_file(zf, archive_path, target)
            installed_avatars[entry["id"]] = str(target)
            result.avatars_installed.append(str(target))

        for entry in _manifest_items(manifest, "skill"):
            action = _choice_for(entry, choices, cwd)
            if action == "skip":
                result.skipped.append(entry["id"])
                continue
            target = _target_for_entry(entry, cwd)
            if action == "rename":
                target = _unique_path(target)
            _copy_zip_file(zf, str(entry.get("archive_path") or ""), target)
            result.skills_installed.append(str(target))

        for entry in _manifest_items(manifest, "extension_file", "extension_folder"):
            action = _choice_for(entry, choices, cwd)
            if action == "skip":
                result.skipped.append(entry["id"])
                continue
            target = _target_for_entry(entry, cwd)
            if action == "rename":
                target = _unique_path(target)
            if entry.get("kind") == "extension_folder":
                _copy_zip_tree(zf, str(entry.get("archive_path") or ""), target)
            else:
                _copy_zip_file(zf, str(entry.get("archive_path") or ""), target)
            set_extension_enabled(target, False, _extension_state_cwd(cwd))
            result.extensions_installed.append(str(target))

    package_settings = _resolved_settings(manifest, installed_avatars)
    for key, value in package_settings.items():
        item_id = f"setting:{key}"
        if choices.get(item_id, "overwrite") == "skip":
            result.skipped.append(item_id)
            continue
        settings[key] = value
        result.settings_applied.append(key)
    SettingsStore().save(settings)
    return result


def _selected_settings(settings: dict, selected: dict[str, YukExportItem]) -> dict:
    data = {}
    for key in _SETTING_KEYS:
        if f"setting:{key}" in selected and key in settings:
            if key in _PROMPT_SETTING_KEYS and not _is_custom_prompt_setting(settings, key):
                continue
            data[key] = settings[key]
    for key in _SECRET_AND_MODEL_KEYS:
        data.pop(key, None)
    return json.loads(json.dumps(data))


def _rewrite_unselected_avatar_settings(settings: dict, selected: dict[str, YukExportItem], refs: dict) -> None:
    if "avatar_human" in settings and "avatar_human" not in refs:
        settings["avatar_human"] = "human"
    if "avatar_agent" in settings and "avatar_agent" not in refs:
        settings["avatar_agent"] = "agent"
    crew = settings.get("crew")
    if isinstance(crew, dict):
        for member_id, cfg in crew.items():
            if not isinstance(cfg, dict):
                continue
            path = f"crew.{member_id}.avatar"
            if path not in refs:
                cfg["avatar"] = f"crew_{member_id}"


def _resolved_settings(manifest: dict, installed_avatars: dict[str, str]) -> dict:
    settings = json.loads(json.dumps(manifest.get("settings") if isinstance(manifest.get("settings"), dict) else {}))
    refs = manifest.get("avatar_refs") if isinstance(manifest.get("avatar_refs"), dict) else {}
    for setting_path, avatar_id in refs.items():
        installed = installed_avatars.get(str(avatar_id))
        if installed:
            _set_dotted_setting(settings, str(setting_path), installed)
    for key in _SECRET_AND_MODEL_KEYS:
        settings.pop(key, None)
    return settings


def _set_dotted_setting(settings: dict, path: str, value: str) -> None:
    if path in ("avatar_human", "avatar_agent"):
        settings[path] = value
        return
    if not path.startswith("crew.") or not path.endswith(".avatar"):
        return
    member = path.split(".")[1]
    crew = settings.setdefault("crew", {})
    if isinstance(crew, dict):
        cfg = crew.setdefault(member, {})
        if isinstance(cfg, dict):
            cfg["avatar"] = value


def _avatar_items(settings: dict) -> list[YukExportItem]:
    items = []
    for setting_path, value, fallback in _avatar_sources(settings):
        path = Path(str(value or ""))
        if path.is_file() and _is_inside(path, config.AVATARS_DIR):
            items.append(
                YukExportItem(
                    id=f"avatar:{setting_path}:{path.name}",
                    section="Avatars",
                    label=f"{setting_path} - {path.name}",
                    path=str(path),
                    kind="avatar",
                    note=f"Fallback: {fallback}",
                )
            )
    return items


def _avatar_sources(settings: dict) -> list[tuple[str, str, str]]:
    sources = [
        ("avatar_human", str(settings.get("avatar_human") or ""), "human"),
        ("avatar_agent", str(settings.get("avatar_agent") or ""), "agent"),
    ]
    crew = settings.get("crew")
    if isinstance(crew, dict):
        for member_id, cfg in sorted(crew.items()):
            if isinstance(cfg, dict):
                sources.append((f"crew.{member_id}.avatar", str(cfg.get("avatar") or ""), f"crew_{member_id}"))
    return sources


def _avatar_setting_path(item_id: str) -> str:
    parts = item_id.split(":", 2)
    return parts[1] if len(parts) >= 2 else ""


def _skill_roots(cwd: str | None) -> list[tuple[str, Path]]:
    roots = [("global", config.AICHS_HOME / "skills")]
    if cwd:
        roots.append(("project", Path(cwd) / ".aichs" / "skills"))
    return roots


def _extension_roots(cwd: str | None) -> list[tuple[str, Path]]:
    roots = [("global", config.AICHS_HOME / "extensions")]
    if cwd:
        roots.append(("project", Path(cwd) / ".aichs" / "extensions"))
    return roots


def _extension_state_cwd(cwd: str | None) -> str | None:
    return cwd or None


def _extension_sources(root: Path) -> list[tuple[Path, Path, str, str]]:
    if not root.exists():
        return []
    items: list[tuple[Path, Path, str, str]] = []
    seen: set[Path] = set()
    for entrypoint in sorted(root.glob("*/extension.py")):
        source = entrypoint.parent
        resolved = source.resolve()
        if resolved not in seen:
            items.append((source, entrypoint, "extension_folder", source.name))
            seen.add(resolved)
    for path in sorted(root.glob("*.py")):
        if path.name == "__init__.py":
            continue
        resolved = path.resolve()
        if resolved not in seen:
            items.append((path, path, "extension_file", path.name))
            seen.add(resolved)
    return items


def _manifest_item(item: YukExportItem) -> dict:
    data = {
        "id": item.id,
        "section": item.section,
        "label": item.label,
        "kind": item.kind,
    }
    if item.scope:
        data["scope"] = item.scope
    if item.note:
        data["note"] = item.note
    return data


def _add_extension_disclosure(entry: dict, source: Path) -> None:
    entrypoint = source / "extension.py" if source.is_dir() else source
    summary = extension_static_summary(entrypoint)
    entry["permissions"] = {
        key: bool(getattr(summary.permissions, key))
        for key in (
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
    }
    entry["permissions_declared"] = bool(summary.permissions.declared)
    entry["requirements"] = {
        "executables": list(summary.requirements.executables),
        "python": list(summary.requirements.python),
    }


def _item_from_manifest(entry: dict) -> YukExportItem:
    return YukExportItem(
        id=str(entry.get("id") or ""),
        section=str(entry.get("section") or ""),
        label=str(entry.get("label") or entry.get("name") or entry.get("id") or ""),
        scope=str(entry.get("scope") or ""),
        path=str(entry.get("archive_path") or ""),
        kind=str(entry.get("kind") or ""),
        enabled=entry.get("enabled") if isinstance(entry.get("enabled"), bool) else None,
        note=str(entry.get("note") or ""),
    )


def _manifest_items(manifest: dict, *kinds: str) -> list[dict]:
    return [
        entry
        for entry in manifest.get("items", [])
        if isinstance(entry, dict) and entry.get("kind") in kinds and entry.get("id")
    ]


def _conflicts(manifest: dict, cwd: str | None) -> list[YukConflict]:
    conflicts: list[YukConflict] = []
    settings = SettingsStore().load()
    for key in (manifest.get("settings") or {}):
        if key in settings:
            conflicts.append(YukConflict(f"setting:{key}", "setting", key, "setting exists"))
    for entry in _manifest_items(manifest, "skill", "extension_file", "extension_folder", "avatar"):
        target = _target_for_entry(entry, cwd) if entry.get("kind") != "avatar" else _avatar_target(str(entry.get("name") or "avatar"))
        if target.exists():
            conflicts.append(YukConflict(str(entry["id"]), str(entry["kind"]), str(target), "target exists"))
    return conflicts


def _choice_for(entry: dict, choices: dict[str, str], cwd: str | None) -> str:
    explicit = choices.get(str(entry.get("id") or ""))
    if explicit in {"overwrite", "skip", "rename"}:
        return explicit
    return "skip" if _target_for_entry(entry, cwd).exists() else "overwrite"


def _target_for_entry(entry: dict, cwd: str | None) -> Path:
    kind = str(entry.get("kind") or "")
    scope = str(entry.get("scope") or "global")
    name = _safe_name(str(entry.get("name") or Path(str(entry.get("archive_path") or "")).name))
    if kind == "skill":
        root = config.AICHS_HOME / "skills" if scope == "global" else Path(cwd or os.getcwd()) / ".aichs" / "skills"
        return root / name
    if kind in ("extension_file", "extension_folder"):
        root = config.AICHS_HOME / "extensions" if scope == "global" else Path(cwd or os.getcwd()) / ".aichs" / "extensions"
        return root / name
    raise ValueError(f"unsupported YUK item kind: {kind}")


def _avatar_target(name: str) -> Path:
    return config.AVATARS_DIR / _safe_name(name or "avatar")


def _copy_zip_file(zf: zipfile.ZipFile, archive_path: str, target: Path) -> None:
    _validate_member_name(archive_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(archive_path, "r") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)


def _copy_zip_tree(zf: zipfile.ZipFile, archive_root: str, target: Path) -> None:
    _validate_member_name(archive_root)
    prefix = archive_root.rstrip("/") + "/"
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    for info in zf.infolist():
        name = info.filename
        if name == archive_root or not name.startswith(prefix) or name.endswith("/"):
            continue
        rel = PurePosixPath(name[len(prefix):])
        if any(part in ("", ".", "..") for part in rel.parts):
            raise ValueError(f"unsafe YUK path: {name}")
        dest = target.joinpath(*rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _write_tree(zf: zipfile.ZipFile, source: Path, archive_root: str) -> list[str]:
    files = []
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        if any(part in _IGNORED_EXTENSION_NAMES for part in path.relative_to(source).parts):
            continue
        rel = path.relative_to(source).as_posix()
        archive_path = f"{archive_root}/{rel}"
        zf.write(path, archive_path)
        files.append(archive_path)
    return files


def _validate_zip(zf: zipfile.ZipFile) -> None:
    for info in zf.infolist():
        _validate_member_name(info.filename)
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) == stat.S_IFLNK:
            raise ValueError(f"YUK package contains a symlink: {info.filename}")


def _validate_member_name(name: str) -> None:
    value = str(name or "")
    if not value or value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"unsafe YUK path: {value}")
    parts = PurePosixPath(value.replace("\\", "/")).parts
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"unsafe YUK path: {value}")


def _validate_manifest(manifest: dict) -> list[str]:
    if not isinstance(manifest, dict):
        raise ValueError("Unsupported YUK package format")
    warnings: list[str] = []
    package_format = str(manifest.get("format") or "")
    if package_format != YUK_FORMAT:
        if package_format.startswith("aichs-yuk/"):
            warnings.append(
                f"Package format {package_format} differs from supported {YUK_FORMAT}; "
                "unknown fields and item types will be ignored."
            )
        elif not package_format:
            warnings.append(
                f"Package has no format marker; treating it as legacy {YUK_FORMAT}."
            )
        else:
            raise ValueError("Unsupported YUK package format")
    if not isinstance(manifest.get("items", []), list):
        manifest["items"] = []
        warnings.append("Package items were not a list; item files will be ignored.")
    if not isinstance(manifest.get("settings", {}), dict):
        manifest["settings"] = {}
        warnings.append("Package settings were not an object; settings will be ignored.")
    if not isinstance(manifest.get("avatar_refs", {}), dict):
        manifest["avatar_refs"] = {}
        warnings.append("Package avatar references were not an object; avatar links will be ignored.")
    return warnings


def _unknown_item_warnings(manifest: dict) -> list[str]:
    supported = {"setting", "skill", "extension_file", "extension_folder", "avatar"}
    warnings: list[str] = []
    for entry in manifest.get("items", []):
        if not isinstance(entry, dict):
            warnings.append("Package contains a non-object item; it will be ignored.")
            continue
        kind = str(entry.get("kind") or "")
        if kind and kind not in supported:
            label = str(entry.get("label") or entry.get("id") or kind)
            warnings.append(f"Unsupported YUK item type {kind!r} for {label}; it will be ignored.")
    return warnings


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for idx in range(2, 1000):
        candidate = parent / f"{stem}-{idx}{suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError(f"could not choose a unique path for {path}")


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "item"))
    return cleaned.strip(" ._-") or "item"


def _setting_label(key: str) -> str:
    return key.replace("_", " ").title()


def _is_custom_prompt_setting(settings: dict, key: str) -> bool:
    if key not in settings:
        return False
    value = _normalized_prompt_text(settings.get(key))
    default = _normalized_prompt_text(_PROMPT_SETTING_DEFAULTS.get(key, ""))
    if not default:
        return bool(value)
    return value != default


def _normalized_prompt_text(value) -> str:
    return str(value or "").strip()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _extension_item_note(enabled: bool, permissions) -> str:
    state = "Enabled" if enabled else "Disabled"
    if not getattr(permissions, "declared", False):
        return f"{state}; permissions undisclosed"
    names = permissions.enabled_names()
    return f"{state}; permissions: {', '.join(names) if names else 'none'}"

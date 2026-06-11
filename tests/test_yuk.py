import json
import zipfile
from pathlib import Path

import pytest

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
    DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY,
    FILE_REVIEW_PROMPT_TEMPLATE_KEY,
    GIT_FIX_PROMPT_TEMPLATE_KEY,
)
from services.tool_registry import is_extension_disabled, set_extension_enabled
from services.yuk import (
    YukExportSelection,
    apply_yuk,
    discover_export_items,
    export_yuk,
    inspect_yuk,
)
from storage.settings import SettingsStore
from tests.conftest import write_extension


def test_export_yuk_selected_items_excludes_models_and_secrets(workspace, tmp_path):
    SettingsStore().save({
        "system_prompt": "Be quietly helpful.",
        "provider_api_keys": {"openai": "secret"},
        "default_models": {"openai": "gpt-test"},
    })
    global_skills = config.AICHS_HOME / "skills"
    global_skills.mkdir(parents=True)
    (global_skills / "global.md").write_text("---\nname: global\n---\nGlobal\n", encoding="utf-8")
    project_skills = workspace / ".aichs" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / "project.md").write_text("---\nname: project\n---\nProject\n", encoding="utf-8")

    package = tmp_path / "profile.yuk"
    export_yuk(
        package,
        str(workspace),
        YukExportSelection({
            "setting:system_prompt",
            "skill:project:project.md",
        }),
    )

    with zipfile.ZipFile(package) as zf:
        manifest = json.loads(zf.read("yuk.json").decode("utf-8"))
        names = set(zf.namelist())

    assert manifest["format"] == "aichs-yuk/v1"
    assert manifest["settings"] == {"system_prompt": "Be quietly helpful."}
    assert "provider_api_keys" not in json.dumps(manifest)
    assert "default_models" not in json.dumps(manifest)
    assert "skills/project/project.md" in names
    assert "skills/global/global.md" not in names


def test_export_yuk_prompt_items_only_include_non_defaults(workspace, tmp_path):
    SettingsStore().save({
        "system_prompt": config.SYSTEM_PROMPT,
        FILE_REVIEW_PROMPT_TEMPLATE_KEY: DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
        DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY: "Please fix {mention} with tests.",
        GIT_FIX_PROMPT_TEMPLATE_KEY: "Debug git {action}: {command}.",
        COMPACT_RESUME_PROMPT_KEY: DEFAULT_COMPACT_RESUME_PROMPT,
        AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY: DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS,
        COMPACTION_SUMMARY_GUIDANCE_KEY: "",
        ARCHIVIST_PROMPT_KEY: DEFAULT_ARCHIVIST_PROMPT,
        COMMIT_MESSAGE_PROMPT_ADDITION_KEY: "",
    })

    item_ids = {item.id for item in discover_export_items(str(workspace))}
    package = tmp_path / "prompts.yuk"
    manifest = export_yuk(package, str(workspace))

    assert f"setting:{DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY}" in item_ids
    assert f"setting:{GIT_FIX_PROMPT_TEMPLATE_KEY}" in item_ids
    assert "setting:system_prompt" not in item_ids
    assert f"setting:{FILE_REVIEW_PROMPT_TEMPLATE_KEY}" not in item_ids
    assert manifest["settings"] == {
        DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY: "Please fix {mention} with tests.",
        GIT_FIX_PROMPT_TEMPLATE_KEY: "Debug git {action}: {command}.",
    }


def test_yuk_round_trips_project_extension_disabled_state(workspace, tmp_path):
    enabled = write_extension(workspace, "enabled.py", "def register(registry): pass")
    disabled = write_extension(workspace, "disabled.py", "def register(registry): pass")
    set_extension_enabled(disabled, False, str(workspace))

    package = tmp_path / "extensions.yuk"
    selected = {
        item.id
        for item in discover_export_items(str(workspace))
        if item.kind.startswith("extension_")
    }
    export_yuk(package, str(workspace), YukExportSelection(selected))

    target = tmp_path / "target"
    target.mkdir()
    result = apply_yuk(package, str(target))

    assert len(result.extensions_installed) == 2
    assert (target / ".aichs" / "extensions" / enabled.name).exists()
    assert is_extension_disabled(target / ".aichs" / "extensions" / enabled.name, str(target))
    imported_disabled = target / ".aichs" / "extensions" / disabled.name
    assert imported_disabled.exists()
    assert is_extension_disabled(imported_disabled, str(target))


def test_yuk_uses_workspace_disabled_state_for_global_extensions(workspace, tmp_path):
    global_extensions = config.AICHS_HOME / "extensions"
    global_extensions.mkdir(parents=True)
    disabled = global_extensions / "global_disabled.py"
    disabled.write_text("def register(registry): pass\n", encoding="utf-8")
    set_extension_enabled(disabled, False, str(workspace))

    items = discover_export_items(str(workspace))
    exported = next(item for item in items if item.id == "extension:global:global_disabled.py")

    assert exported.enabled is False
    assert exported.note == "Disabled; permissions undisclosed"

    package = tmp_path / "global-disabled.yuk"
    export_yuk(package, str(workspace), YukExportSelection({exported.id}))

    target = tmp_path / "target"
    target.mkdir()
    apply_yuk(package, str(target), {exported.id: "overwrite"})

    imported = config.AICHS_HOME / "extensions" / "global_disabled.py"
    assert imported.exists()
    assert is_extension_disabled(imported, str(target))


def test_inspect_yuk_rejects_zip_slip(tmp_path):
    package = tmp_path / "bad.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("yuk.json", json.dumps({"format": "aichs-yuk/v1", "items": []}))
        zf.writestr("../escape.txt", "nope")

    with pytest.raises(ValueError, match="unsafe YUK path"):
        inspect_yuk(package)


def test_inspect_yuk_rejects_invalid_manifest(tmp_path):
    package = tmp_path / "bad.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("yuk.json", json.dumps({"format": "other"}))

    with pytest.raises(ValueError, match="Unsupported YUK"):
        inspect_yuk(package)


def test_inspect_yuk_warns_for_future_format_and_unknown_item(tmp_path, workspace):
    package = tmp_path / "future.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr(
            "yuk.json",
            json.dumps({
                "format": "aichs-yuk/v2",
                "settings": {"system_prompt": "Future."},
                "items": [
                    {"id": "setting:system_prompt", "kind": "setting", "section": "Personality & Prompts", "label": "System Prompt"},
                    {"id": "workflow:demo", "kind": "workflow", "label": "Future workflow"},
                ],
                "future_field": {"ok": True},
            }),
        )

    inspection = inspect_yuk(package, str(workspace))
    result = apply_yuk(package, str(workspace), {"setting:system_prompt": "overwrite"})

    assert any("aichs-yuk/v2" in warning for warning in inspection.warnings)
    assert any("workflow" in warning for warning in inspection.warnings)
    assert SettingsStore().load()["system_prompt"] == "Future."
    assert result.settings_applied == ["system_prompt"]


def test_inspect_yuk_treats_missing_format_as_legacy(tmp_path):
    package = tmp_path / "legacy.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("yuk.json", json.dumps({"settings": {}, "items": []}))

    inspection = inspect_yuk(package)

    assert any("legacy" in warning for warning in inspection.warnings)


def test_inspect_yuk_warns_and_ignores_malformed_optional_sections(tmp_path):
    package = tmp_path / "odd.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr(
            "yuk.json",
            json.dumps({
                "format": "aichs-yuk/v1",
                "settings": [],
                "items": {},
                "avatar_refs": [],
            }),
        )

    inspection = inspect_yuk(package)

    assert inspection.manifest["settings"] == {}
    assert inspection.manifest["items"] == []
    assert len(inspection.warnings) == 3


def test_yuk_import_conflict_skip_and_rename(workspace, tmp_path):
    skills = workspace / ".aichs" / "skills"
    skills.mkdir(parents=True)
    (skills / "review.md").write_text("---\nname: review\n---\nOriginal\n", encoding="utf-8")
    package = tmp_path / "skill.yuk"
    export_yuk(package, str(workspace), YukExportSelection({"skill:project:review.md"}))

    target = tmp_path / "target"
    target_skill = target / ".aichs" / "skills" / "review.md"
    target_skill.parent.mkdir(parents=True)
    target_skill.write_text("---\nname: review\n---\nExisting\n", encoding="utf-8")

    skipped = apply_yuk(package, str(target))
    assert "skill:project:review.md" in skipped.skipped
    assert "Existing" in target_skill.read_text(encoding="utf-8")

    renamed = apply_yuk(package, str(target), {"skill:project:review.md": "rename"})
    assert any(path.endswith("review-2.md") for path in renamed.skills_installed)
    assert (target / ".aichs" / "skills" / "review-2.md").exists()


def test_yuk_avatar_round_trip_rewrites_imported_setting(workspace, tmp_path, monkeypatch):
    avatar_dir = config.AVATARS_DIR
    avatar_dir.mkdir(parents=True)
    avatar = avatar_dir / "human.png"
    avatar.write_bytes(b"fake image")
    SettingsStore().save({"avatar_human": str(avatar)})

    package = tmp_path / "avatars.yuk"
    export_yuk(package, str(workspace))

    manifest = inspect_yuk(package, str(workspace)).manifest
    assert manifest["avatar_refs"]["avatar_human"].startswith("avatar:avatar_human:")

    imported_avatars = tmp_path / "imported_avatars"
    monkeypatch.setattr(config, "AVATARS_DIR", imported_avatars)
    result = apply_yuk(package, str(workspace), {"setting:avatar_human": "overwrite"})

    imported = imported_avatars / "human.png"
    assert str(imported) in result.avatars_installed
    assert SettingsStore().load()["avatar_human"] == str(imported)
    assert imported.read_bytes() == b"fake image"


def test_yuk_folder_extension_overwrite_replaces_existing_tree(workspace, tmp_path):
    source = workspace / ".aichs" / "extensions" / "folder-demo"
    source.mkdir(parents=True)
    (source / "extension.py").write_text("def register(registry): pass\n", encoding="utf-8")
    (source / "notes.txt").write_text("new\n", encoding="utf-8")
    package = tmp_path / "folder.yuk"
    export_yuk(
        package,
        str(workspace),
        YukExportSelection({"extension:project:folder-demo"}),
    )

    target = tmp_path / "target"
    old = target / ".aichs" / "extensions" / "folder-demo"
    old.mkdir(parents=True)
    (old / "old.txt").write_text("old\n", encoding="utf-8")

    result = apply_yuk(package, str(target), {"extension:project:folder-demo": "overwrite"})

    assert str(old) in result.extensions_installed
    assert not (old / "old.txt").exists()
    assert (old / "extension.py").exists()
    assert (old / "notes.txt").read_text(encoding="utf-8") == "new\n"
    assert is_extension_disabled(old, str(target))


def test_yuk_exports_extension_permissions(workspace, tmp_path):
    source = workspace / ".aichs" / "extensions" / "perms"
    source.mkdir(parents=True)
    (source / "aichs-extension.json").write_text(
        json.dumps({"permissions": {"tools": True, "network": True}}),
        encoding="utf-8",
    )
    (source / "extension.py").write_text("def register(registry): pass\n", encoding="utf-8")

    package = tmp_path / "perms.yuk"
    manifest = export_yuk(
        package,
        str(workspace),
        YukExportSelection({"extension:project:perms"}),
    )
    entry = next(item for item in manifest["items"] if item["id"] == "extension:project:perms")

    assert entry["permissions_declared"] is True
    assert entry["permissions"]["tools"] is True
    assert entry["permissions"]["network"] is True
    assert entry["permissions"]["hooks"] is False


def test_yuk_import_strips_secret_and_model_settings(tmp_path, workspace):
    package = tmp_path / "malicious.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr(
            "yuk.json",
            json.dumps({
                "format": "aichs-yuk/v1",
                "settings": {
                    "system_prompt": "Imported.",
                    "provider_api_keys": {"openai": "leak"},
                    "default_models": {"openai": "gpt-test"},
                },
                "items": [
                    {"id": "setting:system_prompt", "kind": "setting", "section": "Personality & Prompts", "label": "System Prompt"},
                    {"id": "setting:provider_api_keys", "kind": "setting", "section": "Personality & Prompts", "label": "Keys"},
                ],
            }),
        )

    apply_yuk(package, str(workspace), {"setting:system_prompt": "overwrite"})
    saved = SettingsStore().load()

    assert saved["system_prompt"] == "Imported."
    assert "provider_api_keys" not in saved
    assert "default_models" not in saved


def test_inspect_yuk_reports_skill_conflict(workspace, tmp_path):
    skills = workspace / ".aichs" / "skills"
    skills.mkdir(parents=True)
    (skills / "review.md").write_text("---\nname: review\n---\nReview\n", encoding="utf-8")
    package = tmp_path / "skill.yuk"
    export_yuk(package, str(workspace), YukExportSelection({"skill:project:review.md"}))

    inspection = inspect_yuk(package, str(workspace))

    assert any(conflict.item_id == "skill:project:review.md" for conflict in inspection.conflicts)


def test_inspect_yuk_rejects_absolute_zip_path(tmp_path):
    package = tmp_path / "absolute.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("yuk.json", json.dumps({"format": "aichs-yuk/v1", "items": []}))
        zf.writestr("/absolute.txt", "nope")

    with pytest.raises(ValueError, match="unsafe YUK path"):
        inspect_yuk(package)

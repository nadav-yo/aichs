import os

import pytest

from storage.settings import (
    DEFAULT_ARCHIVIST_PROMPT,
    DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS,
    DEFAULT_COMPACT_RESUME_PROMPT,
    DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE,
    DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
    SettingsStore,
    archivist_prompt,
    auto_title_prompt_instructions,
    compact_resume_prompt,
    compaction_summary_guidance,
    diagnostic_fix_prompt_template,
    file_editor_tab_spaces,
    file_review_prompt_template,
)


@pytest.fixture
def settings_store(isolate_aichs_home):
    return SettingsStore()


def test_load_save_roundtrip(settings_store, isolate_aichs_home):
    settings_store.save({"theme": "light", "font_size": "large"})
    data = settings_store.load()
    assert data["theme"] == "light"
    assert data["font_size"] == "large"


def test_load_cache_returns_copy_and_observes_file_changes(settings_store):
    from storage.settings import SETTINGS_PATH

    settings_store.save({"theme": "light"})
    first = settings_store.load()
    first["theme"] = "mutated"

    assert settings_store.load()["theme"] == "light"

    SETTINGS_PATH.write_text('{"theme": "modern", "font_size": "medium"}', encoding="utf-8")

    assert settings_store.load()["theme"] == "modern"
    assert settings_store.load()["font_size"] == "medium"


def test_update_merges(settings_store):
    settings_store.save({"theme": "dark"})
    merged = settings_store.update({"font_size": "medium"})
    assert merged["theme"] == "dark"
    assert merged["font_size"] == "medium"


def test_apply_sets_env_when_missing(settings_store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings_store.save({"provider_api_keys": {"claude": "test-key-123"}})
    settings_store.apply()
    assert os.environ.get("ANTHROPIC_API_KEY") == "test-key-123"


def test_apply_saved_overwrites_env(settings_store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "old")
    settings_store.apply_saved({"provider_api_keys": {"openai": "new-key"}})
    assert os.environ["OPENAI_API_KEY"] == "new-key"


def test_load_invalid_json_returns_empty(settings_store, isolate_aichs_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text("{not json", encoding="utf-8")
    assert settings_store.load() == {}


def test_file_editor_tab_spaces_defaults_and_clamps():
    assert file_editor_tab_spaces({}) == 4
    assert file_editor_tab_spaces({"file_editor_tab_spaces": "2"}) == 2
    assert file_editor_tab_spaces({"file_editor_tab_spaces": 0}) == 1
    assert file_editor_tab_spaces({"file_editor_tab_spaces": 99}) == 12
    assert file_editor_tab_spaces({"file_editor_tab_spaces": "bad"}) == 4


def test_file_prompt_templates_default_and_strip():
    assert file_review_prompt_template({}) == DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE
    assert diagnostic_fix_prompt_template({}) == DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE
    assert auto_title_prompt_instructions({}) == DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS
    assert compact_resume_prompt({}) == DEFAULT_COMPACT_RESUME_PROMPT
    assert compaction_summary_guidance({}) == ""
    assert archivist_prompt({}) == DEFAULT_ARCHIVIST_PROMPT
    assert (
        file_review_prompt_template({"file_review_prompt_template": "  Review {path}  "})
        == "Review {path}"
    )
    assert (
        diagnostic_fix_prompt_template({
            "diagnostic_fix_prompt_template": "  Fix {mention}  ",
        })
        == "Fix {mention}"
    )
    assert (
        auto_title_prompt_instructions({
            "auto_title_prompt_instructions": "  Title briefly  ",
        })
        == "Title briefly"
    )
    assert compact_resume_prompt({"compact_resume_prompt": "  Resume now  "}) == "Resume now"
    assert (
        compaction_summary_guidance({"compaction_summary_guidance": "  Keep tests  "})
        == "Keep tests"
    )
    assert archivist_prompt({"archivist_prompt": "  Search memory  "}) == "Search memory"

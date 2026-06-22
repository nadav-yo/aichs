import os

import pytest

from storage.settings import (
    DEFAULT_ARCHIVIST_PROMPT,
    DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS,
    DEFAULT_CANVAS_ACTION_AUTO_APPROVE,
    DEFAULT_CANVAS_PARALLEL_LIMIT,
    DEFAULT_CANVAS_RUN_MODE,
    DEFAULT_COMPACT_RESUME_PROMPT,
    DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE,
    DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
    DEFAULT_GRAPH_AGENT_PROMPT,
    DEFAULT_GRAPH_GENERATION_STRATEGY,
    DEFAULT_GIT_FIX_PROMPT_TEMPLATE,
    SettingsStore,
    archivist_prompt,
    auto_title_prompt_instructions,
    canvas_action_auto_approve,
    canvas_parallel_limit,
    canvas_run_mode,
    compact_resume_prompt,
    compaction_summary_guidance,
    diagnostic_fix_prompt_template,
    file_editor_tab_spaces,
    file_review_prompt_template,
    git_fix_prompt_template,
    graph_agent_prompt,
    graph_generation_strategy,
    resume_session,
    DEFAULT_RESUME_SESSION,
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


def test_apply_saved_blank_key_does_not_clear_external_env(settings_store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "external")
    settings_store.apply_saved({"provider_api_keys": {"openai": ""}, "openai_api_key": ""})
    assert os.environ["OPENAI_API_KEY"] == "external"


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
    assert git_fix_prompt_template({}) == DEFAULT_GIT_FIX_PROMPT_TEMPLATE
    assert auto_title_prompt_instructions({}) == DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS
    assert compact_resume_prompt({}) == DEFAULT_COMPACT_RESUME_PROMPT
    assert compaction_summary_guidance({}) == ""
    assert archivist_prompt({}) == DEFAULT_ARCHIVIST_PROMPT
    assert graph_agent_prompt({}) == DEFAULT_GRAPH_AGENT_PROMPT
    assert "Do not perform that research, implementation, review, or verification yourself" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "Use web_fetch only for graph-planning research" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "mega-feature work" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "If the goal can be summarized as one straightforward chat prompt" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "usually 3-5 new nodes total" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "Do not overcomplicate the graph" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "Branch only when it clarifies real parallel work" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "Break down by responsibility, not by generic phases" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "Ask concise questions about design details" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "multiple question turns" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "Do not ask the user to choose implementation details" in DEFAULT_GRAPH_AGENT_PROMPT
    assert "Reuse and connect existing nodes" in DEFAULT_GRAPH_AGENT_PROMPT
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
        git_fix_prompt_template({
            "git_fix_prompt_template": "  Debug git {action} in {repo}  ",
        })
        == "Debug git {action} in {repo}"
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
    assert graph_agent_prompt({"graph_agent_prompt": "  Build graphs  "}) == "Build graphs"


def test_canvas_settings_defaults_and_validate():
    assert graph_generation_strategy({}) == DEFAULT_GRAPH_GENERATION_STRATEGY
    assert graph_generation_strategy({"graph_generation_strategy": "atomicity"}) == "atomicity"
    assert graph_generation_strategy({"graph_generation_strategy": "bogus"}) == DEFAULT_GRAPH_GENERATION_STRATEGY
    assert canvas_run_mode({}) == DEFAULT_CANVAS_RUN_MODE
    assert canvas_run_mode({"canvas_run_mode": "parallel"}) == "parallel"
    assert canvas_run_mode({"canvas_run_mode": "bogus"}) == DEFAULT_CANVAS_RUN_MODE
    assert canvas_parallel_limit({}) == DEFAULT_CANVAS_PARALLEL_LIMIT
    assert canvas_parallel_limit({"canvas_parallel_limit": 0}) == 1
    assert canvas_parallel_limit({"canvas_parallel_limit": 99}) == 6
    assert canvas_parallel_limit({"canvas_parallel_limit": "bad"}) == DEFAULT_CANVAS_PARALLEL_LIMIT
    assert canvas_action_auto_approve({}) == DEFAULT_CANVAS_ACTION_AUTO_APPROVE
    assert canvas_action_auto_approve({"canvas_action_auto_approve": "coder"}) == "coder"
    assert canvas_action_auto_approve({"canvas_action_auto_approve": "all"}) == "all"
    assert canvas_action_auto_approve({"canvas_action_auto_approve": "bogus"}) == DEFAULT_CANVAS_ACTION_AUTO_APPROVE


def test_resume_session_defaults_and_validates():
    assert resume_session({}) == DEFAULT_RESUME_SESSION
    assert resume_session({"resume_session": "ask"}) == "ask"
    assert resume_session({"resume_session": "never"}) == "never"
    assert resume_session({"resume_session": "bogus"}) == DEFAULT_RESUME_SESSION


def test_git_panel_settings_helpers():
    from storage.settings import (
        git_panel_body_expanded,
        git_panel_lists_split,
        git_panel_mode,
    )

    assert git_panel_mode({}) == "changes"
    assert git_panel_mode({"git_panel_mode": "history"}) == "history"
    assert git_panel_mode({"git_panel_mode": "invalid"}) == "changes"
    assert git_panel_lists_split({}) == [120, 220]
    assert git_panel_lists_split({"git_panel_lists_split": [80, 160]}) == [80, 160]
    assert git_panel_body_expanded({"git_panel_body_expanded": True}) is True

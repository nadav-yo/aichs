from ui.widgets.chat_panel import (
    _crew_model_choice,
    _enabled_crew,
    _crew_for_history_message,
    _crew_notice_text,
    _first_summoned_crew,
    _latest_regenerable_assistant_index,
    _tool_call_notice,
)


def test_chat_panel_crew_helpers():
    scout = _first_summoned_crew("@Scout check this")
    assert scout is not None
    assert scout.id == "scout"
    assert _crew_notice_text(scout, "joined") == "Scout joined the thread."
    assert _crew_notice_text({"id": "critic"}, "left") == "Critic left the thread."
    assert _first_summoned_crew(
        "@Scout check this",
        {"crew": {"scout": {"enabled": False}}},
    ) is None
    enabled = _enabled_crew({"crew": {"scout": {"enabled": False}}})
    assert "scout" not in {member.id for member in enabled}
    assert "critic" in {member.id for member in enabled}


def test_crew_for_history_message():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok", "crew": {"id": "scout"}},
    ]
    assert _crew_for_history_message(history, 1).name == "Scout"
    assert _crew_for_history_message(history, 0) is None
    assert _crew_for_history_message(history, 99) is None


def test_latest_regenerable_assistant_index_is_last_assistant():
    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "old"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "crew", "crew": {"id": "scout"}},
        {"role": "assistant", "content": "latest"},
    ]
    assert _latest_regenerable_assistant_index(history) == 4
    assert _latest_regenerable_assistant_index(history[:3]) == -1
    assert _latest_regenerable_assistant_index(history[:2]) == 1
    assert _latest_regenerable_assistant_index(history[:1]) == -1


def test_crew_model_choice_uses_saved_configured_model():
    scout = _first_summoned_crew("@Scout")
    choice = _crew_model_choice(
        scout,
        "claude-sonnet-4-6",
        {"scout": "gpt-5.4-nano"},
        {"openai"},
    )
    assert choice == "gpt-5.4-nano"


def test_crew_model_choice_falls_back_for_unconfigured_model():
    scout = _first_summoned_crew("@Scout")
    choice = _crew_model_choice(
        scout,
        "claude-sonnet-4-6",
        {"scout": "gpt-5.4-nano"},
        {"claude"},
    )
    assert choice == "claude-sonnet-4-6"


def test_tool_call_notice_includes_extension_destination(tmp_path):
    cwd = str(tmp_path)
    assert (
        _tool_call_notice("web_fetch", {"url": "https://example.com/docs"}, cwd)
        == "Fetching web page 'https://example.com/docs'"
    )
    assert (
        _tool_call_notice("search_web", {"query": "release notes"}, cwd)
        == "Using tool 'search_web' with query 'release notes'"
    )

from services.crew import (
    ASK_CREW_TOOL_NAME,
    all_crew,
    ask_crew_tool_anthropic,
    ask_crew_tool_openai,
    crew_enabled,
    crew_metadata,
    crew_prompt,
    crew_settings,
    crew_name_from_metadata,
    crew_roster_prompt,
    crew_system_prompt,
    summoned_members,
)


def test_summoned_members_by_at_name():
    members = summoned_members("@Scout check this, @Architect plan it, and @Archivist summarize it")
    assert [m.id for m in members] == ["scout", "architect", "archivist"]


def test_summoned_members_deduplicates():
    members = summoned_members("@Scout @scout")
    assert [m.id for m in members] == ["scout"]


def test_crew_metadata_and_prompt():
    scout = all_crew()[0]
    assert [member.id for member in all_crew()] == ["scout", "archivist", "architect"]
    assert crew_name_from_metadata(scout.metadata()) == "Scout"
    assert crew_name_from_metadata({"id": "archivist"}) == "Archivist"
    archivist = all_crew()[1]
    architect = all_crew()[2]
    assert "search_project_chats" in archivist.tools
    assert "list_files" in scout.tools
    assert "list_files" in archivist.tools
    assert "list_files" in architect.tools
    roster = crew_roster_prompt()
    assert "@Scout" in roster
    assert "@Archivist" in roster
    assert "@Architect" in roster
    assert "@Critic" not in roster
    assert "focused second opinion" in roster
    assert "Usually call 0-2 members" in roster
    assert "Crew Role" in crew_system_prompt(scout, "base")


def test_ask_crew_tool_schema():
    anthropic = ask_crew_tool_anthropic()
    assert anthropic["name"] == ASK_CREW_TOOL_NAME
    assert "member" in anthropic["input_schema"]["properties"]

    openai = ask_crew_tool_openai()
    assert openai["function"]["name"] == ASK_CREW_TOOL_NAME


def test_crew_settings_defaults_and_overrides():
    scout = all_crew()[0]
    defaults = crew_settings({}, scout)
    assert defaults["enabled"] is True
    assert defaults["prompt"] == ""
    assert defaults["avatar"] == "crew_scout"

    settings = {
        "crew": {
            "scout": {
                "enabled": False,
                "prompt": "Be curious.",
                "model": "gpt-5.4-nano",
                "color": "7dd3fc",
                "avatar": "agent",
            }
        }
    }
    cfg = crew_settings(settings, scout)
    assert cfg["enabled"] is False
    assert cfg["color"] == "#7dd3fc"
    assert crew_enabled(settings, scout) is False
    assert crew_prompt(scout, settings) == "Be curious."
    meta = crew_metadata(scout, settings)
    assert meta["avatar"] == "agent"
    assert meta["model"] == "gpt-5.4-nano"

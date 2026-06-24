from types import SimpleNamespace

from services.skills import Skill
from services.slash_commands import SlashCommand
from ui.widgets.chat_panel import ChatPanel, _should_complete_slash_selection
from ui.widgets.skill_picker import SkillPicker


def test_skill_picker_current_command(qapp):
    picker = SkillPicker(
        skills=[],
        commands=[SlashCommand("continue", "Continue runtime")],
    )
    picker.filter("/conti")

    current = picker.current()

    assert "QListWidget::item:selected:focus" in picker._list.styleSheet()
    assert current is not None
    kind, data = current
    assert kind == "command"
    assert data.name == "continue"


def test_skill_picker_current_skill(qapp):
    picker = SkillPicker(
        skills=[Skill("review", "Review code", "Review")],
        commands=[],
    )
    picker.filter("/rev")

    current = picker.current()

    assert current is not None
    kind, data = current
    assert kind == "skill"
    assert data.name == "review"


def test_skill_picker_terminal_hint(qapp):
    picker = SkillPicker(skills=[], commands=[], include_terminal=True)
    picker.filter("!")

    current = picker.current()

    assert current is not None
    kind, data = current
    assert kind == "terminal"
    assert data is None


def test_should_complete_slash_selection_only_for_partial_tokens():
    assert _should_complete_slash_selection("/conti", "continue")
    assert not _should_complete_slash_selection("/continue", "continue")
    assert not _should_complete_slash_selection("/continue status", "continue")
    assert not _should_complete_slash_selection("hello", "continue")


def test_mcp_command_selection_completes_instead_of_activating_mode():
    completed = []
    hidden = []
    focused = []
    command = SlashCommand(
        "mcp__unreal_mcp__describe_toolset",
        "Describe a toolset",
        source="mcp",
    )
    panel = SimpleNamespace(
        composer=SimpleNamespace(
            text=lambda: "/mcp__unreal_mcp__describe_toolset",
            input=SimpleNamespace(
                complete_slash_command=completed.append,
                exit_slash_mode=lambda: None,
            ),
            focus_input=lambda: focused.append(True),
        ),
        _skill_picker=SimpleNamespace(hide=lambda: hidden.append(True)),
        _activate_extension_command=lambda _command: (_ for _ in ()).throw(
            AssertionError("MCP commands should not become mode chips on selection")
        ),
    )
    panel._complete_slash_item = lambda item: ChatPanel._complete_slash_item(panel, item)

    ChatPanel._on_command_selected(panel, command)

    assert completed == ["mcp__unreal_mcp__describe_toolset"]
    assert hidden == [True]
    assert focused == [True]

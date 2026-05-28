from services.skills import Skill
from services.slash_commands import SlashCommand
from ui.widgets.chat_panel import _should_complete_slash_selection
from ui.widgets.skill_picker import SkillPicker


def test_skill_picker_current_command(qapp):
    picker = SkillPicker(
        skills=[],
        commands=[SlashCommand("continue", "Continue runtime")],
    )
    picker.filter("/conti")

    current = picker.current()

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

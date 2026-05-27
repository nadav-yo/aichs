from services.tool_registry import ExtensionFileSummary, ExtensionOverview
from ui.widgets.extensions_dialog import (
    _status_tone,
    _summary_text,
    _tab_text_color,
    _tab_title,
    _tab_tooltip,
)


def _summary(path="ext.py", status="Loaded", errors=None, description=""):
    return ExtensionFileSummary(
        path=path,
        status=status,
        tools=[],
        commands=[],
        contexts=[],
        hooks=[],
        badges=[],
        panels=[],
        errors=list(errors or []),
        description=description,
    )


def test_extensions_summary_includes_disabled_count():
    overview = ExtensionOverview(files=[
        _summary("loaded.py"),
        _summary("disabled.py", status="Disabled"),
    ])

    assert _summary_text(overview) == "2 extension files · no errors · 1 disabled"


def test_extensions_dialog_status_helpers():
    assert _status_tone(_summary()) == "success"
    assert _status_tone(_summary(status="Disabled")) == "disabled"
    assert _status_tone(_summary(status="Failed", errors=["boom"])) == "danger"
    assert _tab_title(_summary("disabled.py", status="Disabled")) == "Disabled · disabled.py"
    assert "Disabled" in _tab_tooltip(_summary(status="Disabled"))
    assert _tab_text_color(_summary(status="Failed", errors=["boom"])) == "#f87171"


def test_extensions_dialog_tab_tooltip_includes_description():
    tooltip = _tab_tooltip(_summary(description="Adds runtime guardrails."))

    assert "Loaded." in tooltip
    assert "Adds runtime guardrails." in tooltip

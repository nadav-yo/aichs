from PyQt6.QtWidgets import QLabel, QPushButton

from services.extension_installer import ExtensionInstallCandidate
from services.tool_registry import ExtensionFileSummary, ExtensionOverview, LanguageContribution
from ui.widgets.extensions_dialog import (
    ExtensionInstallDialog,
    ExtensionsDialog,
    _ExtensionDetailPane,
    _list_subtitle,
    _status_tone,
    _summary_text,
)


def _summary(
    path="ext.py",
    status="Loaded",
    errors=None,
    description="",
    display_name="",
    languages=None,
):
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
        display_name=display_name,
        languages=list(languages or []),
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


def test_extensions_dialog_uses_docs_for_api_reference(qapp):
    overview = ExtensionOverview(files=[
        _summary("runtime.py", description="Runtime controls"),
        _summary("guard.py", description="Guardrails"),
    ])
    dialog = ExtensionsDialog(overview)

    assert dialog._selected_path == "runtime.py"
    assert isinstance(dialog._detail_scroll.widget(), _ExtensionDetailPane)
    assert "API Reference" not in [button.text() for button in dialog.findChildren(QPushButton)]

    dialog._show_file_detail(overview.files[1])

    assert dialog._selected_path == "guard.py"
    assert isinstance(dialog._detail_scroll.widget(), _ExtensionDetailPane)


def test_extensions_dialog_uses_manifest_display_name(qapp, tmp_path):
    entrypoint = tmp_path / ".aichs" / "extensions" / "python-lang" / "extension.py"
    overview = ExtensionOverview(files=[
        _summary(str(entrypoint), display_name="Python Language Support"),
    ])

    dialog = ExtensionsDialog(overview)

    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Python Language Support" in labels
    assert "extension.py" not in labels


def test_extensions_dialog_list_subtitle_summarizes_contributions():
    file = _summary("runtime.py", description="Runtime controls")

    assert _list_subtitle(file) == "No registered contributions · Runtime controls"

    language_file = _summary(
        "language.py",
        languages=[LanguageContribution(name="python", file_patterns=["*.py"])],
    )
    assert _list_subtitle(language_file) == "1 language"


def test_extension_install_dialog_selects_candidates(qapp, tmp_path):
    dialog = ExtensionInstallDialog(str(tmp_path))
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "python-lang",
        entrypoint=tmp_path / "python-lang" / "extension.py",
        kind="folder",
        description="Python support",
    )

    dialog._set_candidates([candidate])

    assert dialog.scope_combo.currentData() == "local"
    assert [item.name for item in dialog.selected_candidates()] == ["python-lang"]
    checkbox, _candidate = dialog._candidate_checks[0]
    checkbox.setChecked(False)
    assert dialog.selected_candidates() == []
    assert not dialog.install_btn.isEnabled()

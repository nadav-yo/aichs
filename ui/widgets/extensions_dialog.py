from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from services.tool_registry import ExtensionFileSummary, ExtensionOverview
from ui.theme import palette, chat_font_pt, meta_font_pt, apply_flat_tab_style


class ExtensionsDialog(QDialog):
    def __init__(self, overview: ExtensionOverview, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Extensions")
        self.resize(720, 620)

        p = palette()
        self.setStyleSheet(
            f"QDialog {{ background:{p['BG2']}; color:{p['TEXT']}; }}"
            f"QScrollArea {{ background:{p['BG2']}; border:none; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Extensions")
        title.setStyleSheet(
            f"font-size:{chat_font_pt() + 2}px; font-weight:600; color:{p['TEXT']};"
        )
        root.addWidget(title)

        summary = QLabel(_summary_text(overview))
        summary.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;")
        root.addWidget(summary)

        tabs = QTabWidget()
        apply_flat_tab_style(tabs, "extensionsTabs")
        root.addWidget(tabs, 1)

        if overview.files:
            for file in overview.files:
                tabs.addTab(_ExtensionFileTab(file), _tab_title(file))
        else:
            tabs.addTab(_EmptyTab(), "No extensions")
        tabs.addTab(_ApiReferenceTab(), "API Reference")


class _ExtensionFileTab(QWidget):
    def __init__(self, file: ExtensionFileSummary, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self._layout = QVBoxLayout(body)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._render(file)
        self._layout.addStretch()

    def _render(self, file: ExtensionFileSummary) -> None:
        self._add_status(file)
        self._add_tools(file)
        self._add_commands(file)
        self._add_context(file)
        self._add_hooks(file)
        self._add_ui(file)
        if file.errors:
            self._add_section("Errors", [(error, "") for error in file.errors], tone="danger")

    def _add_status(self, file: ExtensionFileSummary) -> None:
        status = QLabel(file.status)
        status.setStyleSheet(_status_label_style("danger" if file.errors else "success"))
        self._layout.addWidget(status, 0, Qt.AlignmentFlag.AlignLeft)

    def _add_tools(self, file: ExtensionFileSummary) -> None:
        rows = [
            (
                tool.name,
                _join_details(
                    tool.description,
                    "parallel safe" if tool.parallel_safe else "",
                    f"approval: {tool.approval}" if tool.approval else "",
                ),
            )
            for tool in file.tools
        ]
        self._add_section("Tools", rows)

    def _add_commands(self, file: ExtensionFileSummary) -> None:
        rows = [
            (
                f"/{command.name}",
                _join_details(
                    command.description,
                    f"tools: {', '.join(command.tools)}" if command.tools else "tools: all",
                ),
            )
            for command in file.commands
        ]
        self._add_section("Slash Commands", rows)

    def _add_context(self, file: ExtensionFileSummary) -> None:
        rows = [(name, "Injected into workspace context") for name in file.contexts]
        self._add_section("Context", rows)

    def _add_hooks(self, file: ExtensionFileSummary) -> None:
        rows = [(name, "Lifecycle hook") for name in file.hooks]
        self._add_section("Hooks", rows)

    def _add_ui(self, file: ExtensionFileSummary) -> None:
        rows = [(badge.name, "Status badge") for badge in file.badges]
        rows += [(panel.name, f"Panel: {panel.title}") for panel in file.panels]
        self._add_section("UI Contributions", rows)

    def _add_section(
        self,
        heading: str,
        rows: list[tuple[str, str]],
        *,
        tone: str = "",
        heading_tone: str = "",
    ) -> None:
        if not rows:
            return
        label = QLabel(heading)
        label.setStyleSheet(_heading_style(heading_tone or tone))
        self._layout.addWidget(label)
        for title, subtitle in rows:
            self._add_row(title, subtitle, tone=tone)

    def _add_row(self, title: str, subtitle: str, *, tone: str = "") -> None:
        p = palette()
        card = _make_card()
        border = "#5f252d" if tone == "danger" else p["BORDER"]
        card.setStyleSheet(
            f"QFrame#extensionCard {{ background-color:{p['BG3']};"
            f"border:1px solid {border}; border-radius:8px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_label.setStyleSheet(f"color:{p['TEXT']}; font-weight:600;")
        layout.addWidget(title_label)

        if subtitle:
            sub_label = QLabel(subtitle)
            sub_label.setWordWrap(True)
            sub_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            sub_label.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;")
            layout.addWidget(sub_label)

        self._layout.addWidget(card)


class _EmptyTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        p = palette()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        label = QLabel("No extension files found.")
        label.setStyleSheet(f"color:{p['TEXT_DIM']};")
        layout.addWidget(label)
        layout.addStretch()


class _ApiReferenceTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        intro = QLabel(
            "Extensions return structured data. aichs owns the widgets, layout, "
            "and styling, so extension UI stays predictable."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(_intro_style())
        layout.addWidget(intro)

        _add_api_section(layout, "What UI Extensions Can Do", [
            (
                "status_badge",
                "Adds a small top-bar badge. The badge can open a registered panel.",
            ),
            (
                "panel",
                "Adds a structured read-only dialog rendered by aichs.",
            ),
            (
                "Extensions view",
                "Shows loaded extension files and their registered contributions.",
            ),
        ])
        _add_api_section(layout, "Provider Context", [
            ("ctx.cwd", "Current workspace path."),
            ("ctx.model", "Currently selected model id."),
            ("ctx.history", "Current conversation history visible to the chat panel."),
        ])
        _add_api_section(layout, "Status Badge Schema", [
            (
                "registry.status_badge(name, provider)",
                "Registers a top-bar badge provider.",
            ),
            ("label", "Required button text."),
            ("tooltip", "Optional hover text."),
            (
                "tone",
                "Optional: success, danger, warning, accent.",
            ),
            ("panel", "Optional panel name to open. Defaults to the badge name."),
            ("visible", "Set to False to hide the badge."),
        ])
        _add_api_section(layout, "Panel Provider Schema", [
            ("registry.panel(name, title, provider)", "Registers a structured panel."),
            ("title", "Optional panel heading."),
            ("body", "Optional text before sections."),
            ("sections", "Optional list of section objects or strings."),
        ])
        _add_api_section(layout, "Section Schema", [
            ("heading", "Optional section heading."),
            ("body", "Optional text before section items."),
            ("items", "Optional list of item objects or strings."),
        ])
        _add_api_section(layout, "Item Schema", [
            ("title", "Primary row text. Defaults to Item."),
            ("subtitle", "Optional secondary text."),
            ("body", "Optional detail text."),
            ("action", "Optional single action object."),
            ("actions", "Optional list of action objects."),
        ])
        _add_api_section(layout, "Action Schema", [
            ("label", "Button text. Defaults to the action type."),
            ("type", "Supported: open_file, copy, refresh_panel, send_message."),
            ("path", "For open_file: workspace-relative path."),
            ("text", "For copy or send_message."),
            ("refresh_panel", "Re-runs the provider and redraws the current panel."),
            ("send_message", "Sends or queues text like a normal user message."),
        ])
        _add_api_section(layout, "String Shortcuts", [
            ("panel returns a string", "Rendered as body text."),
            ("section is a string", "Rendered as body text."),
            ("item is a string", "Rendered as a single card title."),
        ])
        _add_api_section(layout, "Currently Unsupported", [
            ("tool-running buttons", ""),
            ("file links inside text", ""),
            ("custom row colors or icons", ""),
            ("arbitrary PyQt widgets", ""),
            ("HTML or Markdown rendering", ""),
        ])
        layout.addStretch()


def _add_api_section(layout: QVBoxLayout, heading: str, rows: list[tuple[str, str]]) -> None:
    label = QLabel(heading)
    label.setStyleSheet(_heading_style())
    layout.addWidget(label)

    p = palette()
    card = _make_card()
    card.setStyleSheet(
        f"QFrame#extensionCard {{ background-color:{p['BG3']};"
        f"border:1px solid {p['BORDER']}; border-radius:8px; }}"
    )
    grid = QGridLayout(card)
    grid.setContentsMargins(10, 8, 10, 8)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(6)

    for row, (name, description) in enumerate(rows):
        name_label = QLabel(name)
        name_label.setWordWrap(True)
        name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        name_label.setStyleSheet(f"color:{p['TEXT']}; font-weight:600;")
        grid.addWidget(name_label, row, 0, Qt.AlignmentFlag.AlignTop)

        desc_label = QLabel(description or "-")
        desc_label.setWordWrap(True)
        desc_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        desc_label.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;")
        grid.addWidget(desc_label, row, 1)

    layout.addWidget(card)


def _summary_text(overview: ExtensionOverview) -> str:
    count = len(overview.files)
    noun = "file" if count == 1 else "files"
    errors = overview.error_count
    if errors:
        return f"{count} extension {noun} · {errors} error(s)"
    return f"{count} extension {noun} · no errors"


def _make_card() -> QFrame:
    card = QFrame()
    card.setObjectName("extensionCard")
    return card


def _tab_title(file: ExtensionFileSummary) -> str:
    name = Path(file.path).name
    return f"! {name}" if file.errors else name


def _join_details(*parts: str) -> str:
    return " | ".join(part for part in parts if part)


def _heading_style(tone: str = "") -> str:
    p = palette()
    color = "#f87171" if tone == "danger" else p["TEXT_DIM"]
    return (
        f"color:{color}; font-size:{meta_font_pt()}px;"
        "font-weight:600;"
    )


def _intro_style() -> str:
    p = palette()
    return (
        f"color:{p['TEXT']}; background-color:{p['BG3']};"
        f"border:1px solid {p['BORDER']}; border-radius:8px;"
        "padding:10px;"
    )


def _status_label_style(tone: str) -> str:
    p = palette()
    if tone == "danger":
        bg, fg, border = "#35191d", "#f87171", "#5f252d"
    else:
        bg, fg, border = p["SUCCESS_BG"], p["SUCCESS"], p["SUCCESS_BORDER"]
    return (
        f"background-color:{bg}; color:{fg}; border:1px solid {border};"
        "border-radius:8px; padding-left:8px; padding-right:8px;"
        f"font-size:{meta_font_pt()}px;"
    )

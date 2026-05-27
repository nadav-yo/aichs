from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from services.tool_registry import (
    ExtensionFileSummary,
    ExtensionOverview,
    extension_overview,
    set_extension_enabled,
)
from ui.theme import palette, chat_font_pt, meta_font_pt, apply_flat_tab_style, icon_button_style


class ExtensionsDialog(QDialog):
    def __init__(
        self,
        overview_or_cwd: ExtensionOverview | str,
        parent=None,
        on_reload=None,
    ):
        super().__init__(parent)
        self._cwd = overview_or_cwd if isinstance(overview_or_cwd, str) else ""
        self._overview = (
            extension_overview(self._cwd)
            if isinstance(overview_or_cwd, str)
            else overview_or_cwd
        )
        self._on_reload = on_reload
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

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        title = QLabel("Extensions")
        title.setStyleSheet(
            f"font-size:{chat_font_pt() + 2}px; font-weight:600; color:{p['TEXT']};"
        )
        title_row.addWidget(title)
        title_row.addStretch()

        reload_btn = QPushButton("↻")
        reload_btn.setToolTip("Reload extensions")
        reload_btn.setFixedSize(30, 30)
        reload_btn.setStyleSheet(icon_button_style(30))
        reload_btn.clicked.connect(self._reload)
        title_row.addWidget(reload_btn)
        root.addLayout(title_row)

        self._summary = QLabel()
        self._summary.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;")
        root.addWidget(self._summary)

        self._tabs = QTabWidget()
        apply_flat_tab_style(self._tabs, "extensionsTabs")
        root.addWidget(self._tabs, 1)

        self._render()

    def _reload(self):
        if self._cwd:
            self._overview = extension_overview(self._cwd)
        if self._on_reload:
            self._on_reload()
        self._render()

    def _set_enabled(self, path: str, enabled: bool):
        set_extension_enabled(path, enabled, self._cwd or None)
        self._reload()

    def _render(self):
        overview = self._overview
        self._summary.setText(_summary_text(overview))
        while self._tabs.count():
            self._tabs.removeTab(0)
        if overview.files:
            for file in overview.files:
                index = self._tabs.addTab(
                    _ExtensionFileTab(file, on_toggle=self._set_enabled),
                    _tab_title(file),
                )
                _apply_tab_status(self._tabs, index, file)
        else:
            self._tabs.addTab(_EmptyTab(), "No extensions")
        self._tabs.addTab(_ApiReferenceTab(), "API Reference")


class _ExtensionFileTab(QWidget):
    def __init__(self, file: ExtensionFileSummary, parent=None, on_toggle=None):
        super().__init__(parent)
        self._on_toggle = on_toggle
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
        self._add_description(file)
        self._add_tools(file)
        self._add_commands(file)
        self._add_context(file)
        self._add_hooks(file)
        self._add_ui(file)
        if file.errors:
            self._add_section("Errors", [(error, "") for error in file.errors], tone="danger")

    def _add_status(self, file: ExtensionFileSummary) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        status = QLabel(file.status)
        status.setStyleSheet(_status_label_style(_status_tone(file)))
        row.addWidget(status)

        enabled = file.status != "Disabled"
        checkbox = QCheckBox("Enabled")
        checkbox.setChecked(enabled)
        checkbox.setToolTip(
            "Extension is enabled" if enabled else "Extension is disabled"
        )
        checkbox.setStyleSheet(_enabled_checkbox_style())
        checkbox.toggled.connect(
            lambda checked, path=file.path:
                self._on_toggle and self._on_toggle(path, bool(checked))
        )
        row.addWidget(checkbox)
        row.addStretch()
        self._layout.addLayout(row)

    def _add_description(self, file: ExtensionFileSummary) -> None:
        if not file.description:
            return
        p = palette()
        label = QLabel(file.description)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(
            f"color:{p['TEXT']}; background-color:{p['BG3']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px;"
            "padding:9px 10px;"
        )
        self._layout.addWidget(label)

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
                    "executable" if command.executable else "prompt mode",
                    f"capabilities: {', '.join(command.capabilities)}" if command.capabilities else "",
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
                "metadata",
                "Adds a short extension description shown near status in the Extensions dialog.",
            ),
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
        _add_api_section(layout, "Extension Metadata", [
            (
                "registry.metadata(description=...)",
                "Sets the description after the extension loads.",
            ),
            (
                "EXTENSION_DESCRIPTION",
                "Static fallback used even while the extension is disabled.",
            ),
            (
                "EXTENSION = {'description': ...}",
                "Alternative static fallback.",
            ),
            ("module docstring", "Last-resort static fallback."),
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
    disabled = sum(1 for file in overview.files if file.status == "Disabled")
    parts = [f"{count} extension {noun}"]
    parts.append(f"{errors} error(s)" if errors else "no errors")
    if disabled:
        parts.append(f"{disabled} disabled")
    return " · ".join(parts)


def _make_card() -> QFrame:
    card = QFrame()
    card.setObjectName("extensionCard")
    return card


def _tab_title(file: ExtensionFileSummary) -> str:
    name = Path(file.path).name
    if file.status == "Disabled":
        return f"Disabled · {name}"
    return f"! {name}" if file.errors else name


def _apply_tab_status(tabs: QTabWidget, index: int, file: ExtensionFileSummary) -> None:
    bar = tabs.tabBar()
    bar.setTabToolTip(index, _tab_tooltip(file))
    bar.setTabTextColor(index, QColor(_tab_text_color(file)))


def _tab_tooltip(file: ExtensionFileSummary) -> str:
    suffix = f"\n\n{file.description}" if file.description else ""
    if file.status == "Disabled":
        return (
            "Disabled. This extension is visible here but does not register contributions."
            f"{suffix}"
        )
    if file.errors:
        return f"Failed to load. Open this tab to inspect errors.{suffix}"
    return f"Loaded.{suffix}"


def _tab_text_color(file: ExtensionFileSummary) -> str:
    p = palette()
    if file.status == "Disabled":
        return p["TEXT_DIM"]
    if file.errors:
        return "#f87171"
    return p["TEXT"]


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
    elif tone == "disabled":
        bg, fg, border = p["BG3"], p["TEXT_DIM"], p["BORDER"]
    else:
        bg, fg, border = p["SUCCESS_BG"], p["SUCCESS"], p["SUCCESS_BORDER"]
    return (
        f"background-color:{bg}; color:{fg}; border:1px solid {border};"
        "border-radius:8px; padding-left:8px; padding-right:8px;"
        f"font-size:{meta_font_pt()}px;"
    )


def _status_tone(file: ExtensionFileSummary) -> str:
    if file.status == "Disabled":
        return "disabled"
    if file.errors:
        return "danger"
    return "success"


def _enabled_checkbox_style() -> str:
    p = palette()
    return (
        f"QCheckBox {{ color:{p['TEXT']}; font-size:{meta_font_pt()}px; }}"
        "QCheckBox::indicator { width:15px; height:15px; }"
    )

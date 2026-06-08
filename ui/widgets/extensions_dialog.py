from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from services.extension_installer import (
    ExtensionInstallCandidate,
    ExtensionInstallSource,
    cleanup_extension_install_source,
    install_extension_candidates,
    prepare_extension_install_source,
)
from services.tool_registry import (
    ExtensionFileSummary,
    ExtensionOverview,
    extension_overview,
    set_extension_enabled,
)
from ui.theme import palette, chat_font_pt, meta_font_pt, icon_button_style


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
        self._selected_path = ""
        self.setWindowTitle("Extensions")
        self.resize(900, 620)

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

        add_btn = QPushButton("Add")
        add_btn.setToolTip("Install extensions from a git URL")
        add_btn.clicked.connect(self._install_extensions)
        title_row.addWidget(add_btn)

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

        content = QHBoxLayout()
        content.setContentsMargins(0, 2, 0, 0)
        content.setSpacing(14)
        root.addLayout(content, 1)

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFixedWidth(330)
        self._list_scroll.setStyleSheet(_list_scroll_style())
        self._list_body = QWidget()
        self._list_layout = QVBoxLayout(self._list_body)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(1)
        self._list_scroll.setWidget(self._list_body)
        content.addWidget(self._list_scroll)

        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setStyleSheet(_detail_scroll_style())
        content.addWidget(self._detail_scroll, 1)

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

    def _install_extensions(self) -> None:
        dialog = ExtensionInstallDialog(self._cwd, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._reload()

    def _render(self):
        overview = self._overview
        self._summary.setText(_summary_text(overview))
        _clear_layout(self._list_layout)
        if overview.files:
            selected = _find_file(overview, self._selected_path) if self._selected_path else None
            if self._selected_path and selected is None:
                self._selected_path = ""
            if not self._selected_path:
                selected = overview.files[0]
                self._selected_path = selected.path
            for file in overview.files:
                row = _ExtensionListRow(
                    file,
                    selected=file.path == self._selected_path,
                    on_toggle=self._set_enabled,
                    on_select=self._show_file_detail,
                )
                self._list_layout.addWidget(row)
            if selected:
                self._detail_scroll.setWidget(_ExtensionDetailPane(selected, on_toggle=self._set_enabled))
            else:
                self._show_placeholder()
        else:
            self._list_layout.addWidget(_EmptyList())
            self._show_placeholder()
        self._list_layout.addStretch()

    def _show_file_detail(
        self,
        file: ExtensionFileSummary,
        *,
        rerender_list: bool = True,
    ) -> None:
        self._selected_path = file.path
        self._detail_scroll.setWidget(_ExtensionDetailPane(file, on_toggle=self._set_enabled))
        if rerender_list:
            self._render()

    def _show_placeholder(self) -> None:
        self._detail_scroll.setWidget(_PlaceholderPane())


class ExtensionInstallDialog(QDialog):
    def __init__(self, cwd: str = "", parent=None):
        super().__init__(parent)
        self._cwd = cwd
        self._source: ExtensionInstallSource | None = None
        self._candidate_checks: list[tuple[QCheckBox, ExtensionInstallCandidate]] = []
        self.setWindowTitle("Add Extensions")
        self.resize(620, 480)

        p = palette()
        self.setStyleSheet(
            f"QDialog {{ background:{p['BG2']}; color:{p['TEXT']}; }}"
            f"QScrollArea {{ background:{p['BG2']}; border:1px solid {p['BORDER_SUBTLE']}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Add Extensions")
        title.setStyleSheet(
            f"font-size:{chat_font_pt() + 2}px; font-weight:600; color:{p['TEXT']};"
        )
        root.addWidget(title)

        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Git URL")
        source_row.addWidget(self.url_edit, 1)
        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.clicked.connect(self._fetch)
        source_row.addWidget(self.fetch_btn)
        root.addLayout(source_row)

        scope_row = QHBoxLayout()
        scope_row.setContentsMargins(0, 0, 0, 0)
        scope_row.setSpacing(8)
        scope_label = QLabel("Install to")
        scope_label.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;")
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Local project", "local")
        self.scope_combo.addItem("Global user", "global")
        if not self._cwd:
            self.scope_combo.setCurrentIndex(1)
        scope_row.addWidget(scope_label)
        scope_row.addWidget(self.scope_combo)
        scope_row.addStretch()
        root.addLayout(scope_row)

        self.status_label = QLabel("Fetch a git source to choose extensions.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;")
        root.addWidget(self.status_label)

        self.candidate_scroll = QScrollArea()
        self.candidate_scroll.setWidgetResizable(True)
        self.candidate_body = QWidget()
        self.candidate_layout = QVBoxLayout(self.candidate_body)
        self.candidate_layout.setContentsMargins(10, 10, 10, 10)
        self.candidate_layout.setSpacing(8)
        self.candidate_scroll.setWidget(self.candidate_body)
        root.addWidget(self.candidate_scroll, 1)
        self._set_candidates([])

        buttons = QDialogButtonBox()
        self.install_btn = buttons.addButton("Install", QDialogButtonBox.ButtonRole.AcceptRole)
        self.install_btn.setEnabled(False)
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.install_btn.clicked.connect(self._install)
        cancel.clicked.connect(self.reject)
        root.addWidget(buttons)

    def reject(self) -> None:
        cleanup_extension_install_source(self._source)
        self._source = None
        super().reject()

    def _fetch(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            self._set_status("Enter a git URL.", danger=True)
            return
        self.fetch_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self._set_status("Cloning source...")
        cleanup_extension_install_source(self._source)
        self._source = None
        try:
            self._source = prepare_extension_install_source(url)
        except Exception as exc:
            self._set_candidates([])
            self._set_status(str(exc), danger=True)
            self.fetch_btn.setEnabled(True)
            return
        self._set_candidates(self._source.candidates)
        count = len(self._source.candidates)
        if count:
            noun = "extension" if count == 1 else "extensions"
            self._set_status(f"Found {count} installable {noun}.")
        else:
            self._set_status("No extension.py or root .py extensions found.", danger=True)
        self.fetch_btn.setEnabled(True)

    def _install(self) -> None:
        selected = self.selected_candidates()
        if not selected:
            self._set_status("Choose at least one extension to install.", danger=True)
            return
        try:
            install_extension_candidates(
                selected,
                scope=str(self.scope_combo.currentData()),
                cwd=self._cwd or None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Install failed", str(exc))
            return
        cleanup_extension_install_source(self._source)
        self._source = None
        self.accept()

    def selected_candidates(self) -> list[ExtensionInstallCandidate]:
        return [
            candidate
            for checkbox, candidate in self._candidate_checks
            if checkbox.isChecked()
        ]

    def _set_candidates(self, candidates: list[ExtensionInstallCandidate]) -> None:
        _clear_layout(self.candidate_layout)
        self._candidate_checks = []
        if not candidates:
            empty = QLabel("No extensions discovered yet.")
            empty.setStyleSheet(f"color:{palette()['TEXT_DIM']};")
            self.candidate_layout.addWidget(empty)
            self.candidate_layout.addStretch()
            self.install_btn.setEnabled(False) if hasattr(self, "install_btn") else None
            return
        for candidate in candidates:
            checkbox = QCheckBox(_candidate_label(candidate))
            checkbox.setChecked(True)
            checkbox.setStyleSheet(_enabled_checkbox_style())
            checkbox.toggled.connect(self._sync_install_enabled)
            self.candidate_layout.addWidget(checkbox)
            self._candidate_checks.append((checkbox, candidate))
        self.candidate_layout.addStretch()
        self._sync_install_enabled()

    def _sync_install_enabled(self) -> None:
        if hasattr(self, "install_btn"):
            self.install_btn.setEnabled(bool(self.selected_candidates()))

    def _set_status(self, text: str, *, danger: bool = False) -> None:
        color = "#f87171" if danger else palette()["TEXT_DIM"]
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color:{color}; font-size:{meta_font_pt()}px;")


class _ExtensionListRow(QFrame):
    def __init__(
        self,
        file: ExtensionFileSummary,
        *,
        selected: bool,
        parent=None,
        on_toggle=None,
        on_select=None,
    ):
        super().__init__(parent)
        self._file = file
        self._on_select = on_select
        self.setObjectName("extensionListRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(_list_row_style(selected, _status_tone(file)))
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(3)

        name = QLabel(_extension_name(file))
        name.setWordWrap(True)
        name.setStyleSheet(_list_name_style())
        layout.addWidget(name, 0, 0)

        status = QLabel(_list_status_text(file))
        status.setStyleSheet(_list_meta_style(_status_tone(file)))
        layout.addWidget(status, 0, 1, Qt.AlignmentFlag.AlignRight)

        meta = QLabel(_list_subtitle(file))
        meta.setWordWrap(True)
        meta.setStyleSheet(_list_path_style())
        layout.addWidget(meta, 1, 0, 1, 2)

        enabled = file.status != "Disabled"
        checkbox = QCheckBox("Enabled")
        checkbox.setChecked(enabled)
        checkbox.setToolTip(
            "Extension is enabled" if enabled else "Extension is disabled"
        )
        checkbox.setStyleSheet(_enabled_checkbox_style())
        checkbox.toggled.connect(
            lambda checked, path=file.path:
                on_toggle and on_toggle(path, bool(checked))
        )
        layout.addWidget(checkbox, 2, 0, Qt.AlignmentFlag.AlignLeft)

    def mousePressEvent(self, event):
        if self._on_select:
            self._on_select(self._file)
        super().mousePressEvent(event)


class _ExtensionDetailPane(QWidget):
    def __init__(self, file: ExtensionFileSummary, parent=None, on_toggle=None):
        super().__init__(parent)
        self._on_toggle = on_toggle
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        self._add_header(root, file)
        self._add_description(root, file)
        _add_detail_section(
            root,
            "Tools",
            [
                (
                    tool.name,
                    _join_details(
                        tool.description,
                        "parallel safe" if tool.parallel_safe else "",
                        f"approval: {tool.approval}" if tool.approval else "",
                    ),
                )
                for tool in file.tools
            ],
        )
        _add_detail_section(
            root,
            "Slash Commands",
            [
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
            ],
        )
        _add_detail_section(
            root,
            "Context",
            [(name, "Injected into workspace context") for name in file.contexts],
        )
        _add_detail_section(
            root,
            "Hooks",
            [(name, "Lifecycle hook") for name in file.hooks],
        )
        _add_detail_section(
            root,
            "Language Features",
            [
                (
                    language.name,
                    _join_details(
                        f"patterns: {', '.join(language.file_patterns)}",
                        "diagnostics" if language.diagnostics else "",
                        "symbols" if language.symbols else "",
                        "completion" if language.completion else "",
                    ),
                )
                for language in file.languages
            ],
        )
        ui_rows = [(badge.name, "Status badge") for badge in file.badges]
        ui_rows += [(panel.name, f"Panel: {panel.title}") for panel in file.panels]
        _add_detail_section(root, "UI Contributions", ui_rows)
        _add_detail_section(
            root,
            "Errors",
            [(error, "") for error in file.errors],
            tone="danger",
        )
        root.addStretch()

    def _add_header(self, root: QVBoxLayout, file: ExtensionFileSummary) -> None:
        p = palette()
        header = QFrame()
        header.setObjectName("extensionHeader")
        header.setStyleSheet(_header_style())
        layout = QGridLayout(header)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(5)

        title = QLabel(_extension_name(file))
        title.setWordWrap(True)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title.setStyleSheet(
            f"font-size:{chat_font_pt() + 4}px; font-weight:650; color:{p['TEXT']};"
        )
        layout.addWidget(title, 0, 0)

        status = QLabel(file.status)
        status.setStyleSheet(_status_label_style(_status_tone(file)))
        layout.addWidget(status, 0, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        path = QLabel(file.path)
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;")
        layout.addWidget(path, 1, 0, 1, 2)

        enabled = file.status != "Disabled"
        checkbox = QCheckBox("Enabled")
        checkbox.setChecked(enabled)
        checkbox.setStyleSheet(_enabled_checkbox_style())
        checkbox.toggled.connect(
            lambda checked, path=file.path:
                self._on_toggle and self._on_toggle(path, bool(checked))
        )
        layout.addWidget(checkbox, 2, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(header)

    def _add_description(self, root: QVBoxLayout, file: ExtensionFileSummary) -> None:
        if not file.description:
            return
        p = palette()
        label = QLabel(file.description)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(
            f"color:{p['TEXT']}; background:transparent;"
            f"border-left:2px solid {p['BORDER']}; padding:0 0 0 10px;"
        )
        root.addWidget(label)


class _EmptyList(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        p = palette()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        label = QLabel("No extension files found.")
        label.setStyleSheet(f"color:{p['TEXT_DIM']};")
        layout.addWidget(label)
        layout.addStretch()


class _PlaceholderPane(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        p = palette()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()
        label = QLabel("No extension selected.")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(f"color:{p['TEXT_DIM']};")
        layout.addWidget(label)
        layout.addStretch()


def _add_detail_section(
    layout: QVBoxLayout,
    heading: str,
    rows: list[tuple[str, str]],
    *,
    tone: str = "",
) -> None:
    if not rows:
        return
    label = QLabel(heading)
    label.setStyleSheet(_heading_style(tone))
    layout.addWidget(label)

    table = QFrame()
    table.setObjectName("extensionDetailTable")
    table.setStyleSheet(_detail_table_style(tone))
    grid = QGridLayout(table)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(0)

    for row, (name, description) in enumerate(rows):
        name_label = QLabel(name)
        name_label.setWordWrap(True)
        name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        name_label.setStyleSheet(_detail_name_style(tone))
        name_label.setContentsMargins(0, 8 if row else 0, 0, 8)
        grid.addWidget(name_label, row, 0, Qt.AlignmentFlag.AlignTop)

        desc_label = QLabel(description or "-")
        desc_label.setWordWrap(True)
        desc_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        desc_label.setStyleSheet(_detail_value_style(tone))
        desc_label.setContentsMargins(0, 8 if row else 0, 0, 8)
        grid.addWidget(desc_label, row, 1)

    grid.setColumnStretch(1, 1)
    layout.addWidget(table)


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


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _find_file(
    overview: ExtensionOverview,
    path: str,
) -> ExtensionFileSummary | None:
    return next((file for file in overview.files if file.path == path), None)


def _extension_name(file: ExtensionFileSummary) -> str:
    if file.display_name:
        return file.display_name
    path = Path(file.path)
    if path.name == "extension.py" and path.parent.name:
        return path.parent.name
    return Path(file.path).name


def _candidate_label(candidate: ExtensionInstallCandidate) -> str:
    details = [candidate.name, candidate.kind]
    if candidate.description:
        details.append(candidate.description)
    return " · ".join(details)


def _list_status_text(file: ExtensionFileSummary) -> str:
    if file.errors:
        return f"{file.status} · {len(file.errors)} error(s)"
    return file.status


def _list_subtitle(file: ExtensionFileSummary) -> str:
    counts = [
        (len(file.tools), "tool"),
        (len(file.commands), "command"),
        (len(file.contexts), "context"),
        (len(file.hooks), "hook"),
        (len(file.languages), "language"),
        (len(file.badges) + len(file.panels), "UI"),
    ]
    parts = [
        f"{count} {label}{'' if count == 1 or label == 'UI' else 's'}"
        for count, label in counts
        if count
    ]
    if not parts:
        parts.append("No registered contributions")
    if file.description:
        parts.append(file.description)
    return " · ".join(parts)


def _join_details(*parts: str) -> str:
    return " | ".join(part for part in parts if part)


def _heading_style(tone: str = "") -> str:
    p = palette()
    color = "#f87171" if tone == "danger" else p["TEXT_DIM"]
    return (
        f"color:{color}; font-size:{meta_font_pt()}px;"
        "font-weight:600;"
    )


def _list_scroll_style() -> str:
    p = palette()
    return (
        f"QScrollArea {{ background:{p['BG2']}; border-right:1px solid {p['BORDER_SUBTLE']}; }}"
    )


def _detail_scroll_style() -> str:
    p = palette()
    return (
        f"QScrollArea {{ background:{p['BG2']}; border:none; }}"
        f"QScrollArea QWidget {{ background:{p['BG2']}; }}"
    )


def _list_row_style(selected: bool, tone: str) -> str:
    p = palette()
    bg = p["SELECTION"] if selected else p["BG2"]
    hover = p["SELECTION"] if selected else p["BG3"]
    border = {
        "danger": "#5f252d",
        "disabled": p["BORDER_SUBTLE"],
    }.get(tone, p["BORDER_SUBTLE"])
    return (
        f"QFrame#extensionListRow {{ background:{bg};"
        f"border-bottom:1px solid {border}; border-radius:0; }}"
        f"QFrame#extensionListRow:hover {{ background:{hover}; }}"
    )


def _list_name_style() -> str:
    p = palette()
    return f"color:{p['TEXT']}; font-weight:600;"


def _list_meta_style(tone: str) -> str:
    p = palette()
    color = {
        "danger": "#f87171",
        "disabled": p["TEXT_DIM"],
        "success": p["SUCCESS"],
    }.get(tone, p["TEXT_DIM"])
    return f"color:{color}; font-size:{meta_font_pt()}px; font-weight:600;"


def _list_path_style() -> str:
    p = palette()
    return f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;"


def _header_style() -> str:
    p = palette()
    return (
        f"QFrame#extensionHeader {{ background:transparent;"
        f"border-bottom:1px solid {p['BORDER_SUBTLE']}; border-radius:0; }}"
    )


def _detail_table_style(tone: str = "") -> str:
    p = palette()
    border = "#5f252d" if tone == "danger" else p["BORDER_SUBTLE"]
    return (
        f"QFrame#extensionDetailTable {{ background:transparent;"
        f"border-top:1px solid {border}; border-radius:0; }}"
    )


def _detail_name_style(tone: str = "") -> str:
    p = palette()
    color = "#f87171" if tone == "danger" else p["TEXT"]
    return f"color:{color}; font-weight:600;"


def _detail_value_style(tone: str = "") -> str:
    p = palette()
    color = "#fca5a5" if tone == "danger" else p["TEXT_DIM"]
    return f"color:{color}; font-size:{meta_font_pt()}px;"


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

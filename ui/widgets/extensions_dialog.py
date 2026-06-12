from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, QTimer, pyqtSignal, QSize, QPointF
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
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
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from services.extension_installer import (
    ExtensionInstallCandidate,
    ExtensionInstallSource,
    ExistingExtensionInstall,
    cleanup_extension_install_source,
    format_commit_date,
    format_install_timestamp,
    install_conflicts,
    install_extension_candidates,
    prepare_extension_install_source,
)
from services.tool_registry import (
    ExtensionFileSummary,
    ExtensionOverview,
    extension_overview,
    set_extension_enabled,
)
from ui.theme import (
    ACCENT,
    checkbox_style,
    chat_font_pt,
    compact_combo_box_style,
    dialog_button_box_style,
    dialog_shell_style,
    form_field_style,
    hint_label_style,
    icon_button_style,
    accent_icon_button_style,
    bordered_icon_button_style,
    extension_detail_name_style,
    extension_detail_table_frame_style,
    extension_detail_value_style,
    extension_header_frame_style,
    extension_list_meta_style,
    extension_list_name_style,
    extension_list_row_style,
    extension_panel_heading_style,
    menu_style,
    meta_font_pt,
    palette,
    status_pill_style,
    transparent_scroll_area_style,
    title_label_style,
)


_STATUS_FILTER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("All", "all"),
    ("Enabled", "enabled"),
    ("Disabled", "disabled"),
    ("Needs review", "review"),
    ("Has errors", "errors"),
)


class _ExtensionOverviewSignals(QObject):
    done = pyqtSignal(int, object, str)


class _ExtensionOverviewWorker(QRunnable):
    def __init__(self, generation: int, cwd: str):
        super().__init__()
        self.signals = _ExtensionOverviewSignals()
        self._generation = generation
        self._cwd = cwd

    def run(self) -> None:
        try:
            overview = extension_overview(self._cwd)
        except BaseException as exc:
            self.signals.done.emit(self._generation, None, str(exc) or exc.__class__.__name__)
            return
        self.signals.done.emit(self._generation, overview, "")


class ExtensionsDialog(QDialog):
    def __init__(
        self,
        overview_or_cwd: ExtensionOverview | str,
        parent=None,
        on_reload=None,
    ):
        super().__init__(parent)
        self._cwd = overview_or_cwd if isinstance(overview_or_cwd, str) else ""
        self._overview = ExtensionOverview(files=[]) if self._cwd else overview_or_cwd
        self._on_reload = on_reload
        self._selected_path = ""
        self._filter_query = ""
        self._status_filter = "all"
        self._overview_generation = 0
        self._overview_pool = QThreadPool.globalInstance()
        self._pending_enable_warning_path = ""
        self.setWindowTitle("Extensions")
        self.resize(900, 620)

        self.setStyleSheet(dialog_shell_style() + transparent_scroll_area_style())
        palette()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        title = QLabel("Extensions")
        title.setStyleSheet(title_label_style(font_weight="600"))
        title_row.addWidget(title)
        title_row.addStretch()

        reload_btn = QPushButton("↻")
        reload_btn.setToolTip("Reload extensions")
        reload_btn.setFixedSize(30, 30)
        reload_btn.setStyleSheet(icon_button_style(30))
        reload_btn.clicked.connect(self._reload)
        title_row.addWidget(reload_btn)

        add_btn = QPushButton("")
        add_btn.setToolTip("Install extensions from a git URL")
        add_btn.setFixedSize(30, 30)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setIcon(_extensions_add_icon())
        add_btn.setIconSize(QSize(14, 14))
        add_btn.setStyleSheet(accent_icon_button_style(30))
        add_btn.clicked.connect(self._install_extensions)
        title_row.addWidget(add_btn)
        root.addLayout(title_row)

        self._summary = QLabel()
        self._summary.setStyleSheet(hint_label_style())
        root.addWidget(self._summary)

        content = QHBoxLayout()
        content.setContentsMargins(0, 2, 0, 0)
        content.setSpacing(14)
        root.addLayout(content, 1)

        list_column = QVBoxLayout()
        list_column.setContentsMargins(0, 0, 0, 0)
        list_column.setSpacing(8)

        list_toolbar = QHBoxLayout()
        list_toolbar.setContentsMargins(0, 0, 0, 0)
        list_toolbar.setSpacing(6)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter extensions")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setStyleSheet(_filter_field_style())
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        list_toolbar.addWidget(self._filter_edit, 1)

        self._status_filter_btn = QToolButton()
        self._status_filter_btn.setText("▾")
        self._status_filter_btn.setFixedSize(28, 28)
        self._status_filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._status_filter_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._status_menu = QMenu(self)
        self._status_menu.setStyleSheet(menu_style())
        self._status_actions: dict[str, QAction] = {}
        for label, value in _STATUS_FILTER_OPTIONS:
            action = self._status_menu.addAction(label)
            action.setCheckable(True)
            action.setData(value)
            action.triggered.connect(
                lambda _checked, filter_value=value: self._set_status_filter(filter_value)
            )
            self._status_actions[value] = action
        self._status_actions["all"].setChecked(True)
        self._status_filter_btn.setMenu(self._status_menu)
        list_toolbar.addWidget(self._status_filter_btn)
        self._sync_status_filter_button()
        list_column.addLayout(list_toolbar)

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFixedWidth(330)
        self._list_scroll.setStyleSheet(_list_scroll_style())
        self._list_body = QWidget()
        self._list_layout = QVBoxLayout(self._list_body)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(1)
        self._list_scroll.setWidget(self._list_body)
        list_column.addWidget(self._list_scroll, 1)

        list_wrap = QWidget()
        list_wrap.setFixedWidth(330)
        list_wrap.setLayout(list_column)
        content.addWidget(list_wrap)

        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setStyleSheet(_detail_scroll_style())
        content.addWidget(self._detail_scroll, 1)

        if self._cwd:
            self._load_overview()
        else:
            self._render()

    def _reload(self):
        if self._cwd:
            self._load_overview()
            return
        self._render()
        if self._on_reload:
            self._on_reload()

    def _load_overview(self):
        if not self._cwd:
            self._render()
            return
        self._overview_generation += 1
        generation = self._overview_generation
        self._show_loading()
        worker = _ExtensionOverviewWorker(generation, self._cwd)
        worker.signals.done.connect(self._on_overview_ready)
        self._overview_pool.start(worker)

    def _on_overview_ready(self, generation: int, overview: object, error: str):
        if generation != self._overview_generation:
            return
        if error:
            self._summary.setText(f"Extension overview failed: {error}")
            self._show_placeholder()
            return
        self._overview = overview if isinstance(overview, ExtensionOverview) else ExtensionOverview(files=[])
        self._render()
        if self._on_reload:
            self._on_reload()
        warning_path = self._pending_enable_warning_path
        self._pending_enable_warning_path = ""
        if warning_path:
            selected = _find_file(self._overview, warning_path)
            if selected and selected.errors:
                QMessageBox.warning(
                    self,
                    "Extension failed to load",
                    _extension_error_text(selected),
                )

    def _set_enabled(self, path: str, enabled: bool):
        try:
            set_extension_enabled(path, enabled, self._cwd or None)
            self._pending_enable_warning_path = path if enabled else ""
            self._reload()
        except BaseException as exc:
            if enabled:
                try:
                    set_extension_enabled(path, False, self._cwd or None)
                except BaseException:
                    pass
            QMessageBox.warning(
                self,
                "Extension enable failed" if enabled else "Extension disable failed",
                str(exc) or exc.__class__.__name__,
            )
            try:
                self._reload()
            except BaseException:
                self._show_placeholder()
            return

    def _on_filter_changed(self, *_args) -> None:
        self._filter_query = self._filter_edit.text().strip()
        self._render()

    def _set_status_filter(self, value: str) -> None:
        self._status_filter = value or "all"
        for filter_value, action in self._status_actions.items():
            action.setChecked(filter_value == self._status_filter)
        self._sync_status_filter_button()
        self._render()

    def _sync_status_filter_button(self) -> None:
        labels = {value: label for label, value in _STATUS_FILTER_OPTIONS}
        label = labels.get(self._status_filter, "All")
        active = self._status_filter != "all"
        self._status_filter_btn.setToolTip(
            f"Status filter: {label}" if active else "Filter by status"
        )
        p = palette()
        self._status_filter_btn.setStyleSheet(
            bordered_icon_button_style(
                size_px=28,
                text_color=ACCENT if active else p["TEXT_DIM"],
                border_color=ACCENT if active else p["BORDER"],
            )
        )

    def _install_extensions(self) -> None:
        dialog = ExtensionInstallDialog(self._cwd, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._reload()

    def _render(self):
        overview = self._overview
        visible_files = _filter_extensions(
            overview.files,
            query=self._filter_query,
            status=self._status_filter,
        )
        self._summary.setText(_summary_text(overview, visible_count=len(visible_files)))
        _clear_layout(self._list_layout)
        if overview.files:
            selected = _find_file(overview, self._selected_path) if self._selected_path else None
            if selected and selected not in visible_files:
                selected = visible_files[0] if visible_files else None
                self._selected_path = selected.path if selected else ""
            elif self._selected_path and selected is None:
                self._selected_path = ""
            if not self._selected_path and visible_files:
                selected = visible_files[0]
                self._selected_path = selected.path
            if visible_files:
                for file in visible_files:
                    row = _ExtensionListRow(
                        file,
                        selected=file.path == self._selected_path,
                        on_toggle=self._set_enabled,
                        on_select=self._show_file_detail,
                    )
                    self._list_layout.addWidget(row)
            else:
                self._list_layout.addWidget(_FilteredEmptyList())
            if selected and selected in visible_files:
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

    def _show_loading(self) -> None:
        self._summary.setText("Loading extensions...")
        _clear_layout(self._list_layout)
        label = QLabel("Loading extension overview...")
        label.setStyleSheet(hint_label_style())
        self._list_layout.addWidget(label)
        self._list_layout.addStretch()
        self._show_placeholder()

    def closeEvent(self, event) -> None:
        self._overview_generation += 1
        super().closeEvent(event)


class _ExtensionInstallFetchSignals(QObject):
    done = pyqtSignal(int, object, str)


class _ExtensionInstallFetchWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        url: str,
        previous_source: ExtensionInstallSource | None,
    ):
        super().__init__()
        self.signals = _ExtensionInstallFetchSignals()
        self._generation = generation
        self._url = url
        self._previous_source = previous_source

    def run(self) -> None:
        try:
            cleanup_extension_install_source(self._previous_source)
            source = prepare_extension_install_source(self._url)
        except Exception as exc:
            self.signals.done.emit(self._generation, None, str(exc))
            return
        self.signals.done.emit(self._generation, source, "")


class _ExtensionInstallApplySignals(QObject):
    done = pyqtSignal(int, object, str)


class _ExtensionInstallApplyWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        candidates: list[ExtensionInstallCandidate],
        scope: str,
        cwd: str | None,
        source: ExtensionInstallSource | None,
    ):
        super().__init__()
        self.signals = _ExtensionInstallApplySignals()
        self._generation = generation
        self._candidates = candidates
        self._scope = scope
        self._cwd = cwd
        self._source = source

    def run(self) -> None:
        results = []
        error = ""
        try:
            results = install_extension_candidates(
                self._candidates,
                scope=self._scope,
                cwd=self._cwd,
            )
        except Exception as exc:
            error = str(exc)
        try:
            cleanup_extension_install_source(self._source)
        except Exception as exc:
            if not error:
                error = str(exc)
        if error:
            self.signals.done.emit(self._generation, [], error)
            return
        self.signals.done.emit(self._generation, results, "")


class ExtensionInstallDialog(QDialog):
    def __init__(self, cwd: str = "", parent=None):
        super().__init__(parent)
        self._cwd = cwd
        self._source: ExtensionInstallSource | None = None
        self._candidate_checks: list[tuple[QCheckBox, ExtensionInstallCandidate]] = []
        self._candidate_filter = ""
        self._all_candidates: list[ExtensionInstallCandidate] = []
        self._conflicts: dict[str, ExistingExtensionInstall] = {}
        self._fetch_generation = 0
        self._fetch_active = False
        self._install_generation = 0
        self._install_active = False
        self._worker_pool = QThreadPool(self)
        self._worker_pool.setMaxThreadCount(1)
        self.setWindowTitle("Add Extensions")
        self.resize(620, 480)

        p = palette()
        self.setStyleSheet(
            dialog_shell_style()
            + transparent_scroll_area_style(border=f"1px solid {p['BORDER_SUBTLE']}")
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Git URL")
        self.url_edit.setStyleSheet(form_field_style(selector="QLineEdit", padding="7px 10px"))
        source_row.addWidget(self.url_edit, 1)
        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.clicked.connect(self._fetch)
        source_row.addWidget(self.fetch_btn)
        root.addLayout(source_row)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(8)
        scope_label = QLabel("Install to")
        scope_label.setStyleSheet(hint_label_style())
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Local project", "local")
        self.scope_combo.addItem("Global user", "global")
        self.scope_combo.setStyleSheet(_install_scope_combo_style())
        if not self._cwd:
            self.scope_combo.setCurrentIndex(1)
        controls_row.addWidget(scope_label)
        controls_row.addWidget(self.scope_combo)
        controls_row.addStretch()
        self._select_all_btn = QPushButton("Select all")
        self._select_all_btn.setStyleSheet(_install_toolbar_button_style())
        self._select_all_btn.clicked.connect(lambda: self._set_all_candidates(True))
        controls_row.addWidget(self._select_all_btn)
        self._deselect_all_btn = QPushButton("Deselect all")
        self._deselect_all_btn.setStyleSheet(_install_toolbar_button_style())
        self._deselect_all_btn.clicked.connect(lambda: self._set_all_candidates(False))
        controls_row.addWidget(self._deselect_all_btn)
        root.addLayout(controls_row)
        self.scope_combo.currentIndexChanged.connect(self._on_install_scope_changed)

        self.status_label = QLabel("Fetch a git source to choose extensions.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(hint_label_style())
        root.addWidget(self.status_label)

        self._candidate_filter_edit = QLineEdit()
        self._candidate_filter_edit.setPlaceholderText("Filter extensions")
        self._candidate_filter_edit.setClearButtonEnabled(True)
        self._candidate_filter_edit.setStyleSheet(_filter_field_style())
        self._candidate_filter_edit.textChanged.connect(self._on_candidate_filter_changed)
        root.addWidget(self._candidate_filter_edit)

        self.candidate_scroll = QScrollArea()
        self.candidate_scroll.setWidgetResizable(True)
        self.candidate_body = QWidget()
        self.candidate_layout = QVBoxLayout(self.candidate_body)
        self.candidate_layout.setContentsMargins(0, 0, 0, 0)
        self.candidate_layout.setSpacing(1)
        self.candidate_scroll.setWidget(self.candidate_body)
        root.addWidget(self.candidate_scroll, 1)
        self._set_candidates([])

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self._selection_label = QLabel("")
        self._selection_label.setStyleSheet(hint_label_style())
        footer.addWidget(self._selection_label)
        footer.addStretch()
        buttons = QDialogButtonBox()
        buttons.setStyleSheet(dialog_button_box_style())
        self.install_btn = buttons.addButton("Install selected", QDialogButtonBox.ButtonRole.AcceptRole)
        self.install_btn.setEnabled(False)
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.install_btn.clicked.connect(self._install)
        cancel.clicked.connect(self.reject)
        footer.addWidget(buttons)
        root.addLayout(footer)
        self._sync_list_controls_visible(False)

    def _sync_list_controls_visible(self, visible: bool) -> None:
        self._candidate_filter_edit.setVisible(visible)
        self._select_all_btn.setVisible(visible)
        self._deselect_all_btn.setVisible(visible)

    def _set_all_candidates(self, checked: bool) -> None:
        for checkbox, _candidate in self._candidate_checks:
            if not checkbox.isEnabled():
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        self._sync_install_enabled()

    def _on_candidate_filter_changed(self, text: str) -> None:
        self._candidate_filter = text.strip()
        self._set_candidates(self._all_candidates)

    def _on_install_scope_changed(self, *_args) -> None:
        if self._all_candidates:
            self._set_candidates(self._all_candidates)

    def reject(self) -> None:
        if self._fetch_active:
            self._set_status("Fetch is still running.")
            return
        if self._install_active:
            self._set_status("Install is still running.")
            return
        self._fetch_generation += 1
        self._install_generation += 1
        cleanup_extension_install_source(self._source)
        self._source = None
        super().reject()

    def closeEvent(self, event) -> None:
        if self._fetch_active:
            self._set_status("Fetch is still running.")
            event.ignore()
            return
        if self._install_active:
            self._set_status("Install is still running.")
            event.ignore()
            return
        self._fetch_generation += 1
        self._install_generation += 1
        cleanup_extension_install_source(self._source)
        self._source = None
        super().closeEvent(event)

    def _fetch(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            self._set_status("Enter a git URL.", danger=True)
            return
        self._fetch_generation += 1
        generation = self._fetch_generation
        previous_source = self._source
        self._source = None
        self._fetch_active = True
        self.fetch_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self._set_candidates([])
        self._set_status("Cloning source...")
        worker = _ExtensionInstallFetchWorker(generation, url, previous_source)
        worker.signals.done.connect(self._on_fetch_done)
        self._worker_pool.start(worker)

    def _on_fetch_done(self, generation: int, source: object, error: str) -> None:
        if generation != self._fetch_generation:
            if isinstance(source, ExtensionInstallSource):
                cleanup_extension_install_source(source)
            return
        self._fetch_active = False
        if error:
            self._set_candidates([])
            self._set_status(error, danger=True)
            self.fetch_btn.setEnabled(True)
            return
        self._source = source if isinstance(source, ExtensionInstallSource) else None
        candidates = self._source.candidates if self._source else []
        self._set_candidates(candidates)
        if not candidates:
            self._set_status("No extension.py or root .py extensions found.", danger=True)
        self.fetch_btn.setEnabled(True)

    def _install(self) -> None:
        if self._fetch_active or self._install_active:
            return
        selected = self.selected_candidates()
        if not selected:
            self._set_status("Choose at least one extension to install.", danger=True)
            return
        self._install_generation += 1
        generation = self._install_generation
        self._install_active = True
        self.fetch_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self._set_status("Installing extensions...")
        worker = _ExtensionInstallApplyWorker(
            generation,
            selected,
            str(self.scope_combo.currentData()),
            self._cwd or None,
            self._source,
        )
        worker.signals.done.connect(self._on_install_done)
        self._worker_pool.start(worker)

    def _on_install_done(self, generation: int, _results: object, error: str) -> None:
        if generation != self._install_generation:
            return
        self._install_active = False
        self.fetch_btn.setEnabled(True)
        if error:
            self._sync_install_enabled()
            self._set_status("Install failed.", danger=True)
            QMessageBox.warning(self, "Install failed", error)
            return
        self._source = None
        QMessageBox.information(
            self,
            "Extensions installed",
            "Installed extensions are disabled until you review and enable them.",
        )
        self.accept()

    def _current_scope(self) -> str:
        return str(self.scope_combo.currentData() or "local")

    def _refresh_conflicts(self) -> None:
        if not self._all_candidates:
            self._conflicts = {}
            return
        self._conflicts = install_conflicts(
            self._all_candidates,
            scope=self._current_scope(),
            cwd=self._cwd or None,
        )

    def _current_conflicts(self) -> dict[str, ExistingExtensionInstall]:
        return dict(self._conflicts)

    def _replacing_conflicts(self) -> dict[str, ExistingExtensionInstall]:
        return {
            name: conflict
            for name, conflict in self._current_conflicts().items()
            if conflict.replaces_target
        }

    def selected_candidates(self) -> list[ExtensionInstallCandidate]:
        return [
            candidate
            for checkbox, candidate in self._candidate_checks
            if checkbox.isChecked()
        ]

    def _set_candidates(self, candidates: list[ExtensionInstallCandidate]) -> None:
        self._all_candidates = list(candidates)
        self._refresh_conflicts()
        visible = _filter_install_candidates(candidates, self._candidate_filter)
        conflicts = self._conflicts
        _clear_layout(self.candidate_layout)
        self._candidate_checks = []
        self._sync_list_controls_visible(bool(candidates))
        if not candidates:
            empty = QLabel("No extensions discovered yet.")
            empty.setStyleSheet(hint_label_style())
            self.candidate_layout.addWidget(empty)
            self.candidate_layout.addStretch()
            self.install_btn.setEnabled(False) if hasattr(self, "install_btn") else None
            self._sync_install_button_label(False)
            self._sync_selection_label(0)
            return
        if not visible:
            empty = QLabel("No extensions match the filter.")
            empty.setStyleSheet(hint_label_style())
            self.candidate_layout.addWidget(empty)
            self.candidate_layout.addStretch()
            self._sync_install_enabled()
            self._sync_all_installed_summary()
            return
        replacing = False
        for candidate in visible:
            conflict = conflicts.get(candidate.name)
            row = _InstallCandidateRow(
                candidate,
                conflict=conflict,
                source=self._source,
                install_scope=self._current_scope(),
            )
            row.checkbox.toggled.connect(lambda _checked: self._sync_install_enabled())
            self.candidate_layout.addWidget(row)
            self._candidate_checks.append((row.checkbox, candidate))
            if conflict is not None and conflict.replaces_target and row.checkbox.isChecked():
                replacing = True
        self.candidate_layout.addStretch()
        self._sync_install_enabled()
        self._sync_install_button_label(replacing)
        self._sync_all_installed_summary()

    def _candidate_is_already_installed(self, candidate: ExtensionInstallCandidate) -> bool:
        conflict = self._conflicts.get(candidate.name)
        return conflict is not None and conflict.same_content

    def _all_candidates_already_installed(self) -> bool:
        return bool(self._all_candidates) and all(
            self._candidate_is_already_installed(candidate)
            for candidate in self._all_candidates
        )

    def _sync_all_installed_summary(self) -> None:
        if not hasattr(self, "status_label"):
            return
        if self._fetch_active or self._install_active:
            return
        if self._all_candidates_already_installed():
            count = len(self._all_candidates)
            if count == 1:
                text = "This extension is already installed."
            else:
                text = f"All {count} extensions are already installed."
            self._set_status(text)
        elif self._all_candidates:
            self._set_status("")

    def _sync_install_enabled(self) -> None:
        if hasattr(self, "install_btn"):
            selected = self.selected_candidates()
            self.install_btn.setEnabled(bool(selected))
            replacing = any(
                candidate.name in self._replacing_conflicts()
                for candidate in selected
            )
            self._sync_install_button_label(replacing)
            self._sync_selection_label(len(selected))

    def _selectable_candidate_count(self) -> int:
        return sum(
            1 for checkbox, _candidate in self._candidate_checks if checkbox.isEnabled()
        )

    def _sync_selection_label(self, selected_count: int) -> None:
        if not hasattr(self, "_selection_label"):
            return
        if not self._candidate_checks:
            self._selection_label.setText("")
            return
        selectable = self._selectable_candidate_count()
        if selectable == 0:
            if self._all_candidates_already_installed():
                count = len(self._all_candidates)
                if count == 1:
                    self._selection_label.setText("Already installed")
                else:
                    self._selection_label.setText(
                        f"All {count} extensions already installed"
                    )
            elif self._candidate_checks:
                visible = len(self._candidate_checks)
                suffix = "s" if visible != 1 else ""
                self._selection_label.setText(
                    f"All {visible} visible extension{suffix} already installed"
                )
            else:
                self._selection_label.setText("")
            return
        if selected_count == 0:
            self._selection_label.setText("None selected")
            return
        self._selection_label.setText(f"{selected_count} of {selectable} selected")

    def _sync_install_button_label(self, replacing: bool) -> None:
        if not hasattr(self, "install_btn"):
            return
        self.install_btn.setText("Replace selected" if replacing else "Install selected")

    def _set_status(self, text: str, *, danger: bool = False) -> None:
        color = "#f87171" if danger else palette()["TEXT_DIM"]
        self.status_label.setVisible(bool(text))
        self.status_label.setText(text)
        self.status_label.setStyleSheet(hint_label_style(text_color=color))


class _InstallCandidateRow(QFrame):
    def __init__(
        self,
        candidate: ExtensionInstallCandidate,
        *,
        conflict: ExistingExtensionInstall | None,
        source: ExtensionInstallSource | None,
        install_scope: str = "local",
        parent=None,
    ):
        super().__init__(parent)
        self.checkbox = QCheckBox("")
        already_installed = conflict is not None and conflict.same_content
        self.checkbox.setChecked(conflict is None and not already_installed)
        tooltip = _candidate_disclosure(
            candidate,
            conflict=conflict,
            source=source,
            install_scope=install_scope,
        )
        self.setToolTip(tooltip)
        self.checkbox.setToolTip(tooltip)
        if already_installed:
            self.checkbox.setEnabled(False)
            self.checkbox.setStyleSheet(_disabled_checkbox_style())
        else:
            self.checkbox.setStyleSheet(_enabled_checkbox_style())

        self.setObjectName("installCandidateRow")
        elsewhere = (
            conflict is not None
            and not conflict.replaces_target
            and not conflict.same_content
        )
        self.setStyleSheet(_install_candidate_row_style(elsewhere=elsewhere))

        grid = QGridLayout(self)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        name = QLabel(_candidate_display_name(candidate))
        name.setWordWrap(True)
        name.setStyleSheet(
            hint_label_style() if already_installed else _list_name_style()
        )
        grid.addWidget(self.checkbox, 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(name, 0, 1)

        next_row = 1
        if candidate.description:
            description = QLabel(candidate.description)
            description.setWordWrap(True)
            description.setStyleSheet(hint_label_style())
            grid.addWidget(description, next_row, 1)
            next_row += 1

        if conflict is not None:
            notice = QLabel(_install_conflict_notice_text(
                candidate,
                conflict,
                source,
                install_scope,
            ))
            notice.setWordWrap(True)
            if conflict.same_content:
                notice.setStyleSheet(hint_label_style())
            else:
                notice.setStyleSheet(_install_conflict_notice_style())
            grid.addWidget(notice, next_row, 1)
            next_row += 1

        grid.setColumnStretch(1, 1)


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
            lambda checked, path=file.path: _queue_toggle(on_toggle, path, checked)
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
        _add_detail_section(root, "Review & Risk", _risk_rows(file), tone=_risk_tone(file))
        _add_detail_section(root, "Declared Permissions", _permission_rows(file))
        _add_detail_section(root, "Contributions", _observed_rows(file))
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
            "Permission Violations",
            [(violation, "Blocked by manifest permissions") for violation in file.permission_violations],
            tone="danger",
        )
        _add_detail_section(
            root,
            "Requirements",
            _requirements_rows(file),
            tone="danger" if file.missing_requirements else "",
        )
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
        path.setStyleSheet(hint_label_style())
        layout.addWidget(path, 1, 0, 1, 2)

        enabled = file.status != "Disabled"
        checkbox = QCheckBox("Enabled")
        checkbox.setChecked(enabled)
        checkbox.setStyleSheet(_enabled_checkbox_style())
        checkbox.toggled.connect(
            lambda checked, path=file.path: _queue_toggle(self._on_toggle, path, checked)
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
            f"color:{p['TEXT']}; background-color:transparent;"
            f"border-left:2px solid {p['BORDER']}; padding:0 0 0 10px;"
        )
        root.addWidget(label)


class _FilteredEmptyList(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        label = QLabel("No extensions match the filter.")
        label.setStyleSheet(hint_label_style())
        layout.addWidget(label)
        layout.addStretch()


class _EmptyList(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        palette()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        label = QLabel("No extension files found.")
        label.setStyleSheet(hint_label_style())
        layout.addWidget(label)
        layout.addStretch()


class _PlaceholderPane(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        palette()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()
        label = QLabel("No extension selected.")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(hint_label_style())
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


def _extensions_add_icon(*, size: int = 14, color: str = "#ffffff") -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(max(2.0, size / 7.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    center = size / 2.0
    arm = size * 0.28
    painter.drawLine(QPointF(center - arm, center), QPointF(center + arm, center))
    painter.drawLine(QPointF(center, center - arm), QPointF(center, center + arm))
    painter.end()
    return QIcon(pix)


def _summary_text(overview: ExtensionOverview, *, visible_count: int | None = None) -> str:
    count = len(overview.files)
    noun = "file" if count == 1 else "files"
    errors = overview.error_count
    disabled = sum(1 for file in overview.files if file.status == "Disabled")
    if visible_count is not None and visible_count != count:
        parts = [f"{visible_count} of {count} extension {noun}"]
    else:
        parts = [f"{count} extension {noun}"]
    parts.append(f"{errors} error(s)" if errors else "no errors")
    if disabled:
        parts.append(f"{disabled} disabled")
    return " · ".join(parts)


def _extension_search_text(file: ExtensionFileSummary) -> str:
    parts = [
        _extension_name(file),
        file.path,
        file.description,
        file.status,
        _list_status_text(file),
        _list_subtitle(file),
    ]
    return " ".join(part.lower() for part in parts if part)


def _filter_extensions(
    files: list[ExtensionFileSummary],
    *,
    query: str = "",
    status: str = "all",
) -> list[ExtensionFileSummary]:
    visible = list(files)
    if status == "enabled":
        visible = [file for file in visible if file.status != "Disabled"]
    elif status == "disabled":
        visible = [file for file in visible if file.status == "Disabled"]
    elif status == "review":
        visible = [file for file in visible if file.review_required]
    elif status == "errors":
        visible = [
            file for file in visible
            if file.errors or file.permission_violations
        ]
    query = query.strip().lower()
    if query:
        visible = [
            file for file in visible
            if query in _extension_search_text(file)
        ]
    return visible


def _candidate_search_text(candidate: ExtensionInstallCandidate) -> str:
    parts = [
        candidate.name,
        candidate.display_name,
        candidate.kind,
        candidate.description,
        str(candidate.source_path),
        _permissions_text(candidate.permissions),
    ]
    return " ".join(str(part).lower() for part in parts if part)


def _filter_install_candidates(
    candidates: list[ExtensionInstallCandidate],
    query: str,
) -> list[ExtensionInstallCandidate]:
    query = query.strip().lower()
    if not query:
        return list(candidates)
    return [
        candidate for candidate in candidates
        if query in _candidate_search_text(candidate)
    ]


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


def _queue_toggle(on_toggle, path: str, checked: bool) -> None:
    if on_toggle:
        QTimer.singleShot(0, lambda: on_toggle(path, bool(checked)))


def _extension_error_text(file: ExtensionFileSummary) -> str:
    if not file.errors:
        return "Extension did not load."
    shown = "\n\n".join(str(error) for error in file.errors[:2])
    hidden = len(file.errors) - 2
    if hidden > 0:
        shown += f"\n\n... and {hidden} more error(s)."
    return shown


def _extension_name(file: ExtensionFileSummary) -> str:
    if file.display_name:
        return file.display_name
    path = Path(file.path)
    if path.name == "extension.py" and path.parent.name:
        return path.parent.name
    return Path(file.path).name


def _candidate_display_name(candidate: ExtensionInstallCandidate) -> str:
    if candidate.display_name:
        return candidate.display_name
    return candidate.name


def _install_conflict_notice_text(
    candidate: ExtensionInstallCandidate,
    conflict: ExistingExtensionInstall,
    source: ExtensionInstallSource | None,
    install_scope: str,
) -> str:
    del install_scope
    if conflict.same_content:
        return "Already installed"
    return "\n".join([
        _install_conflict_headline(conflict),
        _install_conflict_timeline(candidate, conflict, source),
    ])


def _install_conflict_headline(conflict: ExistingExtensionInstall) -> str:
    if conflict.scope == "global":
        return "Already installed in global library"
    if conflict.scope == "local":
        return "Already installed in local library"
    return "Already installed elsewhere"


def _install_conflict_timeline(
    candidate: ExtensionInstallCandidate,
    conflict: ExistingExtensionInstall,
    source: ExtensionInstallSource | None,
) -> str:
    current = format_install_timestamp(conflict.modified_at)
    incoming_date = _incoming_commit_date(candidate, source)
    incoming_commit = _incoming_commit_hash(candidate, source)
    if incoming_commit:
        return f"current: {current}. incoming: {incoming_date} ({incoming_commit})"
    return f"current: {current}. incoming: {incoming_date}"


def _incoming_commit_date(
    candidate: ExtensionInstallCandidate,
    source: ExtensionInstallSource | None,
) -> str:
    commit_date = candidate.source_commit_date or (source.commit_date if source else "")
    if commit_date:
        formatted = format_commit_date(commit_date)
        if formatted:
            return formatted
    return "unknown"


def _incoming_commit_hash(
    candidate: ExtensionInstallCandidate,
    source: ExtensionInstallSource | None,
) -> str:
    commit = candidate.source_commit or (source.commit_hash if source else "")
    return commit[:12] if commit else ""


def _install_conflict_hint(
    conflict: ExistingExtensionInstall,
    install_scope: str,
) -> str:
    del conflict, install_scope
    return "Check to replace the installed extension."


def _candidate_disclosure(
    candidate: ExtensionInstallCandidate,
    *,
    conflict: ExistingExtensionInstall | None = None,
    source: ExtensionInstallSource | None = None,
    install_scope: str = "local",
) -> str:
    parts = [
        "Runs local Python when enabled.",
        "Installs disabled until reviewed.",
        f"Permissions: {_permissions_text(candidate.permissions)}",
    ]
    if conflict is not None:
        parts.append(_install_conflict_notice_text(
            candidate,
            conflict,
            source,
            install_scope,
        ))
        if not conflict.same_content:
            parts.append(_install_conflict_hint(conflict, install_scope))
        parts.append(f"Installed at {conflict.path}")
    if candidate.requirements.executables or candidate.requirements.python:
        reqs = []
        if candidate.requirements.executables:
            reqs.append("executables: " + ", ".join(candidate.requirements.executables))
        if candidate.requirements.python:
            reqs.append("python: " + ", ".join(candidate.requirements.python))
        parts.append("Requires " + "; ".join(reqs))
    if candidate.missing_requirements:
        parts.append("Missing: " + ", ".join(candidate.missing_requirements))
    return " ".join(parts)


def _list_status_text(file: ExtensionFileSummary) -> str:
    if file.permission_violations:
        return f"{file.status} · blocked"
    if file.review_required:
        return f"{file.status} · review"
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
    if file.review_required:
        parts.append("needs review")
    if not file.permissions.declared:
        parts.append("permissions undisclosed")
    if file.description:
        parts.append(file.description)
    return " · ".join(parts)


def _join_details(*parts: str) -> str:
    return " | ".join(part for part in parts if part)


def _risk_rows(file: ExtensionFileSummary) -> list[tuple[str, str]]:
    rows = [
        ("Review", "Reviewed" if file.reviewed else "Needs review"),
        ("Python", "Enabled extensions run local Python code in the AICHS process."),
    ]
    rows.extend((message, "") for message in file.risk_messages[1:])
    return rows


def _risk_tone(file: ExtensionFileSummary) -> str:
    return "danger" if file.review_required or not file.permissions.declared else ""


def _permission_rows(file: ExtensionFileSummary) -> list[tuple[str, str]]:
    return [
        ("Manifest", "Declared" if file.permissions.declared else "Undisclosed"),
        ("Allowed", _permissions_text(file.permissions)),
    ]


def _permissions_text(permissions) -> str:
    if not permissions.declared:
        return "undisclosed"
    names = permissions.enabled_names()
    return ", ".join(names) if names else "none"


def _observed_rows(file: ExtensionFileSummary) -> list[tuple[str, str]]:
    rows = []
    observed = [
        (len(file.tools), "tools"),
        (len(file.commands), "commands"),
        (len(file.contexts), "context"),
        (len(file.hooks), "hooks"),
        (len(file.badges) + len(file.panels), "ui"),
        (len(file.languages), "language"),
    ]
    for count, label in observed:
        rows.append((label, str(count)))
    return rows


def _requirements_rows(file: ExtensionFileSummary) -> list[tuple[str, str]]:
    rows = []
    if file.requirements.executables:
        rows.append(("Executables", ", ".join(file.requirements.executables)))
    if file.requirements.python:
        rows.append(("Python modules", ", ".join(file.requirements.python)))
    if file.missing_requirements:
        rows.append(("Missing", ", ".join(file.missing_requirements)))
    return rows


def _heading_style(tone: str = "") -> str:
    return extension_panel_heading_style(tone=tone)


def _list_scroll_style() -> str:
    p = palette()
    return transparent_scroll_area_style(
        border=f"0px solid {p['BORDER_SUBTLE']}; border-right:1px solid {p['BORDER_SUBTLE']}",
        include_viewport=False,
    )


def _detail_scroll_style() -> str:
    return transparent_scroll_area_style()


def _list_row_style(selected: bool, tone: str) -> str:
    return extension_list_row_style(selected=selected, tone=tone)


def _list_name_style() -> str:
    return extension_list_name_style()


def _list_meta_style(tone: str) -> str:
    return extension_list_meta_style(tone)


def _list_path_style() -> str:
    return hint_label_style()


def _header_style() -> str:
    return extension_header_frame_style()


def _detail_table_style(tone: str = "") -> str:
    return extension_detail_table_frame_style(tone=tone)


def _detail_name_style(tone: str = "") -> str:
    return extension_detail_name_style(tone=tone)


def _detail_value_style(tone: str = "") -> str:
    return extension_detail_value_style(tone=tone)


def _status_label_style(tone: str) -> str:
    return status_pill_style(
        tone=tone,
        border_radius=8,
        padding="2px 8px",
    )


def _status_tone(file: ExtensionFileSummary) -> str:
    if file.status == "Disabled":
        return "disabled"
    if file.errors or file.permission_violations:
        return "danger"
    return "success"


def _enabled_checkbox_style() -> str:
    return checkbox_style(font_pt=meta_font_pt(), indicator_px=14, spacing_px=6)


def _disabled_checkbox_style() -> str:
    p = palette()
    return (
        checkbox_style(
            font_pt=meta_font_pt(),
            indicator_px=14,
            spacing_px=6,
            text_color=p["TEXT_DIM"],
        )
        + f"QCheckBox:disabled {{ color: {p['TEXT_DIM']}; }}"
    )


def _install_conflict_notice_style() -> str:
    return (
        "color: #f87171; background-color: transparent; border: none; "
        "border-top: 1px solid #5c2a2a; padding-top: 6px; margin-top: 2px;"
    )


def _install_candidate_row_style(*, elsewhere: bool = False) -> str:
    p = palette()
    bg = "#221a14" if elsewhere else "transparent"
    return (
        f"QFrame#installCandidateRow {{ background-color: {bg}; border: none; "
        f"border-bottom: 1px solid {p['BORDER_SUBTLE']}; }}"
        f"QFrame#installCandidateRow QLabel {{ background-color: transparent; border: none; }}"
    )


def _install_toolbar_button_style() -> str:
    p = palette()
    fs = meta_font_pt()
    return (
        f"QPushButton {{ background: transparent; border: none; color: {p['LINK']}; "
        f"font-size: {fs}pt; padding: 4px 8px; }}"
        f"QPushButton:hover {{ color: {p['TEXT']}; }}"
    )


def _filter_field_style() -> str:
    return form_field_style(selector="QLineEdit", padding="7px 10px")


def _toolbar_combo_style() -> str:
    p = palette()
    fs = meta_font_pt()
    return compact_combo_box_style(
        font_pt=fs,
        padding="5px 28px 5px 8px",
        border_radius=6,
        background=p["BG3"],
        popup_background=p["BG3"],
        popup_item_padding="5px 8px",
    )


def _install_scope_combo_style() -> str:
    return _toolbar_combo_style()

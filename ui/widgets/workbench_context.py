from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re

from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMenu
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from services.language_features import Diagnostic, LanguageFeatureStatus
from services.language_snapshot import LanguageStatusSnapshot, build_language_status_snapshot
from ui.theme import (
    ACCENT,
    chat_font_pt,
    compact_combo_box_style,
    contained_list_style,
    context_panel_title_button_style,
    hint_label_style,
    icon_button_style,
    meta_font_pt,
    palette,
    section_label_style,
    secondary_button_style,
)


@dataclass(frozen=True)
class _RunLogEntry:
    kind: str
    target: str
    raw: str
    conversation_id: str = ""
    status: str = "Logged"
    detail: str = ""
    timestamp: str = ""

    @property
    def summary(self) -> str:
        return self.kind if not self.target else f"{self.kind} - {self.target}"

    @property
    def details(self) -> str:
        lines = [
            f"Type: {self.kind}",
            f"Target: {self.target or '(none)'}",
            f"Status: {self.status}",
        ]
        if self.timestamp:
            lines.append(f"When: {self.timestamp}")
        if self.conversation_id:
            lines.append(f"Conversation: {self.conversation_id}")
        if self.detail and self.detail != self.target:
            lines.extend(["", self.detail])
        lines.extend(["", "Original:", self.raw])
        return "\n".join(lines)


class _LanguageStatusThread(QThread):
    done = pyqtSignal(int, object)

    def __init__(self, generation: int, context: dict, parent=None):
        super().__init__(parent)
        self._generation = generation
        self._context = dict(context)

    def run(self):
        self.done.emit(self._generation, build_language_status_snapshot(self._context))


class WorkbenchContextPanel(QWidget):
    collapse_requested = pyqtSignal()
    language_available_changed = pyqtSignal(bool)
    language_refresh_requested = pyqtSignal()
    language_format_requested = pyqtSignal()
    language_fix_safe_requested = pyqtSignal()
    language_chat_file_requested = pyqtSignal()
    language_quick_fix_requested = pyqtSignal(object)
    language_chat_fix_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tool_items: list[_RunLogEntry] = []
        self._icon_cache: dict[tuple[str, str], QIcon] = {}
        self._current_conversation_id = ""
        self._scope = "chat"
        self._active_panel = "run_log"
        self._language_available = False
        self._language_context: dict = {}
        self._language_statuses: list[LanguageFeatureStatus] = []
        self._language_errors: list[str] = []
        self._language_diagnostics_data: list[Diagnostic] = []
        self._language_status_generation = 0
        self._language_status_key: tuple[str, str, bool] = ("", "", False)
        self._language_status_threads: list[_LanguageStatusThread] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._title = QPushButton("Run Log")
        self._title.setObjectName("contextPanelTitleButton")
        self._title.setToolTip("Collapse run log")
        self._title.clicked.connect(self.collapse_requested.emit)
        header.addWidget(self._title, 1)

        self._collapse_btn = QPushButton(">")
        self._collapse_btn.setAccessibleName("Collapse run log")
        self._collapse_btn.setToolTip("Collapse run log")
        self._collapse_btn.setFixedSize(28, 28)
        self._collapse_btn.clicked.connect(self.collapse_requested.emit)
        header.addWidget(self._collapse_btn)
        layout.addLayout(header)

        self._pages = QStackedWidget()
        layout.addWidget(self._pages, 1)

        self._run_log_widget = QWidget()
        run_layout = QVBoxLayout(self._run_log_widget)
        run_layout.setContentsMargins(0, 0, 0, 0)
        run_layout.setSpacing(10)

        scope_row = QHBoxLayout()
        scope_row.setContentsMargins(0, 0, 0, 0)
        scope_row.setSpacing(8)
        scope_row.addWidget(_section_label("Recent Run"), 1)
        self._scope_combo = QComboBox()
        self._scope_combo.setObjectName("runLogScope")
        self._scope_combo.addItem("This chat", "chat")
        self._scope_combo.addItem("Workspace", "workspace")
        self._scope_combo.setFixedWidth(104)
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_row.addWidget(self._scope_combo, 0)
        run_layout.addLayout(scope_row)

        self._tool_activity = QListWidget()
        self._tool_activity.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tool_activity.customContextMenuRequested.connect(self._show_activity_menu)
        run_layout.addWidget(self._tool_activity, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setToolTip("Copy selected row")
        self._copy_btn.clicked.connect(self.copy_selected_activity)
        actions.addWidget(self._copy_btn)

        self._copy_details_btn = QPushButton("Details")
        self._copy_details_btn.setToolTip("Copy selected row details")
        self._copy_details_btn.clicked.connect(self.copy_selected_activity_details)
        actions.addWidget(self._copy_details_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setToolTip("Clear run log")
        self._clear_btn.clicked.connect(self.clear_activity)
        actions.addWidget(self._clear_btn)
        run_layout.addLayout(actions)
        self._pages.addWidget(self._run_log_widget)

        self._language_widget = QWidget()
        language_layout = QVBoxLayout(self._language_widget)
        language_layout.setContentsMargins(0, 0, 0, 0)
        language_layout.setSpacing(8)
        language_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._language_body = QWidget()
        language_body_layout = QVBoxLayout(self._language_body)
        language_body_layout.setContentsMargins(0, 0, 0, 0)
        language_body_layout.setSpacing(8)
        language_body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        language_identity = QHBoxLayout()
        language_identity.setContentsMargins(0, 0, 0, 0)
        language_identity.setSpacing(8)
        language_identity.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._language_icon = QLabel("--")
        self._language_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._language_icon.setFixedSize(28, 24)
        language_identity.addWidget(self._language_icon, 0, Qt.AlignmentFlag.AlignTop)

        identity_text = QVBoxLayout()
        identity_text.setContentsMargins(0, 0, 0, 0)
        identity_text.setSpacing(2)
        self._language_type = QLabel("No language")
        self._language_type.setWordWrap(True)
        identity_text.addWidget(self._language_type)

        self._language_file = QLabel("No supported file")
        self._language_file.setWordWrap(True)
        identity_text.addWidget(self._language_file)
        language_identity.addLayout(identity_text, 1)
        language_body_layout.addLayout(language_identity)

        self._language_summary = QLabel("")
        self._language_summary.setWordWrap(True)
        language_body_layout.addWidget(self._language_summary)

        self._language_actions_label = _section_label("Actions")
        language_body_layout.addWidget(self._language_actions_label)
        file_actions_top = QHBoxLayout()
        file_actions_top.setContentsMargins(0, 0, 0, 0)
        file_actions_top.setSpacing(6)
        file_actions_top.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._language_format_btn = QPushButton("Format")
        self._language_format_btn.setToolTip("Format the active file")
        self._language_format_btn.clicked.connect(self.language_format_requested.emit)
        file_actions_top.addWidget(self._language_format_btn)

        self._language_fix_safe_btn = QPushButton("Safe Fix")
        self._language_fix_safe_btn.setToolTip("Apply or choose a safe fix for current problems")
        self._language_fix_safe_btn.clicked.connect(self.language_fix_safe_requested.emit)
        file_actions_top.addWidget(self._language_fix_safe_btn)
        file_actions_top.addStretch(1)
        language_body_layout.addLayout(file_actions_top)

        file_actions_bottom = QHBoxLayout()
        file_actions_bottom.setContentsMargins(0, 0, 0, 0)
        file_actions_bottom.setSpacing(6)
        file_actions_bottom.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._language_ask_file_btn = QPushButton("Ask File")
        self._language_ask_file_btn.setToolTip("Ask chat about the active file")
        self._language_ask_file_btn.clicked.connect(self.language_chat_file_requested.emit)
        file_actions_bottom.addWidget(self._language_ask_file_btn)

        self._language_refresh_btn = QPushButton("Refresh")
        self._language_refresh_btn.setToolTip("Refresh language diagnostics")
        self._language_refresh_btn.clicked.connect(self.language_refresh_requested.emit)
        file_actions_bottom.addWidget(self._language_refresh_btn)
        file_actions_bottom.addStretch(1)
        language_body_layout.addLayout(file_actions_bottom)

        self._language_problems_label = _section_label("Problems")
        language_body_layout.addWidget(self._language_problems_label)
        self._language_diagnostics = QListWidget()
        self._language_diagnostics.setMinimumHeight(92)
        self._language_diagnostics.setMaximumHeight(132)
        language_body_layout.addWidget(self._language_diagnostics)

        language_actions = QHBoxLayout()
        language_actions.setContentsMargins(0, 0, 0, 0)
        language_actions.setSpacing(6)
        language_actions.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._language_quick_fix_btn = QPushButton("Quick Fix")
        self._language_quick_fix_btn.setToolTip("Show code actions for the selected problem")
        self._language_quick_fix_btn.clicked.connect(self._request_language_quick_fix)
        language_actions.addWidget(self._language_quick_fix_btn)

        self._language_ask_btn = QPushButton("Ask Fix")
        self._language_ask_btn.setToolTip("Ask chat to fix the selected problem")
        self._language_ask_btn.clicked.connect(self._request_language_chat_fix)
        language_actions.addWidget(self._language_ask_btn)
        language_actions.addStretch(1)
        language_body_layout.addLayout(language_actions)
        language_body_layout.addStretch(1)
        language_layout.addWidget(self._language_body, 0, Qt.AlignmentFlag.AlignTop)
        self._pages.addWidget(self._language_widget)

        self.apply_appearance()
        self._sync_empty_state()
        self.set_active_panel("run_log")

    def set_language_context(self, context: dict):
        self._language_context = dict(context or {})
        self._language_diagnostics_data = list(self._language_context.get("diagnostics") or [])
        self._language_status_generation += 1
        generation = self._language_status_generation
        key = _language_context_key(self._language_context)
        if not _can_load_language_status(self._language_context):
            self._language_status_key = key
            self._language_statuses = []
            self._language_errors = []
            self._set_language_available(False)
            self._render_language()
            return

        if key != self._language_status_key:
            self._language_status_key = key
            self._language_statuses = []
            self._language_errors = []
            self._set_language_available(False)
        self._render_language()
        self._start_language_status_refresh(generation, self._language_context)

    def set_active_panel(self, panel: str):
        self._active_panel = panel if panel in {"run_log", "language"} else "run_log"
        is_language = self._active_panel == "language"
        self._pages.setCurrentIndex(1 if is_language else 0)
        title = "Language" if is_language else "Run Log"
        self._title.setText(title)
        self._title.setToolTip(f"Collapse {title.lower()}")
        self._collapse_btn.setAccessibleName(f"Collapse {title.lower()}")
        self._collapse_btn.setToolTip(f"Collapse {title.lower()}")

    def _start_language_status_refresh(self, generation: int, context: dict):
        thread = _LanguageStatusThread(generation, context, self)
        self._language_status_threads.append(thread)
        thread.done.connect(self._apply_language_status_snapshot)
        thread.finished.connect(lambda t=thread: self._release_language_status_thread(t))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _apply_language_status_snapshot(self, generation: int, snapshot: LanguageStatusSnapshot):
        if generation != self._language_status_generation:
            return
        if (snapshot.repo_root, snapshot.path, snapshot.is_text) != self._language_status_key:
            return
        self._language_statuses = list(snapshot.statuses)
        self._language_errors = list(snapshot.errors)
        self._set_language_available(bool(self._language_statuses))
        self._render_language()

    def _release_language_status_thread(self, thread: _LanguageStatusThread):
        if thread in self._language_status_threads:
            self._language_status_threads.remove(thread)

    def _set_language_available(self, available: bool):
        if available == self._language_available:
            return
        self._language_available = available
        self.language_available_changed.emit(available)

    def _render_language(self):
        self._language_diagnostics.clear()
        if not self._language_statuses:
            path = str(self._language_context.get("path") or "")
            repo_root = str(self._language_context.get("repo_root") or "")
            self._language_icon.setText("--")
            self._language_type.setText("No language")
            self._language_file.setText(
                _display_path(path, repo_root)
                if path and self._language_context.get("is_text")
                else "No supported file"
            )
            self._language_summary.setText(
                "No language support for this file."
                if path and self._language_context.get("is_text")
                else "Open a file with registered language support."
            )
            self._language_summary.setVisible(True)
            self._sync_language_actions()
            return

        path = str(self._language_context.get("path") or "")
        language_name = _language_name(self._language_statuses)
        self._language_icon.setText(_language_icon_text(language_name))
        self._language_type.setText(language_name)
        self._language_file.setText(_display_path(path, str(self._language_context.get("repo_root") or "")))
        summary = _language_summary(
            self._language_statuses,
            self._language_diagnostics_data,
            self._language_errors,
        )
        self._language_summary.setText(summary)
        self._language_summary.setVisible(bool(summary))

        if self._language_diagnostics_data:
            for diagnostic in self._language_diagnostics_data:
                item = QListWidgetItem(_diagnostic_row(diagnostic))
                item.setData(Qt.ItemDataRole.UserRole, diagnostic)
                item.setToolTip(_diagnostic_tooltip(diagnostic))
                color = _diagnostic_color(diagnostic.severity)
                if color:
                    item.setForeground(QColor(color))
                self._language_diagnostics.addItem(item)
        self._sync_language_actions()

    def _selected_language_diagnostics(self) -> list[Diagnostic]:
        item = self._language_diagnostics.currentItem()
        if item is not None:
            diagnostic = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(diagnostic, Diagnostic):
                return [diagnostic]
        return list(self._language_diagnostics_data)

    def _request_language_quick_fix(self):
        diagnostics = self._selected_language_diagnostics()
        if diagnostics:
            self.language_quick_fix_requested.emit(diagnostics)

    def _request_language_chat_fix(self):
        diagnostics = self._selected_language_diagnostics()
        if diagnostics:
            self.language_chat_fix_requested.emit(diagnostics)

    def _sync_language_actions(self):
        supported = bool(self._language_statuses)
        has_diagnostics = bool(self._language_diagnostics_data)
        editable = bool(self._language_context.get("editable", True))
        has_diagnostic_provider = self._has_ready_language_feature("diagnostics")
        has_formatter = self._has_ready_language_feature("format_document")
        has_code_actions = self._has_ready_language_feature("code_actions")

        self._language_actions_label.setVisible(supported)
        self._language_format_btn.setVisible(has_formatter)
        self._language_format_btn.setEnabled(has_formatter and editable)
        self._language_fix_safe_btn.setVisible(has_code_actions and has_diagnostics)
        self._language_fix_safe_btn.setEnabled(has_code_actions and has_diagnostics and editable)
        self._language_ask_file_btn.setVisible(supported)
        self._language_ask_file_btn.setEnabled(supported)
        self._language_refresh_btn.setVisible(has_diagnostic_provider)
        self._language_refresh_btn.setEnabled(has_diagnostic_provider)

        self._language_problems_label.setVisible(has_diagnostics)
        self._language_diagnostics.setVisible(has_diagnostics)
        self._language_quick_fix_btn.setVisible(has_code_actions and has_diagnostics)
        self._language_quick_fix_btn.setEnabled(has_code_actions and has_diagnostics and editable)
        self._language_ask_btn.setVisible(has_diagnostics)
        self._language_ask_btn.setEnabled(supported and has_diagnostics)

    def _has_ready_language_feature(self, feature: str) -> bool:
        return any(status.ready and feature in status.features for status in self._language_statuses)

    def add_tool_activity(self, text: str, conversation_id: str = ""):
        compact = " ".join(str(text or "").split())
        if not compact:
            return
        conv_id = str(conversation_id or self._current_conversation_id or "").strip()
        self._tool_items.insert(0, _parse_activity(compact, conv_id))
        self._tool_items = self._tool_items[:80]
        self._render_activity()

    def set_current_conversation(self, conversation_id: str):
        self._current_conversation_id = str(conversation_id or "").strip()
        self._render_activity()

    def copy_selected_activity(self):
        entry = self._selected_entry()
        item = self._tool_activity.currentItem()
        if entry is None and (item is None or not item.flags() & Qt.ItemFlag.ItemIsSelectable):
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(entry.summary if entry is not None else item.text())

    def copy_selected_activity_details(self):
        entry = self._selected_entry()
        if entry is None:
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(entry.details)

    def clear_activity(self):
        if self._scope == "chat" and self._current_conversation_id:
            self._tool_items = [
                entry for entry in self._tool_items
                if entry.conversation_id != self._current_conversation_id
            ]
        else:
            self._tool_items.clear()
        self._render_activity()

    def _render_activity(self):
        self._tool_activity.clear()
        entries = self._visible_entries()
        for entry in entries:
            item = QListWidgetItem(entry.summary)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setIcon(self._entry_icon(entry))
            if entry.status == "Error":
                item.setForeground(QColor("#d94b4b"))
            item.setToolTip(entry.details)
            self._tool_activity.addItem(item)
        self._sync_empty_state()

    def _sync_empty_state(self):
        if self._tool_activity.count():
            self._copy_btn.setEnabled(True)
            self._copy_details_btn.setEnabled(True)
            self._clear_btn.setEnabled(True)
            self._clear_btn.setToolTip(
                "Clear this chat log" if self._scope == "chat" else "Clear workspace run log"
            )
            return
        text = "No run log for this chat" if self._scope == "chat" else "No run log yet"
        item = QListWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._tool_activity.addItem(item)
        self._copy_btn.setEnabled(False)
        self._copy_details_btn.setEnabled(False)
        self._clear_btn.setEnabled(bool(self._tool_items))
        self._clear_btn.setToolTip(
            "Clear this chat log" if self._scope == "chat" else "Clear workspace run log"
        )

    def _selected_entry(self) -> _RunLogEntry | None:
        item = self._tool_activity.currentItem()
        if item is None:
            return None
        entry = item.data(Qt.ItemDataRole.UserRole)
        return entry if isinstance(entry, _RunLogEntry) else None

    def _show_activity_menu(self, pos):
        item = self._tool_activity.itemAt(pos)
        entry = None
        if item is not None:
            self._tool_activity.setCurrentItem(item)
            entry = self._selected_entry()

        menu = QMenu(self)
        copy_row = menu.addAction("Copy row")
        copy_details = menu.addAction("Copy details")
        copy_row.setEnabled(entry is not None)
        copy_details.setEnabled(entry is not None)
        menu.addSeparator()
        label = "Clear this chat log" if self._scope == "chat" else "Clear workspace run log"
        clear_log = menu.addAction(label)
        clear_log.setEnabled(bool(self._tool_items))

        chosen = menu.exec(self._tool_activity.mapToGlobal(pos))
        if chosen == copy_row:
            self.copy_selected_activity()
        elif chosen == copy_details:
            self.copy_selected_activity_details()
        elif chosen == clear_log:
            self.clear_activity()

    def _visible_entries(self) -> list[_RunLogEntry]:
        if self._scope != "chat":
            return self._tool_items[:12]
        if not self._current_conversation_id:
            return []
        return [
            entry for entry in self._tool_items
            if entry.conversation_id == self._current_conversation_id
        ][:12]

    def _on_scope_changed(self):
        scope = str(self._scope_combo.currentData() or "chat")
        self._scope = scope
        self._render_activity()

    def apply_appearance(self):
        p = palette()
        self._icon_cache.clear()
        self.setStyleSheet(
            f"background-color:{p['BG2']}; color:{p['TEXT']};"
        )
        list_style = contained_list_style(item_padding="4px 6px", item_margin="0px")
        self._tool_activity.setStyleSheet(list_style)
        self._language_diagnostics.setStyleSheet(list_style)
        self._collapse_btn.setStyleSheet(icon_button_style(28))
        action_style = secondary_button_style(
            padding="5px 8px",
            font_size=meta_font_pt(),
            text_color=p["TEXT_DIM"],
            border_color=p["BORDER_SUBTLE"],
        )
        self._copy_btn.setStyleSheet(action_style)
        self._copy_details_btn.setStyleSheet(action_style)
        self._clear_btn.setStyleSheet(action_style)
        self._language_format_btn.setStyleSheet(action_style)
        self._language_fix_safe_btn.setStyleSheet(action_style)
        self._language_ask_file_btn.setStyleSheet(action_style)
        self._language_refresh_btn.setStyleSheet(action_style)
        self._language_quick_fix_btn.setStyleSheet(action_style)
        self._language_ask_btn.setStyleSheet(action_style)
        self._scope_combo.setStyleSheet(
            compact_combo_box_style(
                selector="QComboBox#runLogScope",
                font_pt=meta_font_pt(),
                padding="4px 8px",
                border_radius=6,
                drop_down_width=18,
                border_color=p["BORDER_SUBTLE"],
                popup_background=p["BG3"],
                popup_item_padding="5px 8px",
            )
        )
        language_type_style = (
            f"color:{p['TEXT']}; font-size:{max(13, chat_font_pt())}px;"
            "font-weight:600; padding-bottom:2px;"
        )
        self._language_type.setStyleSheet(language_type_style)
        self._language_file.setStyleSheet(hint_label_style(padding="0 0 2px 0"))
        self._language_icon.setStyleSheet(
            f"background:{p['BG3']}; color:{ACCENT};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px;"
            f"font-size:{meta_font_pt()}px; font-weight:700;"
        )
        self._language_summary.setStyleSheet(hint_label_style(padding="0 0 2px 0"))
        self._title.setStyleSheet(context_panel_title_button_style())
        for label in self.findChildren(QLabel, "contextPanelSection"):
            label.setStyleSheet(
                section_label_style(
                    text_color=ACCENT,
                    font_weight="700",
                    padding="4px 0 0 0",
                )
            )
        if self._tool_items:
            self._render_activity()
        self._render_language()

    def _entry_icon(self, entry: _RunLogEntry) -> QIcon:
        p = palette()
        key = (entry.kind, p["BG2"])
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        icon = _run_log_icon(entry.kind)
        self._icon_cache[key] = icon
        return icon

    def shutdown(self):
        self._language_status_generation += 1
        for thread in list(self._language_status_threads):
            if thread.isRunning():
                thread.wait(3000)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("contextPanelSection")
    return label


def _language_context_key(context: dict) -> tuple[str, str, bool]:
    return (
        str(context.get("repo_root") or ""),
        str(context.get("path") or ""),
        bool(context.get("is_text")),
    )


def _can_load_language_status(context: dict) -> bool:
    repo_root, path, is_text = _language_context_key(context)
    return bool(repo_root and path and is_text)


def _title_button_style(p: dict) -> str:
    return context_panel_title_button_style()


def _relative_path(repo_root: str, path: str) -> str:
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except (OSError, ValueError):
        return Path(path).name


def _display_path(path: str, repo_root: str) -> str:
    if not path:
        return "No file"
    return _relative_path(repo_root, path) if repo_root else os.path.basename(path)


def _language_name(statuses: list[LanguageFeatureStatus]) -> str:
    names = []
    for status in statuses:
        label = str(status.language or "").strip()
        if label and label.casefold() not in {item.casefold() for item in names}:
            names.append(label)
    if not names:
        return "Language"
    return " + ".join(_display_language_name(name) for name in names[:2])


def _display_language_name(name: str) -> str:
    known = {
        "python": "Python",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "markdown": "Markdown",
        "json": "JSON",
        "yaml": "YAML",
        "html": "HTML",
        "css": "CSS",
    }
    return known.get(name.casefold(), name[:1].upper() + name[1:])


def _language_icon_text(name: str) -> str:
    compact = "".join(ch for ch in name if ch.isalnum())
    if not compact:
        return "--"
    known = {
        "python": "Py",
        "javascript": "JS",
        "typescript": "TS",
        "markdown": "Md",
        "json": "{}",
        "yaml": "Y",
        "html": "<>",
        "css": "#",
    }
    return known.get(compact.casefold(), compact[:2].title())


def _language_summary(
    statuses: list[LanguageFeatureStatus],
    diagnostics: list[Diagnostic],
    errors: list[str],
) -> str:
    missing = sorted({item for status in statuses for item in status.missing_requirements})
    parts = []
    if missing:
        parts.append("Missing: " + ", ".join(missing))
    if errors:
        parts.append(f"{len(errors)} extension error{'s' if len(errors) != 1 else ''}")
    return "\n".join(parts)


def _diagnostic_row(diagnostic: Diagnostic) -> str:
    location = f"{max(1, diagnostic.line)}:{max(1, diagnostic.column + 1)}"
    label = " ".join(part for part in (diagnostic.source, diagnostic.code) if part)
    prefix = f"{location} {diagnostic.severity or 'info'}"
    if label:
        prefix = f"{prefix} [{label}]"
    return f"{prefix} - {diagnostic.message}"


def _diagnostic_tooltip(diagnostic: Diagnostic) -> str:
    lines = [
        f"Line: {max(1, diagnostic.line)}",
        f"Column: {max(1, diagnostic.column + 1)}",
        f"Severity: {diagnostic.severity or 'info'}",
    ]
    if diagnostic.source:
        lines.append(f"Source: {diagnostic.source}")
    if diagnostic.code:
        lines.append(f"Code: {diagnostic.code}")
    lines.extend(["", diagnostic.message])
    return "\n".join(lines)


def _diagnostic_color(severity: str) -> str:
    return {
        "error": "#ef4444",
        "warning": "#f59e0b",
        "hint": "#60a5fa",
        "info": ACCENT,
    }.get(str(severity or "info").lower(), ACCENT)


def _parse_activity(text: str, conversation_id: str = "") -> _RunLogEntry:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    match = re.fullmatch(r"Reading file '(.+)'(?: \((.+)\))?", text)
    if match:
        target = match.group(1)
        detail = f"Path: {target}"
        if match.group(2):
            detail = f"{detail}\nNote: {match.group(2)}"
        return _RunLogEntry(
            "Read file", target, text, conversation_id=conversation_id,
            detail=detail, timestamp=timestamp
        )

    match = re.fullmatch(r"Searching files for '(.+)' in '(.+)'", text)
    if match:
        pattern, directory = match.groups()
        return _RunLogEntry(
            "Search files",
            pattern,
            text,
            conversation_id=conversation_id,
            detail=f"Pattern: {pattern}\nDirectory: {directory}",
            timestamp=timestamp,
        )

    match = re.fullmatch(r"Searching files in '(.+)'", text)
    if match:
        directory = match.group(1)
        return _RunLogEntry(
            "Search files",
            directory,
            text,
            conversation_id=conversation_id,
            detail=f"Directory: {directory}",
            timestamp=timestamp,
        )

    match = re.fullmatch(r"Searching project chat history(?: for '(.+)')?", text)
    if match:
        query = match.group(1) or "all chats"
        return _RunLogEntry(
            "Search chats",
            query,
            text,
            conversation_id=conversation_id,
            detail=f"Query: {query}",
            timestamp=timestamp,
        )

    if text.startswith("Running command: "):
        command = text[len("Running command: "):].strip()
        return _RunLogEntry(
            "Run command",
            command or "(empty command)",
            text,
            conversation_id=conversation_id,
            detail=f"Command: {command or '(empty command)'}",
            timestamp=timestamp,
        )

    if text == "Running command":
        return _RunLogEntry(
            "Run command", "", text, conversation_id=conversation_id, timestamp=timestamp
        )

    if text.startswith("Tool error: "):
        message = text[len("Tool error: "):].strip()
        return _RunLogEntry(
            "Tool error",
            message,
            text,
            conversation_id=conversation_id,
            status="Error",
            detail=f"Error: {message}",
            timestamp=timestamp,
        )

    return _RunLogEntry(
        "Tool notice", text, text, conversation_id=conversation_id, timestamp=timestamp
    )


def _run_log_icon(kind: str) -> QIcon:
    p = palette()
    color, symbol = _icon_style(kind)
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(QPen(QColor(p["BORDER_SUBTLE"]), 1))
    painter.drawRoundedRect(1, 1, 16, 16, 5, 5)
    painter.setPen(QColor("white"))
    font = QFont()
    font.setBold(True)
    font.setPointSize(8)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.end()
    return QIcon(pixmap)


def _icon_style(kind: str) -> tuple[str, str]:
    if kind == "Read file":
        return "#4f8cff", "R"
    if kind == "Search files":
        return "#2aa876", "S"
    if kind == "Search chats":
        return "#7d6bff", "C"
    if kind == "Run command":
        return "#c27a24", ">"
    if kind == "Tool error":
        return "#d94b4b", "!"
    return ACCENT, "i"

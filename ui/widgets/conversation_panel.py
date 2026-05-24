from datetime import datetime, date

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QLabel, QLineEdit, QMenu, QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QAction, QFontMetrics

from storage.repository import ConversationStore
from services.export import export_conversation_file
from ui.theme import (
    palette, meta_font_pt, chat_font_pt, app_font,
    new_chat_button_style, search_field_style, conversation_list_style,
)


class TitleLabel(QLabel):
    double_clicked = pyqtSignal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._elide()

    def setText(self, text: str):
        self._full_text = text
        self._elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        if self.width() <= 0:
            super().setText(self._full_text)
            return
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.width(),
        )
        super().setText(elided)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class RenameEdit(QLineEdit):
    escape_pressed = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
        else:
            super().keyPressEvent(event)


class ConversationItem(QWidget):
    delete_requested = pyqtSignal()
    rename_requested = pyqtSignal(str)
    pin_requested    = pyqtSignal()
    export_requested = pyqtSignal()
    edit_started     = pyqtSignal(object)

    def __init__(self, title: str, date_str: str, pinned: bool = False, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._title = title
        self._pinned = pinned

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 4, 8)
        row.setSpacing(4)

        col = QVBoxLayout()
        col.setSpacing(2)
        col.setContentsMargins(0, 0, 0, 0)

        self.title_lbl = TitleLabel(title)
        self.title_lbl.double_clicked.connect(self._start_edit)

        self.title_edit = RenameEdit(title)
        self.title_edit.hide()
        self.title_edit.returnPressed.connect(self._commit_edit)
        self.title_edit.escape_pressed.connect(self._cancel_edit)

        self.date_lbl = QLabel(date_str)

        col.addWidget(self.title_lbl)
        col.addWidget(self.title_edit)
        col.addWidget(self.date_lbl)
        row.addLayout(col, 1)

        self.pin_btn = QLabel("★" if pinned else "☆")
        self.pin_btn.setFixedSize(20, 20)
        self.pin_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pin_btn.setToolTip("Unpin" if pinned else "Pin")
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.mousePressEvent = lambda e: self.pin_requested.emit()
        row.addWidget(self.pin_btn)

        self.del_btn = QLabel("✕")
        self.del_btn.setFixedSize(20, 20)
        self.del_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.mousePressEvent = lambda e: self.delete_requested.emit()
        row.addWidget(self.del_btn)

        self.apply_appearance()

    def apply_appearance(self):
        p = palette()
        fs = chat_font_pt()
        meta = meta_font_pt()
        pin_color = "#f5c518" if self._pinned else p["TEXT_DIM"]
        self.title_lbl.setStyleSheet(
            f"font-size:{fs}px; color:{p['TEXT']}; background:transparent;"
        )
        self.title_edit.setStyleSheet(
            f"font-size:{fs}px; color:{p['TEXT']}; background:{p['BG3']};"
            f"border:1px solid {p['BORDER']}; padding:1px 4px;"
        )
        self.date_lbl.setStyleSheet(
            f"font-size:{meta}px; color:{p['TEXT_DIM']}; background:transparent;"
        )
        icon_fs = max(11, meta)
        self.pin_btn.setStyleSheet(
            f"QLabel {{ color:{pin_color}; background:transparent; font-size:{icon_fs}px; }}"
            "QLabel:hover { color:#f5c518; }"
        )
        self.del_btn.setStyleSheet(
            f"QLabel {{ color:{p['TEXT_DIM']}; background:transparent; font-size:{icon_fs}px; }}"
            "QLabel:hover { color:#ff5555; }"
        )
        self.title_lbl._elide()

    def _start_edit(self):
        self.edit_started.emit(self)
        self.title_lbl.hide()
        self.title_edit.setText(self._title)
        self.title_edit.show()
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def _commit_edit(self):
        new_title = self.title_edit.text().strip() or "Untitled"
        self.title_edit.hide()
        self.title_lbl.setText(new_title)
        self.title_lbl.show()
        if new_title != self._title:
            self._title = new_title
            self.rename_requested.emit(new_title)

    def _cancel_edit(self):
        self.title_edit.hide()
        self.title_lbl.show()

    def cancel_edit(self):
        if self.title_edit.isVisible():
            self._cancel_edit()

    def _context_menu(self, pos):
        menu = QMenu(self)
        rename = QAction("Rename", self)
        rename.triggered.connect(self._start_edit)
        menu.addAction(rename)
        export = QAction("Export as Markdown…", self)
        export.triggered.connect(self.export_requested.emit)
        menu.addAction(export)
        menu.exec(self.mapToGlobal(pos))


def _conversation_item_height() -> int:
    meta = meta_font_pt()
    title_fm = QFontMetrics(app_font())
    date_font = app_font()
    date_font.setPointSize(meta)
    date_fm = QFontMetrics(date_font)
    return 8 + 8 + title_fm.lineSpacing() + 2 + date_fm.lineSpacing() + 4


class ConversationPanel(QWidget):
    selected = pyqtSignal(str)
    new_chat = pyqtSignal()
    renamed  = pyqtSignal(str, str)  # conv_id, title

    def __init__(self, store: ConversationStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._editing_item = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._new_btn = QPushButton("＋  New Chat")
        self._new_btn.clicked.connect(self.new_chat)
        root.addWidget(self._new_btn)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search conversations…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        root.addWidget(self.search)

        self.no_results = QLabel("No results")
        self.no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_results.hide()

        self.list = QListWidget()
        self.list.itemClicked.connect(
            lambda item: self.selected.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        root.addWidget(self.list)
        root.addWidget(self.no_results)

        self._apply_styles()
        self.refresh()

    def _apply_styles(self):
        p = palette()
        fs = chat_font_pt()
        self._new_btn.setStyleSheet(new_chat_button_style())
        self.search.setStyleSheet(search_field_style())
        self.no_results.setStyleSheet(
            f"color:{p['TEXT_DIM']}; font-size:{fs}px; padding:24px; background:{p['BG2']};"
        )
        self.list.setStyleSheet(conversation_list_style())

    def apply_appearance(self):
        self._apply_styles()
        for i in range(self.list.count()):
            item = self.list.item(i)
            widget = self.list.itemWidget(item)
            if isinstance(widget, ConversationItem):
                widget.apply_appearance()

    def _apply_filter(self):
        self.refresh()

    def refresh(self):
        current_path = None
        if self.list.currentItem():
            current_path = self.list.currentItem().data(Qt.ItemDataRole.UserRole)

        query = self.search.text().strip()
        self.list.clear()
        today = date.today()
        visible = 0

        for path, data in self.store.list_all():
            if query and not self.store.matches_search(path, data, query):
                continue

            title = data.get("title", "Untitled")
            updated = data.get("updated_at", "")
            try:
                dt = datetime.fromisoformat(updated)
                date_str = dt.strftime("%H:%M") if dt.date() == today else dt.strftime("%b %d")
            except Exception:
                date_str = ""

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setSizeHint(QSize(0, _conversation_item_height()))
            self.list.addItem(item)

            widget = ConversationItem(title, date_str, pinned=data.get("pinned", False))
            widget.delete_requested.connect(lambda p=str(path): self._delete(p))
            widget.rename_requested.connect(lambda t, p=str(path): self._rename(p, t))
            widget.pin_requested.connect(lambda p=str(path): self._toggle_pin(p))
            widget.export_requested.connect(lambda p=str(path): self._export(p))
            widget.edit_started.connect(self._on_edit_started)
            self.list.setItemWidget(item, widget)

            if str(path) == current_path:
                self.list.setCurrentItem(item)

            visible += 1

        show_empty = bool(query) and visible == 0
        self.no_results.setVisible(show_empty)
        self.list.setVisible(not show_empty)

    def _delete(self, path: str):
        self.store.delete(path)
        self.refresh()

    def _on_edit_started(self, item: ConversationItem):
        if self._editing_item and self._editing_item is not item:
            self._editing_item.cancel_edit()
        self._editing_item = item

    def _rename(self, path: str, title: str):
        conv_id = self.store.rename(path, title)
        self.renamed.emit(conv_id, title)
        self._editing_item = None

    def _toggle_pin(self, path: str):
        self.store.toggle_pin(path)
        self.refresh()

    def _export(self, path: str):
        export_conversation_file(path, parent=self.window())

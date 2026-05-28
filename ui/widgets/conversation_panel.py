from datetime import datetime, date
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QApplication,
    QListWidget, QListWidgetItem, QLabel, QLineEdit, QMenu, QSizePolicy,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QEvent, QMimeData
from PyQt6.QtGui import QAction, QDrag, QFontMetrics, QPainter, QPalette

from storage.repository import ConversationStore
from services.chat_drag import AICHS_CHAT_DROP_MIME, chat_drop_payload, chat_drop_text
from services.export import export_conversation_file
from ui.theme import (
    palette, meta_font_pt, chat_font_pt, app_font,
    new_chat_button_style, search_field_style, conversation_list_style,
)

_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_CONV_ID = Qt.ItemDataRole.UserRole + 1
_ROLE_TITLE = Qt.ItemDataRole.UserRole + 2


class TitleLabel(QLabel):
    """Single-line title; paints elided text so QListWidget layouts cannot wrap it."""

    double_clicked = pyqtSignal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = _normalize_title(text)
        self.setToolTip(self._full_text)
        self.setWordWrap(False)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        super().setText("")
        self._sync_height()

    def setText(self, text: str):
        self._full_text = _normalize_title(text)
        self.setToolTip(self._full_text)
        super().setText("")
        self.update()

    def full_text(self) -> str:
        return self._full_text

    def elided_display(self, width: int | None = None) -> str:
        w = width if width is not None else max(1, self.contentsRect().width())
        return self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(1, w),
        )

    def _sync_height(self):
        h = self.fontMetrics().height()
        self.setFixedHeight(h + 2)

    def apply_font(self):
        self._sync_height()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setFont(self.font())
            color = self.palette().color(QPalette.ColorRole.WindowText)
            if not color.isValid():
                color = self.palette().color(QPalette.ColorRole.Text)
            painter.setPen(color)
            rect = self.contentsRect()
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                self.elided_display(rect.width()),
            )
        finally:
            painter.end()

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


def _normalize_title(text: str) -> str:
    return " ".join(str(text).split())


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
        self._drag_start = None
        self._drag_data: dict | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(9, 7, 6, 7)
        row.setSpacing(6)

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
        self.pin_btn.setFixedSize(18, 18)
        self.pin_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pin_btn.setToolTip("Unpin" if pinned else "Pin")
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.mousePressEvent = lambda e: self.pin_requested.emit()
        row.addWidget(self.pin_btn)

        self.del_btn = QLabel("✕")
        self.del_btn.setFixedSize(18, 18)
        self.del_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.mousePressEvent = lambda e: self.delete_requested.emit()
        row.addWidget(self.del_btn)

        self.apply_appearance()
        self._sync_delete_visibility()

    def set_drag_data(self, conv_id: str, title: str):
        self._drag_data = {
            "id": str(conv_id or "").strip(),
            "title": _normalize_title(title or self._title) or "Untitled",
        }

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.title_lbl.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._drag_data or not self._drag_start:
            super().mouseMoveEvent(event)
            return
        if not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        distance = (event.position().toPoint() - self._drag_start).manhattanLength()
        if distance < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(AICHS_CHAT_DROP_MIME, chat_drop_payload([self._drag_data]))
        mime.setText(chat_drop_text([self._drag_data]))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def apply_appearance(self):
        p = palette()
        fs = chat_font_pt()
        meta = meta_font_pt()
        pin_color = "#f5c518" if self._pinned else p["TEXT_DIM"]
        self.title_lbl.setStyleSheet(
            f"font-size:{max(12, fs - 1)}px; color:{p['TEXT']};"
            "background:transparent; font-weight:500;"
        )
        self.title_lbl.apply_font()
        self.title_edit.setStyleSheet(
            f"font-size:{fs}px; color:{p['TEXT']}; background:{p['BG3']};"
            f"border:1px solid {p['BORDER']}; padding:1px 4px;"
        )
        self.date_lbl.setStyleSheet(
            f"font-size:{meta}px; color:{p['TEXT_DIM']}; background:transparent;"
        )
        icon_fs = max(10, meta)
        self.pin_btn.setStyleSheet(
            f"QLabel {{ color:{pin_color}; background:transparent; font-size:{icon_fs}px; }}"
            "QLabel:hover { color:#f5c518; }"
        )
        self.del_btn.setStyleSheet(
            f"QLabel {{ color:{p['TEXT_DIM']}; background:transparent; font-size:{icon_fs}px; }}"
            "QLabel:hover { color:#ff5555; }"
        )
        self.title_lbl.update()
        self._sync_delete_visibility()

    def _sync_delete_visibility(self):
        self.del_btn.setVisible(not self._pinned)

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
        try:
            if self.title_edit.isVisible():
                self._cancel_edit()
        except RuntimeError:
            pass

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
    return 7 + 7 + title_fm.lineSpacing() + 1 + date_fm.lineSpacing()


class _ConversationList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:
        chats = []
        for item in items:
            conv_id = str(item.data(_ROLE_CONV_ID) or "").strip()
            if not conv_id:
                continue
            chats.append({
                "id": conv_id,
                "title": str(item.data(_ROLE_TITLE) or "Untitled").strip() or "Untitled",
            })
        mime = QMimeData()
        if chats:
            mime.setData(AICHS_CHAT_DROP_MIME, chat_drop_payload(chats))
            mime.setText(chat_drop_text(chats))
        return mime


class ConversationPanel(QWidget):
    selected = pyqtSignal(str)
    new_chat = pyqtSignal()
    renamed  = pyqtSignal(str, str)  # conv_id, title
    deleted  = pyqtSignal(str)       # conv_id

    def __init__(self, store: ConversationStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._editing_item = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._new_btn = QPushButton("+  New Chat")
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

        self.list = _ConversationList()
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.viewport().installEventFilter(self)
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
        self._refresh_item_titles()

    def eventFilter(self, obj, event):
        if obj is self.list.viewport() and event.type() == QEvent.Type.Resize:
            self._refresh_item_titles()
        return super().eventFilter(obj, event)

    def _refresh_item_titles(self):
        for i in range(self.list.count()):
            item = self.list.item(i)
            widget = self.list.itemWidget(item)
            if isinstance(widget, ConversationItem):
                widget.apply_appearance()

    def _apply_filter(self):
        self.refresh()

    def _on_item_clicked(self, item: QListWidgetItem):
        widget = self.list.itemWidget(item)
        if widget is not None and widget is self._editing_item:
            return
        if self._editing_item is not None:
            self._editing_item.cancel_edit()
            self._editing_item = None
        path = item.data(_ROLE_PATH)
        if path:
            self.selected.emit(str(path))

    def refresh(self, selected_id: str | None = None):
        self._editing_item = None
        current_path = None
        current_id = None
        if self.list.currentItem():
            current_path = self.list.currentItem().data(_ROLE_PATH)
            current_id = self.list.currentItem().data(_ROLE_CONV_ID)
        target_id = str(selected_id) if selected_id else (str(current_id) if current_id else None)
        target_path = None if selected_id else (str(current_path) if current_path else None)

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
            conv_id = str(data.get("id") or Path(path).stem)
            item.setData(_ROLE_PATH, str(path))
            item.setData(_ROLE_CONV_ID, conv_id)
            item.setData(_ROLE_TITLE, title)
            item.setSizeHint(QSize(0, _conversation_item_height()))
            self.list.addItem(item)

            widget = ConversationItem(title, date_str, pinned=data.get("pinned", False))
            widget.set_drag_data(conv_id, title)
            widget.delete_requested.connect(lambda p=str(path): self._delete(p))
            widget.rename_requested.connect(lambda t, p=str(path): self._rename(p, t))
            widget.pin_requested.connect(lambda p=str(path): self._toggle_pin(p))
            widget.export_requested.connect(lambda p=str(path): self._export(p))
            widget.edit_started.connect(self._on_edit_started)
            self.list.setItemWidget(item, widget)

            if (target_id and conv_id == target_id) or (target_path and str(path) == target_path):
                self.list.setCurrentItem(item)

            visible += 1

        show_empty = bool(query) and visible == 0
        self.no_results.setVisible(show_empty)
        self.list.setVisible(not show_empty)

    def select_conversation(self, conv_id: str):
        if not conv_id:
            return
        wanted = str(conv_id)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(_ROLE_CONV_ID) == wanted:
                self.list.setCurrentItem(item)
                return
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        self.refresh(selected_id=wanted)

    def clear_selection(self):
        self.list.clearSelection()
        self.list.setCurrentRow(-1)

    def _delete(self, path: str):
        try:
            conv_id = self.store.load(path)["id"]
        except Exception:
            conv_id = Path(path).stem
        self.store.delete(path)
        self.deleted.emit(conv_id)
        self.refresh()

    def _on_edit_started(self, item: ConversationItem):
        prev = self._editing_item
        if prev is not None and prev is not item:
            prev.cancel_edit()
        self._editing_item = item

    def _rename(self, path: str, title: str):
        conv_id = self.store.rename(path, title)
        self.renamed.emit(conv_id, title)
        self._editing_item = None
        self.refresh()

    def _toggle_pin(self, path: str):
        self.store.toggle_pin(path)
        self.refresh()

    def _export(self, path: str):
        export_conversation_file(path, parent=self.window())

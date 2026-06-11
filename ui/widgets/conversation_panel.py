from datetime import datetime, date
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QApplication,
    QListWidget, QListWidgetItem, QLabel, QLineEdit, QMenu, QSizePolicy,
    QAbstractItemView, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, QSize, pyqtSignal, QEvent, QMimeData, QTimer
from PyQt6.QtGui import QAction, QDrag, QFontMetrics, QPainter, QPalette

from storage.repository import ConversationStore
from storage.settings import SettingsStore, trash_retention_days
from services.chat_drag import AICHS_CHAT_DROP_MIME, chat_drop_payload, chat_drop_text
from services.export import default_export_name, export_conversation_file_to_path
from ui.theme import (
    palette, meta_font_pt, chat_font_pt, app_font, hint_label_style,
    new_chat_button_style, search_field_style, conversation_list_style,
    conversation_row_title_style, conversation_row_inline_edit_style,
    conversation_row_icon_label_style, conversation_row_restore_button_style,
    conversation_trash_header_style, title_label_style,
)

_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_CONV_ID = Qt.ItemDataRole.UserRole + 1
_ROLE_TITLE = Qt.ItemDataRole.UserRole + 2
_ROLE_TRASH_HEADER = Qt.ItemDataRole.UserRole + 3
_TRASH_HEADER_HEIGHT = 48
_SEARCH_DEBOUNCE_MS = 150


class _ConversationExportSignals(QObject):
    done = pyqtSignal(int, str, str)


class _ConversationExportWorker(QRunnable):
    def __init__(self, generation: int, conv_path: str, out_path: str):
        super().__init__()
        self.signals = _ConversationExportSignals()
        self._generation = generation
        self._conv_path = conv_path
        self._out_path = out_path

    def run(self) -> None:
        try:
            written = export_conversation_file_to_path(self._conv_path, self._out_path)
        except Exception as exc:
            self.signals.done.emit(self._generation, "", str(exc))
            return
        self.signals.done.emit(self._generation, str(written), "")


class _ConversationActionSignals(QObject):
    done = pyqtSignal(str, str, str, str)


class _ConversationActionWorker(QRunnable):
    def __init__(
        self,
        store: ConversationStore,
        action: str,
        path: str,
        title: str = "",
    ):
        super().__init__()
        self.signals = _ConversationActionSignals()
        self._store = store
        self._action = action
        self._path = path
        self._title = title

    def run(self) -> None:
        try:
            if self._action == "delete":
                try:
                    conv_id = str(self._store.load(self._path).get("id") or Path(self._path).stem)
                except Exception:
                    conv_id = Path(self._path).stem
                self._store.delete(self._path)
                detail = ""
            elif self._action == "restore":
                restored = self._store.restore(self._path)
                conv_id = restored.stem
                detail = str(restored)
            elif self._action == "rename":
                conv_id = self._store.rename(self._path, self._title)
                detail = self._title
            elif self._action == "pin":
                conv_id = Path(self._path).stem
                pinned = self._store.toggle_pin(self._path)
                detail = "true" if pinned else "false"
            else:
                raise ValueError(f"unsupported conversation action: {self._action}")
        except Exception as exc:
            self.signals.done.emit(self._action, "", "", str(exc))
            return
        self.signals.done.emit(self._action, conv_id, detail, "")


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
    restore_requested = pyqtSignal()
    rename_requested = pyqtSignal(str)
    pin_requested    = pyqtSignal()
    export_requested = pyqtSignal()
    edit_started     = pyqtSignal(object)

    def __init__(self, title: str, date_str: str, pinned: bool = False,
                 trashed: bool = False, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._title = title
        self._pinned = pinned
        self._trashed = trashed
        self._active = False
        self._hovered = False
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
        self.del_btn.setToolTip("Move to trash")
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.mousePressEvent = lambda e: self.delete_requested.emit()
        row.addWidget(self.del_btn)

        self.restore_btn = QPushButton("Restore")
        self.restore_btn.setFixedHeight(24)
        self.restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_btn.clicked.connect(self.restore_requested.emit)
        row.addWidget(self.restore_btn)

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

    def enterEvent(self, event):
        self._hovered = True
        self._sync_action_visibility()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._sync_action_visibility()
        super().leaveEvent(event)

    def set_active(self, active: bool):
        self._active = bool(active)
        self._sync_action_visibility()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._trashed or not self._drag_data or not self._drag_start:
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
        pin_color = "#f5c518" if self._pinned else None
        self.title_lbl.setStyleSheet(conversation_row_title_style())
        self.title_lbl.apply_font()
        self.title_edit.setStyleSheet(conversation_row_inline_edit_style())
        self.date_lbl.setStyleSheet(hint_label_style())
        self.pin_btn.setStyleSheet(
            conversation_row_icon_label_style(
                color=pin_color,
                hover_color="#f5c518",
            )
        )
        self.del_btn.setStyleSheet(
            conversation_row_icon_label_style(hover_color="#ff5555")
        )
        self.restore_btn.setStyleSheet(conversation_row_restore_button_style())
        self.title_lbl.update()
        self._sync_action_visibility()

    def _sync_delete_visibility(self):
        self._sync_action_visibility()

    def _sync_action_visibility(self):
        reveal = self._hovered or self._active or self.title_edit.isVisible()
        self.pin_btn.setVisible(not self._trashed and (self._pinned or reveal))
        self.del_btn.setVisible(not self._pinned and not self._trashed and reveal)
        self.restore_btn.setVisible(self._trashed)

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
        self._sync_action_visibility()
        if new_title != self._title:
            self._title = new_title
            self.rename_requested.emit(new_title)

    def _cancel_edit(self):
        self.title_edit.hide()
        self.title_lbl.show()
        self._sync_action_visibility()

    def cancel_edit(self):
        try:
            if self.title_edit.isVisible():
                self._cancel_edit()
        except RuntimeError:
            pass

    def _context_menu(self, pos):
        menu = QMenu(self)
        if self._trashed:
            restore = QAction("Restore", self)
            restore.triggered.connect(self.restore_requested.emit)
            menu.addAction(restore)
            menu.exec(self.mapToGlobal(pos))
            return
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


class TrashHeader(QWidget):
    clicked = pyqtSignal()

    def __init__(self, count: int, expanded: bool, parent=None):
        super().__init__(parent)
        self._count = count
        self._expanded = expanded
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(140)
        self.setFixedHeight(_TRASH_HEADER_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 8, 0)
        row.setSpacing(7)

        self.arrow_lbl = QLabel()
        self.arrow_lbl.setFixedWidth(14)
        self.arrow_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.arrow_lbl)

        self.title_lbl = QLabel("Trash")
        self.title_lbl.setMinimumWidth(54)
        row.addWidget(self.title_lbl, 1)

        self.count_lbl = QLabel()
        self.count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.count_lbl)

        self.apply_appearance()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def apply_appearance(self):
        fs = max(12, chat_font_pt() - 1)
        self.setStyleSheet(conversation_trash_header_style())
        self.arrow_lbl.setText("v" if self._expanded else ">")
        self.arrow_lbl.setStyleSheet(
            hint_label_style(font_pt=fs, font_weight="700")
        )
        self.title_lbl.setStyleSheet(
            title_label_style(font_pt=fs, font_weight="600", text_color=palette()["TEXT"])
        )
        self.count_lbl.setText(str(self._count))
        self.count_lbl.setStyleSheet(hint_label_style())


class ConversationPanel(QWidget):
    selected = pyqtSignal(str)
    new_chat = pyqtSignal()
    renamed  = pyqtSignal(str, str)  # conv_id, title
    deleted  = pyqtSignal(str)       # conv_id

    def __init__(self, store: ConversationStore, settings: SettingsStore | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self._settings = settings or SettingsStore()
        self._editing_item = None
        self._trash_expanded = False
        self._export_generation = 0
        self._export_active = False
        self._export_pool = QThreadPool(self)
        self._export_pool.setMaxThreadCount(1)
        self._action_pool = QThreadPool(self)
        self._action_pool.setMaxThreadCount(1)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(_SEARCH_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(lambda: self.refresh())

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
        self.list.currentItemChanged.connect(self._sync_current_row_actions)
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
            hint_label_style(font_pt=fs, padding="24px", background=p["BG2"])
        )
        self.list.setStyleSheet(conversation_list_style())

    def apply_appearance(self):
        self._apply_styles()
        self._refresh_item_titles()

    def set_store(self, store: ConversationStore):
        self.store = store
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.clear_selection()
        self.refresh()

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
                widget.set_active(item is self.list.currentItem())
            elif isinstance(widget, TrashHeader):
                widget.apply_appearance()

    def _sync_current_row_actions(self, *_args):
        current = self.list.currentItem()
        for i in range(self.list.count()):
            widget = self.list.itemWidget(self.list.item(i))
            if isinstance(widget, ConversationItem):
                widget.set_active(self.list.item(i) is current)

    def _apply_filter(self):
        self._filter_timer.start()

    def _trash_retention_days(self) -> int:
        return trash_retention_days(self._settings.load())

    def _toggle_trash(self):
        self._trash_expanded = not self._trash_expanded
        self.refresh()

    def _add_trash_header(self, count: int):
        item = QListWidgetItem()
        item.setData(_ROLE_TRASH_HEADER, True)
        item.setSizeHint(QSize(160, _TRASH_HEADER_HEIGHT))
        self.list.addItem(item)

        header = TrashHeader(count, self._trash_expanded)
        header.clicked.connect(self._toggle_trash)
        self.list.setItemWidget(item, header)

    def _add_conversation_row(
        self,
        path: Path,
        data: dict,
        *,
        today: date,
        trashed: bool = False,
    ) -> str:
        title = data.get("title", "Untitled")
        updated = data.get("deleted_at", "") if trashed else data.get("updated_at", "")
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

        widget = ConversationItem(
            title,
            date_str,
            pinned=data.get("pinned", False),
            trashed=trashed,
        )
        if not trashed:
            widget.set_drag_data(conv_id, title)
            widget.delete_requested.connect(lambda p=str(path): self._delete(p))
            widget.rename_requested.connect(lambda t, p=str(path): self._rename(p, t))
            widget.pin_requested.connect(lambda p=str(path): self._toggle_pin(p))
            widget.export_requested.connect(lambda p=str(path), t=title: self._export(p, t))
        else:
            widget.restore_requested.connect(lambda p=str(path): self._restore(p))
        widget.edit_started.connect(self._on_edit_started)
        self.list.setItemWidget(item, widget)
        return conv_id

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
        self._filter_timer.stop()
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
        self.store.prune_trash(self._trash_retention_days())
        today = date.today()
        visible = 0
        trash_records = self.store.list_trash()
        if not trash_records:
            self._trash_expanded = False

        for path, data in self.store.list_all():
            if query and not self.store.matches_search(path, data, query):
                continue

            conv_id = self._add_conversation_row(path, data, today=today)

            if (target_id and conv_id == target_id) or (target_path and str(path) == target_path):
                self.list.setCurrentItem(self.list.item(self.list.count() - 1))

            visible += 1

        if trash_records:
            self._add_trash_header(len(trash_records))
            visible += 1
            if self._trash_expanded:
                for path, data in trash_records:
                    if query and not self.store.matches_search(path, data, query):
                        continue
                    self._add_conversation_row(path, data, today=today, trashed=True)
                    visible += 1

        self.no_results.setText("No results")
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
        self._start_action("delete", path)

    def _restore(self, path: str):
        self._start_action("restore", path)

    def _on_edit_started(self, item: ConversationItem):
        prev = self._editing_item
        if prev is not None and prev is not item:
            prev.cancel_edit()
        self._editing_item = item

    def _rename(self, path: str, title: str):
        self._editing_item = None
        self._start_action("rename", path, title)

    def _toggle_pin(self, path: str):
        self._start_action("pin", path)

    def _start_action(self, action: str, path: str, title: str = "") -> None:
        worker = _ConversationActionWorker(self.store, action, path, title)
        worker.signals.done.connect(self._on_action_done)
        self._action_pool.start(worker)

    def _on_action_done(self, action: str, conv_id: str, detail: str, error: str) -> None:
        if error:
            QMessageBox.warning(self, "Conversation action failed", error)
            return
        if action == "delete":
            self.deleted.emit(conv_id)
        elif action == "rename":
            self.renamed.emit(conv_id, detail)
        self.refresh()

    def _export(self, path: str, title: str = ""):
        if self._export_active:
            return
        default = default_export_name({"title": title or Path(path).stem})
        out_path, _ = QFileDialog.getSaveFileName(
            self.window(),
            "Export conversation",
            default,
            "Markdown (*.md)",
        )
        if not out_path:
            return
        self._export_generation += 1
        generation = self._export_generation
        self._export_active = True
        worker = _ConversationExportWorker(generation, path, out_path)
        worker.signals.done.connect(self._on_export_done)
        self._export_pool.start(worker)

    def _on_export_done(self, generation: int, _path: str, error: str) -> None:
        if generation != self._export_generation:
            return
        self._export_active = False
        if error:
            QMessageBox.warning(self, "Export failed", error)

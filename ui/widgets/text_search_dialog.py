import html
import os
import threading

from PyQt6.QtCore import QEvent, QObject, QRunnable, QSize, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.text_search import TextSearchMatch, search_file_contents_with_candidates
from ui.theme import (
    chat_font_pt,
    hint_label_style,
    overlay_dialog_style,
    overlay_results_list_style,
    overlay_search_input_style,
    overlay_separator_style,
    palette,
    search_match_style,
)


class _TextSearchSignals(QObject):
    done = pyqtSignal(int, str, object, object, str)


class _TextSearchWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        root: str,
        query: str,
        candidates: tuple[TextSearchMatch, ...] | None,
        cancel_event: threading.Event,
    ):
        super().__init__()
        self.signals = _TextSearchSignals()
        self._generation = generation
        self._root = root
        self._query = query
        self._candidates = candidates
        self._cancel_event = cancel_event

    def run(self) -> None:
        try:
            matches, candidates = search_file_contents_with_candidates(
                self._root,
                self._query,
                candidates=self._candidates,
                cancelled=self._cancel_event.is_set,
            )
        except Exception as exc:
            self.signals.done.emit(self._generation, self._query, [], (), str(exc))
            return
        self.signals.done.emit(self._generation, self._query, matches, candidates, "")


class _SearchInput(QLineEdit):
    def __init__(self, dialog: "TextSearchDialog", parent=None):
        super().__init__(parent)
        self._dialog = dialog

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Down and self._dialog._list.count():
            row = max(0, self._dialog._list.currentRow())
            self._dialog._list.setCurrentRow(row)
            self._dialog._list.setFocus()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Up and self._dialog._list.count():
            row = self._dialog._list.currentRow()
            if row <= 0:
                event.accept()
                return
            self._dialog._list.setCurrentRow(row - 1)
            self._dialog._list.setFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class _TextSearchRow(QWidget):
    def __init__(self, match: TextSearchMatch, parent=None):
        super().__init__(parent)
        p = palette()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(2)

        path = QLabel(f"{html.escape(match.rel_path)}:{match.line_no}")
        path.setStyleSheet(
            f"color:{p['TEXT']}; font-size:{chat_font_pt()}px; background:transparent;"
        )
        layout.addWidget(path)

        snippet = QLabel(_highlight_line_html(match))
        snippet.setTextFormat(Qt.TextFormat.RichText)
        snippet.setStyleSheet(hint_label_style())
        layout.addWidget(snippet)


class TextSearchDialog(QDialog):
    def __init__(self, root: str, on_open_file, parent=None):
        super().__init__(parent)
        self._root = os.path.abspath(root)
        self._on_open_file = on_open_file
        self._filtered: list[TextSearchMatch] = []
        self._candidate_query = ""
        self._candidate_matches: tuple[TextSearchMatch, ...] = ()
        self._search_generation = 0
        self._search_cancel: threading.Event | None = None
        self._search_pool = QThreadPool.globalInstance()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._run_search)

        self.setWindowTitle("Search files")
        self.setModal(True)
        self.setMinimumWidth(620)
        self.resize(680, 460)

        self.setStyleSheet(overlay_dialog_style())

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)

        self._query = _SearchInput(self)
        self._query.setObjectName("textSearchQuery")
        self._query.setPlaceholderText("Search text in files")
        self._query.setClearButtonEnabled(True)
        self._query.setStyleSheet(overlay_search_input_style())
        self._query.textChanged.connect(self._schedule_search)
        self._query.returnPressed.connect(self._activate_current)
        root_layout.addWidget(self._query)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(overlay_separator_style())
        root_layout.addWidget(sep)

        self._list = QListWidget()
        self._list.setObjectName("textSearchResults")
        self._list.setStyleSheet(overlay_results_list_style())
        self._list.itemClicked.connect(self._on_activated)
        self._list.itemActivated.connect(self._on_activated)
        self._list.installEventFilter(self)
        root_layout.addWidget(self._list, 1)

    def showEvent(self, event):
        super().showEvent(event)
        self._query.setFocus()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj is self._list and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate_current()
                return True
            if key == Qt.Key.Key_Up and self._list.currentRow() <= 0:
                self._query.setFocus()
                return True
        return super().eventFilter(obj, event)

    def _schedule_search(self, _text: str):
        self._timer.start()

    def _run_search(self):
        query = self._query.text().strip()
        self._start_search(query)

    def reject(self) -> None:
        self._cancel_search()
        super().reject()

    def closeEvent(self, event):
        self._cancel_search()
        super().closeEvent(event)

    def _cancel_search(self):
        self._search_generation += 1
        self._timer.stop()
        if self._search_cancel is not None:
            self._search_cancel.set()
            self._search_cancel = None

    def _start_search(self, query: str):
        self._search_generation += 1
        generation = self._search_generation
        if self._search_cancel is not None:
            self._search_cancel.set()
        source_matches = (
            self._candidate_matches
            if _is_query_refinement(self._candidate_query, query)
            else None
        )
        cancel_event = threading.Event()
        self._search_cancel = cancel_event
        worker = _TextSearchWorker(generation, self._root, query, source_matches, cancel_event)
        worker.signals.done.connect(self._on_search_ready)
        self._search_pool.start(worker)

    def _on_search_ready(
        self,
        generation: int,
        query: str,
        matches: object,
        candidates: object,
        error: str,
    ):
        if generation != self._search_generation:
            return
        self._search_cancel = None
        if error:
            self._filtered = []
            self._candidate_matches = ()
            self._candidate_query = query
            self._render_matches()
            return
        self._filtered = list(matches or [])
        self._candidate_matches = tuple(candidates or ())
        self._candidate_query = query
        self._render_matches()

    def _render_matches(self):
        self._list.clear()
        for match in self._filtered:
            row = QListWidgetItem()
            row.setSizeHint(QSize(0, 58))
            row.setData(Qt.ItemDataRole.UserRole, match)
            self._list.addItem(row)
            self._list.setItemWidget(row, _TextSearchRow(match))
        if self._list.count():
            self._list.setCurrentRow(0)

    def _activate_current(self):
        if self._timer.isActive():
            self._timer.stop()
            self._run_search()
        row = self._list.currentItem()
        if row:
            self._on_activated(row)

    def _on_activated(self, row: QListWidgetItem):
        match = row.data(Qt.ItemDataRole.UserRole)
        if isinstance(match, TextSearchMatch):
            self.accept()
            self._on_open_file(match.path, match.line_no)


def _highlight_line_html(match: TextSearchMatch) -> str:
    line = match.line_text
    start = max(0, min(match.start, len(line)))
    end = max(start, min(match.end, len(line)))
    return (
        html.escape(line[:start])
        + f"<span style=\"{search_match_style()}\">"
        + html.escape(line[start:end])
        + "</span>"
        + html.escape(line[end:])
    )


def _is_query_refinement(previous: str, current: str) -> bool:
    prev = previous.strip().casefold()
    cur = current.strip().casefold()
    return bool(prev) and len(cur) >= len(prev) and cur.startswith(prev)

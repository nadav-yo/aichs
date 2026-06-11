import html
import os

from PyQt6.QtCore import QEvent, QObject, QRunnable, QSize, QThreadPool, Qt, pyqtSignal
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

from services.file_search import FileSearchIndex, FileSearchMatch
from ui.theme import (
    chat_font_pt,
    overlay_dialog_style,
    overlay_results_list_style,
    overlay_search_input_style,
    overlay_separator_style,
    palette,
    search_match_style,
)


class _FileSearchIndexSignals(QObject):
    done = pyqtSignal(int, object, str)


class _FileSearchIndexWorker(QRunnable):
    def __init__(self, generation: int, root: str):
        super().__init__()
        self.signals = _FileSearchIndexSignals()
        self._generation = generation
        self._root = root

    def run(self) -> None:
        try:
            index = FileSearchIndex.from_root(self._root)
        except Exception as exc:
            self.signals.done.emit(self._generation, None, str(exc))
            return
        self.signals.done.emit(self._generation, index, "")


class _SearchInput(QLineEdit):
    def __init__(self, dialog: "FileSearchDialog", parent=None):
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


class _FileResultRow(QWidget):
    def __init__(self, match: FileSearchMatch, parent=None):
        super().__init__(parent)
        p = palette()
        self.setMinimumHeight(42)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        label = QLabel(_match_path_html(match))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet(
            f"color:{p['TEXT']}; font-size:{chat_font_pt()}px;"
            "background:transparent; padding:9px 12px;"
        )
        layout.addWidget(label)


class FileSearchDialog(QDialog):
    def __init__(self, root: str, on_open_file, parent=None):
        super().__init__(parent)
        self._root = os.path.abspath(root)
        self._on_open_file = on_open_file
        self._index: FileSearchIndex | None = None
        self._filtered: list[FileSearchMatch] = []
        self._candidate_query = ""
        self._candidate_entries = ()
        self._index_generation = 0
        self._index_pool = QThreadPool.globalInstance()

        self.setWindowTitle("Open file")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.resize(580, 420)

        self.setStyleSheet(overlay_dialog_style())

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)

        self._query = _SearchInput(self)
        self._query.setObjectName("fileSearchQuery")
        self._query.setPlaceholderText("Search files by name")
        self._query.setClearButtonEnabled(True)
        self._query.setStyleSheet(overlay_search_input_style())
        self._query.textChanged.connect(self._refilter)
        self._query.returnPressed.connect(self._activate_current)
        root_layout.addWidget(self._query)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(overlay_separator_style())
        root_layout.addWidget(sep)

        self._list = QListWidget()
        self._list.setObjectName("fileSearchResults")
        self._list.setStyleSheet(overlay_results_list_style())
        self._list.itemClicked.connect(self._on_activated)
        self._list.itemActivated.connect(self._on_activated)
        self._list.installEventFilter(self)
        root_layout.addWidget(self._list, 1)

        self._show_placeholder("Loading files...")
        self._start_index_load()

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

    def _refilter(self, text: str):
        query = text.strip()
        if self._index is None:
            self._filtered = []
            self._candidate_query = query
            self._candidate_entries = ()
            self._show_placeholder("Loading files...")
            return
        source_entries = (
            self._candidate_entries
            if _is_query_refinement(self._candidate_query, query)
            else self._index.entries
        )
        self._filtered, self._candidate_entries = self._index.search_with_candidates(
            query,
            entries=source_entries,
        )
        self._candidate_query = query
        self._list.clear()
        for match in self._filtered:
            row = QListWidgetItem()
            row.setSizeHint(QSize(0, 42))
            row.setData(Qt.ItemDataRole.UserRole, match)
            self._list.addItem(row)
            self._list.setItemWidget(row, _FileResultRow(match))
        if self._list.count():
            self._list.setCurrentRow(0)

    def closeEvent(self, event):
        self._index_generation += 1
        super().closeEvent(event)

    def _start_index_load(self):
        self._index_generation += 1
        generation = self._index_generation
        worker = _FileSearchIndexWorker(generation, self._root)
        worker.signals.done.connect(self._on_index_ready)
        self._index_pool.start(worker)

    def _on_index_ready(self, generation: int, index: object, error: str):
        if generation != self._index_generation:
            return
        if error:
            self._index = FileSearchIndex(())
            self._candidate_entries = ()
            self._show_placeholder(f"Could not index files: {error}")
            return
        self._index = index if isinstance(index, FileSearchIndex) else FileSearchIndex(())
        self._candidate_query = ""
        self._candidate_entries = self._index.entries
        self._refilter(self._query.text())

    def _show_placeholder(self, text: str):
        self._list.clear()
        row = QListWidgetItem(text)
        row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._list.addItem(row)

    def _activate_current(self):
        row = self._list.currentItem()
        if row:
            self._on_activated(row)

    def _on_activated(self, row: QListWidgetItem):
        match = row.data(Qt.ItemDataRole.UserRole)
        if isinstance(match, FileSearchMatch):
            self.accept()
            self._on_open_file(match.path)


def _match_path_html(match: FileSearchMatch) -> str:
    name_start = match.rel_path.rfind(match.name)
    if name_start < 0:
        return _highlight_html(match.rel_path, ())
    indices = tuple(name_start + index for index in match.indices)
    return _highlight_html(match.rel_path, indices)


def _highlight_html(text: str, indices: tuple[int, ...]) -> str:
    highlighted = set(indices)
    out: list[str] = []
    for index, char in enumerate(text):
        escaped = html.escape(char)
        if index in highlighted:
            out.append(
                f"<span style=\"{search_match_style()}\">"
                f"{escaped}</span>"
            )
        else:
            out.append(escaped)
    return "".join(out)


def _is_query_refinement(previous: str, current: str) -> bool:
    prev = previous.strip().casefold()
    cur = current.strip().casefold()
    return not prev or (len(cur) >= len(prev) and cur.startswith(prev))

import os
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QListWidget, QListWidgetItem,
)

from ui.theme import contained_list_style, markdown_css, palette
from ui.markdown_html import markdown_body
from ui.widgets.markdown_browser import RemoteImageTextBrowser, copy_code_url_to_clipboard


_DOC_ORDER = [
    "configuration.md",
    "custom-models.md",
    "extensions.md",
    "skills.md",
    "yuk.md",
    "compact.md",
]
_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def docs_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "docs",
        Path(sys.executable).resolve().parent / "docs",
        Path(sys.prefix) / "share" / "aichs" / "docs",
    ]
    if hasattr(sys, "_MEIPASS"):
        candidates.insert(1, Path(sys._MEIPASS) / "docs")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def doc_title(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    match = _HEADING_RE.search(text)
    if match:
        return match.group(1).strip()
    return path.stem.replace("-", " ").title()


def available_docs(root: Path | None = None) -> list[Path]:
    root = root or docs_dir()
    ordered = [root / name for name in _DOC_ORDER if (root / name).is_file()]
    extras = sorted(
        path for path in root.glob("*.md")
        if path.name not in _DOC_ORDER
    )
    return ordered + extras


def markdown_document_html(markdown_text: str) -> str:
    body = markdown_body(markdown_text, extensions=["fenced_code", "tables", "toc"])
    p = palette()
    css = (
        markdown_css()
        + f"body {{ background:{p['BG2']}; padding:8px 10px 16px 10px; }}"
    )
    return f"<style>{css}</style>{body}"


class _DocsIndexSignals(QObject):
    done = pyqtSignal(int, object, str)


class _DocsIndexWorker(QRunnable):
    def __init__(self, generation: int, root: Path):
        super().__init__()
        self.signals = _DocsIndexSignals()
        self._generation = generation
        self._root = root

    def run(self):
        try:
            entries = [(path.name, doc_title(path)) for path in available_docs(self._root)]
        except BaseException as exc:
            self.signals.done.emit(self._generation, [], str(exc) or exc.__class__.__name__)
            return
        self.signals.done.emit(self._generation, entries, "")


class _DocLoadSignals(QObject):
    done = pyqtSignal(int, str, str, str)


class _DocLoadWorker(QRunnable):
    def __init__(self, generation: int, root: Path, name: str):
        super().__init__()
        self.signals = _DocLoadSignals()
        self._generation = generation
        self._root = root
        self._name = name

    def run(self):
        path = (self._root / self._name).resolve()
        if not _is_doc_path(self._root, path):
            self.signals.done.emit(self._generation, self._name, "", "Document is outside the docs directory.")
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.signals.done.emit(self._generation, self._name, "", str(exc))
            return
        self.signals.done.emit(self._generation, self._name, text, "")


class DocsDialog(QDialog):
    def __init__(self, parent=None, root: Path | None = None):
        super().__init__(parent)
        self._root = root or docs_dir()
        self._docs: list[str] = []
        self._pool = QThreadPool.globalInstance()
        self._index_generation = 0
        self._doc_generation = 0
        self._selecting_doc = False
        self._pending_anchor = ""
        self._pending_anchor_generation = 0

        self.setWindowTitle("Documentation")
        self.resize(860, 620)
        self.setMinimumSize(620, 420)

        p = palette()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self.nav = QListWidget()
        self.nav.setFixedWidth(210)
        self.nav.setStyleSheet(
            contained_list_style(
                item_padding="8px 10px",
                item_radius=6,
                item_margin="2px 4px",
                border_radius=8,
                bg=p["BG3"],
                border=p["BORDER"],
            )
        )
        layout.addWidget(self.nav)

        self.viewer = RemoteImageTextBrowser()
        self.viewer.setOpenLinks(False)
        self.viewer.setStyleSheet(
            f"QTextBrowser {{ background:{p['BG2']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px; padding:12px; }}"
        )
        self.viewer.anchorClicked.connect(self._open_link)
        layout.addWidget(self.viewer, 1)

        self.nav.currentItemChanged.connect(self._on_doc_selected)
        self._load_docs_index()

    def _on_doc_selected(self, current: QListWidgetItem | None, _previous=None):
        if self._selecting_doc:
            return
        if current:
            self.open_doc(str(current.data(Qt.ItemDataRole.UserRole)))

    def open_doc(self, name: str, *, anchor: str = ""):
        path = (self._root / name).resolve()
        if not _is_doc_path(self._root, path):
            return
        self._doc_generation += 1
        generation = self._doc_generation
        self._pending_anchor = anchor
        self._pending_anchor_generation = generation
        self._show_markdown(f"Loading `{path.name}`...")
        worker = _DocLoadWorker(generation, self._root, path.name)
        worker.signals.done.connect(self._on_doc_ready)
        self._pool.start(worker)

    def _open_link(self, url: QUrl):
        if copy_code_url_to_clipboard(url):
            return
        target = url.toString()
        if target.startswith("#"):
            self.viewer.scrollToAnchor(target[1:])
            return
        if url.isRelative() or url.isLocalFile():
            raw = url.toLocalFile() if url.isLocalFile() else target
            local = (self._root / raw.split("#", 1)[0]).resolve()
            if _is_doc_path(self._root, local):
                anchor = target.split("#", 1)[1] if "#" in target else ""
                self.open_doc(local.name, anchor=anchor)
                return
        QDesktopServices.openUrl(url)

    def _is_doc_path(self, path: Path) -> bool:
        return _is_doc_path(self._root, path)

    def _select_doc(self, name: str):
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                if self.nav.currentRow() == row:
                    return
                self._selecting_doc = True
                try:
                    self.nav.setCurrentRow(row)
                finally:
                    self._selecting_doc = False
                return

    def _load_docs_index(self):
        self._index_generation += 1
        generation = self._index_generation
        self._docs = []
        self.nav.clear()
        self._show_markdown("Loading documentation...")
        worker = _DocsIndexWorker(generation, self._root)
        worker.signals.done.connect(self._on_docs_index_ready)
        self._pool.start(worker)

    def _on_docs_index_ready(self, generation: int, entries: object, error: str):
        if generation != self._index_generation:
            return
        self.nav.clear()
        if error:
            self._show_markdown(f"# Documentation\n\nCould not list docs: {error}")
            return
        docs = [(str(name), str(title)) for name, title in entries]
        self._docs = [name for name, _title in docs]
        if not docs:
            self._show_markdown(
                f"Documentation was not found at `{str(self._root).replace('`', '')}`."
            )
            return
        for name, title in docs:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(name)
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)

    def _on_doc_ready(self, generation: int, name: str, text: str, error: str):
        if generation != self._doc_generation:
            return
        path = (self._root / name).resolve()
        if error:
            text = f"# Documentation\n\nCould not read `{name}`: {error}"
        self.viewer.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent) + os.sep))
        self.viewer.setHtml(markdown_document_html(text))
        self._select_doc(path.name)
        if self._pending_anchor_generation == generation and self._pending_anchor:
            self.viewer.scrollToAnchor(self._pending_anchor)
        self._pending_anchor = ""

    def _show_markdown(self, text: str):
        self.viewer.setHtml(markdown_document_html(text))

    def closeEvent(self, event):
        self._index_generation += 1
        self._doc_generation += 1
        super().closeEvent(event)


def _is_doc_path(root: Path, path: Path) -> bool:
    try:
        common = os.path.commonpath([root.resolve(), path.resolve()])
    except ValueError:
        return False
    return common == str(root.resolve()) and path.suffix.lower() == ".md" and path.is_file()

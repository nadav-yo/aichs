import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QListWidget, QListWidgetItem,
)

from services.tool_registry import extension_docs
from ui.theme import contained_list_style, markdown_css, palette
from ui.markdown_html import markdown_body
from ui.widgets.markdown_browser import RemoteImageTextBrowser, copy_code_url_to_clipboard
from ui.widgets.window_chrome import chromed_dialog_layout


_DOC_ORDER = [
    "CHANGELOG.md",
    "configuration.md",
    "custom-models.md",
    "extensions.md",
    "skills.md",
    "yuk.md",
    "compact.md",
]
_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_CHANGELOG_NAMES = frozenset({"CHANGELOG.md", "changelog.md"})


@dataclass(frozen=True)
class DocEntry:
    identifier: str
    title: str
    path: Path
    is_extension: bool = False
    extension_id: str = ""

    @property
    def display_title(self) -> str:
        if self.is_extension:
            return f"{self.title} [EXTENSION]"
        return self.title


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


def resolve_doc_path(root: Path, name: str) -> Path | None:
    """Resolve a doc filename under ``root``, with CHANGELOG falling back to repo root."""
    direct = root / name
    if direct.is_file():
        return direct
    if name not in _CHANGELOG_NAMES:
        return None
    for candidate in (root / "CHANGELOG.md", root / "changelog.md"):
        if candidate.is_file():
            return candidate
    # Source checkouts keep CHANGELOG.md at the repo root next to docs/.
    if root.name == "docs":
        parent = root.parent / "CHANGELOG.md"
        if parent.is_file():
            return parent
    return None


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
    ordered: list[Path] = []
    seen: set[str] = set()
    for name in _DOC_ORDER:
        path = resolve_doc_path(root, name)
        if path is None:
            continue
        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    extras = sorted(
        path for path in root.glob("*.md")
        if path.name not in _DOC_ORDER
        and path.name not in _CHANGELOG_NAMES
        and os.path.normcase(str(path.resolve())) not in seen
    )
    return ordered + extras


def available_doc_entries(root: Path | None = None, cwd: str | None = None) -> list[DocEntry]:
    root = root or docs_dir()
    entries = [
        DocEntry(path.name, doc_title(path), path.resolve())
        for path in available_docs(root)
    ]
    if not cwd:
        return entries
    docs, _errors = extension_docs(cwd)
    for doc in docs:
        path = Path(doc.path)
        if path.suffix.lower() != ".md" or not path.is_file():
            continue
        entries.append(
            DocEntry(
                f"extension:{doc.extension_id}:{doc.name}",
                doc.title,
                path.resolve(),
                is_extension=True,
                extension_id=doc.extension_id,
            )
        )
    return entries


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
    def __init__(self, generation: int, root: Path, cwd: str | None = None):
        super().__init__()
        self.signals = _DocsIndexSignals()
        self._generation = generation
        self._root = root
        self._cwd = cwd

    def run(self):
        try:
            entries = [
                (
                    entry.identifier,
                    entry.title,
                    str(entry.path),
                    entry.is_extension,
                    entry.extension_id,
                )
                for entry in available_doc_entries(self._root, self._cwd)
            ]
        except BaseException as exc:
            self.signals.done.emit(self._generation, [], str(exc) or exc.__class__.__name__)
            return
        self.signals.done.emit(self._generation, entries, "")


class _DocLoadSignals(QObject):
    done = pyqtSignal(int, str, str, str)


class _DocLoadWorker(QRunnable):
    def __init__(self, generation: int, root: Path, name: str | Path, *, allow_outside_root: bool = False):
        super().__init__()
        self.signals = _DocLoadSignals()
        self._generation = generation
        self._root = root
        raw = Path(name)
        self._path = raw.resolve() if raw.is_absolute() else (self._root / raw).resolve()
        self._name = self._path.name
        self._allow_outside_root = allow_outside_root

    def run(self):
        path = self._path
        valid = (
            path.suffix.lower() == ".md" and path.is_file()
            if self._allow_outside_root
            else _is_doc_path(self._root, path)
        )
        if not valid:
            self.signals.done.emit(self._generation, self._name, "", "Document is outside the docs directory.")
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.signals.done.emit(self._generation, self._name, "", str(exc))
            return
        self.signals.done.emit(self._generation, str(path), text, "")


class DocsDialog(QDialog):
    def __init__(self, parent=None, root: Path | None = None, cwd: str | None = None):
        super().__init__(parent)
        self._root = root or docs_dir()
        self._cwd = cwd
        self._docs: list[str] = []
        self._entries: dict[str, DocEntry] = {}
        self._entry_by_path: dict[str, str] = {}
        self._current_doc_path: Path | None = None
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
        layout = chromed_dialog_layout(
            self,
            QHBoxLayout,
            contents_margins=(14, 14, 14, 14),
            spacing=12,
        )

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
        entry = self._entries.get(name)
        if entry is None:
            path = (self._root / name).resolve()
            if not _is_doc_path(self._root, path):
                return
            entry = DocEntry(path.name, doc_title(path), path)
        self._doc_generation += 1
        generation = self._doc_generation
        self._pending_anchor = anchor
        self._pending_anchor_generation = generation
        self._show_markdown(f"Loading `{entry.path.name}`...")
        worker = _DocLoadWorker(
            generation,
            self._root,
            entry.path,
            allow_outside_root=entry.is_extension,
        )
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
            base = self._current_doc_path.parent if self._current_doc_path else self._root
            local = (base / raw.split("#", 1)[0]).resolve()
            anchor = target.split("#", 1)[1] if "#" in target else ""
            entry_id = self._entry_by_path.get(str(local))
            if entry_id:
                self.open_doc(entry_id, anchor=anchor)
                return
            if _is_doc_path(self._root, local):
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
        worker = _DocsIndexWorker(generation, self._root, self._cwd)
        worker.signals.done.connect(self._on_docs_index_ready)
        self._pool.start(worker)

    def _on_docs_index_ready(self, generation: int, entries: object, error: str):
        if generation != self._index_generation:
            return
        self.nav.clear()
        if error:
            self._show_markdown(f"# Documentation\n\nCould not list docs: {error}")
            return
        docs = [_coerce_doc_entry(self._root, entry) for entry in entries]
        self._entries = {entry.identifier: entry for entry in docs}
        self._entry_by_path = {str(entry.path): entry.identifier for entry in docs}
        self._docs = [entry.identifier for entry in docs]
        if not docs:
            self._show_markdown(
                f"Documentation was not found at `{str(self._root).replace('`', '')}`."
            )
            return
        for entry in docs:
            item = QListWidgetItem(entry.display_title)
            item.setData(Qt.ItemDataRole.UserRole, entry.identifier)
            item.setToolTip(str(entry.path))
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)

    def _on_doc_ready(self, generation: int, name: str, text: str, error: str):
        if generation != self._doc_generation:
            return
        path = Path(name).resolve()
        if error:
            text = f"# Documentation\n\nCould not read `{path.name}`: {error}"
        self._current_doc_path = path
        self.viewer.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent) + os.sep))
        self.viewer.setHtml(markdown_document_html(text))
        self._select_doc(self._entry_by_path.get(str(path), path.name))
        if self._pending_anchor_generation == generation and self._pending_anchor:
            self.viewer.scrollToAnchor(self._pending_anchor)
        self._pending_anchor = ""

    def _show_markdown(self, text: str):
        self.viewer.setHtml(markdown_document_html(text))

    def closeEvent(self, event):
        self._index_generation += 1
        self._doc_generation += 1
        super().closeEvent(event)


def _coerce_doc_entry(root: Path, value: object) -> DocEntry:
    if isinstance(value, DocEntry):
        return value
    if isinstance(value, (list, tuple)) and len(value) >= 5:
        identifier, title, path, is_extension, extension_id = value[:5]
        return DocEntry(
            str(identifier),
            str(title),
            Path(str(path)).resolve(),
            bool(is_extension),
            str(extension_id),
        )
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        identifier, title = value[:2]
        path = (root / str(identifier)).resolve()
        return DocEntry(str(identifier), str(title), path)
    raise ValueError(f"invalid doc entry: {value!r}")


def _is_doc_path(root: Path, path: Path) -> bool:
    try:
        common = os.path.commonpath([root.resolve(), path.resolve()])
    except ValueError:
        return False
    return common == str(root.resolve()) and path.suffix.lower() == ".md" and path.is_file()

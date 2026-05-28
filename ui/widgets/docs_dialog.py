import html
import os
import re
import sys
from pathlib import Path

import markdown as _md
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QTextBrowser,
)

from ui.theme import markdown_css, palette


_DOC_ORDER = [
    "configuration.md",
    "custom-models.md",
    "extensions.md",
    "skills.md",
    "compact.md",
]
_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def docs_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "docs",
        Path(getattr(sys, "_MEIPASS", "")) / "docs",
        Path(sys.executable).resolve().parent / "docs",
        Path(sys.prefix) / "share" / "aichs" / "docs",
    ]
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
    body = _md.markdown(markdown_text, extensions=["fenced_code", "tables", "toc"])
    p = palette()
    css = (
        markdown_css()
        + f"body {{ background:{p['BG2']}; padding:0 4px 12px 4px; }}"
        + "table { border-collapse:collapse; margin:8px 0; }"
        + f"th,td {{ border:1px solid {p['BORDER']}; padding:5px 8px; }}"
        + f"th {{ background:{p['BG3']}; }}"
    )
    return f"<style>{css}</style>{body}"


class DocsDialog(QDialog):
    def __init__(self, parent=None, root: Path | None = None):
        super().__init__(parent)
        self._root = root or docs_dir()
        self._docs = available_docs(self._root)

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
            f"QListWidget {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px; outline:none; }}"
            "QListWidget::item { padding:8px 10px; border-radius:6px; margin:2px 4px; }"
            f"QListWidget::item:hover {{ background:{p['BG2']}; }}"
            f"QListWidget::item:selected {{ background:{p['SELECTION']};"
            f"color:{p['SELECTION_TEXT']}; }}"
        )
        layout.addWidget(self.nav)

        self.viewer = QTextBrowser()
        self.viewer.setOpenLinks(False)
        self.viewer.setStyleSheet(
            f"QTextBrowser {{ background:{p['BG2']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px; padding:12px; }}"
        )
        self.viewer.anchorClicked.connect(self._open_link)
        layout.addWidget(self.viewer, 1)

        for path in self._docs:
            item = QListWidgetItem(doc_title(path))
            item.setData(Qt.ItemDataRole.UserRole, path.name)
            item.setToolTip(path.name)
            self.nav.addItem(item)
        self.nav.currentItemChanged.connect(self._on_doc_selected)

        if self._docs:
            self.nav.setCurrentRow(0)
        else:
            self.viewer.setHtml(
                f"<style>{markdown_css()}</style>"
                f"<p>Documentation was not found at {html.escape(str(self._root))}.</p>"
            )

    def _on_doc_selected(self, current: QListWidgetItem | None, _previous=None):
        if current:
            self.open_doc(str(current.data(Qt.ItemDataRole.UserRole)))

    def open_doc(self, name: str):
        path = (self._root / name).resolve()
        if not self._is_doc_path(path):
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            text = f"# Documentation\n\nCould not read `{name}`: {exc}"
        self.viewer.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent) + os.sep))
        self.viewer.setHtml(markdown_document_html(text))
        self._select_doc(path.name)

    def _open_link(self, url: QUrl):
        target = url.toString()
        if target.startswith("#"):
            self.viewer.scrollToAnchor(target[1:])
            return
        if url.isRelative() or url.isLocalFile():
            raw = url.toLocalFile() if url.isLocalFile() else target
            local = (self._root / raw.split("#", 1)[0]).resolve()
            if self._is_doc_path(local):
                self.open_doc(local.name)
                anchor = target.split("#", 1)[1] if "#" in target else ""
                if anchor:
                    self.viewer.scrollToAnchor(anchor)
                return
        QDesktopServices.openUrl(url)

    def _is_doc_path(self, path: Path) -> bool:
        try:
            common = os.path.commonpath([self._root.resolve(), path.resolve()])
        except ValueError:
            return False
        return common == str(self._root.resolve()) and path.suffix.lower() == ".md" and path.is_file()

    def _select_doc(self, name: str):
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self.nav.setCurrentRow(row)
                return

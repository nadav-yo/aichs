from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QVBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

from ui.theme import (
    palette,
    card_frame_style,
    meta_font_pt,
    chat_font_pt,
    MONO_FONT_CSS,
    primary_button_style,
)


class ArtifactCard(QFrame):
    """Named, expandable artifact card for substantial response code blocks."""

    def __init__(
        self,
        language: str,
        code: str,
        on_open,
        title: str = "",
        reason: str = "",
        show_language: bool = True,
        show_preview_actions: bool = True,
        max_width: int = 560,
        parent=None,
    ):
        super().__init__(parent)
        display_title = title or language or "snippet"
        self._code = code
        self._expanded = False
        self._show_preview_actions = show_preview_actions

        self.setMaximumWidth(max_width)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(8)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        self._title_lbl = QLabel(display_title)
        self._title_lbl.setWordWrap(True)
        self._reason_lbl = QLabel(reason or "Extracted for readability.")
        self._reason_lbl.setWordWrap(True)

        title_col.addWidget(self._title_lbl)
        title_col.addWidget(self._reason_lbl)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        self._lang_lbl = QLabel(language or "text")
        self._lang_lbl.setVisible(show_language)
        self._lines_lbl = QLabel()
        line_count = len(code.splitlines())
        self._lines_lbl.setText(f"{line_count} lines" if line_count else "")
        self._lines_lbl.setVisible(bool(line_count))
        meta_row.addWidget(self._lang_lbl)
        meta_row.addWidget(self._lines_lbl)
        meta_row.addStretch()
        title_col.addLayout(meta_row)

        self._toggle_btn = QPushButton("Show")
        self._toggle_btn.setFixedHeight(24)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setEnabled(bool(code))
        self._toggle_btn.clicked.connect(self._toggle_preview)

        self._open_btn = QPushButton("Open")
        self._open_btn.setFixedHeight(24)
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(lambda: on_open(code, display_title))

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setFixedHeight(24)
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._code))

        header.addLayout(title_col, 1)
        if show_preview_actions:
            header.addWidget(self._toggle_btn, 0, Qt.AlignmentFlag.AlignTop)
            header.addWidget(self._copy_btn, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self._open_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setPlainText(code)
        self._preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._preview.setMaximumHeight(260)
        self._preview.hide()
        layout.addWidget(self._preview)

        self.apply_appearance()

    def _toggle_preview(self):
        self._expanded = not self._expanded
        self._preview.setVisible(self._expanded)
        self._toggle_btn.setText("Hide" if self._expanded else "Show")

    def apply_appearance(self):
        p = palette()
        fs = chat_font_pt()
        meta = meta_font_pt()
        self.setStyleSheet(card_frame_style())
        self._title_lbl.setStyleSheet(
            f"color:{p['TEXT']}; font-size:{fs}px; font-weight:600;"
            "background:transparent; border:none;"
        )
        self._reason_lbl.setStyleSheet(
            f"color:{p['TEXT_DIM']}; font-size:{meta}px; background:transparent; border:none;"
        )
        self._lang_lbl.setStyleSheet(
            f"color:{p['TEXT_DIM']}; font-size:{meta}px; font-family:{MONO_FONT_CSS};"
            "background:transparent; border:none;"
        )
        self._lines_lbl.setStyleSheet(
            f"color:{p['TEXT_DIM']}; font-size:{meta}px; background:transparent; border:none;"
        )
        secondary = (
            f"QPushButton {{ background:{p['BG3']}; color:{p['TEXT']}; border:1px solid {p['BORDER']};"
            f"border-radius:4px; padding:0 8px; font-size:{meta}px; }}"
            f"QPushButton:hover {{ background:{p['BORDER']}; }}"
        )
        primary = primary_button_style(
            border_radius=4,
            padding="0 8px",
            font_size=meta,
            font_weight="600",
        )
        self._toggle_btn.setStyleSheet(primary)
        self._copy_btn.setStyleSheet(secondary)
        self._open_btn.setStyleSheet(secondary)
        self._preview.setStyleSheet(
            f"QPlainTextEdit {{ background:{p['BG2']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px;"
            f"font-family:{MONO_FONT_CSS}; font-size:{max(10, fs - 1)}px; padding:8px; }}"
        )

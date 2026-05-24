from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

from ui.theme import palette, ACCENT, card_frame_style, meta_font_pt, chat_font_pt, MONO_FONT_CSS


class ArtifactCard(QFrame):
    """Compact code-block card. 'Open ↗' sends content to the file viewer."""

    def __init__(self, language: str, code: str, on_open, title: str = "", parent=None):
        super().__init__(parent)
        display_title = title or language or "snippet"
        self._code = code

        self.setMaximumWidth(480)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)

        self._lang_lbl = QLabel(language or "text")
        self._lines_lbl = QLabel()
        line_count = len(code.splitlines())
        self._lines_lbl.setText(f"{line_count} lines" if line_count else "")
        self._lines_lbl.setVisible(bool(line_count))

        open_btn = QPushButton("Open ↗")
        open_btn.setFixedHeight(22)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: on_open(code, display_title))

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setFixedHeight(22)
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._code))

        layout.addWidget(self._lang_lbl)
        layout.addWidget(self._lines_lbl)
        layout.addStretch()
        layout.addWidget(self._copy_btn)
        layout.addWidget(open_btn)

        self.apply_appearance()

    def apply_appearance(self):
        p = palette()
        fs = chat_font_pt()
        meta = meta_font_pt()
        self.setStyleSheet(card_frame_style())
        self._lang_lbl.setStyleSheet(
            f"color:{p['TEXT']}; font-size:{fs}px; font-family:{MONO_FONT_CSS}; font-weight:bold;"
            "background:transparent; border:none;"
        )
        self._lines_lbl.setStyleSheet(
            f"color:{p['TEXT_DIM']}; font-size:{meta}px; background:transparent; border:none;"
        )
        self._copy_btn.setStyleSheet(
            f"QPushButton {{ background:{p['BG3']}; color:{p['TEXT']}; border:1px solid {p['BORDER']};"
            f"border-radius:4px; padding:0 8px; font-size:{meta}px; }}"
            f"QPushButton:hover {{ background:{p['BORDER']}; }}"
        )
        for btn in self.findChildren(QPushButton):
            if btn is not self._copy_btn:
                btn.setStyleSheet(
                    f"QPushButton {{ background:{ACCENT}; color:white; border:none;"
                    f"border-radius:4px; padding:0 8px; font-size:{meta}px; }}"
                    f"QPushButton:hover {{ background:#0066dd; }}"
                )

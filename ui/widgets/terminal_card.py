from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel
from PyQt6.QtCore import Qt

from config import MAX_TERMINAL_BLOCKS
from ui.theme import palette, card_frame_style, meta_font_pt, mono_font_pt, mono_font


class TerminalCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumWidth(560)
        self._exit_code: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFrameShape(QFrame.Shape.NoFrame)
        self._output.setMaximumHeight(180)
        self._output.document().setMaximumBlockCount(MAX_TERMINAL_BLOCKS)

        self._footer = QFrame()
        footer_row = QHBoxLayout(self._footer)
        footer_row.setContentsMargins(10, 4, 10, 4)

        self._status = QLabel("Running…")
        footer_row.addStretch()
        footer_row.addWidget(self._status)

        root.addWidget(self._output, 1)
        root.addWidget(self._footer)

        self.apply_appearance()

    def apply_appearance(self):
        p = palette()
        mono = mono_font_pt()
        meta = meta_font_pt()
        self._output.setFont(mono_font(mono))
        self.setStyleSheet(card_frame_style())
        self._output.setStyleSheet(
            f"QTextEdit {{ background:transparent; color:{p['TEXT']}; border:none; padding:8px 10px; }}"
        )
        self._footer.setStyleSheet(
            f"QFrame {{ background:transparent; border-top:1px solid {p['BORDER']}; }}"
        )
        if self._exit_code is None:
            self._status.setStyleSheet(
                f"color:{p['TEXT_DIM']}; font-size:{meta}px; background:transparent; border:none;"
            )
        elif self._exit_code == 0:
            self._status.setStyleSheet(
                f"color:#4ade80; font-size:{meta}px; background:transparent; border:none;"
            )
        else:
            self._status.setStyleSheet(
                f"color:#f87171; font-size:{meta}px; background:transparent; border:none;"
            )

    def append_line(self, line: str):
        self._output.append(line)
        self._output.verticalScrollBar().setValue(
            self._output.verticalScrollBar().maximum()
        )

    def finish(self, exit_code: int = 0):
        self._exit_code = exit_code
        if exit_code == 0:
            self._status.setText("✓  done")
        else:
            self._status.setText(f"✗  exit {exit_code}")
        self.apply_appearance()

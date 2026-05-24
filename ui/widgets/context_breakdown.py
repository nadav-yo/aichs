from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QWidget,
)
from PyQt6.QtCore import Qt

from services.context_budget import ContextBudget, format_bytes
from ui.theme import palette, ACCENT, meta_font_pt, chat_font_pt, MONO_FONT_CSS


class ContextBreakdownDialog(QDialog):
    def __init__(self, budget: ContextBudget, model: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Context usage")
        self.setMinimumWidth(420)

        p = palette()
        fs = chat_font_pt()
        meta = meta_font_pt()
        self.setStyleSheet(f"QDialog {{ background:{p['BG2']}; color:{p['TEXT']}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = QLabel("Context window")
        header.setStyleSheet(f"font-size:{fs + 2}px; font-weight:bold; color:{p['TEXT']};")
        root.addWidget(header)

        summary = QLabel(
            f"{budget.used_tokens:,} / {budget.window_tokens:,} tokens  "
            f"({budget.pct:.0f}%)  ·  {format_bytes(budget.used_bytes)}"
        )
        summary.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta}px;")
        root.addWidget(summary)

        model_lbl = QLabel(f"Model: {model}")
        model_lbl.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta}px;")
        root.addWidget(model_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{p['BORDER']}; max-height:1px;")
        root.addWidget(sep)

        cols = QHBoxLayout()
        cols.addStretch()
        tok_hdr = QLabel("tokens")
        tok_hdr.setFixedWidth(56)
        tok_hdr.setAlignment(Qt.AlignmentFlag.AlignRight)
        tok_hdr.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta}px;")
        size_hdr = QLabel("size")
        size_hdr.setFixedWidth(64)
        size_hdr.setAlignment(Qt.AlignmentFlag.AlignRight)
        size_hdr.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta}px;")
        cols.addWidget(tok_hdr)
        cols.addWidget(size_hdr)
        root.addLayout(cols)

        for seg in budget.segments:
            root.addWidget(self._row(seg.label, seg.byte_count, seg.token_count, seg.detail, p, fs, meta))

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background:{p['BORDER']}; max-height:1px;")
        root.addWidget(sep2)

        footer = QLabel(
            f"Compaction starts around {budget.compaction_limit_tokens:,} tokens "
            f"({budget.reserve_tokens:,} reserved for output)."
        )
        footer.setWordWrap(True)
        footer.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta}px;")
        root.addWidget(footer)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        close.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:white; border:none;"
            f"border-radius:6px; padding:6px 18px; font-size:{meta}px; }}"
        )
        btn_row.addWidget(close)
        root.addLayout(btn_row)

    @staticmethod
    def _row(label: str, nbytes: int, tokens: int, detail: str, p: dict, fs: int, meta: int) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 4)

        left = QVBoxLayout()
        left.setSpacing(2)
        title = QLabel(label)
        title.setStyleSheet(f"color:{p['TEXT']}; font-size:{fs}px;")
        left.addWidget(title)
        if detail:
            sub = QLabel(detail)
            sub.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta}px;")
            left.addWidget(sub)

        size = QLabel(format_bytes(nbytes))
        size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        size.setStyleSheet(f"color:{p['TEXT']}; font-size:{fs}px; font-family:{MONO_FONT_CSS};")

        tokens_lbl = QLabel(f"{tokens:,}")
        tokens_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tokens_lbl.setFixedWidth(56)
        tokens_lbl.setStyleSheet(
            f"color:{p['TEXT_DIM']}; font-size:{meta}px; font-family:{MONO_FONT_CSS};"
        )

        wrap = QWidget()
        outer = QHBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(left, 1)
        outer.addWidget(tokens_lbl)
        outer.addWidget(size)
        return wrap
